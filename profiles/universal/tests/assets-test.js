#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const zlib = require("node:zlib");
const { spawnSync } = require("node:child_process");

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

const ROOT = path.resolve(__dirname, "..", "..", "..");
const DRIVER = path.join(ROOT, "profiles", "universal", "scripts", "assets.js");
const FIXTURES = path.join(__dirname, "fixtures", "brand");
const MASTERS = {
  "assets/brand/icon.svg": path.join(FIXTURES, "pass-icon.svg"),
  "assets/brand/logo.svg": path.join(FIXTURES, "pass-wordmark.svg"),
};

let failures = 0;

function ok(name) {
  process.stdout.write(`ok - assets: ${name}\n`);
}

function notOk(name, error) {
  failures += 1;
  process.stderr.write(`not ok - assets: ${name}\n  ${String(error.message).split("\n").join("\n  ")}\n`);
}

function test(name, run) {
  try {
    run();
    ok(name);
  } catch (error) {
    notOk(name, error);
  }
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function tempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `factory-assets-test-${prefix}-`));
}

function walk(directory) {
  if (!fs.existsSync(directory)) {
    return [];
  }
  const found = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      found.push(...walk(full));
    } else if (entry.isFile()) {
      found.push(full);
    }
  }
  return found;
}

function hash(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function tree(root) {
  return Object.fromEntries(walk(root).map((file) => [path.relative(root, file), hash(file)]).sort(([left], [right]) => left.localeCompare(right)));
}

function copyProfiles(root) {
  for (const profile of ["universal", "apple", "web"]) {
    const target = path.join(root, "profiles", profile, "scripts");
    fs.mkdirSync(target, { recursive: true });
    fs.copyFileSync(path.join(ROOT, "profiles", profile, "scripts", "assets.js"), path.join(target, "assets.js"));
  }
  fs.copyFileSync(
    path.join(ROOT, "profiles", "universal", "scripts", "asset-masters.js"),
    path.join(root, "profiles", "universal", "scripts", "asset-masters.js"),
  );
}

function fixture(prefix, profiles) {
  const root = tempDir(prefix);
  fs.mkdirSync(path.join(root, "assets", "brand"), { recursive: true });
  fs.writeFileSync(path.join(root, "AGENTS.md"), `# fixture\n\nProfiles: ${profiles}\n`);
  for (const [relative, source] of Object.entries(MASTERS)) {
    fs.copyFileSync(source, path.join(root, relative));
  }
  copyProfiles(root);
  return root;
}

function run(root, args, environment = {}) {
  return spawnSync(process.execPath, [DRIVER, ...args], {
    cwd: root,
    encoding: "utf8",
    env: { ...process.env, ...environment },
  });
}

function pngSize(file) {
  const buffer = fs.readFileSync(file);
  assert(buffer.subarray(0, 8).equals(PNG_SIGNATURE), `${file} is not a PNG`);
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

function pngPixel(buffer, x, y) {
  assert(buffer.subarray(0, 8).equals(PNG_SIGNATURE), "the rendered template is not a PNG");
  let offset = 8;
  let width = 0;
  let height = 0;
  let bitDepth = 0;
  let colorType = 0;
  const idat = [];
  while (offset < buffer.length) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.subarray(offset + 4, offset + 8).toString("latin1");
    const data = buffer.subarray(offset + 8, offset + 8 + length);
    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      bitDepth = data[8];
      colorType = data[9];
    } else if (type === "IDAT") {
      idat.push(data);
    }
    offset += 12 + length;
  }
  assert(bitDepth === 8, `expected an 8-bit PNG, got ${bitDepth}`);
  assert(colorType === 6 || colorType === 2, `expected RGB or RGBA, got colour type ${colorType}`);
  const bytes = colorType === 6 ? 4 : 3;
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = width * bytes;
  let previous = Buffer.alloc(stride);
  let cursor = 0;
  const rows = [];
  for (let row = 0; row < height; row += 1) {
    const filter = raw[cursor];
    cursor += 1;
    const current = Buffer.from(raw.subarray(cursor, cursor + stride));
    cursor += stride;
    for (let index = 0; index < stride; index += 1) {
      const left = index >= bytes ? current[index - bytes] : 0;
      const up = previous[index];
      const upLeft = index >= bytes ? previous[index - bytes] : 0;
      const predictor = () => {
        const estimate = left + up - upLeft;
        const leftDistance = Math.abs(estimate - left);
        const upDistance = Math.abs(estimate - up);
        const upLeftDistance = Math.abs(estimate - upLeft);
        return leftDistance <= upDistance && leftDistance <= upLeftDistance ? left : upDistance <= upLeftDistance ? up : upLeft;
      };
      if (filter === 1) {
        current[index] = (current[index] + left) & 0xff;
      } else if (filter === 2) {
        current[index] = (current[index] + up) & 0xff;
      } else if (filter === 3) {
        current[index] = (current[index] + Math.floor((left + up) / 2)) & 0xff;
      } else if (filter === 4) {
        current[index] = (current[index] + predictor()) & 0xff;
      } else {
        assert(filter === 0, `unknown PNG filter ${filter}`);
      }
    }
    rows.push(current);
    previous = current;
  }
  const row = rows[y];
  const index = x * bytes;
  return { r: row[index], g: row[index + 1], b: row[index + 2], a: bytes === 4 ? row[index + 3] : 255 };
}

