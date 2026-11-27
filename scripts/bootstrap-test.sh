#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
base_path=$PATH
REAL_GIT=$(command -v git)
export REAL_GIT
case_name=${BOOTSTRAP_CASE:-all}
tilde='~'

test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
	printf 'bootstrap-test: %s\n' "$*" >&2
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

copy_factory() {
	target=$1
	mkdir -p "$target"
	(cd "$root" && tar --exclude ./.git -cf - .) | (cd "$target" && tar -xf -)
	while IFS= read -r name; do
		case $name in
		'' | '['*) continue ;;
		esac
		rm -rf "$target/skills/$name"
	done <"$root/scripts/tool-skills.txt"
}

init_git() {
	git -C "$1" init -q
	git -C "$1" config user.name "Bootstrap test"
	git -C "$1" config user.email "bootstrap-test@example.invalid"
	git -C "$1" config commit.gpgsign false
}

baseline_repo() {
	init_git "$1"
	git -C "$1" add -A
	git -C "$1" commit -qm "baseline"
}

make_stubs() {
	bin=$1
	mkdir -p "$bin"
	cat >"$bin/git" <<'STUB'
#!/bin/sh
if [ "${1:-}" = -C ] && [ "${3:-}" = push ]; then
	[ "${GIT_TERMINAL_PROMPT:-}" = 0 ] && [ "${GIT_ASKPASS:-}" = /usr/bin/false ] && [ -z "${SSH_AUTH_SOCK:-}" ] || exit 1
	if [ -n "${FIXTURE_PUSH_REMOTE:-}" ]; then
		exec "$REAL_GIT" -C "$2" push --dry-run "$FIXTURE_PUSH_REMOTE" "$6"
	fi
	exit 0
fi
exec "$REAL_GIT" "$@"
STUB
	chmod +x "$bin/git"

	cat >"$bin/ardia" <<'STUB'
#!/bin/sh
set -eu
printf 'ardia %s\n' "$*" >>"$STUB_LOG"
mkdir -p "$HOME/.agents/skills/ardia"
printf '# ardia\n' >"$HOME/.agents/skills/ardia/SKILL.md"
STUB

	cat >"$bin/cua" <<'STUB'
#!/bin/sh
set -eu
printf 'cua %s\n' "$*" >>"$STUB_LOG"
for name in cua-driver cua-sandboxes cua-spaces cua-volume gui-automation supercomputer; do
	mkdir -p "$HOME/.claude/skills/$name"
	printf '# %s\n' "$name" >"$HOME/.claude/skills/$name/SKILL.md"
done
STUB

	cat >"$bin/ctx7" <<'STUB'
#!/bin/sh
set -eu
printf 'ctx7 %s\n' "$*" >>"$STUB_LOG"
mkdir -p "$HOME/.claude/skills/find-docs"
printf '# find-docs\n' >"$HOME/.claude/skills/find-docs/SKILL.md"
STUB

	cat >"$bin/npx" <<'STUB'
#!/bin/sh
set -eu
printf 'npx %s\n' "$*" >>"$STUB_LOG"
mkdir -p "$HOME/.claude/skills/find-docs"
printf '# find-docs\n' >"$HOME/.claude/skills/find-docs/SKILL.md"
STUB

	cat >"$bin/rea" <<'STUB'
#!/bin/sh
set -eu
printf 'rea %s\n' "$*" >>"$STUB_LOG"
mkdir -p "$HOME/.agents/skills/reverse-engineer-anything"
printf '# rea\n' >"$HOME/.agents/skills/reverse-engineer-anything/SKILL.md"
STUB

	cat >"$bin/gh" <<'STUB'
#!/bin/sh
set -eu
printf 'gh %s\n' "$*" >>"$STUB_LOG"
dir=
while [ "$#" -gt 0 ]; do
	case $1 in
	--dir)
		dir=$2
		shift 2
		;;
	*) shift ;;
	esac
done
[ -n "$dir" ] || exit 2
mkdir -p "$dir/gh-stack"
printf '# gh-stack\n' >"$dir/gh-stack/SKILL.md"
STUB

	cat >"$bin/op" <<'STUB'
#!/bin/sh
set -eu
printf 'op %s\n' "$*" >>"$STUB_LOG"
case "$*" in
*'read --no-newline'*'Service Account Auth Token'*)
	printf 'bootstrap-test: op rejects a title in a secret reference\n' >&2
	exit 1
	;;
esac
if [ "$1 $2" = "item list" ]; then
	printf '[{"id":"fixture-token","title":"Service Account Auth Token: Factory"}]\n'
else
	printf 'dummy-value'
fi
STUB
	cat >"$bin/security" <<'STUB'
#!/bin/sh
if [ "$1" = -i ]; then cat >/dev/null; exit 0; fi
printf 'dummy-value'
STUB

	cat >"$bin/claude" <<'STUB'
#!/bin/sh
set -eu
[ "$1" = "--version" ] || exit 9
printf '%s\n' "${CLAUDE_VERSION:-2.1.332 (Claude Code)}"
STUB

	cat >"$bin/launchctl" <<'STUB'
#!/bin/sh
set -eu
printf 'launchctl %s\n' "$*" >>"$STUB_LOG"
if [ "$1" = getenv ]; then
	if [ -n "${LAUNCHCTL_VALUE:-}" ]; then
		printf '%s\n' "$LAUNCHCTL_VALUE"
		exit 0
	fi
	exit 1
fi
exit 0
STUB

	chmod +x "$bin/ardia" "$bin/cua" "$bin/claude" "$bin/ctx7" "$bin/npx" "$bin/rea" "$bin/gh" "$bin/op" "$bin/launchctl" "$bin/security"
}

setup_case() {
	case_dir=$test_root/$1
	factory=$case_dir/factory
	home=$case_dir/home
	bin=$case_dir/bin
	log=$case_dir/installers.log
	copy_factory "$factory"
	mkdir -p "$home" "$bin"
	factory=$(CDPATH='' cd "$factory" && pwd -P)
	home=$(CDPATH='' cd "$home" && pwd -P)
	export XDG_CONFIG_HOME="$home/.config"
	export CODEX_HOME="$home/.codex"
	make_stubs "$bin"
	: >"$log"
	export STUB_LOG="$log"
	PATH="$bin:$base_path"
}

run_bootstrap() {
	run_command mise --cd "$factory" run bootstrap -- --home "$home" "$@"
}

run_discovery() {
	run_command mise --cd "$factory" run bootstrap:check -- --home "$home"
}

run_check_tool_skills() {
	run_command mise --cd "$root" run bootstrap -- --check-tool-skills --root "$1"
}

claude_expected() {
	printf 'Bearer $%s' '{CONTEXT7_API_KEY}'
}

run_python() {
	run_command uv run --offline --no-project --no-config --python "$(mise which python)" python "$@"
}

file_mode() {
	run_python -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[2:])' "$1"
	[ "$code" -eq 0 ] || fail "cannot read the mode of $1: $output"
	printf '%s\n' "$output"
}

run_hook() {
	payload=$1
	tool=$2
	shift 2
	if output=$(printf '%s' "$payload" | "$home/.agents/tools/run" "$tool" "$@" 2>&1); then
		code=0
	else
		code=$?
	fi
}

count_lines() {
	grep -c "$1" "$2" || true
}

count_calls() {
	grep -Fxc "op $1" "$log" || true
}

json_value() {
	file=$1
	path=$2
	run_python -c 'import functools,json,sys; print(functools.reduce(lambda d,p: d[int(p)] if p.isdigit() else d[p], sys.argv[2].split("."), json.load(open(sys.argv[1]))))' "$file" "$path"
	[ "$code" -eq 0 ] || fail "cannot read $path from $file: $output"
	printf '%s\n' "$output"
}

plist_value() {
	file=$1
	path=$2
	run_python -c 'import functools,plistlib,sys; print(functools.reduce(lambda d,p: d[int(p)] if p.isdigit() else d[p], sys.argv[2].split("."), plistlib.load(open(sys.argv[1], "rb"))))' "$file" "$path"
	[ "$code" -eq 0 ] || fail "cannot read $path from $file: $output"
	printf '%s\n' "$output"
}

run_mcp_check() {
	run_python "$factory/scripts/mcp-sync.py" --home "$home" --check
}

snapshot() {
	{
		(cd "$home" && find . -maxdepth 3 -print | LC_ALL=C sort)
		find "$home" -maxdepth 3 -type l -print | LC_ALL=C sort | while IFS= read -r link; do
			printf '%s -> %s\n' "$link" "$(readlink "$link")"
		done
	}
}

case_fresh() {
	setup_case fresh
	baseline_repo "$factory"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "fresh bootstrap failed: $output"
	bootstrap_output=$output
	run_command git -C "$factory" status --porcelain
	[ -z "$output" ] || fail "bootstrap left untracked tool-owned skills: $output"
	output=$bootstrap_output
	expect_contains "$output" "tool-owned skills are installed"
	expect_contains "$output" "vendored skills check passed"
	expect_contains "$output" "discovery check passed"
	[ -L "$home/.agents" ] || fail "$tilde/.agents is not a symlink"
	[ "$(readlink "$home/.agents")" = "$factory" ] || fail "$tilde/.agents points at $(readlink "$home/.agents")"
	for path in "$home/.claude/skills" "$home/.config/opencode/skills"; do
		[ -L "$path" ] || fail "$path is not a symlink"
		[ "$(readlink "$path")" = "$factory/skills" ] || fail "$path points at $(readlink "$path")"
	done
	for path in "$home/.claude/CLAUDE.md" "$home/.codex/AGENTS.md" "$home/.config/opencode/AGENTS.md"; do
		[ -L "$path" ] || fail "$path is not a symlink"
		[ "$(readlink "$path")" = "$home/.agents/AGENTS.md" ] || fail "$path points at $(readlink "$path")"
		[ "$(head -n 1 "$path")" = "# Factory" ] || fail "$path does not show the Factory instructions"
	done
	for name in ardia cua-driver cua-sandboxes cua-spaces cua-volume gui-automation supercomputer find-docs reverse-engineer-anything gh-stack; do
		[ -f "$factory/skills/$name/SKILL.md" ] || fail "installer did not create skills/$name"
	done
	for name in find-skills emil-prototype playwright-cli prototype; do
		[ -f "$factory/skills/$name/SKILL.md" ] || fail "vendored skill skills/$name is missing"
	done
	[ "$(sed -n '2p' "$factory/skills/emil-prototype/SKILL.md")" = "name: emil-prototype" ] || fail "the emil-prototype rename patch did not apply"
	[ "$(count_lines '^ardia ' "$log")" -eq 1 ] || fail "ardia installer did not run once"
	[ "$(count_lines '^cua ' "$log")" -eq 1 ] || fail "cua installer did not run once"
	[ "$(count_lines '^ctx7 ' "$log")" -eq 1 ] || fail "ctx7 installer did not run once"
	[ "$(count_lines '^rea ' "$log")" -eq 1 ] || fail "rea installer did not run once"
	[ "$(count_lines '^gh ' "$log")" -eq 1 ] || fail "gh-stack installer did not run once"
	[ "$(count_lines '^npx ' "$log")" -eq 0 ] || fail "the pinned ctx7 fallback ran instead of ctx7"
	printf 'ok: a fresh temp HOME gets the Factory links\n'
}

