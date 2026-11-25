#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const LOCK_PATH = "assets/exports.lock";
const SOURCE_PATH = "profiles/universal/scripts/assets.js";
const MODULE_PROFILES = ["apple", "web"];
const INK_LIGHT = "#18181b";
const INK_DARK = "#f4f4f5";
const BOARD_LIGHT = "#ffffff";
const BOARD_DARK = "#09090b";
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const FIXED_PDF_DATE = "D:20000101000000Z00'00'";
const PDF_ID = "<00000000000000000000000000000000> <00000000000000000000000000000000>";
const RESVG_TOOL = {
  command: "resvg",
  env: "RESVG",
  hint: "install it with mise install (resvg is pinned in the universal Profile)",
};

const PDF_HELPER = String.raw`import Cocoa
import WebKit

final class PDFDelegate: NSObject, WKNavigationDelegate {
    let webView: WKWebView
    let output: URL
    let size: CGSize

    init(webView: WKWebView, output: URL, size: CGSize) {
        self.webView = webView
        self.output = output
        self.size = size
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let configuration = WKPDFConfiguration()
        configuration.rect = CGRect(origin: .zero, size: size)
        webView.createPDF(configuration: configuration) { result in
            switch result {
            case .success(let data):
                do {
                    try data.write(to: self.output)
                    NSApplication.shared.terminate(nil)
                } catch {
                    FileHandle.standardError.write("svg-to-pdf: \(error)\n".data(using: .utf8)!)
                    exit(1)
                }
            case .failure(let error):
                FileHandle.standardError.write("svg-to-pdf: \(error)\n".data(using: .utf8)!)
                exit(1)
            }
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        FileHandle.standardError.write("svg-to-pdf: \(error)\n".data(using: .utf8)!)
        exit(1)
    }
}

let arguments = CommandLine.arguments
guard arguments.count == 5,
      let width = Double(arguments[3]),
      let height = Double(arguments[4])
else {
    FileHandle.standardError.write("usage: svg-to-pdf in.html out.pdf width height\n".data(using: .utf8)!)
    exit(2)
}
let input = URL(fileURLWithPath: arguments[1])
let output = URL(fileURLWithPath: arguments[2])
let size = CGSize(width: width, height: height)

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let webView = WKWebView(frame: CGRect(origin: .zero, size: size))
webView.setValue(false, forKey: "drawsBackground")
let delegate = PDFDelegate(webView: webView, output: output, size: size)
webView.navigationDelegate = delegate
webView.loadFileURL(input, allowingReadAccessTo: input.deletingLastPathComponent())
app.run()
`;

function usage() {
  return `Usage: assets.js [--write|--check] [--root DIR] [--out DIR] [--modules DIR]

Generate every brand export from assets/brand/icon.svg and logo.svg.

Options:
  --write       Write the exports and assets/exports.lock (default)
  --check       Verify the lock and, when the tools exist, regenerate and compare
  --root DIR    Repository root holding assets/brand (default: the current directory)
  --out DIR     Write or compare exports under DIR (default: the repository root)
  --modules DIR Directory holding profile modules (default: profiles)
  -h, --help    Show this help.`;
}

function parseArgs(argv) {
  const options = {
    mode: "write",
    root: process.cwd(),
    out: null,
    modules: path.resolve(__dirname, "..", ".."),
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      options.help = true;
    } else if (argument === "--write") {
      options.mode = "write";
    } else if (argument === "--check") {
      options.mode = "check";
    } else if (argument === "--root" || argument === "--out" || argument === "--modules") {
      const value = argv[index + 1];
      if (value === undefined) {
        throw new Error(`assets: ${argument} needs a directory`);
      }
      options[argument.slice(2)] = path.resolve(value);
      index += 1;
    } else {
      throw new Error(`assets: unknown argument ${argument}`);
    }
  }
  options.out = options.out ?? options.root;
  return options;
}

function sha256(data) {
  return crypto.createHash("sha256").update(data).digest("hex");
}

function posix(relative) {
  return relative.split(path.sep).join("/");
}

function relativePath(root, file) {
  return posix(path.relative(root, file));
}

function sortedObject(entries) {
  const result = {};
  for (const key of Object.keys(entries).sort()) {
    result[key] = entries[key];
  }
  return result;
}

function walkFiles(directory) {
  if (!fs.existsSync(directory)) {
    return [];
  }
  const found = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      found.push(...walkFiles(full));
    } else if (entry.isFile()) {
      found.push(full);
    }
  }
  return found;
}

