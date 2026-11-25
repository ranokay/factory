#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
base_path=$PATH
case_name=${MCP_SYNC_CASE:-all}

test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
	printf 'mcp-sync-test: %s\n' "$*" >&2
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

claude_expected() {
	printf 'Bearer $%s' '{CONTEXT7_API_KEY}'
}

run_python() {
	run_command uv run --offline --no-project --no-config --python "$(mise which python)" python "$@"
}

json_value() {
	file=$1
	path=$2
	run_python -c 'import functools,json,sys; print(functools.reduce(lambda d,p: d[int(p)] if p.isdigit() else d[p], sys.argv[2].split("."), json.load(open(sys.argv[1]))))' "$file" "$path"
	[ "$code" -eq 0 ] || fail "cannot read $path from $file: $output"
	printf '%s\n' "$output"
}

write_config() {
	case $1 in
	claude) printf '%s\n' "$2" >"$home/.claude.json" ;;
	opencode) printf '%s\n' "$2" >"$home/.config/opencode/opencode.json" ;;
	codex) printf '%s\n' "$2" >"$home/.codex/config.toml" ;;
	*) fail "unknown config $1" ;;
	esac
}

setup_case() {
	case_dir=$test_root/$1
	home=$case_dir/home
	bin=$case_dir/bin
	log=$case_dir/stub.log
	mkdir -p "$home/.config/opencode" "$home/.codex" "$bin"
	home=$(CDPATH='' cd "$home" && pwd -P)
	export XDG_CONFIG_HOME="$home/.config"
	export CODEX_HOME="$home/.codex"
	: >"$log"
	export STUB_LOG="$log"
	PATH="$bin:$base_path"
}

file_mode() {
	run_python -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[2:])' "$1"
	[ "$code" -eq 0 ] || fail "cannot read the mode of $1: $output"
	printf '%s\n' "$output"
}

run_sync() {
	run_command uv run --offline --no-project --no-config --python "$(mise which python)" python "$root/scripts/mcp-sync.py" --home "$home" "$@"
}

make_stubs() {
	mkdir -p "$bin"
	cat >"$bin/security" <<'STUB'
#!/bin/sh
[ "${KEYCHAIN_MISSING:-0}" = 0 ] || exit 1
printf 'fixture-service-token'
STUB
	cat >"$bin/op" <<'STUB'
#!/bin/sh
set -eu
printf 'op %s\n' "$*" >>"$STUB_LOG"
[ "${OP_SERVICE_ACCOUNT_TOKEN:-}" = fixture-service-token ] || exit 1
case "${OP_EMPTY_SECOND:-}:$*" in 1:*Second*) exit 0 ;; esac
if [ -n "${OP_FAIL:-}" ]; then
	printf 'op: account is locked\n' >&2
	exit 1
fi
printf 'dummy-value'
STUB
	cat >"$bin/launchctl" <<'STUB'
#!/bin/sh
set -eu
printf 'launchctl %s\n' "$*" >>"$STUB_LOG"
if [ "$1" = "getenv" ]; then
	if [ -n "${LAUNCHCTL_VALUE:-}" ]; then
		printf '%s\n' "$LAUNCHCTL_VALUE"
		exit 0
	fi
	exit 1
fi
exit 0
STUB
	chmod +x "$bin/op" "$bin/launchctl" "$bin/security"
}

backup_count() {
	find "$home" -name '*.backup-*' -type f | wc -l | tr -d ' '
}