function compareTrees(left, right) {
  const leftFiles = walk(left).map((file) => path.relative(left, file)).sort();
  const rightFiles = walk(right).map((file) => path.relative(right, file)).sort();
  assert(leftFiles.join("\n") === rightFiles.join("\n"), `the two runs produced different files:\n${leftFiles.join("\n")}\n---\n${rightFiles.join("\n")}`);
  for (const relative of leftFiles) {
    assert(hash(path.join(left, relative)) === hash(path.join(right, relative)), `${relative} is not byte-identical`);
  }
}

function screenshot(root) {
  const result = spawnSync("resvg", ["-", "-c", "-w", "64", "-h", "40"], {
    input: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="40" viewBox="0 0 64 40"><rect width="64" height="40" fill="#3b82f6"/></svg>'),
  });
  assert(result.status === 0, "resvg could not create the screenshot fixture");
  const file = path.join(root, "assets", "store", "screenshots", "shot.png");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, result.stdout);
}

function writeMise(root, profiles) {
  const include = profiles.map((profile) => `  "${path.join(ROOT, "profiles", profile, "mise.toml")}",`).join("\n");
  const includes = profiles.map((profile) => `  "${path.join(ROOT, "profiles", profile, "tasks.toml")}",`).join("\n");
  fs.writeFileSync(path.join(root, "mise.toml"), `include = [\n${include}\n]\n\n[task_config]\nincludes = [\n${includes}\n]\n`);
  const trust = spawnSync("mise", ["trust", "-q", "mise.toml"], { cwd: root, encoding: "utf8" });
  assert(trust.status === 0, trust.stderr);
}

function pinnedFactory() {
  const repo = tempDir("factory-pin");
  for (const profile of ["universal", "apple", "web"]) {
    const target = path.join(repo, "profiles", profile, "scripts");
    fs.mkdirSync(target, { recursive: true });
    fs.copyFileSync(path.join(ROOT, "profiles", profile, "scripts", "assets.js"), path.join(target, "assets.js"));
  }
  fs.copyFileSync(
    path.join(ROOT, "profiles", "universal", "scripts", "asset-masters.js"),
    path.join(repo, "profiles", "universal", "scripts", "asset-masters.js"),
  );
  const git = (args) => {
    const result = spawnSync("git", args, { cwd: repo, encoding: "utf8" });
    assert(result.status === 0, result.stderr);
  };
  git(["init", "-q"]);
  git(["add", "-A"]);
  git(["-c", "user.name=assets-test", "-c", "user.email=assets-test@invalid", "commit", "-qm", "pin"]);
  git(["tag", "v-assets-test"]);
  fs.appendFileSync(path.join(repo, "profiles", "universal", "scripts", "assets.js"), "\n// the working tree differs from the pinned tag\n");
  return repo;
}

function runTask(root, args) {
  return spawnSync("mise", ["run", "assets", ...args], {
    cwd: root,
    encoding: "utf8",
    env: { ...process.env, FACTORY_CHECKOUT: ROOT },
  });
}

function webFixture() {
  const root = fixture("web", "universal, web");
  writeMise(root, ["universal", "web"]);
  screenshot(root);
  return root;
}