function shellQuote(value) {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

function toolPresent(command) {
  if (command.includes("/")) {
    try {
      fs.accessSync(command, fs.constants.X_OK);
      return true;
    } catch {
      return false;
    }
  }
  return spawnSync("sh", ["-c", `command -v ${shellQuote(command)}`], { stdio: "ignore" }).status === 0;
}

function resolveTool(tool) {
  return process.env[tool.env] || tool.command;
}

function toolLabel(tool) {
  const command = resolveTool(tool);
  return command === tool.command ? command : `${command} (expected ${tool.command})`;
}

function viewBox(svg) {
  const match = svg.match(/<svg\b[^>]*\bviewBox\s*=\s*["']([^"']+)["']/i);
  if (match === null) {
    return { width: 1024, height: 1024 };
  }
  const numbers = match[1].trim().split(/[\s,]+/).map(Number);
  if (numbers.length !== 4 || !numbers.every(Number.isFinite) || numbers[2] <= 0 || numbers[3] <= 0) {
    return { width: 1024, height: 1024 };
  }
  return { width: numbers[2], height: numbers[3] };
}

function colorize(svg, colour) {
  return svg.replace(/<svg\b([^>]*)>/i, (match, attributes) => {
    const without = attributes.replace(/\scolor\s*=\s*(?:"[^"]*"|'[^']*')/i, "");
    return `<svg${without} color="${colour}">`;
  });
}

function pngSize(buffer) {
  if (buffer.length < 24 || !buffer.subarray(0, 8).equals(PNG_SIGNATURE)) {
    return null;
  }
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

function jpegSize(buffer) {
  if (buffer.length < 4 || buffer[0] !== 0xff || buffer[1] !== 0xd8) {
    return null;
  }
  let offset = 2;
  while (offset + 9 < buffer.length) {
    if (buffer[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    const marker = buffer[offset + 1];
    if (marker === 0xd8 || marker === 0xd9) {
      offset += 2;
      continue;
    }
    if (marker === 0xda) {
      return null;
    }
    const length = buffer.readUInt16BE(offset + 2);
    const isFrame = [0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker);
    if (isFrame) {
      return { width: buffer.readUInt16BE(offset + 7), height: buffer.readUInt16BE(offset + 5) };
    }
    offset += 2 + length;
  }
  return null;
}

function imageSize(buffer) {
  const size = pngSize(buffer) ?? jpegSize(buffer);
  if (size === null) {
    throw new Error("assets: store screenshots must be PNG or JPEG images");
  }
  return size;
}

function readProfiles(root) {
  const file = path.join(root, "AGENTS.md");
  if (!fs.existsSync(file)) {
    return ["universal"];
  }
  const match = fs.readFileSync(file, "utf8").match(/^Profiles:[ \t]*(.+)$/m);
  if (match === null) {
    return ["universal"];
  }
  const profiles = match[1].split(",").map((entry) => entry.trim()).filter(Boolean);
  return profiles.includes("universal") ? profiles : ["universal", ...profiles];
}

function loadMasters(root) {
  const names = ["icon.svg", "logo.svg"];
  const present = names.filter((name) => fs.existsSync(path.join(root, "assets", "brand", name)));
  if (present.length === 0) {
    return null;
  }
  if (present.length !== names.length) {
    const missing = names.filter((name) => !present.includes(name));
    throw new Error(`assets: assets/brand/${missing[0]} is missing; brand-assets writes icon.svg and logo.svg`);
  }
  const masters = {};
  for (const name of names) {
    const relative = `assets/brand/${name}`;
    const file = path.join(root, relative);
    const buffer = fs.readFileSync(file);
    masters[name === "icon.svg" ? "icon" : "logo"] = { path: relative, text: buffer.toString("utf8"), buffer };
  }
  try {
    const checker = require("./asset-masters.js");
    for (const master of Object.values(masters)) {
      const problems = checker.checkFile(path.join(root, master.path));
      if (problems.length > 0) {
        throw new Error(problems.map((problem) => `assets: ${master.path}: ${problem}`).join("\n"));
      }
    }
  } catch (error) {
    if (error.code !== "MODULE_NOT_FOUND") {
      throw error;
    }
  }
  return masters;
}

function loadScreenshots(root) {
  const directory = path.join(root, "assets", "store", "screenshots");
  return walkFiles(directory)
    .filter((file) => /\.(?:png|jpe?g)$/i.test(file))
    .sort()
    .map((file) => ({ path: relativePath(root, file), buffer: fs.readFileSync(file) }));
}

function loadModuleSources(root, modulesDirectory, profiles) {
  const driverSource = fs.readFileSync(__filename, "utf8");
  const modules = [
    {
      profile: "universal",
      sourceKey: SOURCE_PATH,
      source: driverSource,
      tools: { resvg: RESVG_TOOL },
      managedRoots: [],
      generate: universalGenerate,
    },
  ];
  for (const profile of MODULE_PROFILES) {
    if (!profiles.includes(profile)) {
      continue;
    }
    const file = path.join(modulesDirectory, profile, "scripts", "assets.js");
    if (!fs.existsSync(file)) {
      throw new Error(`assets: the ${profile} Profile needs ${posix(path.relative(root, file))}`);
    }
    const source = fs.readFileSync(file, "utf8");
    const loaded = require(file);
    if (loaded.profile !== profile || typeof loaded.generate !== "function") {
      throw new Error(`assets: ${posix(path.relative(root, file))} does not export the ${profile} generator`);
    }
    modules.push({
      profile,
      sourceKey: `profiles/${profile}/scripts/assets.js`,
      source,
      tools: loaded.tools ?? {},
      managedRoots: loaded.managedRoots ?? [],
      generate: loaded.generate,
    });
  }
  return modules;
}

function generatorHashes(modules) {
  const hashes = {};
  for (const module of modules) {
    hashes[module.sourceKey] = sha256(module.source);
  }
  return sortedObject(hashes);
}

function inputHashes(root, masters, screenshots) {
  const hashes = {};
  for (const master of Object.values(masters)) {
    hashes[master.path] = sha256(master.buffer);
  }
  for (const screenshot of screenshots) {
    hashes[screenshot.path] = sha256(screenshot.buffer);
  }
  return sortedObject(hashes);
}

class Context {
  constructor({ root, out, masters, screenshots, tools }) {
    this.root = root;
    this.out = out;
    this.masters = masters;
    this.screenshots = screenshots;
    this.tools = tools;
    this.outputs = new Map();
    this.temporary = [];
    this.resvg = null;
    this.pdfBinary = null;
  }

  write(relative, data) {
    const file = path.join(this.out, relative);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, data);
    this.outputs.set(posix(relative), file);
  }

  writeFrom(relative, source) {
    this.write(relative, fs.readFileSync(source));
  }

  removeFile(relative) {
    fs.rmSync(path.join(this.out, relative), { force: true });
    this.outputs.delete(posix(relative));
  }

  removeDir(relative) {
    fs.rmSync(path.join(this.out, relative), { recursive: true, force: true });
    const prefix = `${posix(relative)}/`;
    for (const key of [...this.outputs.keys()]) {
      if (key.startsWith(prefix)) {
        this.outputs.delete(key);
      }
    }
  }

  tempDir(prefix) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), `factory-assets-${prefix}-`));
    this.temporary.push(directory);
    return directory;
  }

  cleanup() {
    for (const directory of this.temporary) {
      fs.rmSync(directory, { recursive: true, force: true });
    }
    this.temporary = [];
  }

  requireTool(name) {
    const tool = this.tools[name];
    if (tool === undefined) {
      throw new Error(`assets: no tool named ${name} is declared`);
    }
    const command = resolveTool(tool);
    if (!toolPresent(command)) {
      throw new Error(`assets: ${name} not found at ${toolLabel(tool)}; ${tool.hint}`);
    }
    return command;
  }

  resvgCommand() {
    if (this.resvg === null) {
      this.resvg = this.requireTool("resvg");
    }
    return this.resvg;
  }

  renderPng(svg, width, height, options = {}) {
    const resvgArgs = ["-", "-c", "-w", String(width)];
    if (height !== undefined) {
      resvgArgs.push("-h", String(height));
    }
    if (options.background !== undefined) {
      resvgArgs.push("--background", options.background);
    }
    const result = spawnSync(this.resvgCommand(), resvgArgs, {
      input: Buffer.from(svg),
      maxBuffer: 256 * 1024 * 1024,
    });
    if (result.status !== 0) {
      throw new Error(`assets: resvg failed\n${result.stderr.toString()}`);
    }
    return result.stdout;
  }

  pdf(svg, width, height, colour) {
    const html = `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;padding:0;background:transparent}svg{display:block;width:${width}px;height:${height}px;color:${colour}}</style></head><body>${svg}</body></html>`;
    const directory = this.tempDir("pdf");
    const htmlFile = path.join(directory, "input.html");
    const pdfFile = path.join(directory, "output.pdf");
    fs.writeFileSync(htmlFile, html);
    if (this.pdfBinary === null) {
      const source = path.join(directory, "svg-to-pdf.swift");
      const binary = path.join(directory, "svg-to-pdf");
      fs.writeFileSync(source, PDF_HELPER);
      this.requireTool("swiftc");
      const compile = spawnSync(resolveTool(this.tools.swiftc), ["-o", binary, source], { encoding: "utf8" });
      if (compile.status !== 0) {
        throw new Error(`assets: swiftc failed\n${compile.stderr}`);
      }
      this.pdfBinary = binary;
    }
    const run = spawnSync(this.pdfBinary, [htmlFile, pdfFile, String(width), String(height)], { encoding: "utf8" });
    if (run.status !== 0) {
      throw new Error(`assets: the PDF renderer failed\n${run.stderr}`);
    }
    return normalizePdf(fs.readFileSync(pdfFile));
  }

  embedPng(buffer) {
    return `data:image/png;base64,${buffer.toString("base64")}`;
  }
}