case_fresh() {
	setup_case fresh
	make_stubs
	write_config claude '{}'
	write_config opencode '{}'
	write_config codex ''
	run_sync
	[ "$code" -eq 0 ] || fail "render failed: $output"
	[ "$(json_value "$home/.claude.json" mcpServers.context7.headers.Authorization)" = "$(claude_expected)" ] || fail "the Claude header is not its env reference"
	[ "$(json_value "$home/.config/opencode/opencode.json" 'mcp.context7.headers.Authorization')" = 'Bearer {env:CONTEXT7_API_KEY}' ] || fail "the OpenCode header is not its env reference"
	grep -q '^\[mcp_servers.context7\]$' "$home/.codex/config.toml" || fail "Codex has no context7 table"
	grep -q '^bearer_token_env_var = "CONTEXT7_API_KEY"$' "$home/.codex/config.toml" || fail "Codex has no bearer token env var"
	grep -q '^ignore_default_excludes = true$' "$home/.codex/config.toml" || fail "Codex excludes registry secrets from shells"
	if grep -q OP_SERVICE_ACCOUNT_TOKEN "$home/.codex/config.toml"; then fail "service token leaked into config"; fi
	[ "$(json_value "$home/.claude.json" mcpServers.cua-driver.command)" = "$home/.local/bin/cua-driver" ] || fail "the Claude cua-driver command is not expanded"
	[ "$(json_value "$home/.config/opencode/opencode.json" 'mcp.cua-driver.type')" = local ] || fail "OpenCode has no cua-driver server"
	grep -q '^command = "'"$home"'/.local/bin/cua-driver"$' "$home/.codex/config.toml" || fail "Codex has no expanded cua-driver command"
	backups=$(backup_count)
	run_sync
	[ "$code" -eq 0 ] || fail "second render failed: $output"
	[ "$(backup_count)" = "$backups" ] || fail "an unchanged second render wrote a backup"
	run_sync --check
	[ "$code" -eq 0 ] || fail "the check reported drift after a render: $output"
	expect_contains "$output" "in sync"
	printf 'ok: a fresh render uses each harness env syntax and stays in sync\n'
}

case_drift() {
	setup_case drift
	make_stubs
	write_config claude '{}'
	run_sync
	[ "$code" -eq 0 ] || fail "render failed: $output"
	run_python -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["mcpServers"]["context7"]["headers"]["Authorization"]="Bearer literal-value"; open(p, "w").write(json.dumps(d, indent=2) + "\n")' "$home/.claude.json"
	[ "$code" -eq 0 ] || fail "cannot drift the Claude config: $output"
	run_sync --check
	[ "$code" -ne 0 ] || fail "the check passed with a drifted Claude config"
	expect_contains "$output" "$home/.claude.json"
	run_sync
	[ "$code" -eq 0 ] || fail "render did not repair the drift: $output"
	run_sync --check
	[ "$code" -eq 0 ] || fail "the check still reports drift: $output"
	[ "$(json_value "$home/.claude.json" mcpServers.context7.headers.Authorization)" = "$(claude_expected)" ] || fail "the drift was not repaired"
	printf 'ok: the check names drift and the render repairs it\n'
}

case_retired() {
	setup_case retired
	make_stubs
	write_config claude '{"mcpServers":{"caveman":{"type":"stdio","command":"x","args":[],"env":{}},"cua":{"type":"stdio","command":"cua","args":["mcp"],"env":{}}}}'
	write_config opencode '{"mcp":{"caveman":{"type":"local","command":["x"],"enabled":true},"cua":{"type":"local","command":["cua","mcp"],"enabled":true}}}'
	write_config codex '[mcp_servers.caveman]
command = "x"

[mcp_servers.cua]
command = "cua"
args = ["mcp"]
'
	run_sync
	[ "$code" -eq 0 ] || fail "render failed: $output"
	run_python -c 'import json,sys; docs=((json.load(open(sys.argv[1])),"mcpServers"),(json.load(open(sys.argv[2])),"mcp")); assert all("caveman" not in d[k] and "cua" in d[k] for d,k in docs)' "$home/.claude.json" "$home/.config/opencode/opencode.json"
	[ "$code" -eq 0 ] || fail "a retired server or an unmanaged server is wrong in JSON: $output"
	grep -q '^\[mcp_servers.caveman\]$' "$home/.codex/config.toml" && fail "Codex kept the retired caveman server"
	grep -q '^\[mcp_servers.cua\]$' "$home/.codex/config.toml" || fail "Codex dropped the unmanaged cua server"
	run_sync --check
	[ "$code" -eq 0 ] || fail "the check reported drift after removing a retired server: $output"
	printf 'ok: a retired server is removed and an unmanaged one survives\n'
}

