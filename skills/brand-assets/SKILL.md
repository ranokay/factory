---
name: brand-assets
description: "Draw three structurally different icon and logo concepts for a project, preview them in the cache, and commit the chosen hand-built SVG masters."
disable-model-invocation: true
metadata:
  author: ranokay
---

# Brand assets

Ask a short brief, draw three icon and logo concepts in this session, show them on a preview page outside the repository, and commit only the direction ranokay picks.

This skill runs on the frontier model `claudeAgent/claude-opus-5-5` at effort `medium`. Draw in this interactive session. Never spawn a subagent or Worker to draw, and never fall back to a cheaper model. If this session is not that model, stop and ask ranokay to switch before drawing.

## What it produces

Before the pick: three concept icons, three logo lockups and one preview page
under `~/.cache/factory/<owner>/<repo>/brand-assets/`. Nothing enters the
repository.

After the pick: `assets/brand/icon.svg` and `assets/brand/logo.svg`, hand-built
from primitives and Bezier curves, checked, and committed.

Platform exports (`.icns`, `.icon`, xcassets, favicon and `.ico`, PNG sizes, OG
image, README header, store artwork) and `mise run assets` are out of scope for
this skill; they belong to a later issue. Do not create or hand-edit them.

## 1. Brief

Ask one short brief through the harness's question tool:

1. The product name and one line on what it does.
2. Who it is for, and three adjectives for how it should feel.
3. What the mark must contain or avoid: a letterform, a symbol, existing
    colours, a wordmark, an icon-only mark.
4. Where it appears first (menu bar, Dock, browser tab, app icon) and whether it
    must work on light and dark.

Restate the brief in one short paragraph. Ask again only where an answer leaves
a real choice open.

## 2. Resolve the cache and the baseline

Resolve the repository:

```sh
gh repo view --json owner,name --jq '"\(.owner.login)/\(.name)"'
```

If there is no GitHub remote, parse `git remote get-url origin`; without a
remote, use `local/<directory-name>`.

Resolve the Factory checkout once and use it for every script below:

```sh
factory=${FACTORY_CHECKOUT:-$HOME/.agents}
[ -d "$factory/profiles/universal" ] || factory=$HOME/Developer/factory
[ -f "$factory/profiles/universal/scripts/asset-masters.js" ] || {
  echo "brand-assets: set FACTORY_CHECKOUT to the Factory checkout" >&2
  exit 1
}
```

Set `cache=~/.cache/factory/<owner>/<repo>/brand-assets` and create it. Record
the pre-pick state there:

```sh
git rev-parse HEAD > "$cache/baseline-head"
git status --porcelain=v1 -uall > "$cache/baseline-status"
{
  git diff HEAD | shasum -a 256
  git ls-files --others --exclude-standard -z | xargs -0 shasum -a 256
} > "$cache/baseline-content"
```

Write every concept, note and preview to `$cache` until the pick. Never write to
the repository, stage anything or commit before step 6.

## 3. Draw three concepts

Write `concept-1-<slug>-icon.svg`, `concept-2-<slug>-icon.svg` and
`concept-3-<slug>-icon.svg` into `$cache`, and a matching
`concept-N-<slug>-lockup.svg` for each. Each icon is a square 1024 `viewBox`
with a transparent background and no app tile. Each lockup pairs that mark with
the wordmark as outlined paths, at a wide `viewBox`. When the brief asks for an
icon-only mark, the lockup is a wide mark-only variant instead.

- Build every shape from primitives and Bezier curves: `path`, `circle`,
  `rect`, `line`, `polygon`. Never embed a raster, never trace a pixel image.
- No `<image>`, data URIs, `<foreignObject>`, `<style>`, raster or external
  references, or `<text>`. Use presentation attributes, and outline a wordmark as
  paths.
- Draw the neutral ink in `currentColor`; the preview sets it to `#18181b` on
  light and `#f4f4f5` on dark. Accents may use explicit colours. A concept that
  must use fixed ink declares `data-brand-ink="#rrggbb"` on its root and ships a
  dark variant with `--dark`/`--dark-lockup`; otherwise the page flags its
  contrast.
- Keep every path under 400 drawing commands and avoid long runs of tiny
  axis-aligned steps.
- A concept must still read at 16 px on both boards.

