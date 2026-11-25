#!/usr/bin/env node
/* eslint-disable @typescript-eslint/no-var-requires, security/detect-object-injection, security/detect-unsafe-regex, xss/no-mixed-html -- CLI renders a self-contained preview from checked local SVG masters; fixed regexes, escaped text and validated markup */
"use strict";

const fs = require("node:fs");
const path = require("node:path");

// The skill ships inside the Factory checkout, so it uses the profile's one master contract rather than a second copy.
const { checkSource, tagAttributes } = require("../../../profiles/universal/scripts/asset-masters.js");

const SIZES = [16, 32, 64, 128, 256, 512, 1024];
const MIN_CONTRAST = 3;
const BOARDS = {
  light: { background: "#f6f6f3", text: "#18181b" },
  dark: { background: "#0e0e11", text: "#f4f4f5" },
};

const USAGE = `Usage: brand-preview.js --output <file> --title <title> --concept <name>=<svg> [--concept ...] [--lockup <name>=<svg>] [--dark <name>=<svg>] [--dark-lockup <name>=<svg>] [--note <name>=<text>] [--subtitle <text>] [--site <host>]

Render brand concepts as one self-contained HTML page.
Pass one --concept per direction; the skill passes three. Add one --lockup per direction to show its logo lockup.
Declare neutral ink on every root as data-brand-ink="currentColor" (or a fixed colour); add --dark or --dark-lockup for a fixed-ink concept.
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
  const options = { output: null, title: null, subtitle: null, site: "example.app", concepts: [], lockups: new Map(), darks: new Map(), darkLockups: new Map(), notes: new Map() };
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
    } else if (flag === "dark") {
      const [name, file] = splitPair(next, "--dark");
      options.darks.set(name, file);
    } else if (flag === "dark-lockup") {
      const [name, file] = splitPair(next, "--dark-lockup");
      options.darkLockups.set(name, file);
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
    // eslint-disable-next-line security/detect-non-literal-fs-filename -- reads a caller-supplied master path
    source = fs.readFileSync(file, "utf8"); // nosemgrep: javascript.pathtraversal.rule-non-literal-fs-filename
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

const NAMED_COLORS = {
  black: "#000000",
  white: "#ffffff",
  gray: "#808080",
  grey: "#808080",
  silver: "#c0c0c0",
  red: "#ff0000",
  green: "#008000",
  blue: "#0000ff",
  yellow: "#ffff00",
  orange: "#ffa500",
  purple: "#800080",
};

function hslToRgb(hue, saturation, lightness) {
  const h = ((hue % 360) + 360) % 360 / 360;
  const s = saturation / 100;
  const l = lightness / 100;
  if (s === 0) {
    const channel = Math.round(l * 255);
    return { r: channel, g: channel, b: channel, a: 1 };
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const convert = (t) => {
    let value = t;
    if (value < 0) value += 1;
    if (value > 1) value -= 1;
    if (value < 1 / 6) return p + (q - p) * 6 * value;
    if (value < 1 / 2) return q;
    if (value < 2 / 3) return p + (q - p) * (2 / 3 - value) * 6;
    return p;
  };
  return { r: Math.round(convert(h + 1 / 3) * 255), g: Math.round(convert(h) * 255), b: Math.round(convert(h - 1 / 3) * 255), a: 1 };
}

function parseColor(value) {
  const text = String(value).trim().toLowerCase();
  if (NAMED_COLORS[text] !== undefined) {
    return parseColor(NAMED_COLORS[text]);
  }
  let match = /^#([0-9a-f]{3})$/.exec(text);
  if (match !== null) {
    const [r, g, b] = match[1].split("").map((digit) => Number.parseInt(digit + digit, 16));
    return { r, g, b, a: 1 };
  }
  match = /^#([0-9a-f]{4})$/.exec(text);
  if (match !== null) {
    const [r, g, b, a] = match[1].split("").map((digit) => Number.parseInt(digit + digit, 16));
    return { r, g, b, a: a / 255 };
  }
  match = /^#([0-9a-f]{6})$/.exec(text);
  if (match !== null) {
    return {
      r: Number.parseInt(match[1].slice(0, 2), 16),
      g: Number.parseInt(match[1].slice(2, 4), 16),
      b: Number.parseInt(match[1].slice(4, 6), 16),
      a: 1,
    };
  }
  match = /^#([0-9a-f]{8})$/.exec(text);
  if (match !== null) {
    return {
      r: Number.parseInt(match[1].slice(0, 2), 16),
      g: Number.parseInt(match[1].slice(2, 4), 16),
      b: Number.parseInt(match[1].slice(4, 6), 16),
      a: Number.parseInt(match[1].slice(6, 8), 16) / 255,
    };
  }
  match = /^hsla?\(\s*([0-9.]+)(?:deg)?[,\s]+([0-9.]+)%[,\s]+([0-9.]+)%(?:[,\s/]+([0-9.]+))?\s*\)$/.exec(text);
  if (match !== null) {
    const color = hslToRgb(Number.parseFloat(match[1]), Number.parseFloat(match[2]), Number.parseFloat(match[3]));
    color.a = match[4] === undefined ? 1 : Number.parseFloat(match[4]);
    return color;
  }
  match = /^rgba?\(\s*([0-9.]+)[,\s]+([0-9.]+)[,\s]+([0-9.]+)(?:[,\s/]+([0-9.]+))?\s*\)$/.exec(text);
  if (match !== null) {
    return {
      r: Number.parseFloat(match[1]),
      g: Number.parseFloat(match[2]),
      b: Number.parseFloat(match[3]),
      a: match[4] === undefined ? 1 : Number.parseFloat(match[4]),
    };
  }
  return null;
}

function composite(foreground, background) {
  const alpha = foreground.a === undefined ? 1 : foreground.a;
  return {
    r: alpha * foreground.r + (1 - alpha) * background.r,
    g: alpha * foreground.g + (1 - alpha) * background.g,
    b: alpha * foreground.b + (1 - alpha) * background.b,
    a: 1,
  };
}

function channel(value) {
  const normalized = value / 255;
  return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
}

function luminance(color) {
  return 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b);
}

function contrastRatio(foreground, background) {
  const a = luminance(foreground);
  const b = luminance(background);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

function paintsRendered(source, value) {
  const wanted = value.toLowerCase();
  return paintedElements(source).some(
    (element) =>
      element.rendered &&
      element.name !== "svg" &&
      !CONTAINERS.has(element.name) &&
      [element.fill, element.stroke].some((paint) => paint !== undefined && paint.trim().toLowerCase() === wanted),
  );
}

function inkFor(source, textColor) {
  const root = source.match(/<svg\b((?:[^>"']|"[^"]*"|'[^']*')*)>/i);
  if (root === null) {
    return { color: null, raw: null, problem: "is not an SVG document" };
  }
  const declared = /data-brand-ink\s*=\s*["']([^"']+)["']/i.exec(root[1]);
  if (declared === null) {
    return { color: null, raw: null, problem: "declare the neutral ink with data-brand-ink" };
  }
  const raw = declared[1].trim();
  if (raw.toLowerCase() === "currentcolor") {
    if (!paintsRendered(source, "currentColor")) {
      return { color: null, raw, problem: "declares currentColor but paints no currentColor" };
    }
    return { color: parseColor(textColor), raw: textColor };
  }
  const color = parseColor(raw);
  if (color === null) {
    return { color: null, raw, problem: `cannot read data-brand-ink ${raw}` };
  }
  if (!paintsRendered(source, raw)) {
    return { color: null, raw, problem: `declares ${raw} but paints no ${raw}` };
  }
  return { color, raw };
}

const CONTAINERS = new Set([
  "svg",
  "g",
  "defs",
  "symbol",
  "clippath",
  "mask",
  "marker",
  "pattern",
  "lineargradient",
  "radialgradient",
  "filter",
  "switch",
  "a",
]);
const NON_RENDERING = new Set(["defs", "clippath", "mask", "marker", "pattern", "lineargradient", "radialgradient", "filter", "symbol"]);

function styleProperties(style) {
  const properties = {};
  if (style === undefined) {
    return properties;
  }
  for (const declaration of style.split(";")) {
    const index = declaration.indexOf(":");
    if (index === -1) {
      continue;
    }
    const name = declaration.slice(0, index).trim().toLowerCase();
    const value = declaration.slice(index + 1).replace(/!important/gi, "").trim();
    if (name !== "" && value !== "") {
      properties[name] = value;
    }
  }
  return properties;
}

function parseOpacity(value, fallback) {
  if (value === undefined) {
    return fallback;
  }
  const text = String(value).replace(/!important/gi, "").trim();
  const ratio = text.endsWith("%") ? Number.parseFloat(text) / 100 : Number.parseFloat(text);
  return Number.isFinite(ratio) ? Math.min(1, Math.max(0, ratio)) : fallback;
}

function paintedElements(source) {
  const elements = [];
  const stack = [{ name: "svg", groupOpacity: 1, fill: undefined, stroke: undefined, fillOpacity: 1, strokeOpacity: 1, rendered: true }];
  const token = /<\/?([A-Za-z][\w:.-]*)((?:[^>"']|"[^"]*"|'[^']*')*)?>/g;
  let match;
  while ((match = token.exec(source)) !== null) {
    const closing = match[0].startsWith("</");
    const name = match[1].toLowerCase().split(":").pop();
    if (closing) {
      for (let index = stack.length - 1; index > 0; index -= 1) {
        if (stack[index].name === name) {
          stack.length = index;
          break;
        }
      }
      continue;
    }
    const attributes = tagAttributes(match[2] ?? "");
    const style = styleProperties(attributes.style);
    const parent = stack[stack.length - 1];
    const groupOpacity = parent.groupOpacity * parseOpacity(style.opacity ?? attributes.opacity, 1);
    const fill = style.fill ?? attributes.fill ?? parent.fill;
    const stroke = style.stroke ?? attributes.stroke ?? parent.stroke;
    const fillOpacity = parseOpacity(style["fill-opacity"] ?? attributes["fill-opacity"], parent.fillOpacity);
    const strokeOpacity = parseOpacity(style["stroke-opacity"] ?? attributes["stroke-opacity"], parent.strokeOpacity);
    const rendered = parent.rendered && !NON_RENDERING.has(name);
    elements.push({ name, fill, stroke, groupOpacity, fillOpacity, strokeOpacity, rendered });
    if (CONTAINERS.has(name) && !/\/\s*>$/.test(match[0])) {
      stack.push({ name, groupOpacity, fill, stroke, fillOpacity, strokeOpacity, rendered });
    }
  }
  return elements;
}

function partialOpacityOnInk(source, inkRaw) {
  for (const element of paintedElements(source)) {
    if (!element.rendered || (element.name !== "svg" && CONTAINERS.has(element.name))) {
      continue;
    }
    const paintsInk = (value) =>
      value !== undefined && (/^currentcolor$/i.test(value.trim()) || value.trim().toLowerCase() === inkRaw.toLowerCase());
    const fillAlpha = element.groupOpacity * element.fillOpacity;
    const strokeAlpha = element.groupOpacity * element.strokeOpacity;
    if ((paintsInk(element.fill) && fillAlpha < 1) || (paintsInk(element.stroke) && strokeAlpha < 1) || (element.name === "svg" && element.groupOpacity < 1)) {
      return true;
    }
  }
  return false;
}

function gradientStops(source, id) {
  for (const match of source.matchAll(/<(?:linear|radial)Gradient\b((?:[^>"']|"[^"]*"|'[^']*')*)>([\s\S]*?)<\/(?:linear|radial)Gradient>/gi)) {
    if (tagAttributes(match[1]).id !== id) {
      continue;
    }
    const stops = [];
    for (const stop of match[2].matchAll(/<stop\b((?:[^>"']|"[^"]*"|'[^']*')*)>/gi)) {
      const attributes = tagAttributes(stop[1]);
      if (attributes["stop-color"] !== undefined) {
        stops.push({ color: attributes["stop-color"], opacity: attributes["stop-opacity"] });
      }
    }
    return stops;
  }
  return [];
}

function fixedPaintProblems(source, board, kind, theme, inkRaw) {
  const problems = [];
  const seen = new Set();
  const background = parseColor(board.background);
  for (const element of paintedElements(source)) {
    if (!element.rendered) {
      continue;
    }
    for (const [value, opacity] of [
      [element.fill, element.groupOpacity * element.fillOpacity],
      [element.stroke, element.groupOpacity * element.strokeOpacity],
    ]) {
      if (value === undefined) {
        continue;
      }
      const lower = value.trim().toLowerCase();
      if (
        lower === "" ||
        lower === "none" ||
        lower === "transparent" ||
        lower === "inherit" ||
        lower === "currentcolor" ||
        lower === String(inkRaw).toLowerCase()
      ) {
        continue;
      }
      if (lower.startsWith("url(")) {
        const reference = /url\(\s*["']?#([^"')\s]+)/i.exec(value);
        if (reference === null) {
          continue;
        }
        for (const stop of gradientStops(source, reference[1])) {
          const color = parseColor(stop.color);
          if (color === null) {
            continue;
          }
          const alpha = Math.min(1, Math.max(0, opacity * (stop.opacity === undefined ? 1 : parseFloat(stop.opacity))));
          const key = `stop|${stop.color.toLowerCase()}|${alpha}`;
          if (seen.has(key)) {
            continue;
          }
          seen.add(key);
          const effective = composite({ ...color, a: (color.a === undefined ? 1 : color.a) * alpha }, background);
          const ratio = contrastRatio(effective, background);
          if (ratio < MIN_CONTRAST) {
            problems.push(`${theme} ${kind} gradient stop ${stop.color}${alpha < 1 ? ` at ${alpha}` : ""}: ${ratio.toFixed(1)}:1; minimum ${MIN_CONTRAST}:1`);
          }
        }
        continue;
      }
      const color = parseColor(value);
      if (color === null) {
        continue;
      }
      const alpha = Math.min(1, Math.max(0, opacity));
      const key = `${lower}|${alpha}`;
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      const effective = composite({ ...color, a: (color.a === undefined ? 1 : color.a) * alpha }, background);
      const ratio = contrastRatio(effective, background);
      if (ratio < MIN_CONTRAST) {
        problems.push(`${theme} ${kind} accent ${value}${alpha < 1 ? ` at ${alpha}` : ""}: ${ratio.toFixed(1)}:1; minimum ${MIN_CONTRAST}:1`);
      }
    }
  }
  return problems;
}

function contrastProblems(concept) {
  const problems = [];
  const check = (theme, source, kind) => {
    const board = BOARDS[theme];
    const background = parseColor(board.background);
    const ink = inkFor(source, board.text);
    if (ink.color === null) {
      problems.push(`${theme} ${kind}: ${ink.problem}`);
      return;
    }
    if (partialOpacityOnInk(source, ink.raw)) {
      problems.push(`${theme} ${kind}: partial opacity makes the ink untestable; use solid paint`);
    }
    const ratio = contrastRatio(composite(ink.color, background), background);
    if (ratio < MIN_CONTRAST) {
      problems.push(`${theme} ${kind}: ${ink.raw} is ${ratio.toFixed(1)}:1; minimum ${MIN_CONTRAST}:1`);
    }
    problems.push(...fixedPaintProblems(source, board, kind, theme, ink.raw));
  };
  check("light", concept.svg, "icon");
  check("dark", concept.darkSvg === undefined ? concept.svg : concept.darkSvg, "icon");
  if (concept.lockup !== undefined) {
    check("light", concept.lockup, "lockup");
    check("dark", concept.darkLockup === undefined ? concept.lockup : concept.darkLockup, "lockup");
  }
  return problems;
}

function mark(svg, size, extra = "") {
  const classes = extra === "" ? "mark" : `mark ${extra}`;
  return `<span class="${classes}" style="width:${size}px;height:${size}px" aria-hidden="true">${svg}</span>`;
}

function board(concept, theme, site, boardId) {
  const name = escapeHtml(concept.name);
  const note = theme === "light" ? "Light" : "Dark";
  const iconSource = theme === "dark" && concept.darkSvg !== undefined ? concept.darkSvg : concept.svg;
  const lockupBase = theme === "dark" && concept.darkLockup !== undefined ? concept.darkLockup : concept.lockup;
  const icon = namespaceIds(iconSource, `board-${boardId}-icon-`);
  const lockupSource = lockupBase === undefined ? undefined : namespaceIds(lockupBase, `board-${boardId}-lockup-`);
  const sizes = SIZES.map(
    (size) => `<figure class="size">${mark(icon, size)}<figcaption>${size} px</figcaption></figure>`,
  ).join("");
  const lockup =
    lockupSource === undefined
      ? ""
      : `<div class="lockup">
          <span class="lockup-label">Logo lockup</span>
          <span class="lockup-art" aria-hidden="true">${lockupSource}</span>
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
          <div class="menu-left">${mark(icon, 18, "template")}<b>${name}</b><span>File</span><span>Edit</span><span>View</span></div>
          <div class="menu-right"><span>Mon 9:41</span></div>
        </div>
        <div class="browser">
          <div class="tabs">
            <span class="tab active">${mark(icon, 16)}<span>${name}</span></span>
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
          <span class="app brand-app">${mark(icon, 64)}</span>
        </div>
        <p class="board-note">Menu bar 18 px template · tab favicon 16 px · Dock 64 px</p>
      </div>`;
}

function section(concept, index, site) {
  const number = String(index + 1).padStart(2, "0");
  const name = escapeHtml(concept.name);
  const note = escapeHtml(concept.note);
  const flags =
    concept.flags.length === 0
      ? ""
      : `<div class="flag" role="status"><strong>Contrast check</strong><span>${concept.flags.map(escapeHtml).join(" · ")}</span></div>`;
  return `<section class="concept" id="concept-${index + 1}">
      <header class="concept-head">
        <span class="number">${number}</span>
        <div>
          <h2>${name}</h2>
          <p class="metaphor">${note}</p>
        </div>
        <span class="head-mark">${mark(concept.svg, 56)}</span>
      </header>
      ${flags}
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
    for (const [name, file] of options.darks) {
      const index = options.concepts.findIndex((candidate) => candidate.name === name);
      if (index === -1) {
        throw new Error(`--dark names no concept: ${name}`);
      }
      options.concepts[index].darkSvg = namespaceIds(loadSvg(file), `concept-${index + 1}-dark-icon-`);
    }
    for (const [name, file] of options.darkLockups) {
      const index = options.concepts.findIndex((candidate) => candidate.name === name);
      if (index === -1) {
        throw new Error(`--dark-lockup names no concept: ${name}`);
      }
      options.concepts[index].darkLockup = namespaceIds(loadSvg(file), `concept-${index + 1}-dark-lockup-`);
    }
    for (const concept of options.concepts) {
      concept.flags = contrastProblems(concept);
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
    // eslint-disable-next-line security/detect-non-literal-fs-filename -- output path is the caller supplied cache path
    fs.mkdirSync(path.dirname(path.resolve(options.output)), { recursive: true }); // nosemgrep: javascript.pathtraversal.rule-non-literal-fs-filename
    // eslint-disable-next-line security/detect-non-literal-fs-filename -- output path is the caller supplied cache path
    fs.writeFileSync(options.output, html);
  } catch (error) {
    process.stderr.write(`brand-preview: ${error.message}\n`);
    return 1;
  }
  console.log(`brand-preview: wrote ${options.output} with ${options.concepts.length} concepts`);
  return 0;
}

process.exitCode = main(process.argv.slice(2));
