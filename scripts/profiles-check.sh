#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
uv=$(mise -C "$root" which uv)
exec "$uv" run --offline --no-project --no-config "$root/scripts/profiles-check.py" --root "${1:-.}"