case_second() {
	setup_case second
	run_bootstrap
	[ "$code" -eq 0 ] || fail "first bootstrap failed: $output"
	before=$(snapshot)
	installers_before=$(grep -v '^op ' "$log" || true)
	run_bootstrap
	[ "$code" -eq 0 ] || fail "second bootstrap failed: $output"
	expect_contains "$output" "Already bootstrapped: no changes."
	[ "$(snapshot)" = "$before" ] || fail "the second run changed the tree or symlink targets"
	[ "$(grep -v '^op ' "$log" || true)" = "$installers_before" ] || fail "the second run invoked an installer again"
	printf 'ok: the second run changes nothing and says so\n'
}

case_broken() {
	setup_case broken
	run_bootstrap
	[ "$code" -eq 0 ] || fail "first bootstrap failed: $output"
	rm "$home/.claude/CLAUDE.md"
	run_discovery
	[ "$code" -ne 0 ] || fail "discovery passed without ~/.claude/CLAUDE.md"
	expect_contains "$output" "claude-code"
	expect_contains "$output" "$tilde/.claude/CLAUDE.md"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap did not repair a managed link: $output"
	mkdir -p "$home/.codex/skills"
	ln -s "$home/missing-target" "$home/.codex/skills/rogue"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap passed with a broken Codex link"
	expect_contains "$output" "codex"
	expect_contains "$output" "$tilde/.codex/skills/rogue"
	rm "$home/.codex/skills/rogue"
	rm -rf "$home/.claude/skills"
	mkdir -p "$home/.claude/skills/rogue"
	printf '# rogue\n' >"$home/.claude/skills/rogue/SKILL.md"
	run_discovery
	[ "$code" -ne 0 ] || fail "discovery passed with a real skills directory"
	expect_contains "$output" "claude-code"
	expect_contains "$output" "$tilde/.claude/skills"
	rm -rf "$home/.claude/skills"
	printf 'not a directory\n' >"$home/.claude/skills"
	run_discovery
	[ "$code" -ne 0 ] || fail "discovery passed with a skills file"
	expect_contains "$output" "claude-code"
	expect_contains "$output" "$tilde/.claude/skills"
	expect_not_contains "$output" "Traceback"
	printf 'ok: a broken link names the harness and the path\n'
}

case_refuse() {
	setup_case refuse
	mkdir -p "$home/.agents" "$home/.claude/skills"
	printf 'keep\n' >"$home/.agents/keep.txt"
	printf 'keep\n' >"$home/.claude/skills/keep.txt"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap replaced real directories without approval"
	expect_contains "$output" "refusing to replace these managed paths"
	expect_contains "$output" "$tilde/.agents"
	expect_contains "$output" "$tilde/.claude/skills"
	expect_contains "$output" "mise run bootstrap -- --adopt-existing"
	[ -f "$home/.agents/keep.txt" ] || fail "refusal removed ~/.agents/keep.txt"
	[ ! -L "$home/.agents" ] || fail "refusal replaced ~/.agents"
	[ -f "$home/.claude/skills/keep.txt" ] || fail "refusal removed ~/.claude/skills/keep.txt"
	[ ! -L "$home/.claude/skills" ] || fail "refusal replaced ~/.claude/skills"
	[ ! -e "$home/.claude/CLAUDE.md" ] || fail "refusal wrote a link before approval"
	run_bootstrap --adopt-existing
	[ "$code" -eq 0 ] || fail "approved bootstrap failed: $output"
	[ -L "$home/.agents" ] || fail "$tilde/.agents is not a symlink after approval"
	[ -L "$home/.claude/skills" ] || fail "$tilde/.claude/skills is not a symlink after approval"
	agents_backup=$(find "$home" -maxdepth 1 -type d -name '.agents.backup-*' | head -n 1)
	[ -n "$agents_backup" ] || fail "no dated backup for ~/.agents"
	[ -f "$agents_backup/keep.txt" ] || fail "the ~/.agents backup lost keep.txt"
	skills_backup=$(find "$home/.claude" -maxdepth 1 -type d -name 'skills.backup-*' | head -n 1)
	[ -n "$skills_backup" ] || fail "no dated backup for ~/.claude/skills"
	[ -f "$skills_backup/keep.txt" ] || fail "the ~/.claude/skills backup lost keep.txt"
	printf 'ok: real directories are refused, then backed up with approval\n'
}

case_external() {
	setup_case external
	mkdir -p "$home/old-agents" "$home/old-skills" "$home/.claude"
	printf 'old\n' >"$home/old-agents/AGENTS.md"
	printf 'old\n' >"$home/old-skills/SKILL.md"
	ln -s "$home/old-agents" "$home/.agents"
	ln -s "$home/old-skills" "$home/.claude/skills"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap repointed external links without approval"
	expect_contains "$output" "refusing to replace these managed paths"
	expect_contains "$output" "$tilde/.agents"
	expect_contains "$output" "$tilde/.claude/skills"
	expect_contains "$output" "mise run bootstrap -- --adopt-existing"
	[ "$(readlink "$home/.agents")" = "$home/old-agents" ] || fail "refusal moved ~/.agents"
	[ "$(readlink "$home/.claude/skills")" = "$home/old-skills" ] || fail "refusal moved ~/.claude/skills"
	[ ! -e "$home/.claude/CLAUDE.md" ] || fail "refusal wrote a link before approval"
	run_bootstrap --adopt-existing
	[ "$code" -eq 0 ] || fail "approved bootstrap failed: $output"
	[ "$(readlink "$home/.agents")" = "$factory" ] || fail "$tilde/.agents does not point at the Factory"
	[ "$(readlink "$home/.claude/skills")" = "$factory/skills" ] || fail "$tilde/.claude/skills does not point at the Factory"
	agents_backup=$(find "$home" -maxdepth 1 -name '.agents.backup-*' | head -n 1)
	[ -L "$agents_backup" ] || fail "no symlink backup for ~/.agents"
	[ "$(readlink "$agents_backup")" = "$home/old-agents" ] || fail "the ~/.agents backup lost its target"
	skills_backup=$(find "$home/.claude" -maxdepth 1 -name 'skills.backup-*' | head -n 1)
	[ -L "$skills_backup" ] || fail "no symlink backup for ~/.claude/skills"
	[ "$(readlink "$skills_backup")" = "$home/old-skills" ] || fail "the skills backup lost its target"
	[ -f "$home/old-agents/AGENTS.md" ] || fail "the old ~/.agents target was removed"
	[ -f "$home/old-skills/SKILL.md" ] || fail "the old skills target was removed"
	printf 'ok: external symlinks need approval and are backed up\n'
}

case_worktree() {
	setup_case worktree
	baseline_repo "$factory"
	worktree_dir=$case_dir/worktree
	git -C "$factory" worktree add -q "$worktree_dir"
	fake_home=$case_dir/fake-home
	mkdir -p "$fake_home"
	uv_bin=$(mise which uv)
	python_bin=$(mise which python)
	run_command env HOME="$fake_home" UV_CACHE_DIR="$HOME/.cache/uv" "$uv_bin" run --offline --no-project --no-config --python "$python_bin" python "$worktree_dir/scripts/bootstrap.py" --home "$fake_home"
	[ "$code" -ne 0 ] || fail "bootstrap ran from a linked worktree against the real home"
	expect_contains "$output" "linked git worktree"
	expect_contains "$output" "$worktree_dir"
	expect_contains "$output" "main checkout"
	expect_contains "$output" "$factory"
	[ ! -e "$fake_home/.agents" ] || fail "the worktree refusal wrote a link"
	printf 'ok: linked worktrees are refused for the real home\n'
}

case_sync() {
	setup_case sync
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	before=$(cat "$factory/skills/ardia/SKILL.md")
	run_command mise --cd "$factory" run skills:sync --check
	[ "$code" -eq 0 ] || fail "skills:sync --check failed with tool-owned skills present: $output"
	expect_contains "$output" "check passed"
	[ "$(cat "$factory/skills/ardia/SKILL.md")" = "$before" ] || fail "skills:sync rewrote a tool-owned skill"
	[ -f "$factory/skills/find-docs/SKILL.md" ] || fail "skills:sync removed a tool-owned skill"
	printf 'ok: skills:sync --check ignores tool-owned skill directories\n'
}

case_codex() {
	setup_case codex
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	mkdir -p "$home/.codex/skills/.system/imagegen" "$home/.codex/skills/codex-primary-runtime"
	printf '# system\n' >"$home/.codex/skills/.system/imagegen/SKILL.md"
	ln -s "$home/.agents/skills/babysit-pr" "$home/.codex/skills/babysit-pr"
	run_discovery
	[ "$code" -eq 0 ] || fail "managed and Factory-linked Codex entries failed: $output"
	mkdir -p "$home/.codex/skills/gh-stack"
	printf '# gh-stack\n' >"$home/.codex/skills/gh-stack/SKILL.md"
	run_discovery
	[ "$code" -ne 0 ] || fail "discovery passed with an extra Codex skill"
	expect_contains "$output" "codex"
	expect_contains "$output" "$tilde/.codex/skills/gh-stack"
	expect_not_contains "$output" ".system"
	expect_not_contains "$output" "codex-primary-runtime"
	expect_not_contains "$output" "babysit-pr"
	mkdir -p "$home/.codex/skills/emilkowalski-skills/skills/animate"
	printf '# animate\n' >"$home/.codex/skills/emilkowalski-skills/skills/animate/SKILL.md"
	run_discovery
	expect_contains "$output" "$tilde/.codex/skills/emilkowalski-skills"
	expect_not_contains "$output" "codex-primary-runtime"
	printf 'ok: Codex extras are named and managed entries are ignored\n'
}