case_absent() {
	setup_case absent
	make_stubs
	rm -rf "$home/.config/opencode" "$home/.codex"
	run_sync --check
	[ "$code" -ne 0 ] || fail "the check passed without configs: $output"
	expect_contains "$output" "$home/.claude.json"
	expect_contains "$output" "$home/.config/opencode/opencode.json"
	expect_contains "$output" "$home/.codex/config.toml"
	run_sync
	[ "$code" -eq 0 ] || fail "render failed without configs: $output"
	[ -f "$home/.claude.json" ] || fail "the render did not create the Claude config"
	[ -f "$home/.config/opencode/opencode.json" ] || fail "the render did not create the OpenCode config"
	[ -f "$home/.codex/config.toml" ] || fail "the render did not create the Codex config"
	[ "$(json_value "$home/.claude.json" mcpServers.context7.headers.Authorization)" = "$(claude_expected)" ] || fail "the created Claude header is not its env reference"
	[ "$(json_value "$home/.config/opencode/opencode.json" 'mcp.context7.headers.Authorization')" = 'Bearer {env:CONTEXT7_API_KEY}' ] || fail "the created OpenCode header is not its env reference"
	grep -q '^\[mcp_servers.context7\]$' "$home/.codex/config.toml" || fail "the created Codex config has no context7 table"
	grep -q '^bearer_token_env_var = "CONTEXT7_API_KEY"$' "$home/.codex/config.toml" || fail "the created Codex config has no bearer token env var"
	run_python -c 'import json,sys; d=json.load(open(sys.argv[1])); assert list(d)==["mcpServers"] and sorted(d["mcpServers"])==["context7","cua-driver"], d' "$home/.claude.json"
	[ "$code" -eq 0 ] || fail "the created Claude config is not minimal: $output"
	run_python -c 'import json,sys; d=json.load(open(sys.argv[1])); assert list(d)==["mcp"] and sorted(d["mcp"])==["context7","cua-driver"], d' "$home/.config/opencode/opencode.json"
	[ "$code" -eq 0 ] || fail "the created OpenCode config is not minimal: $output"
	run_python -c 'import sys,tomllib; c=tomllib.load(open(sys.argv[1],"rb")); assert sorted(c["mcp_servers"])==["context7","cua-driver"] and c["shell_environment_policy"]["ignore_default_excludes"] is True, c' "$home/.codex/config.toml"
	[ "$code" -eq 0 ] || fail "the created Codex config does not parse or is not minimal: $output"
	run_python -c 'import sys; assert open(sys.argv[1]).read().startswith("[shell_environment_policy]\n")' "$home/.codex/config.toml"
	[ "$code" -eq 0 ] || fail "the created Codex config starts with a blank line: $output"
	[ "$(backup_count)" = 0 ] || fail "the render backed up a config it created"
	run_sync --check
	[ "$code" -eq 0 ] || fail "the check reported drift after creating the configs: $output"
	expect_contains "$output" "in sync"
	printf 'ok: an absent harness config is created with the registry servers\n'
}

case_preserve() {
	setup_case preserve
	make_stubs
	write_config claude '{"numStartups":7,"mcpServers":{"cua":{"type":"stdio","command":"cua","args":["mcp"],"env":{}}}}'
	write_config opencode '{"theme":"dark","mcp":{"cua":{"type":"local","command":["cua","mcp"],"enabled":true}}}'
	run_sync
	[ "$code" -eq 0 ] || fail "render failed: $output"
	[ "$(json_value "$home/.claude.json" numStartups)" = 7 ] || fail "the render dropped an unmanaged Claude key"
	[ "$(json_value "$home/.config/opencode/opencode.json" theme)" = dark ] || fail "the render dropped an unmanaged OpenCode key"
	[ "$(json_value "$home/.claude.json" mcpServers.cua.command)" = cua ] || fail "the render dropped an unmanaged Claude server"
	[ "$(json_value "$home/.config/opencode/opencode.json" 'mcp.cua.command.0')" = cua ] || fail "the render dropped an unmanaged OpenCode server"
	run_python -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["numStartups"]=99; open(p,"w").write(json.dumps(d,indent=2)+"\n")' "$home/.claude.json"
	[ "$code" -eq 0 ] || fail "cannot churn an unrelated key: $output"
	run_sync --check
	[ "$code" -eq 0 ] || fail "unrelated churn was reported as drift: $output"
	run_sync
	[ "$code" -eq 0 ] || fail "render failed after unrelated churn: $output"
	[ "$(json_value "$home/.claude.json" numStartups)" = 99 ] || fail "the render overwrote an unrelated key"
	printf 'ok: keys and servers outside the registry survive\n'
}

