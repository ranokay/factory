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
	: >"$log"
	export STUB_LOG="$log"
	PATH="$bin:$base_path"
}

run_sync() {
	run_command uv run --offline --no-project --no-config --python "$(mise which python)" python "$root/scripts/mcp-sync.py" --home "$home" "$@"
}

make_stubs() {
	mkdir -p "$bin"
	cat >"$bin/op" <<'STUB'
#!/bin/sh
set -eu
printf 'op %s\n' "$*" >>"$STUB_LOG"
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
	chmod +x "$bin/op" "$bin/launchctl"
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
	run_sync
	[ "$code" -eq 0 ] || fail "render failed without configs: $output"
	[ ! -e "$home/.claude.json" ] || fail "the render created a missing Claude config"
	[ ! -e "$home/.config/opencode/opencode.json" ] || fail "the render created a missing OpenCode config"
	[ ! -e "$home/.codex/config.toml" ] || fail "the render created a missing Codex config"
	run_sync --check
	[ "$code" -eq 0 ] || fail "the check failed without configs: $output"
	printf 'ok: an absent harness config is left absent\n'
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
	[ "$(stat -f '%Lp' "$home/.claude.json" 2>/dev/null || stat -c '%a' "$home/.claude.json")" = 600 ] || fail "the render changed the Claude config mode"
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
	grep -q 'op read --no-newline op://Private/Context7 API/credential' "$log" || fail "op was not asked for the registry reference"
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

case_all() {
	case_fresh
	case_drift
	case_retired
	case_absent
	case_preserve
	case_backup
	case_env
	case_env_fail
	case_env_check
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
all) case_all ;;
*)
	printf 'unknown case: %s\n' "$case_name" >&2
	exit 2
	;;
esac