case_env() {
	setup_case env
	xdg=$case_dir/xdg
	codex=$case_dir/codex
	mkdir -p "$xdg" "$codex"
	run_command env XDG_CONFIG_HOME="$xdg" CODEX_HOME="$codex" mise --cd "$factory" run bootstrap -- --home "$home"
	[ "$code" -eq 0 ] || fail "bootstrap with XDG_CONFIG_HOME and CODEX_HOME failed: $output"
	[ -L "$xdg/opencode/skills" ] || fail "XDG_CONFIG_HOME/opencode/skills is not a symlink"
	[ "$(readlink "$xdg/opencode/skills")" = "$factory/skills" ] || fail "the OpenCode skills link is wrong"
	[ -L "$xdg/opencode/AGENTS.md" ] || fail "XDG_CONFIG_HOME/opencode/AGENTS.md is not a symlink"
	[ -L "$codex/AGENTS.md" ] || fail "CODEX_HOME/AGENTS.md is not a symlink"
	[ "$(readlink "$codex/AGENTS.md")" = "$home/.agents/AGENTS.md" ] || fail "the Codex instructions link is wrong"
	[ -f "$xdg/opencode/opencode.json" ] || fail "the relocated OpenCode config was not created"
	[ -f "$codex/config.toml" ] || fail "the relocated Codex config was not created"
	[ "$(json_value "$xdg/opencode/opencode.json" 'mcp.context7.headers.Authorization')" = 'Bearer {env:CONTEXT7_API_KEY}' ] || fail "the relocated OpenCode config has no context7 env reference"
	grep -q '^\[mcp_servers.context7\]$' "$codex/config.toml" || fail "the relocated Codex config has no context7 table"
	run_command env XDG_CONFIG_HOME="$xdg" CODEX_HOME="$codex" mise --cd "$factory" run bootstrap:check -- --home "$home"
	[ "$code" -eq 0 ] || fail "discovery with XDG_CONFIG_HOME and CODEX_HOME failed: $output"
	printf 'ok: XDG_CONFIG_HOME and CODEX_HOME are honoured\n'
}

case_hooks() {
	setup_case hooks
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	run_hook '{"tool_name":"Bash","tool_input":{"command":"echo hi","shell":"/bin/zsh"}}' command_guard
	[ "$code" -eq 0 ] || fail "command_guard failed through ~/.agents: $output"
	run_hook '{"tool_name":"Bash","tool_input":{"command":"grep -R foo . --include=*.py | head","shell":"/bin/zsh"}}' command_guard
	[ "$code" -eq 0 ] || fail "command_guard denied a payload through ~/.agents: $output"
	expect_contains "$output" '"permissionDecision": "deny"'
	run_hook "{\"cwd\":\"$home\"}" environment_check startup
	[ "$code" -eq 0 ] || fail "environment_check failed through ~/.agents: $output"
	expect_contains "$output" "Checkout preflight"
	printf 'ok: the hook helpers still resolve and run\n'
}