**The difference check runs before the concepts are shown.** For each concept,
write one line: “it forms *<metaphor>* as *<silhouette>*”. Then compare the
three icons at 16 px in one colour (the preview page's silhouette strip does
this), and compare the three lockups beside each other. Check both the light and
dark board: a `Contrast check` banner means the ink falls below 3:1 on that
board, so redraw it or add the dark variant. If two share a metaphor, or collapse
into the same silhouette, redraw one and repeat the check. Three recolours of
one shape are not three concepts.

Run the checker on the concepts before showing them:

```sh
node "$factory/profiles/universal/scripts/asset-masters.js" "$cache"/*.svg
```

Fix every reported file and rerun.

## 4. Preview

Generate one self-contained page with the script beside this skill:

```sh
node "$factory/skills/brand-assets/scripts/brand-preview.js" \
  --title "<Product> brand concepts" \
  --subtitle "<one line from the brief>" \
  --site "<product-slug>.app" \
  --concept "Orbit=$cache/concept-1-orbit-icon.svg" \
  --concept "Fold=$cache/concept-2-fold-icon.svg" \
  --concept "Signal=$cache/concept-3-signal-icon.svg" \
  --lockup "Orbit=$cache/concept-1-orbit-lockup.svg" \
  --lockup "Fold=$cache/concept-2-fold-lockup.svg" \
  --lockup "Signal=$cache/concept-3-signal-lockup.svg" \
  --note "Orbit=<one-line metaphor>" \
  --note "Fold=<one-line metaphor>" \
  --note "Signal=<one-line metaphor>" \
  --output "$cache/preview.html"
```

The command above relies on `currentColor`. For a fixed-ink concept, add
`--dark "Name=$cache/concept-N-slug-dark-icon.svg"` and
`--dark-lockup "Name=$cache/concept-N-slug-dark-lockup.svg"` for the same name.

Show it with T3:

1. `html_preview({ path: "$cache/preview.html", width: 1440 })` and read
    `contentHeight`.
2. `html_render({ path: "$cache/preview.html", title: "<Product> brand concepts", height: <contentHeight> })`.

The page shows each concept's icon at 16, 32, 64, 128, 256, 512 and 1024 px,
its logo lockup, a 16 px silhouette strip, the mark as a menu-bar template
glyph, as a favicon in a mock tab bar, and in a mock Dock, on light and dark. A
`Contrast check` banner names any ink below 3:1 on a board. The size ladder
scrolls for 512 and 1024; “Show all sizes” expands one board to the full page
width so the 1024 master is shown whole. If T3 preview tools are unavailable,
open the file with `open` and give ranokay its path.

## 5. The pick

Before asking for a pick, prove the working tree did not move:

```sh
test "$(git rev-parse HEAD)" = "$(cat "$cache/baseline-head")"
git status --porcelain=v1 -uall > "$cache/pre-pick-status"
diff -u "$cache/baseline-status" "$cache/pre-pick-status"
{
  git diff HEAD | shasum -a 256
  git ls-files --others --exclude-standard -z | xargs -0 shasum -a 256
} > "$cache/pre-pick-content"
diff -u "$cache/baseline-content" "$cache/pre-pick-content"
```

If either check differs, stop and show the difference. Do not ask for a pick on
a tree that changed while the concepts were drawn.

Ask one question: pick concept 1, 2 or 3; ask for a revision of one; or ask for
a fourth direction. A revision stays in `$cache`, regenerates the page and reruns
the difference check. The baseline must still match before the next ask.

## 6. Commit the chosen masters

After the pick, draw the final masters into `assets/brand/` in the repository:

- `icon.svg`: the chosen concept's icon redrawn at a 1024 square `viewBox`,
  transparent background, primitives and Bezier curves, neutral ink in
  `currentColor`.
- `logo.svg`: the chosen concept's lockup redrawn, with the wordmark outlined as
  paths and its ink in `currentColor`, never `<text>`. For an icon-only brand,
  make a wide variant of the mark.

Then:

```sh
node "$factory/profiles/universal/scripts/asset-masters.js" assets/brand/*.svg
mise run check
```

Fix everything either command reports. Commit only the masters, for example
`feat: add <product> brand masters`, and report the files, the checker result,
the Gate result and the commit SHA. Do not push unless the surrounding Run's
rules say to.