function normalizePdf(buffer) {
  const text = buffer
    .toString("latin1")
    .replace(/D:\d{14}Z00'00'/g, FIXED_PDF_DATE)
    .replace(/\/ID \[ <[0-9a-f]{32}>[\s]*<[0-9a-f]{32}> \]/g, `/ID [ ${PDF_ID} ]`);
  return Buffer.from(text, "latin1");
}

function fit(width, height, maximumWidth, maximumHeight) {
  const scale = Math.min(maximumWidth / width, maximumHeight / height);
  return { width: Math.round(width * scale), height: Math.round(height * scale) };
}

function universalGenerate(ctx) {
  const logo = ctx.masters.logo.text;
  const logoBox = viewBox(logo);
  const header = (output, width, height, logoWidth, ink, board) => {
    const art = ctx.renderPng(colorize(logo, ink), logoWidth);
    const heightOfArt = Math.round((logoWidth * logoBox.height) / logoBox.width);
    const left = Math.round((width - logoWidth) / 2);
    const top = Math.round((height - heightOfArt) / 2);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><rect width="${width}" height="${height}" fill="${board}"/><image href="${ctx.embedPng(art)}" x="${left}" y="${top}" width="${logoWidth}" height="${heightOfArt}"/></svg>`;
    ctx.write(output, ctx.renderPng(svg, width, height));
  };
  header("assets/brand/og-image.png", 1200, 630, 840, INK_LIGHT, BOARD_LIGHT);
  header("assets/brand/readme-header-light.png", 1200, 320, 760, INK_LIGHT, BOARD_LIGHT);
  header("assets/brand/readme-header-dark.png", 1200, 320, 760, INK_DARK, BOARD_DARK);

  ctx.removeDir("assets/store/framed");
  for (const screenshot of ctx.screenshots) {
    const size = imageSize(screenshot.buffer);
    const shown = fit(size.width, size.height, 2400, 1500);
    const left = Math.round((2880 - shown.width) / 2);
    const top = Math.round((1800 - shown.height) / 2);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="2880" height="1800" viewBox="0 0 2880 1800"><rect width="2880" height="1800" fill="#f4f4f5"/><rect x="${left - 16}" y="${top - 16}" width="${shown.width + 32}" height="${shown.height + 32}" rx="24" fill="#ffffff" stroke="#e4e4e7" stroke-width="2"/><image href="${ctx.embedPng(screenshot.buffer)}" x="${left}" y="${top}" width="${shown.width}" height="${shown.height}"/></svg>`;
    const name = path.basename(screenshot.path).replace(/\.[^.]+$/, "");
    ctx.write(`assets/store/framed/${name}.png`, ctx.renderPng(svg, 2880, 1800));
  }
}

