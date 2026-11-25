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

function lockup(color) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 80"><circle cx="40" cy="40" r="28" fill="${color}"/><path d="M88 58c0-18 14-32 32-32s32 14 32 32" fill="none" stroke="${color}" stroke-width="8" stroke-linecap="round"/></svg>\n`;
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
  const orbitLockup = write("orbit-lockup.svg", lockup("#2563eb"));
  const foldLockup = write("fold-lockup.svg", lockup("#16a34a"));
  const signalLockup = write("signal-lockup.svg", lockup("#ea580c"));
  const output = path.join(workspace, "out", "preview.html");
  const args = [
    "--title", "Northwind",
    "--subtitle", "A calm tool for long projects.",
    "--site", "northwind.app",
    "--concept", `Orbit=${orbit}`,
    "--concept", `Fold=${fold}`,
    "--concept", `Signal=${signal}`,
    "--lockup", `Orbit=${orbitLockup}`,
    "--lockup", `Fold=${foldLockup}`,
    "--lockup", `Signal=${signalLockup}`,
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
  assert.ok(html.includes("Silhouettes at 16 px"));
  assert.equal((html.match(/class="silhouette"/g) ?? []).length, 3);
  assert.equal((html.match(/class="lockup"/g) ?? []).length, 6);
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

  const gradient = (color) =>
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="g"><stop offset="0" stop-color="${color}"/><stop offset="1" stop-color="#111827"/></linearGradient><circle id="dot" cx="8" cy="8" r="4"/></defs><rect width="64" height="64" fill="url(#g)"/><circle id="abc" cx="16" cy="16" r="4" fill="#abc"/><use href="#dot"/></svg>\n`;
  const gradientA = write("gradient-a.svg", gradient("#111827"));
  const gradientB = write("gradient-b.svg", gradient("#e4572e"));
  const namespaced = path.join(workspace, "out", "namespaced.html");
  run(["--title", "Gradients", "--concept", `A=${gradientA}`, "--concept", `B=${gradientB}`, "--output", namespaced]);
  const namespacedHtml = fs.readFileSync(namespaced, "utf8");
  assert.ok(namespacedHtml.includes('id="concept-1-icon-g"'));
  assert.ok(namespacedHtml.includes('id="concept-2-icon-g"'));
  assert.ok(namespacedHtml.includes('id="concept-1-icon-dot"'));
  assert.ok(namespacedHtml.includes('id="concept-1-icon-abc"'));
  assert.ok(namespacedHtml.includes("url(#concept-1-icon-g)"));
  assert.ok(namespacedHtml.includes("url(#concept-2-icon-g)"));
  assert.ok(namespacedHtml.includes('href="#concept-1-icon-dot"'));
  assert.ok(namespacedHtml.includes('stop-color="#111827"'));
  assert.ok(namespacedHtml.includes('fill="#abc"'));
  assert.ok(!namespacedHtml.includes("url(#g)"));

  const noViewBox = write("no-viewbox.svg", "<svg xmlns=\"http://www.w3.org/2000/svg\"><rect width=\"8\" height=\"8\"/></svg>\n");
  expectFailure(fails(["--title", "Bad", "--concept", `No=${noViewBox}`, "--output", path.join(workspace, "bad.html")]), "has no viewBox");

  const defaults = path.join(workspace, "out", "defaults.html");
  run(["--title", "Defaults", "--concept", `Orbit=${orbit}`, "--output", defaults]);
  assert.ok(fs.readFileSync(defaults, "utf8").includes("https://example.app"));
  expectFailure(fails(["--title", "Bad", "--site", "", "--concept", `Orbit=${orbit}`, "--output", path.join(workspace, "bad.html")]), "needs a host name");

  const scripted = write("scripted.svg", svg("#111827", "<script>alert(1)</script>"));
  expectFailure(fails(["--title", "Bad", "--concept", `Script=${scripted}`, "--output", path.join(workspace, "bad.html")]), "contains <script>");

  const texted = write("texted.svg", "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\"><text x=\"8\" y=\"40\">arc</text></svg>\n");
  expectFailure(fails(["--title", "Bad", "--concept", `Text=${texted}`, "--output", path.join(workspace, "bad.html")]), "contains <text>");

  const styled = write("styled.svg", "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 8 8\"><style>#paint{fill:#111827}</style><rect id=\"paint\" width=\"8\" height=\"8\"/></svg>\n");
  expectFailure(fails(["--title", "Bad", "--concept", `Styled=${styled}`, "--output", path.join(workspace, "bad.html")]), "contains <style>");

  const external = write("external.svg", svg("#111827", '<image href="logo.png" width="8" height="8"/>'));
  expectFailure(fails(["--title", "Bad", "--concept", `External=${external}`, "--output", path.join(workspace, "bad.html")]), "contains <image>");

  const externalUrl = write("external-url.svg", "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 8 8\"><rect width=\"8\" height=\"8\" fill=\"url(sprite.svg#p)\"/></svg>\n");
  expectFailure(fails(["--title", "Bad", "--concept", `Url=${externalUrl}`, "--output", path.join(workspace, "bad.html")]), "references an external stylesheet or URL");

  expectFailure(fails(["--title", "Bad", "--concept", `Missing=${path.join(workspace, "missing.svg")}`, "--output", path.join(workspace, "bad.html")]), "cannot read");

  expectFailure(fails(["--title", "Bad", "--concept", `Same=${orbit}`, "--concept", `Same=${fold}`, "--output", path.join(workspace, "bad.html")]), "duplicate concept name");

  expectFailure(fails(["--title", "Bad", "--concept", `Orbit=${orbit}`, "--note", "Elsewhere=nope", "--output", path.join(workspace, "bad.html")]), "names no concept");
  expectFailure(fails(["--title", "Bad", "--concept", `Orbit=${orbit}`, "--lockup", "Elsewhere=nope", "--output", path.join(workspace, "bad.html")]), "--lockup names no concept");

  console.log("brand-preview: tests passed");
} finally {
  fs.rmSync(workspace, { recursive: true, force: true });
}