case_secret_guard() {
	setup_case secret-guard
	mkdir -p "$home/.config/opencode/plugins"
	printf 'keep rtk\n' >"$home/.config/opencode/plugins/rtk.ts"
	run_python - "$home" <<'PY'
import json
import sys
from pathlib import Path

home = Path(sys.argv[1])
runner = f"{home}/.agents/tools/run"
sonar = f"{home}/.claude/hooks/sonar-secrets/build-scripts"
settings = {
	"theme": "dark",
	"hooks": {
		"PreToolUse": [
			{"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]},
			{
				"matcher": "Bash",
				"hooks": [{"type": "command", "command": f"{runner} command_guard", "timeout": 5}],
			},
			{
				"matcher": "Read",
				"hooks": [{"type": "command", "command": f"'{sonar}/pretool-secrets.sh'", "timeout": 60}],
			},
			{
				"matcher": "Bash",
				"hooks": [{"type": "command", "command": f"{runner} secret_guard tool", "timeout": 1}],
			},
		],
		"SessionStart": [
			{"hooks": [{"type": "command", "command": f"{runner} environment_check startup", "timeout": 10}]}
		],
		"UserPromptSubmit": [
			{
				"matcher": "*",
				"hooks": [{"type": "command", "command": f"'{sonar}/prompt-secrets.sh'", "timeout": 60}],
			},
			{"hooks": [{"type": "command", "command": f"{runner} secret_guard prompt", "timeout": 1}]},
		],
	},
}
path = home / ".claude" / "settings.json"
path.parent.mkdir(parents=True)
path.write_text(json.dumps(settings, indent=2) + "\n")
PY
	[ "$code" -eq 0 ] || fail "cannot seed Claude settings: $output"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	settings=$home/.claude/settings.json
	plugin=$home/.config/opencode/plugins/factory-secret-guard.ts
	[ -L "$plugin" ] || fail "bootstrap did not install the OpenCode secret guard plugin"
	[ "$(readlink "$plugin")" = "$home/.agents/tools/opencode/secret-guard.ts" ] || fail "the plugin symlink points at the wrong guard"
	[ "$(cat "$home/.config/opencode/plugins/rtk.ts")" = 'keep rtk' ] || fail "bootstrap changed the existing rtk plugin"
	run_python - "$settings" "$home" <<'PY'
import json
import sys
from pathlib import Path

home = Path(sys.argv[2])
runner = f"{home}/.agents/tools/run"
sonar = f"{home}/.claude/hooks/sonar-secrets/build-scripts"
settings = json.loads(Path(sys.argv[1]).read_text())
assert settings["theme"] == "dark"
pre = settings["hooks"]["PreToolUse"]
assert pre[0] == {"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}
assert pre[1]["hooks"] == [{"type": "command", "command": f"{runner} command_guard", "timeout": 5}]
assert pre[2]["hooks"] == [
	{"type": "command", "command": f"'{sonar}/pretool-secrets.sh'", "timeout": 60}
]
assert settings["hooks"]["SessionStart"][0]["hooks"] == [
	{"type": "command", "command": f"{runner} environment_check startup", "timeout": 10}
]
assert settings["hooks"]["UserPromptSubmit"][0]["hooks"] == [
	{"type": "command", "command": f"'{sonar}/prompt-secrets.sh'", "timeout": 60}
]
for event, mode in (("UserPromptSubmit", "prompt"), ("PreToolUse", "tool")):
	groups = [
		group
		for group in settings["hooks"][event]
		if any(hook.get("command") == f"{runner} secret_guard {mode}" for hook in group.get("hooks", []))
	]
	assert len(groups) == 1, (event, groups)
	assert groups[0]["matcher"] == "*"
	assert groups[0]["hooks"] == [
		{
			"type": "command",
			"command": f"{runner} secret_guard {mode}",
			"timeout": 15,
			"onFailure": "block",
		}
	]
PY
	[ "$code" -eq 0 ] || fail "Claude hooks were not merged correctly: $output"
	run_python - "$settings" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(json.dumps(json.loads(path.read_text()), separators=(",", ":")))
PY
	[ "$code" -eq 0 ] || fail "cannot reformat Claude settings: $output"
	before=$(cksum "$settings")
	run_discovery
	[ "$code" -eq 0 ] || fail "discovery rejected semantically current secret guards: $output"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "second bootstrap failed: $output"
	expect_contains "$output" "Already bootstrapped: no changes."
	[ "$(cksum "$settings")" = "$before" ] || fail "the second bootstrap rewrote Claude settings"
	[ "$(readlink "$plugin")" = "$home/.agents/tools/opencode/secret-guard.ts" ] || fail "the second bootstrap changed the plugin link"
	run_python - "$settings" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
settings = json.loads(path.read_text())
settings["hooks"]["UserPromptSubmit"] = []
path.write_text(json.dumps(settings, indent=2) + "\n")
PY
	[ "$code" -eq 0 ] || fail "cannot remove the prompt guard fixture: $output"
	run_discovery
	[ "$code" -ne 0 ] || fail "discovery passed without the prompt guard"
	expect_contains "$output" "Factory secret guard"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap did not repair the prompt guard: $output"
	run_discovery
	[ "$code" -eq 0 ] || fail "discovery failed after the guard repair: $output"
	printf 'not json\n' >"$settings"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap overwrote malformed Claude settings"
	expect_contains "$output" "cannot install the Factory secret guard"
	[ "$(cat "$settings")" = 'not json' ] || fail "bootstrap changed malformed Claude settings"
	printf 'ok: bootstrap installs both secret guards once and preserves unrelated hooks\n'
}

case_secret_guard_refusal() {
	setup_case secret-guard-refusal
	mkdir -p "$home/.claude"
	printf 'not json\n' >"$home/.claude/settings.json"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap overwrote malformed Claude settings"
	expect_contains "$output" "cannot install the Factory secret guard"
	[ "$(cat "$home/.claude/settings.json")" = 'not json' ] || fail "bootstrap changed malformed Claude settings"
	[ ! -e "$home/.agents" ] || fail "bootstrap wrote links before installing the Claude hooks"
	[ ! -e "$home/.config/opencode/plugins/factory-secret-guard.ts" ] || fail "bootstrap linked the plugin without Claude hooks"
	printf 'ok: bootstrap refuses malformed Claude settings before writing links\n'
}

case_secret_guard_disabled() {
	setup_case secret-guard-disabled
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed before disabling hooks: $output"
	settings=$home/.claude/settings.json
	run_python - "$settings" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
settings = json.loads(path.read_text())
settings["disableAllHooks"] = True
path.write_text(json.dumps(settings, indent=2) + "\n")
PY
	[ "$code" -eq 0 ] || fail "cannot disable hooks in the fixture: $output"
	cp "$settings" "$settings.before"
	run_discovery
	[ "$code" -ne 0 ] || fail "discovery accepted disabled hooks"
	expect_contains "$output" "disableAllHooks"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap installed guards while all hooks were disabled"
	expect_contains "$output" "disableAllHooks"
	cmp -s "$settings" "$settings.before" || fail "bootstrap changed disabled hook settings"
	printf 'ok: bootstrap rejects settings that disable all hooks\n'
}

case_secret_guard_version() {
	setup_case secret-guard-version
	export CLAUDE_VERSION='2.1.294 (Claude Code)'
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap accepted an old Claude Code"
	expect_contains "$output" "2.1.295"
	[ ! -e "$home/.agents" ] || fail "bootstrap wrote links before checking Claude Code"
	export CLAUDE_VERSION='unknown'
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap accepted an unreadable Claude Code version"
	expect_contains "$output" "2.1.295"
	unset CLAUDE_VERSION
	rm "$bin/claude"
	no_claude_path=$bin
	mirrors=0
	for dir in $(printf '%s' "$base_path" | tr ':' ' '); do
		if [ -e "$dir/claude" ]; then
			mirrors=$((mirrors + 1))
			mirror=$case_dir/path-without-claude-$mirrors
			mkdir -p "$mirror"
			for entry in "$dir"/*; do
				[ "${entry##*/}" = claude ] || ln -s "$entry" "$mirror/"
			done
			dir=$mirror
		fi
		no_claude_path="$no_claude_path:$dir"
	done
	PATH=$no_claude_path
	run_bootstrap
	PATH="$bin:$base_path"
	[ "$code" -eq 0 ] || fail "bootstrap failed without Claude Code: $output"
	expect_contains "$output" "Claude Code not found"
	grep -q 'secret_guard prompt' "$home/.claude/settings.json" || fail "bootstrap skipped the Claude hooks without Claude Code"
	printf 'ok: bootstrap requires Claude Code v2.1.295 or later when it is installed\n'
}

case_repair() {
	setup_case repair
	fixture_dir=$case_dir/fixture
	run_python "$root/scripts/skills-sync-fixture.py" clean "$fixture_dir"
	[ "$code" -eq 0 ] || fail "the fixture failed: $output"
	cp "$fixture_dir/skills.lock" "$factory/skills.lock"
	cp -R "$fixture_dir/skills/." "$factory/skills/"
	printf 'hand edit\n' >>"$factory/skills/alpha/SKILL.md"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap repaired a drifted vendored skill without approval"
	expect_contains "$output" "skills/alpha/SKILL.md"
	expect_contains "$output" "mise run skills:sync"
	grep -q 'hand edit' "$factory/skills/alpha/SKILL.md" || fail "bootstrap destroyed the local edit"
	[ ! -e "$home/.agents" ] || fail "bootstrap wrote links before refusing the drift"
	[ ! -e "$factory/skills/ardia/SKILL.md" ] || fail "bootstrap installed tools before refusing the drift"
	printf 'ok: bootstrap refuses a drifted vendored skill\n'
}

case_tracked() {
	fixture=$test_root/tracked
	mkdir -p "$fixture/skills/ardia"
	init_git "$fixture"
	printf '# ardia\n' >"$fixture/skills/ardia/SKILL.md"
	git -C "$fixture" add skills/ardia/SKILL.md
	git -C "$fixture" commit -qm "import ardia"
	mkdir -p "$fixture/skills/new-tool"
	printf '# new tool\n' >"$fixture/skills/new-tool/SKILL.md"
	mkdir -p "$fixture/skills/rogue"
	printf '# rogue\n' >"$fixture/skills/rogue/SKILL.md"
	printf 'skills/rogue/\n' >"$fixture/.gitignore"
	run_check_tool_skills "$fixture"
	[ "$code" -ne 0 ] || fail "the tracked tool-owned skill passed"
	expect_contains "$output" "skills/ardia/SKILL.md"
	expect_contains "$output" "skills/new-tool/SKILL.md"
	expect_contains "$output" "ignored skill is not declared tool-owned: skills/rogue"
	run_check_tool_skills "$root"
	[ "$code" -eq 0 ] || fail "the Factory failed the tool-owned skill checks: $output"
	expect_contains "$output" "tool-owned skill checks passed"
	printf 'ok: tool-owned skill tracking and ignore drift are checked\n'
}

case_synced_tracked() {
	setup_case synced-tracked
	baseline_repo "$factory"
	mkdir -p "$factory/skills/synced/bucket/nested/skill"
	printf '# nested\n' >"$factory/skills/synced/bucket/nested/skill/SKILL.md"
	git -C "$factory" add -f skills/synced/bucket/nested/skill/SKILL.md
	run_check_tool_skills "$factory"
	[ "$code" -ne 0 ] || fail "a tracked synced cache passed the check"
	expect_contains "$output" "skills/synced/bucket/nested/skill/SKILL.md"
	printf 'ok: a tracked synced skill fails the check and names the path\n'
}

case_synced_ignored() {
	setup_case synced-ignored
	baseline_repo "$factory"
	mkdir -p "$factory/skills/synced/bucket-one/alpha" "$factory/skills/synced/bucket-two/beta/references"
	printf '# alpha\n' >"$factory/skills/synced/bucket-one/alpha/SKILL.md"
	printf '# beta\n' >"$factory/skills/synced/bucket-two/beta/SKILL.md"
	printf '# cache root\n' >"$factory/skills/synced/SKILL.md"
	run_command git -C "$factory" status --porcelain
	[ -z "$output" ] || fail "a populated synced cache is not ignored: $output"
	run_check_tool_skills "$factory"
	[ "$code" -eq 0 ] || fail "an ignored synced cache failed the check: $output"
	expect_contains "$output" "tool-owned skill checks passed"
	grep -v '^skills/synced/$' "$factory/.gitignore" >"$factory/.gitignore.replaced"
	mv "$factory/.gitignore.replaced" "$factory/.gitignore"
	run_check_tool_skills "$factory"
	[ "$code" -ne 0 ] || fail "a synced cache without its ignore rule passed the check"
	expect_contains "$output" "synced skill cache is not ignored: skills/synced/bucket-one/alpha/SKILL.md"
	printf 'ok: an ignored synced cache keeps git status clean and passes the check\n'
}

case_synced_discovery() {
	setup_case synced-discovery
	baseline_repo "$factory"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	count=$(printf '%s\n' "$output" | sed -n 's/.*(\([0-9][0-9]*\) skills)/\1/p')
	[ -n "$count" ] || fail "bootstrap did not report a skill count: $output"
	mkdir -p "$factory/skills/synced/bucket/skill"
	printf '# synced\n' >"$factory/skills/synced/bucket/skill/SKILL.md"
	printf '# cache root\n' >"$factory/skills/synced/SKILL.md"
	run_command git -C "$factory" status --porcelain
	[ -z "$output" ] || fail "bootstrap plus a populated synced cache is not clean: $output"
	run_discovery
	[ "$code" -eq 0 ] || fail "discovery failed with a synced cache present: $output"
	expect_contains "$output" "discovery check passed for claude-code, opencode and codex ($count skills)"
	expect_not_contains "$output" "skills/synced"
	[ -f "$factory/skills/synced/bucket/skill/SKILL.md" ] || fail "discovery removed the synced cache"
	printf 'ok: discovery counts are unchanged with the synced cache present\n'
}

write_mcp_configs() {
	mkdir -p "$home/.config/opencode" "$home/.codex"
	printf '{"mcpServers":{"keep":{"type":"stdio","command":"keep","args":[],"env":{}}}}\n' >"$home/.claude.json"
	printf '{"mcp":{"keep":{"type":"local","command":["keep"],"enabled":true}}}\n' >"$home/.config/opencode/opencode.json"
	printf '[mcp_servers.keep]\ncommand = "keep"\n' >"$home/.codex/config.toml"
}

backup_total() {
	find "$home" -name '*.backup-*' | wc -l | tr -d ' '
}

case_mcp() {
	setup_case mcp
	write_mcp_configs
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	expect_contains "$output" "MCP configs are in sync"
	[ "$(json_value "$home/.claude.json" mcpServers.context7.headers.Authorization)" = "$(claude_expected)" ] || fail "Claude has no env reference for context7"
	[ "$(json_value "$home/.claude.json" mcpServers.keep.command)" = keep ] || fail "bootstrap dropped an unmanaged Claude server"
	[ "$(json_value "$home/.config/opencode/opencode.json" 'mcp.context7.headers.Authorization')" = 'Bearer {env:CONTEXT7_API_KEY}' ] || fail "OpenCode has no env reference for context7"
	[ "$(json_value "$home/.config/opencode/opencode.json" 'mcp.keep.command.0')" = keep ] || fail "bootstrap dropped an unmanaged OpenCode server"
	grep -q '^bearer_token_env_var = "CONTEXT7_API_KEY"$' "$home/.codex/config.toml" || fail "Codex has no env reference for context7"
	grep -q '^\[mcp_servers.keep\]$' "$home/.codex/config.toml" || fail "bootstrap dropped an unmanaged Codex server"
	backups=$(backup_total)
	[ "$backups" -ge 3 ] || fail "bootstrap did not back up each rewritten config"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "second bootstrap failed: $output"
	[ "$(backup_total)" = "$backups" ] || fail "an unchanged bootstrap wrote another backup"
	printf '{"headers":{"Authorization":"Bearer literal-value"}}\n' >"$home/.claude.json"
	run_mcp_check
	[ "$code" -ne 0 ] || fail "the MCP check passed with a drifted config"
	expect_contains "$output" "$home/.claude.json"
	run_discovery
	[ "$code" -ne 0 ] || fail "bootstrap:check passed with a drifted MCP config"
	expect_contains "$output" "MCP configs are not in sync"
	expect_contains "$output" "$home/.claude.json"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap did not repair the drift: $output"
	run_mcp_check
	[ "$code" -eq 0 ] || fail "the MCP check still reports drift: $output"
	printf 'not json\n' >"$home/.claude.json"
	run_discovery
	[ "$code" -ne 0 ] || fail "bootstrap:check passed with malformed JSON"
	expect_contains "$output" "MCP configs are not in sync"
	expect_contains "$output" "$home/.claude.json"
	expect_not_contains "$output" "Traceback"
	printf 'ok: bootstrap renders, checks, backs up and repairs the MCP configs\n'
}

case_mcp_absent() {
	setup_case mcp-absent
	[ ! -e "$home/.claude.json" ] || fail "the fixture home already has a Claude config"
	[ ! -e "$home/.config/opencode/opencode.json" ] || fail "the fixture home already has an OpenCode config"
	[ ! -e "$home/.codex/config.toml" ] || fail "the fixture home already has a Codex config"
	run_discovery
	[ "$code" -ne 0 ] || fail "bootstrap:check passed without MCP configs: $output"
	expect_contains "$output" "MCP configs are not in sync"
	expect_contains "$output" "$home/.claude.json"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed without MCP configs: $output"
	expect_contains "$output" "MCP configs are in sync"
	[ -f "$home/.claude.json" ] || fail "bootstrap did not create the Claude config"
	[ -f "$home/.config/opencode/opencode.json" ] || fail "bootstrap did not create the OpenCode config"
	[ -f "$home/.codex/config.toml" ] || fail "bootstrap did not create the Codex config"
	[ "$(json_value "$home/.claude.json" mcpServers.context7.headers.Authorization)" = "$(claude_expected)" ] || fail "the created Claude config has no context7 env reference"
	[ "$(json_value "$home/.config/opencode/opencode.json" 'mcp.context7.headers.Authorization')" = 'Bearer {env:CONTEXT7_API_KEY}' ] || fail "the created OpenCode config has no context7 env reference"
	grep -q '^bearer_token_env_var = "CONTEXT7_API_KEY"$' "$home/.codex/config.toml" || fail "the created Codex config has no context7 env reference"
	grep -q '^\[shell_environment_policy\]$' "$home/.codex/config.toml" || fail "the created Codex config has no shell environment policy"
	grep -q '^ignore_default_excludes = true$' "$home/.codex/config.toml" || fail "the created Codex config does not pass registry secrets"
	run_mcp_check
	[ "$code" -eq 0 ] || fail "the MCP check reported drift after bootstrap created the configs: $output"
	printf 'ok: bootstrap creates MCP configs for harnesses that have never run\n'
}

case_secrets_agent() {
	setup_case secrets-agent
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	plist=$home/Library/LaunchAgents/dev.factory.secrets.plist
	[ -f "$plist" ] || fail "bootstrap did not write the secrets agent plist"
	program=$(plist_value "$plist" ProgramArguments.0)
	case $program in
	/usr/bin/python*) fail "the secrets agent program is $program" ;;
	esac
	[ "$(plist_value "$plist" ProgramArguments.1)" = "$home/.agents/scripts/mcp-sync.py" ] || fail "the secrets agent program is not the Factory mcp-sync"
	[ "$(plist_value "$plist" ProgramArguments.2)" = --env ] || fail "the secrets agent does not run --env"
	[ "$(plist_value "$plist" KeepAlive.SuccessfulExit)" = False ] || fail "the secrets agent does not retry on failure"
	[ "$(plist_value "$plist" ThrottleInterval)" -gt 0 ] || fail "the secrets agent has no throttle"
	[ "$(plist_value "$plist" RunAtLoad)" = True ] || fail "the secrets agent does not run at load"
	[ "$(plist_value "$plist" StandardErrorPath)" = "$home/Library/Logs/factory-secrets.log" ] || fail "the secrets agent has no log"
	before=$(cksum "$plist")
	run_bootstrap
	[ "$code" -eq 0 ] || fail "second bootstrap failed: $output"
	[ "$(cksum "$plist")" = "$before" ] || fail "the second bootstrap rewrote the plist"
	run_discovery
	[ "$code" -eq 0 ] || fail "discovery failed with the secrets agent present: $output"
	printf 'ok: the secrets agent uses an absolute interpreter and retries loudly\n'
}