case_backup() {
	setup_case backup
	make_stubs
	write_config claude '{"numStartups":7,"mcpServers":{}}'
	chmod 600 "$home/.claude.json"
	run_sync
	[ "$code" -eq 0 ] || fail "render failed: $output"
	backup=$(find "$home" -maxdepth 1 -name '.claude.json.backup-*' -type f | head -n 1)
	[ -n "$backup" ] || fail "the render did not back up the Claude config"
	run_python -c 'import json,sys; assert json.load(open(sys.argv[1]))["numStartups"] == 7' "$backup"
	[ "$(file_mode "$home/.claude.json")" = 600 ] || fail "the render changed the Claude config mode"
	before=$(backup_count)
	run_sync
	[ "$(backup_count)" = "$before" ] || fail "an unchanged render wrote another backup"
	printf 'ok: a rewritten config is backed up and keeps its mode\n'
}

case_env() {
	setup_case env
	make_stubs
	write_config claude '{}'
	run_sync --env
	[ "$code" -eq 0 ] || fail "setting the env failed: $output"
	expect_not_contains "$output" "dummy-value"
	grep -q 'launchctl setenv CONTEXT7_API_KEY dummy-value' "$log" || fail "launchctl did not receive the secret"
	grep -q 'op read --no-newline op://factory/Context7 API/credential' "$log" || fail "op was not asked for the registry reference"
	if grep -q 'fixture-service-token\|OP_SERVICE_ACCOUNT_TOKEN' "$log"; then fail "service token leaked"; fi
	printf 'ok: the env mode reads the reference and sets only the name in output\n'
}

case_env_fail() {
	setup_case env-fail
	make_stubs
	write_config claude '{}'
	export OP_FAIL=1
	run_sync --env
	[ "$code" -ne 0 ] || fail "the env mode passed while op failed"
	expect_contains "$output" "CONTEXT7_API_KEY"
	expect_not_contains "$output" "dummy-value"
	if grep -q 'setenv' "$log"; then fail "launchctl was called after op failed"; fi
	printf 'ok: an unreadable reference fails loudly and sets nothing\n'
}

case_reference_check() {
	setup_case reference-check
	make_stubs
	KEYCHAIN_MISSING=1 run_sync --check-references
	[ "$code" -ne 0 ] || fail "missing Keychain token passed"
	expect_contains "$output" "missing or inaccessible Keychain token"
	OP_FAIL=1 run_sync --check-references
	[ "$code" -ne 0 ] || fail "unreadable item passed"
	expect_contains "$output" "op://factory/Context7 API/credential"
	expect_not_contains "$output" "fixture-service-token"
	registry=$case_dir/two.json
	printf '%s\n' '{"servers":{"a":{"transport":"http","url":"https://example.invalid","targets":["codex"],"headers":{"one":{"env":"FIRST_KEY","reference":"op://factory/First/credential"},"two":{"env":"SECOND_KEY","reference":"op://factory/Second/credential"}}}}}' >"$registry"
	OP_FAIL=1 OP_EMPTY_SECOND=1 run_sync --check-references --registry "$registry"
	[ "$code" -ne 0 ] || fail "unreadable items passed"
	expect_contains "$output" "FIRST_KEY (op://factory/First/credential)"
	expect_contains "$output" "SECOND_KEY (op://factory/Second/credential)"
	expect_contains "$output" "is empty in 1Password"
	grep -q 'op read --no-newline op://factory/Second/credential' "$log" || fail "second reference was not attempted"
	printf 'ok: reference checks name missing tokens and unreadable items\n'
}

case_policy_conflict() {
	setup_case policy-conflict
	write_config codex '[shell_environment_policy]
include_only = ["PATH"]'
	run_sync --check
	[ "$code" -ne 0 ] || fail "a conflicting include_only passed"
	expect_contains "$output" "CONTEXT7_API_KEY"
	write_config codex '[shell_environment_policy]
include_only = ["CONTEXT[7]_API_KEY"]'
	run_sync --check
	[ "$code" -ne 0 ] || fail "a bracket expression was treated as a Codex wildcard"
	expect_contains "$output" "CONTEXT7_API_KEY"
	write_config codex '[shell_environment_policy]
exclude = ["*KEY*"]'
	run_sync
	[ "$code" -ne 0 ] || fail "a conflicting exclusion passed"
	expect_contains "$output" "CONTEXT7_API_KEY"
	write_config codex '[shell_environment_policy]
inherit = "none"'
	run_sync
	[ "$code" -ne 0 ] || fail "inherit none passed"
	expect_contains "$output" "CONTEXT7_API_KEY"
	write_config codex '[shell_environment_policy.filters]
"*KEY*" = "exclude"'
	run_sync
	[ "$code" -ne 0 ] || fail "a conflicting keyed exclusion passed"
	expect_contains "$output" "CONTEXT7_API_KEY"
	write_config codex '[shell_environment_policy.filters]
PATH = "include"'
	run_sync
	[ "$code" -ne 0 ] || fail "a conflicting keyed include passed"
	expect_contains "$output" "CONTEXT7_API_KEY"
	write_config codex '[shell_environment_policy]
include_only = ["context7_api_key"]'
	run_sync
	[ "$code" -eq 0 ] || fail "a case-insensitive Codex match was rejected: $output"
	printf 'ok: conflicting Codex policies name blocked registry variables\n'
}

