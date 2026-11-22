#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
cd "$root"

# shellcheck disable=SC2046 # The file list has no whitespace and must stay as separate arguments.
exec renovate-config-validator --strict --no-global default.json \
	$(find . \( -name .git -o -name node_modules -o -name .venv -o -name venv \) -prune -o -name renovate.json -print | sort)