module.exports = {
  Context,
  colorize,
  generatorHashes,
  inputHashes,
  loadMasters,
  loadModuleSources,
  loadScreenshots,
  parseArgs,
  readProfiles,
  resolveTool,
  sha256,
  toolPresent,
  usage,
  viewBox,
  walkFiles,
};

function readLock(root) {
  const file = path.join(root, LOCK_PATH);
  if (!fs.existsSync(file)) {
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    throw new Error(`assets: ${LOCK_PATH} is not valid JSON: ${error.message}`);
  }
}

function removeEmptyDirectories(directory, stop) {
  let current = directory;
  while (current.startsWith(stop) && current !== stop) {
    try {
      fs.rmdirSync(current);
    } catch {
      return;
    }
    current = path.dirname(current);
  }
}

function exportsFrom(ctx) {
  const hashes = {};
  for (const [relative, file] of ctx.outputs) {
    hashes[relative] = sha256(fs.readFileSync(file));
  }
  return sortedObject(hashes);
}

function generateModule(module, { root, out, masters, screenshots }) {
  const ctx = new Context({ root, out, masters, screenshots, tools: module.tools });
  try {
    module.generate(ctx);
    return ctx;
  } finally {
    ctx.cleanup();
  }
}

function write(options) {
  const masters = loadMasters(options.root);
  if (masters === null) {
    process.stdout.write("assets: no SVG masters under assets/brand; nothing to generate\n");
    return 0;
  }
  const profiles = readProfiles(options.root);
  const modules = loadModuleSources(options.root, options.modules, profiles);
  const screenshots = loadScreenshots(options.root);
  const ctx = new Context({ root: options.root, out: options.out, masters, screenshots, tools: {} });
  try {
    for (const module of modules) {
      Object.assign(ctx.tools, module.tools);
      module.generate(ctx);
    }
    const exports = exportsFrom(ctx);
    const lock = {
      version: 1,
      generator: generatorHashes(modules),
      masters: inputHashes(options.root, masters, screenshots),
      exports,
    };
    const previous = readLock(options.out);
    if (previous !== null && typeof previous.exports === "object") {
      for (const relative of Object.keys(previous.exports)) {
        if (exports[relative] === undefined) {
          const file = path.join(options.out, relative);
          fs.rmSync(file, { force: true });
          removeEmptyDirectories(path.dirname(file), options.out);
        }
      }
    }
    ctx.write(LOCK_PATH, `${JSON.stringify(lock, null, 2)}\n`);
    process.stdout.write(`assets: wrote ${Object.keys(exports).length} exports and ${LOCK_PATH}\n`);
    return 0;
  } finally {
    ctx.cleanup();
  }
}