case_gc_agent() {
	setup_case gc-agent
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	plist=$home/Library/LaunchAgents/dev.factory.gc.plist
	[ -f "$plist" ] || fail "gc plist missing"
	[ "$(plist_value "$plist" Label)" = dev.factory.gc ] || fail "wrong label"
	[ "$(plist_value "$plist" StartCalendarInterval.Weekday)" = 0 ] || fail "wrong day"
	[ "$(plist_value "$plist" StartCalendarInterval.Hour)" = 3 ] || fail "wrong hour"
	[ "$(plist_value "$plist" StartCalendarInterval.Minute)" = 0 ] || fail "wrong minute"
	[ "$(plist_value "$plist" ProgramArguments.2)" = "$home/.agents" ] || fail "wrong working directory"
	[ "$(plist_value "$plist" ProgramArguments.3)" = run ] || fail "does not run a task"
	[ "$(plist_value "$plist" ProgramArguments.4)" = gc ] || fail "does not run gc"
	[ "$(plist_value "$plist" StandardOutPath)" = "$home/Library/Logs/factory-gc.log" ] || fail "wrong log path"
	[ "$(plist_value "$plist" StandardErrorPath)" = "$home/Library/Logs/factory-gc-error.log" ] || fail "wrong error log path"
	expect_contains "$(plist_value "$plist" EnvironmentVariables.PATH)" "$(dirname "$(command -v mise)")"
	expect_contains "$(plist_value "$plist" EnvironmentVariables.PATH)" "$(dirname "$(command -v gh)")"
	[ -n "$(env -i PATH="$(plist_value "$plist" EnvironmentVariables.PATH)" /bin/sh -c 'command -v uv')" ] || fail "uv is not on the gc agent PATH"
	run_discovery
	[ "$code" -eq 0 ] || fail "gc discovery failed: $output"
	printf 'ok: gc plist installed at %s and discovery passed\n' "$plist"
	run_python -c 'import plistlib,sys; p=sys.argv[1]; d=plistlib.loads(open(p,"rb").read()); d["StartCalendarInterval"]["Hour"]=4; open(p,"wb").write(plistlib.dumps(d))' "$plist"
	run_discovery
	[ "$code" -ne 0 ] || fail "drift went undetected"
	expect_contains "$output" dev.factory.gc.plist
	printf 'ok: gc plist drift detected\n'
	rm "$plist"
	run_discovery
	[ "$code" -ne 0 ] || fail "missing plist went undetected"
	expect_contains "$output" dev.factory.gc.plist
	printf 'ok: missing gc plist detected\n'
}

case_verify_agent() {
	setup_case verify-agent
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	plist=$home/Library/LaunchAgents/dev.factory.verify-skill-maintenance.plist
	[ -f "$plist" ] || fail "verify-skill-maintenance plist missing"
	[ "$(plist_value "$plist" Label)" = dev.factory.verify-skill-maintenance ] || fail "wrong label"
	[ "$(plist_value "$plist" StartCalendarInterval.Weekday)" = 1 ] || fail "wrong day"
	[ "$(plist_value "$plist" StartCalendarInterval.Hour)" = 3 ] || fail "wrong hour"
	[ "$(plist_value "$plist" StartCalendarInterval.Minute)" = 0 ] || fail "wrong minute"
	[ "$(plist_value "$plist" ProgramArguments.2)" = "$home/.agents" ] || fail "wrong working directory"
	[ "$(plist_value "$plist" ProgramArguments.3)" = run ] || fail "does not run a task"
	[ "$(plist_value "$plist" ProgramArguments.4)" = verify-skill:maintain ] || fail "does not run verify-skill:maintain"
	[ "$(plist_value "$plist" StandardOutPath)" = "$home/Library/Logs/factory-verify-skill-maintenance.log" ] || fail "wrong log path"
	[ "$(plist_value "$plist" StandardErrorPath)" = "$home/Library/Logs/factory-verify-skill-maintenance-error.log" ] || fail "wrong error log path"
	expect_contains "$(plist_value "$plist" EnvironmentVariables.PATH)" "$(dirname "$(command -v mise)")"
	expect_contains "$(plist_value "$plist" EnvironmentVariables.PATH)" "$(dirname "$(command -v gh)")"
	[ -n "$(env -i PATH="$(plist_value "$plist" EnvironmentVariables.PATH)" /bin/sh -c 'command -v uv')" ] || fail "uv is not on the verify-skill-maintenance agent PATH"
	if grep -q 'launchctl bootstrap gui/' "$log"; then fail "the temp home loaded the agent"; fi
	run_discovery
	[ "$code" -eq 0 ] || fail "verify-skill-maintenance discovery failed: $output"
	printf 'ok: verify-skill-maintenance plist installed at %s and discovery passed\n' "$plist"
	run_python -c 'import plistlib,sys; p=sys.argv[1]; d=plistlib.loads(open(p,"rb").read()); d["StartCalendarInterval"]["Hour"]=4; open(p,"wb").write(plistlib.dumps(d))' "$plist"
	run_discovery
	[ "$code" -ne 0 ] || fail "verify-skill-maintenance drift went undetected"
	expect_contains "$output" dev.factory.verify-skill-maintenance.plist
	printf 'ok: verify-skill-maintenance plist drift detected\n'
	rm "$plist"
	run_discovery
	[ "$code" -ne 0 ] || fail "missing verify-skill-maintenance plist went undetected"
	expect_contains "$output" dev.factory.verify-skill-maintenance.plist
	printf 'ok: missing verify-skill-maintenance plist detected\n'
}

case_verify_agent_narrow() {
	setup_case verify-agent-narrow
	run_bootstrap --install-verify-agent
	[ "$code" -eq 0 ] || fail "narrow verify-agent install failed: $output"
	[ -f "$home/Library/LaunchAgents/dev.factory.verify-skill-maintenance.plist" ] || fail "narrow install wrote no plist"
	[ ! -e "$home/.claude" ] || fail "narrow install wrote harness links"
	[ ! -e "$home/.agents" ] || fail "narrow install wrote the agents link"
	if grep -q 'launchctl bootstrap gui/' "$log"; then fail "the narrow install loaded the agent"; fi
	printf 'ok: the narrow verify-agent install writes only the plist\n'
}

case_plist_drift() {
	setup_case plist-drift
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	plist=$home/Library/LaunchAgents/dev.factory.secrets.plist
	run_python -c 'import plistlib,sys; p=sys.argv[1]; d=plistlib.loads(open(p, "rb").read()); d["ProgramArguments"][0]="/nonexistent/factory-python"; open(p, "wb").write(plistlib.dumps(d))' "$plist"
	[ "$code" -eq 0 ] || fail "cannot drift the plist: $output"
	run_discovery
	[ "$code" -ne 0 ] || fail "discovery passed with a missing interpreter in the plist"
	expect_contains "$output" "$tilde/Library/LaunchAgents/dev.factory.secrets.plist"
	expect_contains "$output" "/nonexistent/factory-python"
	run_python -c 'import plistlib,sys; p=sys.argv[1]; d=plistlib.loads(open(p, "rb").read()); d["ThrottleInterval"]=5; open(p, "wb").write(plistlib.dumps(d))' "$plist"
	[ "$code" -eq 0 ] || fail "cannot drift the plist throttle: $output"
	run_discovery
	[ "$code" -ne 0 ] || fail "discovery passed with a stale plist"
	expect_contains "$output" "$tilde/Library/LaunchAgents/dev.factory.secrets.plist"
	rm "$plist"
	run_discovery
	[ "$code" -ne 0 ] || fail "discovery passed without the plist"
	expect_contains "$output" "$tilde/Library/LaunchAgents/dev.factory.secrets.plist"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap did not restore the plist: $output"
	run_discovery
	[ "$code" -eq 0 ] || fail "discovery failed after the repair: $output"
	printf 'ok: the check names a missing, stale or absent secrets agent\n'
}

