#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

function silhouette(svg) {
  return svg
    .replace(/\b(fill|stroke|stop-color)\s*=\s*(?:"([^"]*)"|'([^']*)')/g, (match, name, double, single) => {
      const value = (double ?? single ?? "").trim().toLowerCase();
      return value === "none" || value === "transparent" ? match : `${name}="#000000"`;
    })
    .replace(/\scolor\s*=\s*(?:"[^"]*"|'[^']*')/i, ' color="#000000"');
}

function iconJson() {
  return `${JSON.stringify(
    {
      fill: { solid: "srgb:1.000000,1.000000,1.000000,1.000000" },
      groups: [
        {
          layers: [
            {
              "image-name": "mark.svg",
              name: "Mark",
              position: { scale: 1, "translation-in-points": [0, 0] },
            },
          ],
        },
      ],
      "supported-platforms": { squares: "shared" },
    },
    null,
    2,
  )}\n`;
}

function contents(images, intent) {
  return `${JSON.stringify(
    {
      images,
      info: { author: "xcode", version: 1 },
      properties: {
        "preserves-vector-representation": true,
        "template-rendering-intent": intent,
      },
    },
    null,
    2,
  )}\n`;
}

module.exports = {
  profile: "apple",
  tools: {
    ictool: {
      command: "/usr/bin/ictool",
      env: "ICTOOL",
      hint: "install Xcode 26 or later so /usr/bin/ictool exists, or set ICTOOL",
    },
    swift: {
      command: "swift",
      env: "SWIFT",
      hint: "install the macOS Swift toolchain, or set SWIFT",
    },
    swiftc: {
      command: "swiftc",
      env: "SWIFTC",
      hint: "install the macOS Swift toolchain, or set SWIFTC",
    },
  },
  managedRoots: ["assets/brand/AppIcon.icon", "assets/brand/AppIcon.icns", "assets/brand/Brand.xcassets"],
  generate(context) {
    const ictool = context.requireTool("ictool");
    context.requireTool("swift");
    context.requireTool("swiftc");

    const icon = context.masters.icon.text;
    const logo = context.masters.logo.text;
    const logoBox = context.viewBox(logo, context.masters.logo.path);

    context.removeDir("assets/brand/AppIcon.icon");
    context.removeDir("assets/brand/Brand.xcassets");
    context.removeFile("assets/brand/AppIcon.icns");

    context.write("assets/brand/AppIcon.icon/Assets/mark.svg", icon.replace(/currentColor/g, "#000000"));
    context.write("assets/brand/AppIcon.icon/icon.json", iconJson());

    const compiled = context.tempDir("icns");
    const result = spawnSync(
      ictool,
      [
        path.join(context.out, "assets/brand/AppIcon.icon"),
        "--app-icon",
        "AppIcon",
        "--compile",
        compiled,
        "--output-partial-info-plist",
        path.join(compiled, "info.plist"),
        "--minimum-deployment-target",
        "11.0",
        "--platform",
        "macosx",
        "--target-device",
        "mac",
      ],
      { encoding: "utf8" },
    );
    if (result.status !== 0) {
      throw new Error(`assets: ictool failed\n${result.stderr}${result.stdout}`);
    }
    const icns = path.join(compiled, "AppIcon.icns");
    if (!fs.existsSync(icns)) {
      throw new Error("assets: ictool did not write AppIcon.icns");
    }
    context.writeFrom("assets/brand/AppIcon.icns", icns);

    context.write(
      "assets/brand/Brand.xcassets/Contents.json",
      `${JSON.stringify({ info: { author: "xcode", version: 1 } }, null, 2)}\n`,
    );
    context.write(
      "assets/brand/Brand.xcassets/Logo.imageset/Contents.json",
      contents(
        [
          { filename: "Logo-Light.pdf", idiom: "universal" },
          {
            appearances: [{ appearance: "luminosity", value: "dark" }],
            filename: "Logo-Dark.pdf",
            idiom: "universal",
          },
        ],
        "original",
      ),
    );
    context.write(
      "assets/brand/Brand.xcassets/Logo.imageset/Logo-Light.pdf",
      context.pdf(context.colorize(logo, context.ink.light), logoBox.width, logoBox.height, context.ink.light),
    );
    context.write(
      "assets/brand/Brand.xcassets/Logo.imageset/Logo-Dark.pdf",
      context.pdf(context.colorize(logo, context.ink.dark), logoBox.width, logoBox.height, context.ink.dark),
    );
    context.write(
      "assets/brand/Brand.xcassets/MenuBarTemplate.imageset/Contents.json",
      contents([{ filename: "MenuBarTemplate.pdf", idiom: "universal" }], "template"),
    );
    context.write(
      "assets/brand/Brand.xcassets/MenuBarTemplate.imageset/MenuBarTemplate.pdf",
      context.pdf(silhouette(icon), 18, 18, "#000000"),
    );
  },
};
