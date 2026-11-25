#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const PATH_BUDGET = 400;
const SHORT_RUN = 24;
const EPSILON = 0.01;
const COMMAND_PARAMS = { m: 2, l: 2, h: 1, v: 1, c: 6, s: 4, q: 4, t: 2, a: 7, z: 0 };
const RASTER_EXTENSION = /\.(?:png|jpe?g|gif|webp|bmp|tiff?|avif|heic|ico)(?:[?#].*)?$/i;
const DATA_URI = /data:[a-z0-9.+-]+\//i;
const BASE64 = /;base64,/i;
const EXTERNAL_URL = /url\(\s*['"]?(?!#)/i;
const TAG = /<([A-Za-z][\w:.-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>/g;
const ATTRIBUTE = /([A-Za-z_:][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
const PATH_TOKEN = /([MmZzLlHhVvCcSsQqTtAa])|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)/g;

const USAGE = `Usage: asset-masters.js [options] [file...]

Check brand masters for embedded rasters and traced staircase paths.
With no files, checks assets/brand/**/*.svg in the current directory.

Rules:
  - reject <image>, <foreignObject>, <script>, <style>, <text>, <use>, <symbol>,
    <pattern>, <mask>, <filter> and <clipPath>
  - reject data URIs, base64 payloads and empty, raster or external href/src references
  - require a root viewBox
  - reject ${SHORT_RUN} or more consecutive short axis-aligned segments
    (short means at most 0.5% of the longest viewBox side, and at least 0.5)
  - reject a single path over ${PATH_BUDGET} drawing commands

Options:
  -h, --help  Show this help.`;

function tagAttributes(source) {
  const attributes = {};
  let match;
  ATTRIBUTE.lastIndex = 0;
  while ((match = ATTRIBUTE.exec(source)) !== null) {
    attributes[match[1].toLowerCase()] = match[2] ?? match[3] ?? "";
  }
  return attributes;
}

function tags(source) {
  const found = [];
  let match;
  TAG.lastIndex = 0;
  while ((match = TAG.exec(source)) !== null) {
    found.push({ name: match[1].toLowerCase(), attributes: tagAttributes(match[2]) });
  }
  return found;
}

function rootAttributes(source) {
  const cleaned = source
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<\?[\s\S]*?\?>/g, "")
    .replace(/<!DOCTYPE[^>]*>/gi, "")
    .trim();
  const match = cleaned.match(/^<svg\b((?:[^>"']|"[^"]*"|'[^']*')*)>/i);
  return match === null ? null : tagAttributes(match[1]);
}

function viewBoxScale(source) {
  const attributes = rootAttributes(source);
  if (attributes === null || attributes.viewbox === undefined) {
    return 100;
  }
  const numbers = attributes.viewbox.trim().split(/[\s,]+/).map(Number);
  if (numbers.length === 4 && numbers.every(Number.isFinite)) {
    return Math.max(numbers[2], numbers[3]);
  }
  return 100;
}

function pathSegments(source) {
  const tokens = [];
  let match;
  PATH_TOKEN.lastIndex = 0;
  while ((match = PATH_TOKEN.exec(source)) !== null) {
    tokens.push(match[1] !== undefined ? { command: match[1] } : { number: Number(match[2]) });
  }
  const segments = [];
  let index = 0;
  let command = null;
  while (index < tokens.length) {
    if (tokens[index].command !== undefined) {
      command = tokens[index].command;
      index += 1;
      if (command.toLowerCase() === "z") {
        segments.push({ type: command });
        command = null;
        continue;
      }
    } else if (command === null) {
      return null;
    }
    const params = COMMAND_PARAMS[command.toLowerCase()];
    const values = [];
    for (let offset = 0; offset < params; offset += 1) {
      const token = tokens[index + offset];
      if (token === undefined || token.command !== undefined) {
        return null;
      }
      values.push(token.number);
    }
    index += params;
    segments.push({ type: command, params: values });
    if (command.toLowerCase() === "m") {
      command = command === "M" ? "L" : "l";
    }
  }
  return segments;
}

function endpoint(segment, current) {
  const params = segment.params;
  const relative = segment.type !== segment.type.toUpperCase();
  const lower = segment.type.toLowerCase();
  const offset = (x, y) => (relative ? { x: current.x + x, y: current.y + y } : { x, y });
  switch (lower) {
    case "m":
    case "l":
    case "t":
      return offset(params[0], params[1]);
    case "h":
      return { x: relative ? current.x + params[0] : params[0], y: current.y };
    case "v":
      return { x: current.x, y: relative ? current.y + params[0] : params[0] };
    case "c":
      return offset(params[4], params[5]);
    case "s":
    case "q":
      return offset(params[2], params[3]);
    case "a":
      return offset(params[5], params[6]);
    default:
      return current;
  }
}

function axisAlignedRun(run, previous, next, short) {
  const dx = next.x - previous.x;
  const dy = next.y - previous.y;
  const length = Math.hypot(dx, dy);
  const axisAligned = Math.abs(dx) <= EPSILON || Math.abs(dy) <= EPSILON;
  return axisAligned && length > EPSILON && length <= short ? run + 1 : 0;
}

function hasStaircase(segments, short) {
  let start = { x: 0, y: 0 };
  let current = start;
  let run = 0;
  for (const segment of segments) {
    const lower = segment.type.toLowerCase();
    if (lower === "z") {
      current = start;
      run = 0;
      continue;
    }
    const next = endpoint(segment, current);
    if (lower === "l" || lower === "h" || lower === "v") {
      run = axisAlignedRun(run, current, next, short);
      if (run >= SHORT_RUN) {
        return true;
      }
    } else {
      run = 0;
    }
    if (lower === "m") {
      start = next;
    }
    current = next;
  }
  return false;
}

function pointPairs(source) {
  const numbers = source.trim().split(/[\s,]+/).filter(Boolean).map(Number);
  if (numbers.length < 2 || numbers.length % 2 !== 0 || !numbers.every(Number.isFinite)) {
    return null;
  }
  const points = [];
  for (let index = 0; index < numbers.length; index += 2) {
    points.push({ x: numbers[index], y: numbers[index + 1] });
  }
  return points;
}

function hasPolygonStaircase(points, short) {
  let run = 0;
  for (let index = 1; index < points.length; index += 1) {
    run = axisAlignedRun(run, points[index - 1], points[index], short);
    if (run >= SHORT_RUN) {
      return true;
    }
  }
  return false;
}

function checkSource(source) {
  const problems = new Set();
  const root = rootAttributes(source);
  if (root === null) {
    problems.add("is not an SVG document");
  } else if (root.viewbox === undefined) {
    problems.add("has no viewBox");
  }
  if (DATA_URI.test(source)) {
    problems.add("contains a data URI");
  }
  if (BASE64.test(source)) {
    problems.add("contains a base64 payload");
  }
  if (EXTERNAL_URL.test(source) || /@import\b/i.test(source)) {
    problems.add("references an external stylesheet or URL");
  }
  const short = Math.max(0.5, 0.005 * viewBoxScale(source));
  for (const tag of tags(source)) {
    const local = tag.name.split(":").pop();
    if (local === "image") {
      problems.add("contains <image>");
    }
    if (local === "foreignobject") {
      problems.add("contains <foreignObject>");
    }
    if (local === "script") {
      problems.add("contains <script>");
    }
    if (local === "style") {
      problems.add("contains <style>; use presentation attributes");
    }
    if (local === "text") {
      problems.add("contains <text>; outline the wordmark as paths");
    }
    if (local === "use") {
      problems.add("contains <use>; inline the mark's shapes");
    }
    if (local === "symbol") {
      problems.add("contains <symbol>; inline the mark's shapes");
    }
    for (const [forbidden, label] of [
      ["pattern", "pattern"],
      ["mask", "mask"],
      ["filter", "filter"],
      ["clippath", "clipPath"],
    ]) {
      if (local === forbidden) {
        problems.add(`contains <${label}>; draw the mark in place`);
      }
    }
    for (const [name, value] of Object.entries(tag.attributes)) {
      const localName = name.split(":").pop();
      if (localName === "href" || localName === "src") {
        const reference = value.trim();
        if (reference.startsWith("#")) {
          continue;
        }
        if (reference === "") {
          problems.add("has an empty href");
          continue;
        }
        if (DATA_URI.test(reference)) {
          continue;
        }
        if (RASTER_EXTENSION.test(reference)) {
          problems.add(`references raster asset ${reference}`);
        } else {
          problems.add(`references external asset ${reference}`);
        }
      }
    }
    if (local === "path" && tag.attributes.d !== undefined) {
      const segments = pathSegments(tag.attributes.d);
      if (segments === null) {
        problems.add("has path data that cannot be parsed");
        continue;
      }
      if (segments.length > PATH_BUDGET) {
        problems.add(`has a path with ${segments.length} commands; the budget is ${PATH_BUDGET}`);
      }
      if (hasStaircase(segments, short)) {
        problems.add(`has a staircase of ${SHORT_RUN} consecutive short axis-aligned segments`);
      }
    }
    if ((local === "polygon" || local === "polyline") && tag.attributes.points !== undefined) {
      const points = pointPairs(tag.attributes.points);
      if (points === null) {
        problems.add(`has ${local} points that cannot be parsed`);
        continue;
      }
      if (points.length > PATH_BUDGET) {
        problems.add(`has a ${local} with ${points.length} points; the budget is ${PATH_BUDGET}`);
      }
      if (hasPolygonStaircase(points, short)) {
        problems.add(`has a staircase of ${SHORT_RUN} consecutive short axis-aligned segments`);
      }
    }
  }
  return [...problems];
}

function checkFile(file) {
  let source;
  try {
    source = fs.readFileSync(file, "utf8");
  } catch (error) {
    return [`cannot read: ${error.message}`];
  }
  return checkSource(source);
}

function defaultFiles() {
  const root = path.join(process.cwd(), "assets", "brand");
  const found = [];
  const walk = (directory) => {
    let entries;
    try {
      entries = fs.readdirSync(directory, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const full = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (entry.isFile() && entry.name.toLowerCase().endsWith(".svg")) {
        found.push(path.relative(process.cwd(), full));
      }
    }
  };
  walk(root);
  return found.sort();
}

function main(argv) {
  if (argv.includes("--help") || argv.includes("-h")) {
    console.log(USAGE);
    return 0;
  }
  const explicit = argv.filter((argument) => !argument.startsWith("--"));
  const files = explicit.length > 0 ? explicit : defaultFiles();
  if (files.length === 0) {
    console.log("asset-masters: no SVG masters under assets/brand");
    return 0;
  }
  let failed = false;
  for (const file of files) {
    for (const problem of checkFile(file)) {
      process.stderr.write(`${file}: ${problem}\n`);
      failed = true;
    }
  }
  if (failed) {
    process.stderr.write("asset-masters: redraw masters with primitives and Bezier curves; no embedded rasters or traced staircases\n");
    return 1;
  }
  console.log(`asset-masters: ${files.length} file(s) passed`);
  return 0;
}

module.exports = { checkFile, checkSource, main, tagAttributes };

if (require.main === module || process.argv[1] === "asset-masters") {
  process.exitCode = main(process.argv.slice(2));
}
