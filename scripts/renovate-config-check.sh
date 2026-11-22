#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
cd "$root"

files=$(mktemp "${TMPDIR:-/tmp}/factory-renovate.XXXXXX")
trap 'rm -f "$files"' EXIT HUP INT TERM
find . \( -name .git -o -name node_modules -o -name .venv -o -name venv \) -prune -o -name renovate.json -print0 >"$files"
validator=$(mise -C "$root" which renovate-config-validator)
xargs -0 "$validator" --strict --no-global default.json <"$files"
