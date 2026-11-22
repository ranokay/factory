#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
cd "$root"

files=$(mktemp "${TMPDIR:-/tmp}/factory-renovate.XXXXXX")
output=$(mktemp "${TMPDIR:-/tmp}/factory-renovate-output.XXXXXX")
trap 'rm -f "$files" "$output"' EXIT HUP INT TERM
find . \( -name .git -o -name node_modules -o -name .venv -o -name venv \) -prune -o -name renovate.json -print0 >"$files"
validator=$(mise -C "$root" which renovate-config-validator)
if ! xargs -0 "$validator" --strict --no-global default.json <"$files" >"$output" 2>&1; then
	cat "$output"
	exit 1
fi
cat "$output"
if grep -q 'RE2 not usable' "$output"; then
	printf '%s\n' 'RE2 failed to load; the Gate must evaluate regexes with RE2' >&2
	exit 1
fi