case_ssh_agent() {
	setup_case ssh-agent
	baseline_repo "$factory"
	git -C "$factory" remote add origin https://github.com/example/factory.git
	mkdir -p "$home/Library/Group Containers/2BUA8C4S2C.com.1password/t" "$home/.ssh"
	printf 'Host *\n  IdentitiesOnly yes\nHost example\n  User someone\n' >"$home/.ssh/config"
	: >"$home/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	expect_contains "$output" "added github.com"
	grep -q '^Host github.com$' "$home/.ssh/config" || fail "the ssh config has no github.com block"
	grep -q 'IdentityAgent .*2BUA8C4S2C.com.1password/t/agent.sock' "$home/.ssh/config" || fail "the ssh config does not use the 1Password agent"
	grep -q '^Host example$' "$home/.ssh/config" || fail "the ssh config lost an existing block"
	[ "$(file_mode "$home/.ssh/config")" = 600 ] || fail "the ssh config is not 600"
	run_command ssh -F "$home/.ssh/config" -G github.com
	[ "$code" -eq 0 ] || fail "ssh cannot parse the config: $output"
	expect_contains "$output" "identityagent $home/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"
	expect_contains "$output" "identitiesonly no"
	run_command ssh -F "$home/.ssh/config" -G example
	[ "$code" -eq 0 ] || fail "ssh cannot parse the host example block: $output"
	expect_contains "$output" "user someone"
	backup=$(find "$home/.ssh" -name 'config.backup-*' | head -n 1)
	[ -n "$backup" ] || fail "the ssh config was not backed up"
	grep -q '^Host example$' "$backup" || fail "the ssh config backup lost the original"
	[ "$(git -C "$factory" remote get-url origin)" = "git@github.com:example/factory.git" ] || fail "the origin was not rewritten to SSH"
	[ "$(git -C "$factory" remote get-url --push origin)" = "https://github.com/example/factory.git" ] || fail "push URL is not HTTPS"
	git -C "$factory" config --local --unset-all credential.https://github.com.helper
	run_discovery
	[ "$code" -ne 0 ] || fail "missing GitHub helper passed"
	expect_contains "$output" "GitHub credential helper is missing"
	git -C "$factory" config --local --add credential.https://github.com.helper ''
	git -C "$factory" config --local --add credential.https://github.com.helper '!gh auth git-credential'
	git -C "$factory" remote set-url --push origin git@github.com:example/factory.git
	run_discovery
	[ "$code" -ne 0 ] || fail "SSH push URL passed"
	expect_contains "$output" "GitHub origin push URL is not HTTPS"
	git -C "$factory" remote set-url --push origin https://github.com/example/factory.git
	run_bootstrap
	[ "$code" -eq 0 ] || fail "second bootstrap failed: $output"
	[ "$(count_lines '^# Factory: github.com' "$home/.ssh/config")" -eq 1 ] || fail "the ssh block was written twice"
	printf 'ForwardX11 yes\n  \n# keep-trailing   \n' >>"$home/.ssh/config"
	run_command ssh -F "$home/.ssh/config" -G example
	[ "$code" -eq 0 ] || fail "ssh cannot parse the config with a global line: $output"
	expect_contains "$output" "forwardx11 yes"
	run_python -c 'import sys; p=sys.argv[1]; t=open(p).read(); open(p,"w").write(t.replace("    User git\n", "    User git\n# note\n\n    IdentitiesOnly yes\n", 1))' "$home/.ssh/config"
	[ "$code" -eq 0 ] || fail "cannot drift the ssh block: $output"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap did not repair the ssh block: $output"
	expect_contains "$output" "updated github.com"
	[ "$(count_lines '^# Factory: github.com' "$home/.ssh/config")" -eq 1 ] || fail "the repaired block duplicated the marker"
	[ "$(count_lines 'IdentityAgent .*1password' "$home/.ssh/config")" -eq 1 ] || fail "the repair left an orphaned agent line"
	run_command ssh -F "$home/.ssh/config" -G github.com
	[ "$code" -eq 0 ] || fail "ssh cannot parse the repaired config: $output"
	expect_contains "$output" "identitiesonly no"
	run_command ssh -F "$home/.ssh/config" -G example
	expect_contains "$output" "forwardx11 yes"
	grep -q '^  $' "$home/.ssh/config" || fail "the repair dropped a whitespace-only line"
	grep -q '^# keep-trailing   $' "$home/.ssh/config" || fail "the repair did not preserve trailing content byte-for-byte"
	printf '\n# Factory: github.com through the 1Password SSH agent\nHost github.com\n    IdentityAgent "%s"\n' "$home/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock" >>"$home/.ssh/config"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap did not remove a duplicate block: $output"
	[ "$(count_lines '^# Factory: github.com' "$home/.ssh/config")" -eq 1 ] || fail "a duplicate ssh block survived"
	printf '\n# Factory: github.com through the 1Password SSH agent\n\n    Bogus\n' >>"$home/.ssh/config"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap did not repair a corrupt block: $output"
	[ "$(count_lines '^Host github.com$' "$home/.ssh/config")" -eq 1 ] || fail "a corrupt block orphaned a second Host github.com"
	grep -q 'Bogus' "$home/.ssh/config" && fail "the corrupt block kept its stray line"
	run_command ssh -F "$home/.ssh/config" -G github.com
	[ "$code" -eq 0 ] || fail "ssh cannot parse the repaired corrupt block: $output"
	expect_contains "$output" "identitiesonly no"
	printf '\n# Factory: github.com through the 1Password SSH agent\n\nHost github.company\n    User similar\n' >>"$home/.ssh/config"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed behind a look-alike block: $output"
	grep -q '^Host github.company$' "$home/.ssh/config" || fail "the repair removed a look-alike host block"
	grep -q '^    User similar$' "$home/.ssh/config" || fail "the repair removed the look-alike options"
	printf '\n# Factory: github.com through the 1Password SSH agent\nHost github.com\n    IdentitiesOnly yes\n\nMatch all\n' >>"$home/.ssh/config"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed behind a blank-terminated block: $output"
	[ "$(count_lines '^Match all$' "$home/.ssh/config")" -eq 1 ] || fail "the repair left a duplicated Match all"
	printf '\n# Factory: github.com through the 1Password SSH agent\n\n# note one\n# note two\n\n    IdentitiesOnly yes\n    IdentityAgent "/stale/agent.sock"\n' >>"$home/.ssh/config"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed behind a commented block: $output"
	[ "$(count_lines 'IdentityAgent .*1password' "$home/.ssh/config")" -eq 1 ] || fail "the commented block left a stale agent line"
	grep -q 'note one\|note two\|/stale/agent.sock' "$home/.ssh/config" && fail "the commented block kept its stale lines"
	run_command ssh -F "$home/.ssh/config" -G github.com
	[ "$code" -eq 0 ] || fail "ssh cannot parse after a commented block: $output"
	expect_contains "$output" "identitiesonly no"
	printf '\n# Factory: github.com through the 1Password SSH agent\n\n  Host indented-example\n    User someone\n' >>"$home/.ssh/config"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed behind an indented host block: $output"
	grep -q '^  Host indented-example$' "$home/.ssh/config" || fail "the repair removed an indented user block"
	grep -q '^    User someone$' "$home/.ssh/config" || fail "the repair removed the indented user options"
	printf '\nHost github.company\n    User similar\n' >>"$home/.ssh/config"
	run_python -c 'import sys; p=sys.argv[1]; t=open(p).read(); open(p,"w").write(t.replace("    IdentitiesOnly no\n", "    IdentitiesOnly yes\n", 1))' "$home/.ssh/config"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed behind a look-alike host block: $output"
	grep -q '^Host github.company$' "$home/.ssh/config" || fail "the repair removed a look-alike host block"
	printf 'ok: the ssh config routes GitHub through 1Password and the origin moves to SSH\n'
}

case_ssh_crlf() {
	setup_case ssh-crlf
	baseline_repo "$factory"
	git -C "$factory" remote add origin https://github.com/example/factory.git
	mkdir -p "$home/Library/Group Containers/2BUA8C4S2C.com.1password/t" "$home/.ssh"
	printf 'Host example\r\n  User someone\r\n' >"$home/.ssh/config"
	: >"$home/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed on a CRLF config: $output"
	run_python -c 'import sys; b=open(sys.argv[1],"rb").read(); assert b"Host example\r\n" in b, "user block"; assert b"IdentitiesOnly no\r\n" in b, "managed block"; assert b"Host github.com\r\n" in b, "host line"' "$home/.ssh/config"
	[ "$code" -eq 0 ] || fail "the CRLF config was not preserved: $output"
	run_python -c 'import sys; p=sys.argv[1]; b=open(p,"rb").read(); open(p,"wb").write(b.replace(b"    IdentitiesOnly no\r\n", b"    IdentitiesOnly yes\r\n", 1))' "$home/.ssh/config"
	[ "$code" -eq 0 ] || fail "cannot drift the CRLF block: $output"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed to repair the CRLF block: $output"
	run_python -c 'import sys; b=open(sys.argv[1],"rb").read(); assert b"IdentitiesOnly no\r\n" in b, "repair"; assert b"IdentitiesOnly yes" not in b, "stale"; assert b"Host example\r\n" in b, "user block"' "$home/.ssh/config"
	[ "$code" -eq 0 ] || fail "the CRLF repair lost line endings: $output"
	run_python -c 'import sys; p=sys.argv[1]; b=open(p,"rb").read(); open(p,"wb").write(b.replace(b"    User git\r\n", b"    User git\r\n# note\r\n\r\n    IdentitiesOnly yes\r\n", 1))' "$home/.ssh/config"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed on a CRLF block with a comment: $output"
	run_python -c 'import sys; b=open(sys.argv[1],"rb").read(); assert b"IdentitiesOnly no\r\n" in b, "repair"; assert b"IdentitiesOnly yes" not in b, "stale"; assert b.count(b"IdentityAgent") == 1, "orphan"' "$home/.ssh/config"
	[ "$code" -eq 0 ] || fail "the CRLF block with a comment was not fully repaired: $output"
	printf 'ok: a CRLF ssh config keeps its line endings\n'
}

