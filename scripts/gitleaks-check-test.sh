#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
case_name=${GITLEAKS_CHECK_CASE:-all}

test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
	printf 'gitleaks-check-test: %s\n' "$*" >&2
	exit 1
}

expect_contains() {
	haystack=$1
	needle=$2
	case $haystack in
	*"$needle"*) ;;
	*) fail "expected '$needle' in: $haystack" ;;
	esac
}

expect_not_contains() {
	haystack=$1
	needle=$2
	case $haystack in
	*"$needle"*) fail "did not expect '$needle' in: $haystack" ;;
	esac
}

run_command() {
	if output=$("$@" 2>&1); then
		code=0
	else
		code=$?
	fi
}

random_hex() {
	dd if=/dev/urandom bs=32 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n'
}

random_alnum() {
	dd if=/dev/urandom bs=48 count=1 2>/dev/null | base64 | tr -dc 'A-Za-z0-9' | cut -c1-"$1"
}

run_scan() {
	run_command gitleaks dir --config "$root/.gitleaks.toml" --redact --verbose --no-banner "$1"
}

case_fabricated() {
	fake="ctx7sk-$(random_hex)"
	mkdir -p "$test_root/fabricated"
	printf '{"headers":{"Authorization":"Bearer %s"}}\n' "$fake" >"$test_root/fabricated/config.json"
	run_scan "$test_root/fabricated"
	[ "$code" -ne 0 ] || fail "the Context7 rule did not fire on a fabricated key"
	expect_contains "$output" "mcp-literal-credential"
	expect_not_contains "$output" "$fake"
	printf 'ok: the Context7 rule fires on a fabricated key and redacts it\n'
}

case_env_reference() {
	fake="ctx7sk-$(random_hex)"
	mkdir -p "$test_root/references"
	cat >"$test_root/references/claude.json" <<'JSON'
{"headers":{"Authorization":"Bearer ${CONTEXT7_API_KEY}"}}
JSON
	cat >"$test_root/references/opencode.json" <<'JSON'
{"headers":{"Authorization":"Bearer {env:CONTEXT7_API_KEY}"}}
JSON
	cat >"$test_root/references/codex.toml" <<'TOML'
bearer_token_env_var = "CONTEXT7_API_KEY"
TOML
	printf '{"reference":"op://factory/Context7 API/credential"}\n' >"$test_root/references/registry.json"
	run_scan "$test_root/references"
	[ "$code" -eq 0 ] || fail "the env references were reported as a leak: $output"
	expect_not_contains "$output" "$fake"
	printf 'ok: an env reference is not a secret\n'
}

case_bare_key() {
	fake="ctx7sk-$(random_hex)"
	mkdir -p "$test_root/bare"
	printf '{"value":"%s"}\n' "$fake" >"$test_root/bare/config.json"
	run_scan "$test_root/bare"
	[ "$code" -ne 0 ] || fail "a bare Context7 key was not detected"
	expect_contains "$output" "context7-key-token"
	expect_not_contains "$output" "$fake"
	printf 'ok: a bare Context7 key is detected and redacted\n'
}

case_url_host() {
	mkdir -p "$test_root/url"
	printf 'See https://ctx7.example.com/docs/page for details.\n' >"$test_root/url/page.md"
	run_scan "$test_root/url"
	[ "$code" -eq 0 ] || fail "a ctx7 URL host was reported as a key: $output"
	printf 'ok: a ctx7 URL host is not a key\n'
}

case_named_value() {
	fake="$(random_hex)"
	mkdir -p "$test_root/named"
	printf '{"CONTEXT7_API_KEY":"%s"}\n' "$fake" >"$test_root/named/config.json"
	run_scan "$test_root/named"
	[ "$code" -ne 0 ] || fail "a key stored under CONTEXT7_API_KEY was not detected"
	expect_contains "$output" "mcp-literal-credential"
	expect_not_contains "$output" "$fake"
	printf 'ok: a key under the CONTEXT7_API_KEY name is detected and redacted\n'
}

case_default_rules() {
	fake=$(random_alnum 32)
	mkdir -p "$test_root/defaults"
	printf 'apiToken = %s\n' "$fake" >"$test_root/defaults/settings.txt"
	run_scan "$test_root/defaults"
	[ "$code" -ne 0 ] || fail "the default rules stopped firing"
	expect_contains "$output" "RuleID:"
	expect_not_contains "$output" "$fake"
	printf 'ok: the default rules still fire\n'
}

case_all() {
	case_fabricated
	case_env_reference
	case_bare_key
	case_url_host
	case_named_value
	case_default_rules
}

case $case_name in
fabricated) case_fabricated ;;
env-reference) case_env_reference ;;
bare-key) case_bare_key ;;
url-host) case_url_host ;;
named-value) case_named_value ;;
default-rules) case_default_rules ;;
all) case_all ;;
*)
	printf 'unknown case: %s\n' "$case_name" >&2
	exit 2
	;;
esac
