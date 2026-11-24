#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)

if [ -n "${GITLEAKS_CHECK_CASE:-}" ]; then
	exec sh "$script_dir/gitleaks-check-test.sh"
fi

gitleaks dir --config "$root/.gitleaks.toml" --redact --verbose --no-banner "$root"
gitleaks git --config "$root/.gitleaks.toml" --redact --verbose --no-banner "$root"