case_ssh_no_agent() {
	setup_case ssh-no-agent
	baseline_repo "$factory"
	git -C "$factory" remote add origin https://github.com/example/factory.git
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap passed without the 1Password SSH agent"
	expect_contains "$output" "2BUA8C4S2C.com.1password/t/agent.sock"
	expect_contains "$output" "1Password"
	[ ! -e "$home/.ssh/config" ] || fail "bootstrap wrote an ssh config without the agent"
	[ "$(git -C "$factory" remote get-url origin)" = "https://github.com/example/factory.git" ] || fail "bootstrap rewrote origin without SSH auth"
	printf 'ok: a missing 1Password agent fails loudly and leaves origin alone\n'
}

case_ssh_existing() {
	setup_case ssh-existing
	baseline_repo "$factory"
	git -C "$factory" remote add origin git@github.com:example/factory.git
	mkdir -p "$home/Library/Group Containers/2BUA8C4S2C.com.1password/t" "$home/.ssh"
	printf 'Host github.com\n  User git\n  IdentityFile ~/.ssh/other\n  # IdentityAgent "%s"\n' "$home/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock" >"$home/.ssh/config"
	: >"$home/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	expect_contains "$output" "SSH auth through it is unverified"
	[ "$(count_lines '^Host github.com$' "$home/.ssh/config")" -eq 1 ] || fail "bootstrap duplicated the user block"
	grep -q 'IdentityFile ~/.ssh/other' "$home/.ssh/config" || fail "bootstrap edited the user block"
	git -C "$factory" remote set-url origin https://github.com/example/factory.git
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed behind an unmanaged block: $output"
	expect_contains "$output" "SSH auth through it is unverified"
	[ "$(git -C "$factory" remote get-url origin)" = "git@github.com:example/factory.git" ] || fail "bootstrap left the origin on HTTPS"
	printf 'Host github.com *.github.com\n  IdentityAgent "%s"\n' "$home/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock" >"$home/.ssh/config"
	git -C "$factory" remote set-url origin https://github.com/example/factory.git
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed behind an agent block: $output"
	expect_contains "$output" "already routes github.com through 1Password"
	[ "$(git -C "$factory" remote get-url origin)" = "git@github.com:example/factory.git" ] || fail "bootstrap left the origin on HTTPS behind an agent block"
	printf 'Host github.com\n  IdentityAgent="%s"\n' "$home/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock" >"$home/.ssh/config"
	git -C "$factory" remote set-url origin https://github.com/example/factory.git
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed behind an equals-form agent block: $output"
	expect_contains "$output" "already routes github.com through 1Password"
	[ "$(git -C "$factory" remote get-url origin)" = "git@github.com:example/factory.git" ] || fail "bootstrap left the origin on HTTPS behind an equals-form agent block"
	run_command ssh -F "$home/.ssh/config" -G github.com
	[ "$code" -eq 0 ] || fail "ssh cannot parse the equals-form block: $output"
	expect_contains "$output" "identityagent $home/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"
	printf 'Host github.com\n  IdentityAgentBroken "%s"\n' "$home/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock" >"$home/.ssh/config"
	git -C "$factory" remote set-url origin https://github.com/example/factory.git
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed behind a look-alike agent block: $output"
	expect_contains "$output" "SSH auth through it is unverified"
	printf 'ok: an existing github.com block is left alone and an HTTPS origin still moves to SSH\n'
}

case_launchd() {
	setup_case launchd
	write_mcp_configs
	[ "$(command -v op)" = "$bin/op" ] || fail "the op stub is not first on PATH"
	[ "$(command -v launchctl)" = "$bin/launchctl" ] || fail "the launchctl stub is not first on PATH"
	uv_bin=$(mise which uv)
	python_bin=$(mise which python)
	export LAUNCHCTL_VALUE=value
	run_command env HOME="$home" MISE_DATA_DIR="$HOME/.local/share/mise" UV_CACHE_DIR="$HOME/.cache/uv" "$uv_bin" run --offline --no-project --no-config --python "$python_bin" python "$factory/scripts/bootstrap.py" --home "$home"
	[ "$code" -eq 0 ] || fail "bootstrap with a temp HOME failed: $output"
	expect_contains "$output" "MCP configs are in sync"
	grep -q 'op read --no-newline op://factory/Context7 API/credential' "$log" || fail "the agent did not read the 1Password reference"
	grep -q 'launchctl setenv CONTEXT7_API_KEY dummy-value' "$log" || fail "the agent did not set the launchd variable"
	grep -q 'launchctl bootstrap gui/' "$log" || fail "bootstrap did not load the secrets agent"
	run_command env HOME="$home" MISE_DATA_DIR="$HOME/.local/share/mise" UV_CACHE_DIR="$HOME/.cache/uv" "$uv_bin" run --offline --no-project --no-config --python "$python_bin" python "$factory/scripts/bootstrap.py" --check --home "$home"
	[ "$code" -eq 0 ] || fail "the check failed with the launchd variable set: $output"
	run_command env HOME="$home" LAUNCHCTL_VALUE='' MISE_DATA_DIR="$HOME/.local/share/mise" UV_CACHE_DIR="$HOME/.cache/uv" "$uv_bin" run --offline --no-project --no-config --python "$python_bin" python "$factory/scripts/bootstrap.py" --check --home "$home"
	[ "$code" -ne 0 ] || fail "the check passed with the launchd variable unset"
	expect_contains "$output" "the launchd secret check failed"
	expect_contains "$output" "CONTEXT7_API_KEY"
	printf 'ok: bootstrap loads the agent and the check catches an unset launchd secret\n'
}

case_service_token() {
	setup_case service-token
	export KEYCHAIN_FIXTURE="$case_dir/keychain"
	cat >"$bin/security" <<'STUB'
#!/bin/sh
if [ "$1" = -i ]; then
	input=$(cat)
	case $input in *'-T /usr/bin/security'*'-w "dummy-value"'*) ;; *) exit 1 ;; esac
	printf dummy-value >"$KEYCHAIN_FIXTURE"
	exit 0
fi
[ -f "$KEYCHAIN_FIXTURE" ] || exit 1
case " $* " in *' -w '*) cat "$KEYCHAIN_FIXTURE" ;; esac
STUB
	chmod +x "$bin/security"
	list_call='item list --vault Private --format json'
	read_call='read --no-newline op://Private/fixture-token/credential'
	run_bootstrap
	[ "$code" -eq 0 ] || fail "initial token provisioning failed: $output"
	expect_not_contains "$output" "dummy-value"
	[ "$(count_calls "$list_call")" = 1 ] || fail "initial token title lookup missing"
	[ "$(count_calls "$read_call")" = 1 ] || fail "initial token read missing"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "idempotent token provisioning failed: $output"
	[ "$(count_calls "$list_call")" = 1 ] || fail "token title lookup repeated"
	[ "$(count_calls "$read_call")" = 1 ] || fail "token read repeated"
	run_bootstrap --rotate-op-token
	[ "$code" -eq 0 ] || fail "token rotation failed: $output"
	[ "$(count_calls "$list_call")" = 2 ] || fail "rotation did not look up the token title"
	[ "$(count_calls "$read_call")" = 2 ] || fail "rotation did not read token"
	printf 'ok: token provisioning is idempotent and rotation is explicit\n'
}

case_keychain_store_failure() {
	setup_case keychain-store-failure
	cat >"$bin/op" <<'STUB'
#!/bin/sh
set -eu
printf 'op %s\n' "$*" >>"$STUB_LOG"
if [ "$1 $2" = "item list" ]; then
	printf '[{"id":"fixture-token","title":"Service Account Auth Token: Factory"}]\n'
else
	printf '%s' 'dummy-"value'
fi
STUB
	chmod +x "$bin/op"
	cat >"$bin/security" <<'STUB'
#!/bin/sh
if [ "$1" = -i ]; then input=$(cat); printf 'security: fixture interaction is not allowed: %s\n' "$input" >&2; exit 36; fi
exit 44
STUB
	chmod +x "$bin/security"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap stored the token despite a failing security command"
	expect_contains "$output" "cannot store service account token in login Keychain"
	expect_contains "$output" "fixture interaction is not allowed"
	expect_contains "$output" "[redacted]"
	expect_not_contains "$output" 'dummy-"value'
	expect_not_contains "$output" 'dummy-\"value'
	printf 'ok: a failing security store names its reason and redacts the token\n'
}

case_keychain_verify_failure() {
	setup_case keychain-verify-failure
	cat >"$bin/security" <<'STUB'
#!/bin/sh
if [ "$1" = -i ]; then cat >/dev/null; exit 0; fi
printf 'security: fixture item could not be read\n' >&2
exit 44
STUB
	chmod +x "$bin/security"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap passed despite an unreadable keychain item"
	expect_contains "$output" "cannot read it back from the login Keychain"
	expect_contains "$output" "fixture item could not be read"
	printf 'ok: an unreadable keychain item names the readback failure\n'
}

case_keychain_mismatch() {
	setup_case keychain-mismatch
	marker=$case_dir/stored
	cat >"$bin/security" <<STUB
#!/bin/sh
if [ "\$1" = -i ]; then cat >/dev/null; : >"$marker"; exit 0; fi
[ -f "$marker" ] || exit 44
printf 'wrong-value'
STUB
	chmod +x "$bin/security"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap passed despite a mismatched keychain readback"
	expect_contains "$output" "does not match the 1Password value"
	expect_not_contains "$output" "wrong-value"
	printf 'ok: a mismatched keychain readback fails loudly\n'
}

case_token_lookup() {
	setup_case token-lookup
	export KEYCHAIN_FIXTURE="$case_dir/keychain"
	cat >"$bin/security" <<'STUB'
#!/bin/sh
if [ "$1" = -i ]; then cat >/dev/null; printf dummy-value >"$KEYCHAIN_FIXTURE"; exit 0; fi
[ -f "$KEYCHAIN_FIXTURE" ] || exit 1
case " $* " in *' -w '*) cat "$KEYCHAIN_FIXTURE" ;; esac
STUB
	export OP_ITEM_LIST_FILE="$case_dir/item-list.json"
	cat >"$bin/op" <<'STUB'
