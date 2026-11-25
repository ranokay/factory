#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

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
  assert(buffer.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])), `${file} is not a PNG`);
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
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

  fs.writeFileSync(icon, fs.readFileSync(path.join(FIXTURES, "fail-staircase.svg")));
  const traced = run(root, ["--write", "--modules", modules]);
  assert(traced.status === 1, "a traced master was generated");
  assert(traced.stderr.includes("staircase"), traced.stderr);
}

function lockOnlyFixture() {
  const root = fixture("lock-only", "universal, apple");
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
  } else {
    const missing = runTask(root, []);
    assert(missing.status === 1, "the apple exports were generated without ictool");
    assert(missing.stderr.includes("ictool"), missing.stderr);
  }

  const lockOnly = lockOnlyFixture();
  const lockOnlyModules = path.join(lockOnly.root, "profiles");
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

for (const [name, runTest] of [
  ["a fixture gets every universal and web export from one command", universalWebTest],
  ["apple exports and the lock-only check", appleTest],
]) {
  test(name, runTest);
}

if (failures > 0) {
  process.stderr.write(`assets: ${failures} test(s) failed\n`);
  process.exitCode = 1;
}
