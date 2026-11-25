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
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><circle cx="32" cy="32" r="24" fill="currentColor"/><circle cx="52" cy="12" r="6" fill="${color}"/>${body}</svg>\n`;
}

function lockup(color) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 80" data-brand-ink="currentColor"><circle cx="40" cy="40" r="28" fill="currentColor"/><path d="M88 58c0-18 14-32 32-32s32 14 32 32" fill="none" stroke="currentColor" stroke-width="8" stroke-linecap="round"/><circle cx="196" cy="24" r="8" fill="${color}"/></svg>\n`;
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
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><defs><linearGradient id="g"><stop offset="0" stop-color="${color}"/><stop offset="1" stop-color="#111827"/></linearGradient><linearGradient id="g2" href="#g"/><circle id="dot" cx="8" cy="8" r="4"/></defs><rect width="64" height="64" fill="url(#g2)"/><circle id="abc" cx="16" cy="16" r="4" fill="#abc"/><circle cx="32" cy="32" r="4" fill="currentColor"/></svg>\n`;
  const gradientA = write("gradient-a.svg", gradient("#111827"));
  const gradientB = write("gradient-b.svg", gradient("#e4572e"));
  const namespaced = path.join(workspace, "out", "namespaced.html");
  run(["--title", "Gradients", "--concept", `A=${gradientA}`, "--concept", `B=${gradientB}`, "--output", namespaced]);
  const namespacedHtml = fs.readFileSync(namespaced, "utf8");
  assert.ok(namespacedHtml.includes('id="concept-1-icon-g"'));
  assert.ok(namespacedHtml.includes('id="concept-2-icon-g"'));
  assert.ok(namespacedHtml.includes('id="concept-1-icon-abc"'));
  assert.ok(namespacedHtml.includes("url(#concept-1-icon-g2)"));
  assert.ok(namespacedHtml.includes("url(#concept-2-icon-g2)"));
  assert.ok(namespacedHtml.includes('href="#concept-1-icon-g"'));
  assert.ok(namespacedHtml.includes('stop-color="#111827"'));
  assert.ok(namespacedHtml.includes('fill="#abc"'));
  assert.ok(!namespacedHtml.includes("url(#g)"));

  assert.ok(!html.includes("Contrast check"));
  const fixedInk = write("fixed-ink.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="#141821"><rect width="64" height="64" fill="#141821"/></svg>\n`);
  const flagged = path.join(workspace, "out", "flagged.html");
  run(["--title", "Fixed ink", "--concept", `Fixed=${fixedInk}`, "--output", flagged]);
  const flaggedHtml = fs.readFileSync(flagged, "utf8");
  assert.ok(flaggedHtml.includes("Contrast check"));
  assert.ok(flaggedHtml.includes("dark icon: #141821 is 1.1:1; minimum 3:1"));

  const fixedLockup = write("fixed-lockup.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 80" data-brand-ink="#141821"><rect id="lightlock" width="240" height="80" fill="#141821"/></svg>\n`);
  const fixedDark = write("fixed-ink-dark.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><circle id="darkonly" cx="32" cy="32" r="24" fill="currentColor"/></svg>\n`);
  const darkLockup = write("fixed-lockup-dark.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 80" data-brand-ink="currentColor"><rect id="darklock" width="240" height="80" fill="currentColor"/></svg>\n`);
  const variant = path.join(workspace, "out", "variant.html");
  run([
    "--title", "Fixed ink",
    "--concept", `Fixed=${fixedInk}`,
    "--lockup", `Fixed=${fixedLockup}`,
    "--dark", `Fixed=${fixedDark}`,
    "--dark-lockup", `Fixed=${darkLockup}`,
    "--output", variant,
  ]);
  const variantHtml = fs.readFileSync(variant, "utf8");
  assert.ok(!variantHtml.includes("Contrast check"));
  const darkBoard = variantHtml.slice(variantHtml.indexOf('class="board board-dark"'));
  assert.ok(darkBoard.includes('id="concept-1-dark-icon-darkonly"'));
  assert.ok(darkBoard.includes('id="concept-1-dark-lockup-darklock"'));

  const undeclared = write("undeclared.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#141821"/></svg>\n`);
  const undeclaredOut = path.join(workspace, "out", "undeclared.html");
  run(["--title", "Undeclared", "--concept", `Undeclared=${undeclared}`, "--output", undeclaredOut]);
  assert.ok(fs.readFileSync(undeclaredOut, "utf8").includes("declare the neutral ink with data-brand-ink"));

  const lying = write("lying.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><rect width="64" height="64" fill="#18181b"/></svg>\n`);
  const lyingOut = path.join(workspace, "out", "lying.html");
  run(["--title", "Lying", "--concept", `Lying=${lying}`, "--output", lyingOut]);
  assert.ok(fs.readFileSync(lyingOut, "utf8").includes("declares currentColor but paints no currentColor"));

  const defsOnly = write("defs-only.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><defs><rect width="8" height="8" fill="currentColor"/></defs><rect width="64" height="64" fill="#18181b"/></svg>\n`);
  const defsOnlyOut = path.join(workspace, "out", "defs-only.html");
  run(["--title", "Defs only", "--concept", `Defs=${defsOnly}`, "--output", defsOnlyOut]);
  assert.ok(fs.readFileSync(defsOnlyOut, "utf8").includes("declares currentColor but paints no currentColor"));

  const faint = write("faint.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><rect width="64" height="64" fill="currentColor" opacity=".04"/></svg>\n`);
  const faintOut = path.join(workspace, "out", "faint.html");
  run(["--title", "Faint", "--concept", `Faint=${faint}`, "--output", faintOut]);
  assert.ok(fs.readFileSync(faintOut, "utf8").includes("partial opacity makes the ink untestable"));

  const accentOpacity = write("accent-opacity.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><rect width="64" height="64" fill="currentColor"/><circle cx="52" cy="12" r="6" fill="#e4572e" fill-opacity=".5"/></svg>\n`);
  const accentOpacityOut = path.join(workspace, "out", "accent-opacity.html");
  run(["--title", "Accent", "--concept", `Accent=${accentOpacity}`, "--output", accentOpacityOut]);
  const accentOpacityHtml = fs.readFileSync(accentOpacityOut, "utf8");
  assert.ok(!accentOpacityHtml.includes("partial opacity"));
  assert.ok(accentOpacityHtml.includes("light icon accent #e4572e at 0.5"));

  const groupOpacity = write("group-opacity.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><g opacity=".2"><rect width="64" height="64" fill="currentColor"/></g></svg>\n`);
  const groupOpacityOut = path.join(workspace, "out", "group-opacity.html");
  run(["--title", "Group", "--concept", `Group=${groupOpacity}`, "--output", groupOpacityOut]);
  assert.ok(fs.readFileSync(groupOpacityOut, "utf8").includes("partial opacity makes the ink untestable"));

  const inheritedInk = write("inherited-ink.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><g fill="currentColor"><rect width="64" height="64" opacity=".2"/></g></svg>\n`);
  const inheritedInkOut = path.join(workspace, "out", "inherited-ink.html");
  run(["--title", "Inherited", "--concept", `Inherited=${inheritedInk}`, "--output", inheritedInkOut]);
  assert.ok(fs.readFileSync(inheritedInkOut, "utf8").includes("partial opacity makes the ink untestable"));

  const inheritedAccent = write("inherited-accent.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><rect width="64" height="64" fill="currentColor"/><g fill="#888888"><circle cx="52" cy="12" r="6" opacity=".5"/></g></svg>\n`);
  const inheritedAccentOut = path.join(workspace, "out", "inherited-accent.html");
  run(["--title", "Accent", "--concept", `Accent=${inheritedAccent}`, "--output", inheritedAccentOut]);
  assert.ok(fs.readFileSync(inheritedAccentOut, "utf8").includes("accent #888888 at 0.5"));

  const styleAccent = write("style-accent.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><rect width="64" height="64" fill="currentColor"/><circle cx="52" cy="12" r="6" style="fill:#888888;opacity:.2"/></svg>\n`);
  const styleAccentOut = path.join(workspace, "out", "style-accent.html");
  run(["--title", "Style", "--concept", `Style=${styleAccent}`, "--output", styleAccentOut]);
  assert.ok(fs.readFileSync(styleAccentOut, "utf8").includes("accent #888888 at 0.2"));

  const styleInk = write("style-ink-opacity.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><rect width="64" height="64" style="fill:currentColor" fill-opacity=".2"/></svg>\n`);
  const styleInkOut = path.join(workspace, "out", "style-ink-opacity.html");
  run(["--title", "Style", "--concept", `Style=${styleInk}`, "--output", styleInkOut]);
  assert.ok(fs.readFileSync(styleInkOut, "utf8").includes("partial opacity makes the ink untestable"));

  const inheritedFillOpacity = write("inherited-fill-opacity.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><g fill="currentColor" fill-opacity=".2"><rect width="64" height="64"/></g></svg>\n`);
  const inheritedFillOpacityOut = path.join(workspace, "out", "inherited-fill-opacity.html");
  run(["--title", "Inherited", "--concept", `Inherited=${inheritedFillOpacity}`, "--output", inheritedFillOpacityOut]);
  assert.ok(fs.readFileSync(inheritedFillOpacityOut, "utf8").includes("partial opacity makes the ink untestable"));

  const stylePercent = write("style-percent.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><rect width="64" height="64" fill="currentColor"/><circle cx="52" cy="12" r="6" style="fill:#888888;opacity:20% !important"/></svg>\n`);
  const stylePercentOut = path.join(workspace, "out", "style-percent.html");
  run(["--title", "Style", "--concept", `Style=${stylePercent}`, "--output", stylePercentOut]);
  assert.ok(fs.readFileSync(stylePercentOut, "utf8").includes("accent #888888 at 0.2"));

  const hiddenDefs = write("hidden-defs.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><defs><rect width="8" height="8" fill="currentColor" opacity=".2"/></defs><rect width="64" height="64" fill="currentColor"/></svg>\n`);
  const hiddenDefsOut = path.join(workspace, "out", "hidden-defs.html");
  run(["--title", "Defs", "--concept", `Defs=${hiddenDefs}`, "--output", hiddenDefsOut]);
  assert.ok(!fs.readFileSync(hiddenDefsOut, "utf8").includes("Contrast check"));

  const groupOverride = write("group-override.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor" fill="currentColor"><g opacity=".5"><rect width="64" height="64" fill="#e4572e"/></g></svg>\n`);
  const groupOverrideOut = path.join(workspace, "out", "group-override.html");
  run(["--title", "Override", "--concept", `Override=${groupOverride}`, "--output", groupOverrideOut]);
  assert.ok(!fs.readFileSync(groupOverrideOut, "utf8").includes("partial opacity"));

  const multiplied = write("multiplied.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><rect width="64" height="64" fill="currentColor"/><circle cx="52" cy="12" r="6" fill="#e4572e" opacity=".5" fill-opacity=".5"/></svg>\n`);
  const multipliedOut = path.join(workspace, "out", "multiplied.html");
  run(["--title", "Multiplied", "--concept", `Multiplied=${multiplied}`, "--output", multipliedOut]);
  assert.ok(fs.readFileSync(multipliedOut, "utf8").includes("accent #e4572e at 0.25"));

  const styledInk = write("styled-ink.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="currentColor"><rect width="64" height="64" style="fill:currentColor"/></svg>\n`);
  const styledInkOut = path.join(workspace, "out", "styled-ink.html");
  run(["--title", "Styled ink", "--concept", `Styled=${styledInk}`, "--output", styledInkOut]);
  assert.ok(!fs.readFileSync(styledInkOut, "utf8").includes("Contrast check"));

  const wrongPaint = write("wrong-paint.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="#141821"><rect width="64" height="64" fill="#ffffff"/></svg>\n`);
  const wrongPaintOut = path.join(workspace, "out", "wrong-paint.html");
  run(["--title", "Wrong paint", "--concept", `Wrong=${wrongPaint}`, "--output", wrongPaintOut]);
  assert.ok(fs.readFileSync(wrongPaintOut, "utf8").includes("declares #141821 but paints no #141821"));

  const alpha = write("alpha.svg", `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" data-brand-ink="rgba(128,128,128,0.2)"><rect width="64" height="64" fill="rgba(128,128,128,0.2)"/></svg>\n`);
  const alphaOut = path.join(workspace, "out", "alpha.html");
  run(["--title", "Alpha", "--concept", `Alpha=${alpha}`, "--output", alphaOut]);
  assert.ok(fs.readFileSync(alphaOut, "utf8").includes("light icon: rgba(128,128,128,0.2)"));

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