function lockProfiles(paths) {
  return {
    universal: paths.filter((relative) => !/^(?:public\/(?:favicon\.(?:svg|ico)|apple-touch-icon\.png|icon-\d+\.png)|assets\/brand\/(?:AppIcon\.(?:icon\/|icns)|Brand\.xcassets\/))/.test(relative)),
    web: paths.filter((relative) => /^public\/(?:favicon\.(?:svg|ico)|apple-touch-icon\.png|icon-\d+\.png)$/.test(relative)),
    apple: paths.filter((relative) => /^assets\/brand\/(?:AppIcon\.(?:icon\/|icns)|Brand\.xcassets\/)/.test(relative)),
  };
}

function verifyLock(options) {
  const lock = readLock(options.root);
  if (lock === null) {
    throw new Error(`assets: ${LOCK_PATH} is missing; run mise run assets`);
  }
  if (lock.version !== 1) {
    throw new Error(`assets: ${LOCK_PATH} has an unknown version; run mise run assets`);
  }
  const masters = loadMasters(options.root);
  if (masters === null) {
    throw new Error(`assets: ${LOCK_PATH} exists but assets/brand has no masters; run mise run assets`);
  }
  const profiles = readProfiles(options.root);
  const modules = loadModuleSources(options.root, options.modules, profiles);
  const screenshots = loadScreenshots(options.root);
  const problems = [];

  const generator = generatorHashes(modules);
  for (const [source, hash] of Object.entries(lock.generator ?? {})) {
    if (generator[source] === undefined) {
      problems.push(`${source} is not loaded by the ${profiles.join(", ")} Profiles; run mise run assets`);
    } else if (generator[source] !== hash) {
      problems.push(`${source} changed since ${LOCK_PATH}; run mise run assets`);
    }
  }
  for (const source of Object.keys(generator)) {
    if ((lock.generator ?? {})[source] === undefined) {
      problems.push(`${source} is not covered by ${LOCK_PATH}; run mise run assets`);
    }
  }

  const inputs = inputHashes(options.root, masters, screenshots);
  for (const [input, hash] of Object.entries(lock.masters ?? {})) {
    if (inputs[input] === undefined) {
      problems.push(`${input} is missing since ${LOCK_PATH}; run mise run assets`);
    } else if (inputs[input] !== hash) {
      problems.push(`${input} changed since ${LOCK_PATH}; run mise run assets`);
    }
  }
  for (const input of Object.keys(inputs)) {
    if ((lock.masters ?? {})[input] === undefined) {
      problems.push(`${input} is not covered by ${LOCK_PATH}; run mise run assets`);
    }
  }

  const exports = lock.exports ?? {};
  const verified = [];
  for (const [relative, hash] of Object.entries(exports)) {
    const file = path.join(options.root, relative);
    if (!fs.existsSync(file)) {
      problems.push(`${relative} is missing; run mise run assets`);
      continue;
    }
    if (sha256(fs.readFileSync(file)) !== hash) {
      problems.push(`${relative} changed since ${LOCK_PATH}; run mise run assets`);
      continue;
    }
    verified.push(relative);
  }

  if (problems.length > 0) {
    for (const problem of [...new Set(problems)]) {
      writeError(problem);
    }
    return { status: 1, lock, modules, masters, screenshots, verified };
  }
  return { status: 0, lock, modules, masters, screenshots, verified };
}

