#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

// The skill ships inside the Factory checkout, so it uses the profile's one master contract rather than a second copy.
const { checkSource } = require(path.join(__dirname, "../../../profiles/universal/scripts/asset-masters.js"));

const SIZES = [16, 32, 64, 128, 256, 512, 1024];

const USAGE = `Usage: brand-preview.js --output <file> --title <title> --concept <name>=<svg> [--concept ...] [--lockup <name>=<svg>] [--note <name>=<text>] [--subtitle <text>] [--site <host>]

Render brand concepts as one self-contained HTML page.
Pass one --concept per direction; the skill passes three. Add one --lockup per direction to show its logo lockup.
Each SVG must pass the brand master checker (viewBox, primitives and Bezier curves, no raster or external references).`;

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function splitPair(value, flag) {
  const index = value.indexOf("=");
  if (index <= 0) {
    throw new Error(`${flag} needs <name>=<value>, got ${value}`);
  }
  return [value.slice(0, index).trim(), value.slice(index + 1).trim()];
}

function parseArgs(argv) {
  const options = { output: null, title: null, subtitle: null, site: "example.app", concepts: [], lockups: new Map(), notes: new Map() };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      options.help = true;
      continue;
    }
    if (!argument.startsWith("--")) {
      throw new Error(`unexpected argument ${argument}`);
    }
    const flag = argument.slice(2);
    const next = argv[index + 1];
    if (next === undefined) {
      throw new Error(`${argument} needs a value`);
    }
    index += 1;
    if (flag === "output") {
      options.output = next;
    } else if (flag === "title") {
      options.title = next;
    } else if (flag === "subtitle") {
      options.subtitle = next;
    } else if (flag === "site") {
      options.site = next.trim();
    } else if (flag === "concept") {
      const [name, file] = splitPair(next, "--concept");
      if (name === "" || file === "") {
        throw new Error("--concept needs a non-empty name and path");
      }
      options.concepts.push({ name, file });
    } else if (flag === "lockup") {
      const [name, file] = splitPair(next, "--lockup");
      options.lockups.set(name, file);
    } else if (flag === "note") {
      const [name, text] = splitPair(next, "--note");
      options.notes.set(name, text);
    } else {
      throw new Error(`unknown option --${flag}`);
    }
  }
  return options;
}

function loadSvg(file) {
  let source;
  try {
    source = fs.readFileSync(file, "utf8");
  } catch (error) {
    throw new Error(`${file}: cannot read: ${error.message}`);
  }
  source = source.replace(/^<\?xml[^>]*\?>\s*/i, "").replace(/^<!DOCTYPE[^>]*>\s*/i, "").trim();
  const problems = checkSource(source);
  if (problems.length > 0) {
    throw new Error(`${file}: ${problems.join("; ")}`);
  }
  return source;
}