#!/bin/sh
set -eu
printf 'op %s\n' "$*" >>"$STUB_LOG"
if [ "$1 $2" = "item list" ]; then cat "$OP_ITEM_LIST_FILE"; exit 0; fi
printf 'dummy-value\n'
STUB
	chmod +x "$bin/security" "$bin/op"
	printf '%s\n' '[]' >"$OP_ITEM_LIST_FILE"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap passed with no matching token item"
	expect_contains "$output" "no 1Password item titled 'Service Account Auth Token: Factory' in the Private vault"
	printf '%s\n' '[{"id":"one","title":"Service Account Auth Token: Factory"},{"id":"two","title":"Service Account Auth Token: Factory"}]' >"$OP_ITEM_LIST_FILE"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap passed with two matching token items"
	expect_contains "$output" "2 1Password items are titled 'Service Account Auth Token: Factory' in the Private vault"
	[ "$(grep -c '^op read ' "$log" || true)" = 0 ] || fail "token was read without a unique item"
	[ ! -f "$KEYCHAIN_FIXTURE" ] || fail "a token was stored without a unique item"
	printf '%s\n' '{"title":"Service Account Auth Token: Factory"}' >"$OP_ITEM_LIST_FILE"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap passed with a malformed vault listing"
	expect_contains "$output" "cannot parse the Private 1Password vault listing"
	printf '%s\n' '[{"title":"Service Account Auth Token: Factory"}]' >"$OP_ITEM_LIST_FILE"
	run_bootstrap
	[ "$code" -ne 0 ] || fail "bootstrap passed with an item that has no ID"
	expect_contains "$output" "the item titled 'Service Account Auth Token: Factory' in the Private vault has no ID"
	[ "$(grep -c '^op read ' "$log" || true)" = 0 ] || fail "token was read without an item ID"
	[ ! -f "$KEYCHAIN_FIXTURE" ] || fail "a token was stored without an item ID"
	printf 'ok: the token lookup names zero, duplicate and malformed matches\n'
}

reference_scan() {
	run_python - "$1" <<'PY'
import re
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
terminator = re.compile(r"""["'`\n]""")
bad = set()
tracked = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
for name in tracked.decode().split("\0"):
	if not name:
		continue
	path = root / name
	if path.is_symlink() or not path.is_file():
		continue
	text = path.read_text(errors="replace")
	for match in re.finditer("op://", text):
		rest = text[match.end():]
		stop = terminator.search(rest)
		reference = "op://" + (rest[:stop.start()] if stop else rest)
		parts = reference.removeprefix("op://").split("/")
		if len(parts) > 1 and parts[0] and ":" in parts[1]:
			bad.add(f"{name}: {reference}")
if bad:
	print("\n".join(sorted(bad)), file=sys.stderr)
	raise SystemExit(1)
PY
}

case_reference_syntax() {
	setup_case reference-syntax
	reference_scan "$root"
	[ "$code" -eq 0 ] || fail "an op:// reference puts a colon in its item segment: $output"
	bad=$case_dir/bad
	mkdir -p "$bad"
	printf '{"reference":"op://factory/Bad%s Item/credential"}\n' ':' >"$bad/registry.json"
	init_git "$bad"
	git -C "$bad" add -A
	git -C "$bad" commit -qm baseline
	reference_scan "$bad"
	[ "$code" -ne 0 ] || fail "the reference check accepted a colon in the item segment"
	printf 'op read --no-newline op://factory/Bad%sItem/credential\n' ':' >"$bad/unquoted.sh"
	git -C "$bad" add -A
	git -C "$bad" commit -qm unquoted
	reference_scan "$bad"
	[ "$code" -ne 0 ] || fail "the reference check accepted an unquoted colon in the item segment"
	expect_contains "$output" "unquoted.sh"
	printf "grep -c 'op read --no-newline op://Private/Service Account Auth Token%s Factory/credential' \"\$log\"\n" ':' >"$bad/legacy.sh"
	git -C "$bad" add -A
	git -C "$bad" commit -qm legacy
	reference_scan "$bad"
	[ "$code" -ne 0 ] || fail "the reference check accepted the legacy title inside a quoted command"
	expect_contains "$output" "legacy.sh"
	printf 'ok: no op:// reference puts a colon in its item segment\n'
}

case_push_history() {
	setup_case push-history
	baseline_repo "$factory"
	branch=$(git -C "$factory" branch --show-current)
	remote=$case_dir/remote.git
	git init -q --bare "$remote"
	git -C "$remote" config receive.denyNonFastForwards true
	"$REAL_GIT" -C "$factory" push -q "$remote" "HEAD:refs/heads/$branch"
	git clone -q "$remote" "$case_dir/advance"
	git -C "$case_dir/advance" config user.name Fixture
	git -C "$case_dir/advance" config user.email fixture@example.invalid
	printf 'advanced\n' >"$case_dir/advance/advance"
	git -C "$case_dir/advance" add advance
	git -C "$case_dir/advance" commit -qm advanced
	"$REAL_GIT" -C "$case_dir/advance" push -q origin "HEAD:refs/heads/$branch"
	run_command "$REAL_GIT" -C "$factory" push --dry-run "$remote" "HEAD:refs/heads/$branch"
	[ "$code" -ne 0 ] || fail "fixture remote did not reject the behind branch"
	git -C "$factory" remote add origin https://github.com/example/factory.git
	git -C "$factory" remote set-url --push origin https://github.com/example/factory.git
	git -C "$factory" config --local --add credential.https://github.com.helper ''
	git -C "$factory" config --local --add credential.https://github.com.helper '!gh auth git-credential'
	export FIXTURE_PUSH_REMOTE="$remote"
	run_command python3 -c 'import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]+"/scripts"); import bootstrap; bootstrap.ROOT=Path(sys.argv[2]); errors=bootstrap.github_push_errors(); print("\n".join(errors)); sys.exit(bool(errors))' "$factory" "$factory"
	[ "$code" -eq 0 ] || fail "behind checkout push check failed: $output"
	linked=$case_dir/linked
	git -C "$factory" worktree add -q --detach "$linked" HEAD
	run_command python3 -c 'import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]+"/scripts"); import bootstrap; bootstrap.ROOT=Path(sys.argv[2]); errors=bootstrap.github_push_errors(); print("\n".join(errors)); sys.exit(bool(errors))' "$factory" "$linked"
	[ "$code" -eq 0 ] || fail "detached worktree push check failed: $output"
	[ "$(git -C "$remote" for-each-ref --format='%(refname)' refs/heads | wc -l | tr -d ' ')" = 1 ] || fail "dry run created a remote ref"
	printf 'ok: push check works behind remote and from detached worktree\n'
}

case_unreadable_references() {
	setup_case unreadable-references
	run_bootstrap
	[ "$code" -eq 0 ] || fail "fixture bootstrap failed: $output"
	cat >"$factory/mcp.json" <<'JSON'
{"servers":{"a":{"transport":"http","url":"https://example.invalid","targets":["codex"],"headers":{"one":{"env":"FIRST_KEY","reference":"op://factory/First/credential"},"two":{"env":"SECOND_KEY","reference":"op://factory/Second/credential"}}}}}
JSON
	cat >"$bin/op" <<'STUB'
#!/bin/sh
exit 1
STUB
	chmod +x "$bin/op"
	run_discovery
	[ "$code" -ne 0 ] || fail "unreadable items passed bootstrap check"
	expect_contains "$output" "FIRST_KEY (op://factory/First/credential)"
	expect_contains "$output" "SECOND_KEY (op://factory/Second/credential)"
	printf 'ok: bootstrap check names every unreadable reference\n'
}

case_plist_interpreter() {
	setup_case plist-interpreter
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	run_python -c 'import os,sys; sys.path.insert(0, sys.argv[1]); import bootstrap; sys.executable = "/usr/bin/true"; os.environ["PATH"] = "/usr/bin:/bin"; code = bootstrap.main(["--check", "--home", sys.argv[2]]); print(f"exit={code}")' "$factory/scripts" "$home"
	[ "$code" -eq 0 ] || fail "the check run failed to execute: $output"
	expect_contains "$output" "exit=1"
	expect_contains "$output" "cannot resolve an absolute Python"
	expect_contains "$output" "$tilde/Library/LaunchAgents/dev.factory.secrets.plist"
	printf 'ok: an unresolvable interpreter names the plist\n'
}

case_all() {
	case_unreadable_references
	case_push_history
	case_service_token
	case_keychain_store_failure
	case_keychain_verify_failure
	case_keychain_mismatch
	case_token_lookup
	case_reference_syntax
	case_fresh
	case_second
	case_broken
	case_refuse
	case_external
	case_worktree
	case_sync
	case_codex
	case_env
	case_hooks
	case_secret_guard
	case_secret_guard_refusal
	case_secret_guard_disabled
	case_secret_guard_version
	case_repair
	case_tracked
	case_synced_tracked
	case_synced_ignored
	case_synced_discovery
	case_mcp
	case_mcp_absent
	case_secrets_agent
	case_gc_agent
	case_verify_agent
	case_verify_agent_narrow
	case_plist_drift
	case_plist_interpreter
	case_ssh_agent
	case_ssh_no_agent
	case_ssh_existing
	case_ssh_crlf
	case_launchd
}

case $case_name in
unreadable-references) case_unreadable_references ;;
push-history) case_push_history ;;
service-token) case_service_token ;;
keychain-store-failure) case_keychain_store_failure ;;
keychain-verify-failure) case_keychain_verify_failure ;;
keychain-mismatch) case_keychain_mismatch ;;
token-lookup) case_token_lookup ;;
reference-syntax) case_reference_syntax ;;
fresh) case_fresh ;;
second) case_second ;;
broken) case_broken ;;
refuse) case_refuse ;;
external) case_external ;;
worktree) case_worktree ;;
sync) case_sync ;;
codex) case_codex ;;
env) case_env ;;
hooks) case_hooks ;;
secret-guard) case_secret_guard ;;
secret-guard-refusal) case_secret_guard_refusal ;;
secret-guard-disabled) case_secret_guard_disabled ;;
secret-guard-version) case_secret_guard_version ;;
repair) case_repair ;;
tracked) case_tracked ;;
synced-tracked) case_synced_tracked ;;
synced-ignored) case_synced_ignored ;;
synced-discovery) case_synced_discovery ;;
mcp) case_mcp ;;
mcp-absent) case_mcp_absent ;;
secrets-agent) case_secrets_agent ;;
gc-agent) case_gc_agent ;;
verify-agent) case_verify_agent ;;
verify-agent-narrow) case_verify_agent_narrow ;;
plist-drift) case_plist_drift ;;
plist-interpreter) case_plist_interpreter ;;
ssh-agent) case_ssh_agent ;;
ssh-no-agent) case_ssh_no_agent ;;
ssh-existing) case_ssh_existing ;;
ssh-crlf) case_ssh_crlf ;;
launchd) case_launchd ;;
all) case_all ;;
*)
	printf 'unknown case: %s\n' "$case_name" >&2
	exit 2
	;;
esac
