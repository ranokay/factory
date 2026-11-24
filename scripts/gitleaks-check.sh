#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)

gitleaks dir --config "$root/.gitleaks.toml" --redact --verbose --no-banner "$root"
gitleaks git --config "$root/.gitleaks.toml" --redact --verbose --no-banner "$root"
