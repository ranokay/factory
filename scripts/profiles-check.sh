#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
python=$(mise -C "$root" which python)
exec "$python" "$root/scripts/profiles-check.py" --root "${1:-.}"
