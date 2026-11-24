#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const { execFileSync, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const GENERATOR = path.join(__dirname, "brand-preview.js");
const SIZES = [16, 32, 64, 128, 256, 512, 1024];
const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "brand-preview-test-"));

function run(args) {
  return execFileSync(process.execPath, [GENERATOR, ...args], { encoding: "utf8" });
}

function fails(args) {
  return spawnSync(process.execPath, [GENERATOR, ...args], { encoding: "utf8" });
}

function svg(color, body = "") {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="24" fill="${color}"/>${body}</svg>\n`;
}

function write(name, content) {
  const file = path.join(workspace, name);
  fs.writeFileSync(file, content);
  return file;
}

function expectFailure(result, message) {
  assert.equal(result.status, 1, result.stderr);
  assert.ok(result.stderr.includes(message), result.stderr);
}

try {
  const orbit = write("orbit.svg", svg("#2563eb"));
  const fold = write("fold.svg", svg("#16a34a"));
  const signal = write("signal.svg", svg("#ea580c"));
  const output = path.join(workspace, "out", "preview.html");
  const args = [
    "--title", "Northwind",
    "--subtitle", "A calm tool for long projects.",
    "--site", "northwind.app",
    "--concept", `Orbit=${orbit}`,
    "--concept", `Fold=${fold}`,
    "--concept", `Signal=${signal}`,
    "--note", "Orbit=A ring in motion",
    "--output", output,
  ];

  assert.match(run(args), /wrote/);
  const html = fs.readFileSync(output, "utf8");
  for (const value of ["Northwind", "Orbit", "Fold", "Signal", "A calm tool for long projects.", "A ring in motion"]) {
    assert.ok(html.includes(value), value);
  }
  for (const size of SIZES) {
    assert.ok(html.includes(`<figcaption>${size} px</figcaption>`), `${size} px`);
  }
  assert.equal((html.match(/class="board board-light"/g) ?? []).length, 3);
  assert.equal((html.match(/class="board board-dark"/g) ?? []).length, 3);
  assert.equal((html.match(/class="rail"/g) ?? []).length, 6);
  assert.ok(html.includes('class="menu"'));
  assert.ok(html.includes('class="tabs"'));
  assert.ok(html.includes('class="dock"'));
  assert.ok(html.includes("Menu bar 18 px template"));
  assert.ok(html.includes("Show all sizes"));
  assert.ok(html.includes("https://northwind.app"));
  assert.ok(html.includes('class="mark template"'));
  assert.ok(!html.includes("<script"));
  assert.ok(!html.includes("src="));

  const again = path.join(workspace, "out", "preview-again.html");
  run([...args.slice(0, -1), again]);
  assert.equal(fs.readFileSync(again, "utf8"), html);

  const escaped = path.join(workspace, "out", "escaped.html");
  run([
    "--title", `A <b>& "B"`,
    "--concept", `Ring & Co=${orbit}`,
    "--output", escaped,
  ]);
  const escapedHtml = fs.readFileSync(escaped, "utf8");
  assert.ok(escapedHtml.includes("A &lt;b&gt;&amp; &quot;B&quot;"));
  assert.ok(escapedHtml.includes("Ring &amp; Co"));
  assert.ok(!escapedHtml.includes("A <b>"));

  const marker = path.join(workspace, "out", "marker.html");
  run(["--title", "Markers", "--concept", `%%CONCEPTS%%=${orbit}`, "--output", marker]);
  const markerHtml = fs.readFileSync(marker, "utf8");
  assert.ok(markerHtml.includes("%%CONCEPTS%%"));
  assert.equal((markerHtml.match(/<section class="concept"/g) ?? []).length, 1);

  const noViewBox = write("no-viewbox.svg", "<svg xmlns=\"http://www.w3.org/2000/svg\"><rect width=\"8\" height=\"8\"/></svg>\n");
  expectFailure(fails(["--title", "Bad", "--concept", `No=${noViewBox}`, "--output", path.join(workspace, "bad.html")]), "needs a viewBox");

  const defaults = path.join(workspace, "out", "defaults.html");
  run(["--title", "Defaults", "--concept", `Orbit=${orbit}`, "--output", defaults]);
  assert.ok(fs.readFileSync(defaults, "utf8").includes("https://example.app"));
  expectFailure(fails(["--title", "Bad", "--site", "", "--concept", `Orbit=${orbit}`, "--output", path.join(workspace, "bad.html")]), "needs a host name");

  const scripted = write("scripted.svg", svg("#111827", "<script>alert(1)</script>"));
  expectFailure(fails(["--title", "Bad", "--concept", `Script=${scripted}`, "--output", path.join(workspace, "bad.html")]), "contains <script>");

  const external = write("external.svg", svg("#111827", '<image href="logo.png" width="8" height="8"/>'));
  expectFailure(fails(["--title", "Bad", "--concept", `External=${external}`, "--output", path.join(workspace, "bad.html")]), "references an external asset");

  expectFailure(fails(["--title", "Bad", "--concept", `Missing=${path.join(workspace, "missing.svg")}`, "--output", path.join(workspace, "bad.html")]), "cannot read");

  expectFailure(fails(["--title", "Bad", "--concept", `Same=${orbit}`, "--concept", `Same=${fold}`, "--output", path.join(workspace, "bad.html")]), "duplicate concept name");

  expectFailure(fails(["--title", "Bad", "--concept", `Orbit=${orbit}`, "--note", "Elsewhere=nope", "--output", path.join(workspace, "bad.html")]), "names no concept");

  console.log("brand-preview: tests passed");
} finally {
  fs.rmSync(workspace, { recursive: true, force: true });
}