case_env_check() {
	setup_case env-check
	make_stubs
	write_config claude '{}'
	export LAUNCHCTL_VALUE=value
	run_sync --env --check
	[ "$code" -eq 0 ] || fail "the env check failed with launchd set: $output"
	expect_contains "$output" "launchd has every secret"
	LAUNCHCTL_VALUE='' run_sync --env --check
	[ "$code" -ne 0 ] || fail "the env check passed with launchd unset"
	expect_contains "$output" "CONTEXT7_API_KEY"
	printf 'ok: the env check reports a missing launchd variable\n'
}

case_literal_header() {
	setup_case literal-header
	make_stubs
	write_config claude '{}'
	registry=$case_dir/registry.json
	printf '{"servers":{"bad":{"transport":"http","url":"https://example.invalid/mcp","headers":{"Authorization":"Bearer literal-value"},"targets":["claude-code"]}}}\n' >"$registry"
	run_sync --check --registry "$registry"
	[ "$code" -ne 0 ] || fail "a literal header was accepted"
	expect_contains "$output" "must be an object with env and reference"
	printf 'ok: a literal registry header is refused\n'
}

case_no_references() {
	setup_case no-references
	registry=$case_dir/registry.json
	printf '{"servers":{"local":{"transport":"stdio","command":"cua","targets":["claude-code"]}}}\n' >"$registry"
	run_sync --env --registry "$registry"
	[ "$code" -eq 0 ] || fail "the env mode failed without secret references: $output"
	expect_contains "$output" "nothing to set"
	printf 'ok: a registry without secret references is a no-op\n'
}

case_malformed() {
	setup_case malformed
	make_stubs
	write_config claude 'not json'
	run_sync --check
	[ "$code" -ne 0 ] || fail "the check passed on a malformed config"
	expect_contains "$output" "$home/.claude.json"
	expect_not_contains "$output" "Traceback"
	write_config claude 'null'
	run_sync --check
	[ "$code" -ne 0 ] || fail "the check passed on a null config"
	expect_contains "$output" "$home/.claude.json"
	expect_contains "$output" "expected a JSON object"
	expect_not_contains "$output" "Traceback"
	write_config claude '{"mcpServers": []}'
	run_sync --check
	[ "$code" -ne 0 ] || fail "the check passed on a list-valued mcpServers"
	expect_contains "$output" "$home/.claude.json"
	expect_contains "$output" "must be an object"
	expect_not_contains "$output" "Traceback"
	write_config claude '{"mcpServers": null}'
	run_sync --check
	[ "$code" -ne 0 ] || fail "the check passed on a null mcpServers"
	expect_contains "$output" "$home/.claude.json: mcpServers must be an object"
	expect_not_contains "$output" "Traceback"
	printf 'not json\n' >"$case_dir/registry.json"
	run_sync --check --registry "$case_dir/registry.json"
	[ "$code" -ne 0 ] || fail "the check passed on a malformed registry"
	expect_contains "$output" "$case_dir/registry.json"
	expect_not_contains "$output" "Traceback"
	printf 'ok: malformed configs and registries are named errors, not tracebacks\n'
}

case_all() {
	case_fresh
	case_drift
	case_retired
	case_absent
	case_preserve
	case_backup
	case_env
	case_env_fail
	case_reference_check
	case_policy_conflict
	case_env_check
	case_literal_header
	case_no_references
	case_malformed
}

case $case_name in
fresh) case_fresh ;;
drift) case_drift ;;
retired) case_retired ;;
absent) case_absent ;;
preserve) case_preserve ;;
backup) case_backup ;;
env) case_env ;;
env-fail) case_env_fail ;;
env-check) case_env_check ;;
literal-header) case_literal_header ;;
no-references) case_no_references ;;
malformed) case_malformed ;;
all) case_all ;;
*)
	printf 'unknown case: %s\n' "$case_name" >&2
	exit 2
	;;
esac
