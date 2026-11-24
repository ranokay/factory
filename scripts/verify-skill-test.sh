#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
fixture="$root/scripts/fixtures/verify-skill/project"
tmp=$(mktemp -d "${TMPDIR:-/tmp}/factory-verify-skill.XXXXXX")
tmp=$(CDPATH='' cd "$tmp" && pwd -P)
real_home=$HOME
trap 'cleanup' EXIT HUP INT TERM

fail() {
	printf 'verify-skill-test: %s\n' "$*" >&2
	exit 1
}

export HOME="$tmp/home"
export XDG_CONFIG_HOME="$HOME/.config"
export MISE_CONFIG_DIR="$HOME/.config/mise"
export MISE_DATA_DIR="$HOME/.local/share/mise"
export MISE_STATE_DIR="$HOME/.local/state/mise"
export MISE_CACHE_DIR="$HOME/.cache/mise"
export MISE_GLOBAL_CONFIG_FILE="$MISE_CONFIG_DIR/config.toml"
export MISE_IGNORED_CONFIG_PATHS="$real_home/.config/mise/config.toml:$real_home/.mise.toml"
export MISE_CEILING_PATHS="$tmp"
mkdir -p "$MISE_CONFIG_DIR" "$MISE_DATA_DIR" "$MISE_STATE_DIR" "$MISE_CACHE_DIR"
: >"$MISE_GLOBAL_CONFIG_FILE"

cleanup() {
	if [ -d "$tmp/lifecycle-state" ]; then
		(cd "$tmp/lifecycle" && mise run verify:counter -- stop >/dev/null 2>&1) || true
	fi
}

prepare() {
	directory="$tmp/$1"
	mkdir -p "$directory"
	cp -R "$fixture/." "$directory/"
	mv "$directory/mise.toml.tmpl" "$directory/mise.toml"
	(cd "$directory" && mise trust -q mise.toml)
	printf '%s\n' "$directory"
}

check() {
	python3 "$root/scripts/verify-skill.py" --root "$1"
}

task() {
	(cd "$1" && shift && mise run verify:counter -- "$@")
}

replace() {
	python3 - "$1" "$2" "$3" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text()
if sys.argv[2] not in text: raise SystemExit(f'missing text in {path}: {sys.argv[2]!r}')
path.write_text(text.replace(sys.argv[2], sys.argv[3], 1))
PY
}

insert_after() {
	python3 - "$1" "$2" "$3" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
lines = path.read_text().splitlines()
index = lines.index(sys.argv[2])
lines.insert(index + 1, sys.argv[3])
path.write_text("\n".join(lines) + "\n")
PY
}

expect_failure() {
	name=$1
	needle=$2
	if output=$(check "$tmp/$name" 2>&1); then
		fail "$name passed but must fail"
	fi
	case $output in
	*"$needle"*) ;;
	*) fail "$name failed without '$needle': $output" ;;
	esac
}

passing=$(prepare passing)
[ "$(check "$passing")" = "verify-skill: $passing: ok (verify-counter, 7 commands, 3 features)" ] ||
	fail "the passing fixture did not pass"

stopped=$(prepare stopped)
export VERIFY_COUNTER_STATE="$tmp/stopped-state" VERIFY_COUNTER_EVIDENCE="$tmp/stopped-evidence"
mkdir -p "$VERIFY_COUNTER_EVIDENCE"
if output=$(task "$stopped" doctor 2>/dev/null); then
	fail "doctor passed against a stopped app"
fi
case $output in
*'"ok": false'*) ;;
*) fail "stopped doctor is not actionable JSON: $output" ;;
esac
case $output in
*'mise run verify:counter launch'*) ;;
*) fail "stopped doctor does not name launch: $output" ;;
esac
unset VERIFY_COUNTER_STATE VERIFY_COUNTER_EVIDENCE

lifecycle=$(prepare lifecycle)
export VERIFY_COUNTER_STATE="$tmp/lifecycle-state" VERIFY_COUNTER_EVIDENCE="$tmp/lifecycle-evidence"
mkdir -p "$VERIFY_COUNTER_EVIDENCE"
launch=$(task "$lifecycle" launch)
pid=$(printf '%s' "$launch" | python3 -c 'import json,sys; print(json.load(sys.stdin)["pid"])') ||
	fail "launch did not print JSON: $launch"
url=$(printf '%s' "$launch" | python3 -c 'import json,sys; print(json.load(sys.stdin)["url"])') ||
	fail "launch did not print a URL: $launch"