function namespaceIds(source, prefix) {
  const ids = new Set();
  for (const match of source.matchAll(/\sid\s*=\s*(["'])([^"']+)\1/g)) {
    ids.add(match[2]);
  }
  if (ids.size === 0) {
    return source;
  }
  const renamed = source.replace(/\sid\s*=\s*(["'])([^"']+)\1/g, (match, quote, id) => ` id=${quote}${prefix}${id}${quote}`);
  return renamed
    .replace(/url\(\s*(["']?)#([^"')\s]+)\1\s*\)/g, (match, quote, id) => (ids.has(id) ? `url(${quote}#${prefix}${id}${quote})` : match))
    .replace(/(\b(?:xlink:)?href\s*=\s*)(["'])#([^"']+)\2/g, (match, attribute, quote, id) => (ids.has(id) ? `${attribute}${quote}#${prefix}${id}${quote}` : match));
}

function mark(svg, size, extra = "") {
  const classes = extra === "" ? "mark" : `mark ${extra}`;
  return `<span class="${classes}" style="width:${size}px;height:${size}px" aria-hidden="true">${svg}</span>`;
}

function board(concept, theme, site, boardId) {
  const name = escapeHtml(concept.name);
  const note = theme === "light" ? "Light" : "Dark";
  const sizes = SIZES.map(
    (size) => `<figure class="size">${mark(concept.svg, size)}<figcaption>${size} px</figcaption></figure>`,
  ).join("");
  const lockup =
    concept.lockup === undefined
      ? ""
      : `<div class="lockup">
          <span class="lockup-label">Logo lockup</span>
          <span class="lockup-art" aria-hidden="true">${concept.lockup}</span>
        </div>`;
  return `<div class="board board-${theme}">
        <div class="board-label">${note}</div>
        <input class="sizes-toggle" id="sizes-${boardId}" type="checkbox">
        <div class="rail-head">
          <span>Actual size</span>
          <label class="toggle" for="sizes-${boardId}"><span class="when-off">Show all sizes</span><span class="when-on">Collapse</span></label>
        </div>
        <div class="rail" role="group" aria-label="${note} size ladder">${sizes}</div>
        <p class="rail-hint">Scroll the ladder for 512 and 1024.</p>
        ${lockup}
        <div class="menu">
          <div class="menu-left">${mark(concept.svg, 18, "template")}<b>${name}</b><span>File</span><span>Edit</span><span>View</span></div>
          <div class="menu-right"><span>Mon 9:41</span></div>
        </div>
        <div class="browser">
          <div class="tabs">
            <span class="tab active">${mark(concept.svg, 16)}<span>${name}</span></span>
            <span class="tab"><span>Overview</span></span>
            <span class="tab"><span>Settings</span></span>
          </div>
          <div class="address"><span>https://${escapeHtml(site)}</span></div>
        </div>
        <div class="dock" aria-label="Mock Dock">
          <span class="app app-a">A</span>
          <span class="app app-b">B</span>
          <span class="app app-c">C</span>
          <span class="app app-d">D</span>
          <span class="app brand-app">${mark(concept.svg, 64)}</span>
        </div>
        <p class="board-note">Menu bar 18 px template · tab favicon 16 px · Dock 64 px</p>
      </div>`;
}

function section(concept, index, site) {
  const number = String(index + 1).padStart(2, "0");
  const name = escapeHtml(concept.name);
  const note = escapeHtml(concept.note);
  return `<section class="concept" id="concept-${index + 1}">
      <header class="concept-head">
        <span class="number">${number}</span>
        <div>
          <h2>${name}</h2>
          <p class="metaphor">${note}</p>
        </div>
        <span class="head-mark">${mark(concept.svg, 56)}</span>
      </header>
      <div class="boards">
        ${board(concept, "light", site, `${index + 1}-light`)}
        ${board(concept, "dark", site, `${index + 1}-dark`)}
      </div>
    </section>`;
}

function indexCard(concept, index) {
  return `<a class="index-card" href="#concept-${index + 1}">${mark(concept.svg, 32)}<span class="index-copy"><strong>${escapeHtml(concept.name)}</strong><span>${escapeHtml(concept.note)}</span></span></a>`;
}

function silhouette(concept) {
  return `<span class="silhouette">${mark(concept.svg, 16, "silhouette-glyph")}<span>${escapeHtml(concept.name)}</span></span>`;
}

function render(concepts, options) {
  const template = fs.readFileSync(path.join(__dirname, "preview-template.html"), "utf8");
  const slots = {
    TITLE: escapeHtml(options.title),
    SUBTITLE: escapeHtml(options.subtitle),
    INDEX: concepts.map(indexCard).join(""),
    SILHOUETTES: concepts.map(silhouette).join(""),
    CONCEPTS: concepts.map((concept, index) => section(concept, index, options.site)).join(""),
  };
  return template.replace(/%%(TITLE|SUBTITLE|INDEX|SILHOUETTES|CONCEPTS)%%/g, (match, key) => slots[key]);
}

function main(argv) {
  let options;
  try {
    options = parseArgs(argv);
  } catch (error) {
    process.stderr.write(`brand-preview: ${error.message}\n`);
    return 1;
  }
  if (options.help) {
    console.log(USAGE);
    return 0;
  }
  if (options.output === null || options.title === null) {
    process.stderr.write("brand-preview: --output and --title are required\n");
    return 1;
  }
  if (options.concepts.length === 0) {
    process.stderr.write("brand-preview: pass at least one --concept\n");
    return 1;
  }
  const names = new Set();
  try {
    for (const [index, concept] of options.concepts.entries()) {
      if (names.has(concept.name)) {
        throw new Error(`duplicate concept name ${concept.name}`);
      }
      names.add(concept.name);
      concept.svg = namespaceIds(loadSvg(concept.file), `concept-${index + 1}-icon-`);
      concept.note = options.notes.has(concept.name) ? options.notes.get(concept.name) : "One direction";
    }
    for (const [name, file] of options.lockups) {
      const index = options.concepts.findIndex((candidate) => candidate.name === name);
      if (index === -1) {
        throw new Error(`--lockup names no concept: ${name}`);
      }
      options.concepts[index].lockup = namespaceIds(loadSvg(file), `concept-${index + 1}-lockup-`);
    }
    for (const name of options.notes.keys()) {
      if (!names.has(name)) {
        throw new Error(`--note names no concept: ${name}`);
      }
    }
    const subtitle =
      options.subtitle ?? `${options.concepts.length} directions for the mark, shown at real sizes and where the icon will live.`;
    if (options.site === "") {
      throw new Error("--site needs a host name");
    }
    const html = render(options.concepts, { title: options.title, subtitle, site: options.site });
    fs.mkdirSync(path.dirname(path.resolve(options.output)), { recursive: true });
    fs.writeFileSync(options.output, html);
  } catch (error) {
    process.stderr.write(`brand-preview: ${error.message}\n`);
    return 1;
  }
  console.log(`brand-preview: wrote ${options.output} with ${options.concepts.length} concepts`);
  return 0;
}

process.exitCode = main(process.argv.slice(2));
