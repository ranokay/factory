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
grep -q 'RE2 failed to load' "$tmp/validator.log" || {
	printf '%s\n' 'validator failed for an unexpected reason' >&2
	exit 1
}

cat >"$tmp/failing" <<'STUB'
#!/bin/sh
printf '%s\n' 'validator output marker'
exit 3
STUB
chmod +x "$tmp/failing"
if RENOVATE_CONFIG_VALIDATOR="$tmp/failing" sh "$root/scripts/renovate-config-check.sh" >"$tmp/failing.log" 2>&1; then
	printf '%s\n' 'validator accepted a failing stub' >&2
	exit 1
else
	status=$?
fi
if printf './x\0' | xargs -0 sh -c 'exit 3' >/dev/null 2>&1; then
	xargs_status=0
else
	xargs_status=$?
fi
[ "$status" -eq "$xargs_status" ] || {
	printf '%s\n' "validator lost exit status $status (xargs uses $xargs_status)" >&2
	exit 1
}
grep -q 'validator output marker' "$tmp/failing.log" || {
	printf '%s\n' 'validator lost its captured output' >&2
	exit 1
}

cat >"$tmp/renovate" <<'STUB'
#!/bin/sh
printf '%s\n' 'RE2 not usable, falling back to RegExp'
printf '%s\n' 'renovate output marker'
STUB
chmod +x "$tmp/renovate"
if RENOVATE_BINARY="$tmp/renovate" python3 "$root/scripts/renovate-managers-test.py" >"$tmp/test.log" 2>&1; then
	printf '%s\n' 'managers test accepted the RE2 fallback' >&2
	exit 1
fi
grep -q 'Renovate could not load RE2' "$tmp/test.log" || {
	printf '%s\n' 'managers test failed for an unexpected reason' >&2
	exit 1
}
grep -q 'renovate output marker' "$tmp/test.log" || {
	printf '%s\n' 'managers test lost its captured output' >&2
	exit 1
}