port=${url##*:}
task "$lifecycle" doctor >/dev/null || fail "doctor failed on a launched app"
[ "$(task "$lifecycle" increment)" = '{"count": 1, "ok": true}' ] || fail "increment did not report one"
[ "$(task "$lifecycle" count)" = '{"count": 1, "ok": true}' ] || fail "count did not persist the increment"
task "$lifecycle" stop >/dev/null || fail "stop failed"
if kill -0 "$pid" 2>/dev/null; then
	fail "the fixture server outlived its stop"
fi
if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
	fail "a listener outlived the lifecycle case"
fi
if pgrep -f "$lifecycle/app.py" >/dev/null 2>&1; then
	fail "a fixture child process outlived the lifecycle case"
fi
unset VERIFY_COUNTER_STATE VERIFY_COUNTER_EVIDENCE

no_skill=$(prepare no-skill)
rm -rf "$no_skill/.agents/skills/verify-counter"
expect_failure no-skill "no .agents/skills/verify-* directory found"

no_description=$(prepare no-description)
replace "$no_description/.agents/skills/verify-counter/SKILL.md" "description: \"Drive the Counter fixture app over HTTP on loopback to prove its health, count and reset behaviour. Use when a change touches app.py or the counter endpoints.\"" ""
expect_failure no-description "frontmatter has no description"

no_cleanup=$(prepare no-cleanup)
replace "$no_cleanup/.agents/skills/verify-counter/SKILL.md" "## Cleanup" "## Teardown"
cleanup_message="missing the \`## Cleanup\` section"
expect_failure no-cleanup "$cleanup_message"

no_index=$(prepare no-index)
replace "$no_index/.agents/skills/verify-counter/references/features/README.md" "- [Reset](./reset.md) covers returning the count to zero." ""
expect_failure no-index "reset.md is not indexed"

no_gotchas=$(prepare no-gotchas)
replace "$no_gotchas/.agents/skills/verify-counter/references/features/reset.md" "## Gotchas" "## Notes"
gotchas_message="missing the \`## Gotchas\` section"
expect_failure no-gotchas "$gotchas_message"

plain_claude=$(prepare plain-claude)
rm "$plain_claude/.claude/skills/verify-counter"
mkdir -p "$plain_claude/.claude/skills/verify-counter"
expect_failure plain-claude "must be a relative symlink"

no_task=$(prepare no-task)
replace "$no_task/mise.toml" '[tasks."verify:counter"]' '[tasks."verify:renamed"]'
replace "$no_task/.agents/skills/verify-counter/SKILL.md" "mise run verify:counter" "mise run verify:renamed"
expect_failure no-task 'mise.toml has no [tasks."verify:counter"]'

not_json=$(prepare not-json)
replace "$not_json/.agents/skills/verify-counter/scripts/verify.py" 'print(json.dumps(payload, sort_keys=True))' 'print("ok")'
expect_failure not-json "stdout is not one JSON object"

dry_leak=$(prepare dry-leak)
replace "$dry_leak/.agents/skills/verify-counter/scripts/verify.py" 'return emit({"ok": True, "dry_run": True, "action": action})' 'Path("dry-run-leak").write_text("changed"); return emit({"ok": True, "dry_run": True, "action": action})'
expect_failure dry-leak "changed the tree: dry-run-leak"

unknown_ok=$(prepare unknown-ok)
replace "$unknown_ok/.agents/skills/verify-counter/scripts/verify.py" 'return fail(f"unknown subcommand {command!r}", f"{TASK} commands")' 'return emit({"ok": True})'
expect_failure unknown-ok "an unknown command must exit non-zero"

no_alternative=$(prepare no-alternative)
replace "$no_alternative/.agents/skills/verify-counter/scripts/verify.py" 'return fail(f"unknown subcommand {command!r}", f"{TASK} commands")' 'return fail(f"unknown subcommand {command!r}", "")'
expect_failure no-alternative "must report an error and an alternative"

undocumented=$(prepare undocumented)
insert_after "$undocumented/.agents/skills/verify-counter/scripts/verify.py" '    {"name": "commands", "destructive": False, "description": "list the subcommands"},' '    {"name": "snapshot", "destructive": False, "description": "not documented"},'
expect_failure undocumented "SKILL.md does not document the \`snapshot\` subcommand"

several=$(prepare several)
cp -R "$several/.agents/skills/verify-counter" "$several/.agents/skills/verify-second"
expect_failure several "several verification skills found"

cursor=$(prepare cursor)
mkdir -p "$cursor/.cursor/skills"
mv "$cursor/.agents/skills/verify-counter" "$cursor/.cursor/skills/verify-counter"
rm -rf "$cursor/.agents/skills"
expect_failure cursor "belong in .agents/skills, not .cursor/skills"

echo "verify-skill-test: passing fixture, lifecycle cleanup and every failing rule passed"
