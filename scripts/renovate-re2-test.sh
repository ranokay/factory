#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/factory-renovate-re2.XXXXXX")
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

cat >"$tmp/validator" <<'STUB'
#!/bin/sh
printf '%s\n' 'RE2 not usable, falling back to RegExp'
STUB
chmod +x "$tmp/validator"
if RENOVATE_CONFIG_VALIDATOR="$tmp/validator" sh "$root/scripts/renovate-config-check.sh" >"$tmp/validator.log" 2>&1; then
	printf '%s\n' 'validator accepted the RE2 fallback' >&2
	exit 1
fi
grep -q 'RE2 failed to load' "$tmp/validator.log"

cat >"$tmp/renovate" <<'STUB'
#!/bin/sh
printf '%s\n' 'RE2 not usable, falling back to RegExp'
STUB
chmod +x "$tmp/renovate"
if RENOVATE_BINARY="$tmp/renovate" python3 "$root/scripts/renovate-managers-test.py" >"$tmp/test.log" 2>&1; then
	printf '%s\n' 'managers test accepted the RE2 fallback' >&2
	exit 1
fi
grep -q 'Renovate could not load RE2' "$tmp/test.log"