function universalWebTest() {
  const root = webFixture();
  const first = runTask(root, []);
  assert(first.status === 0, first.stderr);
  const expected = [
    "assets/brand/og-image.png",
    "assets/brand/readme-header-light.png",
    "assets/brand/readme-header-dark.png",
    "assets/store/framed/shot.png",
    "public/favicon.svg",
    "public/favicon.ico",
    "public/apple-touch-icon.png",
    "public/icon-192.png",
    "public/icon-512.png",
    "public/icon-16.png",
    "public/icon-32.png",
    "public/icon-48.png",
    "public/icon-128.png",
    "assets/exports.lock",
  ];
  for (const relative of expected) {
    assert(fs.existsSync(path.join(root, relative)), `${relative} was not generated`);
  }
  assert(pngSize(path.join(root, "assets/brand/og-image.png")).width === 1200, "the OG image is not 1200 wide");
  assert(pngSize(path.join(root, "assets/brand/og-image.png")).height === 630, "the OG image is not 630 tall");
  assert(pngSize(path.join(root, "assets/store/framed/shot.png")).width === 2880, "the store frame is not 2880 wide");
  assert(pngSize(path.join(root, "assets/store/framed/shot.png")).height === 1800, "the store frame is not 1800 tall");
  assert(fs.readFileSync(path.join(root, "public/favicon.ico")).readUInt16LE(2) === 1, "the favicon is not an ICO");

  const before = tree(root);
  const secondRun = runTask(root, []);
  assert(secondRun.status === 0, secondRun.stderr);
  assert(JSON.stringify(tree(root)) === JSON.stringify(before), "a second mise run assets changed the tree");

  const modules = path.join(root, "profiles");
  const out1 = tempDir("run-one");
  const out2 = tempDir("run-two");
  const one = run(root, ["--write", "--out", out1, "--modules", modules]);
  assert(one.status === 0, one.stderr);
  const two = run(root, ["--write", "--out", out2, "--modules", modules]);
  assert(two.status === 0, two.stderr);
  compareTrees(out1, out2);

  const clean = run(root, ["--check", "--modules", modules]);
  assert(clean.status === 0, clean.stderr);

  const taskCheck = runTask(root, ["--check"]);
  assert(taskCheck.status === 0, taskCheck.stderr);

  const favicon = path.join(root, "public/favicon.svg");
  const original = fs.readFileSync(favicon);
  fs.writeFileSync(favicon, `${original.toString("utf8")}<!-- edited -->\n`);
  const editedExport = run(root, ["--check", "--modules", modules]);
  assert(editedExport.status === 1, "a hand-edited export passed the check");
  assert(editedExport.stderr.includes("public/favicon.svg changed"), editedExport.stderr);
  fs.writeFileSync(favicon, original);

  const icon = path.join(root, "assets/brand/icon.svg");
  const originalIcon = fs.readFileSync(icon);
  fs.writeFileSync(icon, `${originalIcon.toString("utf8")}\n`);
  const editedMaster = run(root, ["--check", "--modules", modules]);
  assert(editedMaster.status === 1, "a master edited without regenerating passed the check");
  assert(editedMaster.stderr.includes("assets/brand/icon.svg changed"), editedMaster.stderr);
  fs.writeFileSync(icon, originalIcon);

  const webModule = path.join(root, "profiles", "web", "scripts", "assets.js");
  const originalModule = fs.readFileSync(webModule);
  fs.writeFileSync(webModule, `${originalModule.toString("utf8")}\n`);
  const editedGenerator = run(root, ["--check", "--modules", modules]);
  assert(editedGenerator.status === 1, "an edited generator passed the check");
  assert(editedGenerator.stderr.includes("profiles/web/scripts/assets.js changed"), editedGenerator.stderr);
  fs.writeFileSync(webModule, originalModule);

  const lockFile = path.join(root, "assets", "exports.lock");
  const originalLock = fs.readFileSync(lockFile);
  const staleFile = path.join(root, "assets", "brand", "extra.png");
  fs.writeFileSync(staleFile, "stale\n");
  const lock = JSON.parse(originalLock);
  lock.exports["assets/brand/extra.png"] = hash(staleFile);
  fs.writeFileSync(lockFile, `${JSON.stringify(lock, null, 2)}\n`);
  const stale = run(root, ["--check", "--modules", modules]);
  assert(stale.status === 1, "a stale lock export passed the check");
  assert(stale.stderr.includes("assets/brand/extra.png is in assets/exports.lock"), stale.stderr);
  fs.writeFileSync(lockFile, originalLock);
  fs.rmSync(staleFile, { force: true });

  const unexpected = path.join(root, "public", "icon-64.png");
  fs.writeFileSync(unexpected, "unexpected\n");
  const unexpectedCheck = run(root, ["--check", "--modules", modules]);
  assert(unexpectedCheck.status === 1, "an unreferenced web export passed the check");
  assert(unexpectedCheck.stderr.includes("public/icon-64.png is not produced"), unexpectedCheck.stderr);
  fs.rmSync(unexpected, { force: true });

  const stray = path.join(root, "assets", "brand", "random-export.png");
  fs.writeFileSync(stray, "stray\n");
  const strayCheck = run(root, ["--check", "--modules", modules]);
  assert(strayCheck.status === 1, "an unreferenced brand export passed the check");
  assert(strayCheck.stderr.includes("assets/brand/random-export.png is not produced"), strayCheck.stderr);
  fs.rmSync(stray, { force: true });

  const strayFrame = path.join(root, "assets", "store", "framed", "extra.png");
  fs.writeFileSync(strayFrame, "stray\n");
  const strayFrameCheck = run(root, ["--check", "--modules", modules]);
  assert(strayFrameCheck.status === 1, "an unreferenced store frame passed the check");
  assert(strayFrameCheck.stderr.includes("assets/store/framed/extra.png is not produced"), strayFrameCheck.stderr);
  fs.rmSync(strayFrame, { force: true });

  const strayFrameJpg = path.join(root, "assets", "store", "framed", "extra.jpg");
  fs.writeFileSync(strayFrameJpg, "stray\n");
  const strayFrameJpgCheck = run(root, ["--check", "--modules", modules]);
  assert(strayFrameJpgCheck.status === 1, "an unreferenced non-PNG store frame passed the check");
  assert(strayFrameJpgCheck.stderr.includes("assets/store/framed/extra.jpg is not produced"), strayFrameJpgCheck.stderr);
  fs.rmSync(strayFrameJpg, { force: true });

  fs.writeFileSync(favicon, Buffer.from(`${original.toString("utf8")}<!-- divergent -->\n`));
  const divergentLock = JSON.parse(fs.readFileSync(lockFile, "utf8"));
  divergentLock.exports["public/favicon.svg"] = hash(favicon);
  fs.writeFileSync(lockFile, `${JSON.stringify(divergentLock, null, 2)}\n`);
  const divergence = run(root, ["--check", "--modules", modules]);
  assert(divergence.status === 1, "a lock-consistent but divergent export passed the check");
  assert(divergence.stderr.includes("differs from what mise run assets generates"), divergence.stderr);
  fs.writeFileSync(favicon, original);
  fs.writeFileSync(lockFile, originalLock);

  const agents = path.join(root, "AGENTS.md");
  fs.renameSync(agents, `${agents}.bak`);
  const noProfiles = run(root, ["--check", "--modules", modules]);
  assert(noProfiles.status === 1, "a missing Profiles line passed the check");
  assert(noProfiles.stderr.includes("AGENTS.md is missing"), noProfiles.stderr);
  fs.renameSync(`${agents}.bak`, agents);

  const logo = path.join(root, "assets/brand/logo.svg");
  const originalLogo = fs.readFileSync(logo, "utf8");
  fs.writeFileSync(logo, originalLogo.replace(/viewBox\s*=\s*"[^"]*"/, 'viewBox="nope"'));
  const badViewBox = run(root, ["--write", "--modules", modules]);
  assert(badViewBox.status === 1, "an invalid viewBox was accepted");
  assert(badViewBox.stderr.includes("invalid viewBox"), badViewBox.stderr);
  fs.writeFileSync(logo, originalLogo);

  const badFactory = spawnSync("mise", ["run", "assets", "--check"], {
    cwd: root,
    encoding: "utf8",
    env: { ...process.env, FACTORY_CHECKOUT: "/nonexistent" },
  });
  assert(badFactory.status === 1, "an explicitly broken FACTORY_CHECKOUT fell back silently");
  assert(badFactory.stderr.includes("FACTORY_CHECKOUT"), badFactory.stderr);

  const miseFile = path.join(root, "mise.toml");
  const originalMise = fs.readFileSync(miseFile, "utf8");
  fs.writeFileSync(miseFile, `# git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=v1\n${originalMise}`);
  const badPinned = spawnSync("mise", ["run", "assets", "--check"], {
    cwd: root,
    encoding: "utf8",
    env: { ...process.env, FACTORY_CHECKOUT: "/nonexistent" },
  });
  assert(badPinned.status === 1, "a pinned task with a broken FACTORY_CHECKOUT passed");
  assert(badPinned.stderr.includes("FACTORY_CHECKOUT"), badPinned.stderr);
  fs.writeFileSync(miseFile, originalMise);

  const pinned = pinnedFactory();
  const pinnedTmp = tempDir("pinned-tmp");
  fs.writeFileSync(miseFile, `# git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=v-assets-test\n${originalMise}`);
  const pinnedCheck = spawnSync("mise", ["run", "assets", "--check"], {
    cwd: root,
    encoding: "utf8",
    env: { ...process.env, FACTORY_CHECKOUT: pinned, TMPDIR: pinnedTmp },
  });
  assert(pinnedCheck.status === 0, pinnedCheck.stderr);
  assert(
    fs.readdirSync(pinnedTmp).filter((name) => name.startsWith("factory-assets-")).length === 0,
    "the pinned task leaked its temporary tree",
  );
  fs.writeFileSync(miseFile, originalMise);

  fs.writeFileSync(icon, fs.readFileSync(path.join(FIXTURES, "fail-staircase.svg")));
  const traced = run(root, ["--write", "--modules", modules]);
  assert(traced.status === 1, "a traced master was generated");
  assert(traced.stderr.includes("staircase"), traced.stderr);
}

function lockOnlyFixture() {
  const root = fixture("lock-only", "universal, web, apple");
  const stub = `module.exports = {
  profile: "apple",
  tools: { ictool: { command: "/usr/bin/ictool", env: "ICTOOL", hint: "install Xcode 26 or later" } },
  generate(context) {
    context.write("assets/brand/AppIcon.icns", "fake icns\\n");
  },
};
`;
  fs.writeFileSync(path.join(root, "profiles", "apple", "scripts", "assets.js"), stub);
  const written = run(root, ["--write", "--modules", path.join(root, "profiles")]);
  assert(written.status === 0, written.stderr);
  return { root, icns: path.join(root, "assets", "brand", "AppIcon.icns") };
}

function appleTest() {
  const root = fixture("apple", "universal, apple");
  writeMise(root, ["universal", "apple"]);
  const modules = path.join(root, "profiles");
  const ictool = "/usr/bin/ictool";
  if (process.platform === "darwin" && fs.existsSync(ictool)) {
    const written = runTask(root, []);
    assert(written.status === 0, written.stderr);
    for (const relative of [
      "assets/brand/AppIcon.icon/icon.json",
      "assets/brand/AppIcon.icon/Assets/mark.svg",
      "assets/brand/AppIcon.icns",
      "assets/brand/Brand.xcassets/Logo.imageset/Logo-Light.pdf",
      "assets/brand/Brand.xcassets/Logo.imageset/Logo-Dark.pdf",
      "assets/brand/Brand.xcassets/MenuBarTemplate.imageset/MenuBarTemplate.pdf",
    ]) {
      assert(fs.existsSync(path.join(root, relative)), `${relative} was not generated`);
    }
    assert(fs.readFileSync(path.join(root, "assets/brand/AppIcon.icns")).subarray(0, 4).toString() === "icns", "the compatibility icon is not an icns");
    assert(fs.readFileSync(path.join(root, "assets/brand/Brand.xcassets/Logo.imageset/Logo-Light.pdf")).subarray(0, 4).toString() === "%PDF", "the logo is not a PDF");

    const out1 = tempDir("apple-one");
    const out2 = tempDir("apple-two");
    const one = run(root, ["--write", "--out", out1, "--modules", modules]);
    assert(one.status === 0, one.stderr);
    const two = run(root, ["--write", "--out", out2, "--modules", modules]);
    assert(two.status === 0, two.stderr);
    compareTrees(out1, out2);

    const clean = run(root, ["--check", "--modules", modules]);
    assert(clean.status === 0, clean.stderr);

    const menuPdf = path.join(root, "assets/brand/Brand.xcassets/MenuBarTemplate.imageset/MenuBarTemplate.pdf");
    const menuPng = path.join(tempDir("menu-template"), "template.png");
    const rendered = spawnSync("sips", ["-s", "format", "png", menuPdf, "--out", menuPng], { encoding: "utf8" });
    assert(rendered.status === 0, rendered.stderr);
    const interior = pngPixel(fs.readFileSync(menuPng), 12, 9);
    assert(interior.a < 16, `the menu-bar template is not transparent between the ring and the dot (alpha ${interior.a})`);
  } else {
    const missing = runTask(root, []);
    assert(missing.status === 1, "the apple exports were generated without ictool");
    assert(missing.stderr.includes("ictool"), missing.stderr);
  }

  const lockOnly = lockOnlyFixture();
  const lockOnlyModules = path.join(lockOnly.root, "profiles");
  const appleOnlyMissing = run(lockOnly.root, ["--check", "--modules", lockOnlyModules], { ICTOOL: "/nonexistent" });
  assert(appleOnlyMissing.status === 0, appleOnlyMissing.stderr);
  assert(appleOnlyMissing.stdout.includes("apple exports need ictool"), appleOnlyMissing.stdout);
  assert(!appleOnlyMissing.stdout.includes("universal exports need resvg"), appleOnlyMissing.stdout);
  assert(!appleOnlyMissing.stdout.includes("web exports need resvg"), appleOnlyMissing.stdout);

  const noTools = { ICTOOL: "/nonexistent", RESVG: "/nonexistent" };
  const clean = run(lockOnly.root, ["--check", "--modules", lockOnlyModules], noTools);
  assert(clean.status === 0, clean.stderr);
  assert(clean.stdout.includes("lock only"), clean.stdout);
  assert(clean.stdout.includes("ictool"), clean.stdout);

  const original = fs.readFileSync(lockOnly.icns);
  fs.writeFileSync(lockOnly.icns, Buffer.concat([original, Buffer.from("edited")]));
  const editedExport = run(lockOnly.root, ["--check", "--modules", lockOnlyModules], noTools);
  assert(editedExport.status === 1, "a hand-edited apple export passed the lock-only check");
  assert(editedExport.stderr.includes("assets/brand/AppIcon.icns changed"), editedExport.stderr);
  fs.writeFileSync(lockOnly.icns, original);

  const icon = path.join(lockOnly.root, "assets/brand/icon.svg");
  const originalIcon = fs.readFileSync(icon);
  fs.writeFileSync(icon, `${originalIcon.toString("utf8")}\n`);
  const editedMaster = run(lockOnly.root, ["--check", "--modules", lockOnlyModules], noTools);
  assert(editedMaster.status === 1, "a master edited without regenerating passed the lock-only check");
  assert(editedMaster.stderr.includes("assets/brand/icon.svg changed"), editedMaster.stderr);
  fs.writeFileSync(icon, originalIcon);

  const missing = run(root, ["--write", "--modules", modules], { ICTOOL: "/nonexistent" });
  assert(missing.status === 1, "writing apple exports without ictool passed");
  assert(missing.stderr.includes("ictool"), missing.stderr);
}

function checkerMissingTest() {
  const root = fixture("checker", "universal, web");
  const driver = path.join(tempDir("checker-driver"), "profiles", "universal", "scripts", "assets.js");
  fs.mkdirSync(path.dirname(driver), { recursive: true });
  fs.copyFileSync(DRIVER, driver);
  const missing = spawnSync(process.execPath, [driver, "--write", "--modules", path.join(root, "profiles")], {
    cwd: root,
    encoding: "utf8",
  });
  assert(missing.status === 1, "a missing master checker was skipped");
  assert(missing.stderr.includes("asset-masters.js"), missing.stderr);
}

for (const [name, runTest] of [
  ["a fixture gets every universal and web export from one command", universalWebTest],
  ["apple exports and the lock-only check", appleTest],
  ["a missing master checker fails instead of skipping validation", checkerMissingTest],
]) {
  test(name, runTest);
}

if (failures > 0) {
  process.stderr.write(`assets: ${failures} test(s) failed\n`);
  process.exitCode = 1;
}
