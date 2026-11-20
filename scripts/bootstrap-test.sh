#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
base_path=$PATH
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
for name in cua-driver cua-sandboxes cua-spaces cua-volume gui-automation; do
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
printf 'dummy-value'
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

	chmod +x "$bin/ardia" "$bin/cua" "$bin/ctx7" "$bin/npx" "$bin/rea" "$bin/gh" "$bin/op" "$bin/launchctl"
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
	run_bootstrap
	[ "$code" -eq 0 ] || fail "fresh bootstrap failed: $output"
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
	for name in ardia cua-driver cua-sandboxes cua-spaces cua-volume gui-automation find-docs reverse-engineer-anything gh-stack; do
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
	installers_before=$(cat "$log")
	run_bootstrap
	[ "$code" -eq 0 ] || fail "second bootstrap failed: $output"
	expect_contains "$output" "Already bootstrapped: no changes."
	[ "$(snapshot)" = "$before" ] || fail "the second run changed the tree or symlink targets"
	[ "$(cat "$log")" = "$installers_before" ] || fail "the second run invoked an installer again"
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
	expect_contains "$output" "Expecting value"
	expect_contains "$output" "$home/.claude.json"
	expect_not_contains "$output" "Traceback"
	printf 'ok: bootstrap renders, checks, backs up and repairs the MCP configs\n'
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
	printf 'Host example\n  User someone\n' >"$home/.ssh/config"
	: >"$home/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "bootstrap failed: $output"
	expect_contains "$output" "added github.com"
	grep -q '^Host github.com$' "$home/.ssh/config" || fail "the ssh config has no github.com block"
	grep -q 'IdentityAgent .*2BUA8C4S2C.com.1password/t/agent.sock' "$home/.ssh/config" || fail "the ssh config does not use the 1Password agent"
	grep -q '^Host example$' "$home/.ssh/config" || fail "the ssh config lost an existing block"
	[ "$(stat -f '%Lp' "$home/.ssh/config" 2>/dev/null || stat -c '%a' "$home/.ssh/config")" = 600 ] || fail "the ssh config is not 600"
	run_command ssh -F "$home/.ssh/config" -G github.com
	[ "$code" -eq 0 ] || fail "ssh cannot parse the config: $output"
	expect_contains "$output" "identityagent $home/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"
	backup=$(find "$home/.ssh" -name 'config.backup-*' | head -n 1)
	[ -n "$backup" ] || fail "the ssh config was not backed up"
	grep -q '^Host example$' "$backup" || fail "the ssh config backup lost the original"
	[ "$(git -C "$factory" remote get-url origin)" = "git@github.com:example/factory.git" ] || fail "the origin was not rewritten to SSH"
	run_bootstrap
	[ "$code" -eq 0 ] || fail "second bootstrap failed: $output"
	[ "$(count_lines '^# Factory: github.com' "$home/.ssh/config")" -eq 1 ] || fail "the ssh block was written twice"
	printf 'ok: the ssh config routes GitHub through 1Password and the origin moves to SSH\n'
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
	grep -q 'op read --no-newline op://Private/Context7 API/credential' "$log" || fail "the agent did not read the 1Password reference"
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
	case_repair
	case_tracked
	case_synced_tracked
	case_synced_ignored
	case_synced_discovery
	case_mcp
	case_secrets_agent
	case_plist_drift
	case_plist_interpreter
	case_ssh_agent
	case_ssh_no_agent
	case_ssh_existing
	case_launchd
}

case $case_name in
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
repair) case_repair ;;
tracked) case_tracked ;;
synced-tracked) case_synced_tracked ;;
synced-ignored) case_synced_ignored ;;
synced-discovery) case_synced_discovery ;;
mcp) case_mcp ;;
secrets-agent) case_secrets_agent ;;
plist-drift) case_plist_drift ;;
plist-interpreter) case_plist_interpreter ;;
ssh-agent) case_ssh_agent ;;
ssh-no-agent) case_ssh_no_agent ;;
ssh-existing) case_ssh_existing ;;
launchd) case_launchd ;;
all) case_all ;;
*)
	printf 'unknown case: %s\n' "$case_name" >&2
	exit 2
	;;
esac
