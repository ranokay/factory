#!/usr/bin/env node
/* eslint-disable @typescript-eslint/no-var-requires, no-undef, security/detect-object-injection, security/detect-unsafe-regex, security/detect-non-literal-fs-filename, security/detect-non-literal-regexp, security/detect-non-literal-require, security/detect-possible-timing-attacks, security-node/detect-non-literal-require-calls, security-node/non-literal-reg-expr, security-node/detect-possible-timing-attacks, xss/no-mixed-html -- Local asset generator and tests read and write checked repository paths, build fixed regexes and render checked SVG; the CLI has no untrusted web input */
"use strict";

const RESVG_TOOL = {
  command: "resvg",
  env: "RESVG",
  hint: "install it with mise install (resvg is pinned in the web Profile)",
};

function ico(images) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(images.length, 4);
  const entries = [];
  let offset = 6 + images.length * 16;
  for (const image of images) {
    const entry = Buffer.alloc(16);
    entry.writeUInt8(image.size >= 256 ? 0 : image.size, 0);
    entry.writeUInt8(image.size >= 256 ? 0 : image.size, 1);
    entry.writeUInt8(0, 2);
    entry.writeUInt8(0, 3);
    entry.writeUInt16LE(1, 4);
    entry.writeUInt16LE(32, 6);
    entry.writeUInt32LE(image.png.length, 8);
    entry.writeUInt32LE(offset, 12);
    entries.push(entry);
    offset += image.png.length;
  }
  return Buffer.concat([header, ...entries, ...images.map((image) => image.png)]);
}

module.exports = {
  profile: "web",
  tools: { resvg: RESVG_TOOL },
  managedGlobs: ["public/favicon*", "public/apple-touch-icon.png", "public/icon-*.png"],
  generate(context) {
    const icon = context.colorize(context.masters.icon.text, "#18181b");
    context.write("public/favicon.svg", icon);

    const images = [16, 32, 48].map((size) => ({ size, png: context.renderPng(icon, size, size) }));
    context.write("public/favicon.ico", ico(images));
    context.write("public/apple-touch-icon.png", context.renderPng(icon, 180, 180, { background: "#ffffff" }));
    context.write("public/icon-192.png", context.renderPng(icon, 192, 192, { background: "#ffffff" }));
    context.write("public/icon-512.png", context.renderPng(icon, 512, 512, { background: "#ffffff" }));
    for (const size of [16, 32, 48, 128]) {
      context.write(`public/icon-${size}.png`, context.renderPng(icon, size, size));
    }
  },
};