function writeError(message) {
  process.stderr.write(`${message.startsWith("assets: ") ? message : `assets: ${message}`}\n`);
}

function check(options) {
  if (!fs.existsSync(path.join(options.root, LOCK_PATH)) && loadMasters(options.root) === null) {
    process.stdout.write("assets: no SVG masters under assets/brand; nothing to check\n");
    return 0;
  }
  let state;
  try {
    state = verifyLock(options);
  } catch (error) {
    writeError(error.message);
    return 1;
  }
  if (state.status !== 0) {
    return state.status;
  }

  const grouped = lockProfiles(Object.keys(state.lock.exports ?? {}));
  const lockOnly = [];
  const generated = [];
  for (const module of state.modules) {
    const missing = Object.entries(module.tools)
      .filter(([, tool]) => !toolPresent(resolveTool(tool)))
      .map(([name, tool]) => `${name} (${toolLabel(tool)})`);
    if (missing.length > 0) {
      const paths = grouped[module.profile] ?? [];
      lockOnly.push(`${module.profile} exports need ${missing.join(", ")}; verified ${paths.length} export(s) by lock only: ${paths.join(", ")}`);
      continue;
    }
    generated.push(module);
  }

  const out = fs.mkdtempSync(path.join(os.tmpdir(), "factory-assets-check-"));
  const problems = [];
  try {
    for (const module of generated) {
      const ctx = generateModule(module, { root: options.root, out, masters: state.masters, screenshots: state.screenshots });
      const produced = new Set(ctx.outputs.keys());
      for (const [relative, file] of ctx.outputs) {
        const committed = path.join(options.root, relative);
        if (!fs.existsSync(committed)) {
          problems.push(`${relative} is not committed; run mise run assets`);
        } else if (!fs.readFileSync(committed).equals(fs.readFileSync(file))) {
          problems.push(`${relative} differs from what mise run assets generates`);
        }
      }
      for (const root of module.managedRoots) {
        const full = path.join(options.root, root);
        for (const file of walkFiles(full)) {
          if (!produced.has(relativePath(options.root, file))) {
            problems.push(`${relativePath(options.root, file)} is not produced by mise run assets`);
          }
        }
      }
    }
  } catch (error) {
    problems.push(error.message);
  } finally {
    fs.rmSync(out, { recursive: true, force: true });
  }

  for (const line of lockOnly) {
    process.stdout.write(`assets: ${line}\n`);
  }
  if (problems.length > 0) {
    for (const problem of [...new Set(problems)]) {
      writeError(problem);
    }
    return 1;
  }
  process.stdout.write(`assets: ${state.verified.length} export(s) match ${LOCK_PATH}${generated.length === 0 ? "; tools missing, lock only" : ""}\n`);
  return 0;
}

function main(argv) {
  try {
    const options = parseArgs(argv);
    if (options.help) {
      process.stdout.write(`${usage()}\n`);
      return 0;
    }
    return options.mode === "check" ? check(options) : write(options);
  } catch (error) {
    writeError(error.message);
    return 1;
  }
}

if (require.main === module) {
  process.exitCode = main(process.argv.slice(2));
}
