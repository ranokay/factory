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

case_keyword_catalog() {
	run_command python3 "$root/scripts/gitleaks-keywords-check.py"
	[ "$code" -eq 0 ] || fail "the gitleaks keyword catalog is stale: $output"
	fixture=$test_root/keyword-catalog
	mkdir -p "$fixture/tools" "$fixture/profiles/universal"
	cp "$root/mise.toml" "$fixture/mise.toml"
	cp "$root/profiles/universal/mise.toml" "$fixture/profiles/universal/mise.toml"
	cp "$root/.gitleaks.toml" "$fixture/.gitleaks.toml"
	cp "$root/tools/gitleaks-keywords.json" "$fixture/tools/gitleaks-keywords.json"
	cp "$root/tools/gitleaks-rule-samples.json.gz" "$fixture/tools/gitleaks-rule-samples.json.gz"
	run_command python3 - "$fixture/profiles/universal/mise.toml" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(path.read_text().replace('gitleaks = "8.30.1"', 'gitleaks = "8.30.2"'))
PY
	[ "$code" -eq 0 ] || fail "cannot bump the fixture gitleaks pin: $output"
	run_command python3 "$root/scripts/gitleaks-keywords-check.py" --root "$fixture"
	[ "$code" -ne 0 ] || fail "a gitleaks pin bump did not invalidate the keyword catalog"
	expect_contains "$output" "gitleaks version"
	run_command python3 - "$fixture/profiles/universal/mise.toml" "$fixture/tools/gitleaks-keywords.json" "$root/scripts" <<'PY'
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[3])
from gitleaks_catalog import digest

pin = Path(sys.argv[1])
pin.write_text(pin.read_text().replace('gitleaks = "8.30.2"', 'gitleaks = "8.30.1"'))
path = Path(sys.argv[2])
catalog = json.loads(path.read_text())
rule = next(rule for rule in catalog["rules"] if rule["id"] == "1password-secret-key")
rule["regex"] += "x"
catalog["rules_sha256"] = digest(catalog["rules"])
path.write_text(json.dumps(catalog, indent=2) + "\n")
PY
	[ "$code" -eq 0 ] || fail "cannot drift the default rule fixture: $output"
	run_command python3 "$root/scripts/gitleaks-keywords-check.py" --root "$fixture"
	[ "$code" -ne 0 ] || fail "default-rule drift passed the independently pinned digest"
	expect_contains "$output" "default-rule digest"
	cp "$root/tools/gitleaks-keywords.json" "$fixture/tools/gitleaks-keywords.json"
	run_command python3 - "$fixture/tools/gitleaks-keywords.json" "$root/scripts" <<'PY'
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
from gitleaks_catalog import digest

path = Path(sys.argv[1])
catalog = json.loads(path.read_text())
rule = next(rule for rule in catalog["rules"] if rule["python_safe"])
rule["python_safe"] = False
rule["python_unsafe_reason"] = "forged classification"
catalog["rules_sha256"] = digest(catalog["rules"])
path.write_text(json.dumps(catalog, indent=2) + "\n")
PY
	[ "$code" -eq 0 ] || fail "cannot drift the merged rule fixture: $output"
	run_command python3 "$root/scripts/gitleaks-keywords-check.py" --root "$fixture"
	[ "$code" -ne 0 ] || fail "merged-rule drift passed the independently pinned digest"
	expect_contains "$output" "merged-rule digest"
	cp "$root/tools/gitleaks-keywords.json" "$fixture/tools/gitleaks-keywords.json"
	run_command python3 - "$fixture/tools/gitleaks-rule-samples.json.gz" "$root/scripts" <<'PY'
import base64
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
from gitleaks_catalog import digest

path = Path(sys.argv[1])
with gzip.open(path, "rt") as source:
	fixture = json.load(source)
fixture["samples"][0]["text"] = base64.b64encode(b"tampered").decode()
samples = []
for sample in fixture["samples"]:
	decoded = {"id": sample["id"], "text": base64.b64decode(sample["text"]).decode()}
	if "path" in sample:
		decoded["path"] = sample["path"]
	samples.append(decoded)
fixture["samples_sha256"] = digest(samples)
with gzip.GzipFile(filename=path, mode="wb", mtime=0) as output:
	output.write(json.dumps(fixture, separators=(",", ":")).encode())
PY
	[ "$code" -eq 0 ] || fail "cannot drift the true-positive fixture: $output"
	run_command python3 "$root/scripts/gitleaks-keywords-check.py" --root "$fixture"
	[ "$code" -ne 0 ] || fail "true-positive drift passed the independently pinned digest"
	expect_contains "$output" "true-positive digest"
	cp "$root/tools/gitleaks-keywords.json" "$fixture/tools/gitleaks-keywords.json"
	cp "$root/tools/gitleaks-rule-samples.json.gz" "$fixture/tools/gitleaks-rule-samples.json.gz"
	run_command python3 - "$fixture/tools/gitleaks-keywords.json" "$root/scripts" <<'PY'
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
from gitleaks_catalog import digest

path = Path(sys.argv[1])
catalog = json.loads(path.read_text())
catalog["unkeyworded_rules"][0]["path"] = "(?i)\\.pem$"
catalog["unkeyworded_rules_sha256"] = digest(catalog["unkeyworded_rules"])
path.write_text(json.dumps(catalog, indent=2) + "\n")
PY
	[ "$code" -eq 0 ] || fail "cannot drift the unkeyworded rule fixture: $output"
	run_command python3 "$root/scripts/gitleaks-keywords-check.py" --root "$fixture"
	[ "$code" -ne 0 ] || fail "unkeyworded-rule drift passed the independently pinned digest"
	expect_contains "$output" "unkeyworded-rule digest"
	run_command python3 - "$fixture/.gitleaks.toml" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(path.read_text().replace("useDefault = true", 'useDefault = true\ndisabledRules = ["generic-api-key"]'))
PY
	[ "$code" -eq 0 ] || fail "cannot disable a default rule in the fixture: $output"
	run_command python3 "$root/scripts/gitleaks-keywords-check.py" --root "$fixture"
	[ "$code" -ne 0 ] || fail "disabled default rules passed the independently pinned catalog"
	expect_contains "$output" "must not disable default rules"
	printf 'ok: the keyword catalog matches independently pinned upstream rules, path rules and samples\n'
}

case_all() {
	case_keyword_catalog
	case_fabricated
	case_env_reference
	case_bare_key
	case_url_host
	case_named_value
	case_default_rules
}

case $case_name in
keyword-catalog) case_keyword_catalog ;;
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
