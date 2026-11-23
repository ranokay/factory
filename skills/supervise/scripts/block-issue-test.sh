#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
block=$script_dir/block-issue

test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

# shellcheck source=skills/supervise/scripts/test-lib.sh
# shellcheck disable=SC1091
. "$script_dir/test-lib.sh"
init_gh_stub "$test_root"

first_content() {
	awk '/^## Blocker$/ { heading = 1; next } heading && NF { print; exit }' "$GH_BODY"
}

case_post() {
	reset_gh_fixtures
	output=$("$block" o/one 6 --reason 'Worker failed the Gate three times' --unblock 'Raise the timeout in mise.toml' --evidence 'https://example.test/evidence' --finding 'F1: the Gate still fails' --detail 'The Gate log is attached to the run.')
	expect_contains "$output" 'https://example.test/issue/6#issuecomment-1'
	expect_body '@ranokay Worker failed the Gate three times'
	expect_body '**Unblock step:** Raise the timeout in mise.toml'
	expect_body '**Last evidence:**'
	expect_body '- https://example.test/evidence'
	expect_body '**Open findings:**'
	expect_body '- F1: the Gate still fails'
	expect_body '<summary>Details</summary>'
	expect_body 'The Gate log is attached to the run.'
	expect_body '<!-- factory:blocker -->'
	if ! head -n 1 "$GH_LOG" | grep -Fq 'issue view 6 -R o/one --json labels --jq .labels[].name'; then
		printf 'expected the label lookup first, got:\n%s\n' "$(cat "$GH_LOG")" >&2
		return 1
	fi
	if ! sed -n '2p' "$GH_LOG" | grep -Fq 'issue comment 6 -R o/one'; then
		printf 'expected the comment before the label, got:\n%s\n' "$(cat "$GH_LOG")" >&2
		return 1
	fi
	if ! sed -n '3p' "$GH_LOG" | grep -Fq 'issue edit 6 -R o/one --add-label blocked'; then
		printf 'expected the label after the comment, got:\n%s\n' "$(cat "$GH_LOG")" >&2
		return 1
	fi
	printf 'ok: posts one comment, then the blocked label\n'
}

case_order() {
	reset_gh_fixtures
	"$block" o/one 6 --reason 'Worker failed the Gate' --unblock 'Raise the timeout in mise.toml' --evidence 'https://example.test/evidence' --finding 'F1: the Gate still fails' >/dev/null
	first=$(first_content)
	expect_contains "$first" '**Unblock step:** Raise the timeout in mise.toml'
	printf 'ok: puts the unblock step first\n'
}

case_unblock_multiline() {
	reset_gh_fixtures
	if "$block" o/one 6 --reason 'reason' --unblock "$(printf 'one\ntwo')" >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal for a multi-line unblock step\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'must be one line'
	if [ -s "$GH_LOG" ]; then
		printf 'expected no gh calls, got:\n%s\n' "$(cat "$GH_LOG")" >&2
		return 1
	fi
	printf 'ok: refuses a multi-line unblock step\n'
}

case_idempotent() {
	reset_gh_fixtures
	printf 'blocked\n' >"$GH_LABELS"
	printf '%s\n' 'https://example.test/issue/6#issuecomment-0' >"$GH_COMMENTS"
	output=$("$block" o/one 6 --reason 'again' --unblock 'again')
	expect_contains "$output" 'https://example.test/issue/6#issuecomment-0'
	if grep -Fq 'issue comment' "$GH_LOG"; then
		printf 'expected no second Blocker comment, got:\n%s\n' "$(cat "$GH_LOG")" >&2
		return 1
	fi
	printf 'ok: an already blocked issue keeps its one comment\n'
}

case_missing() {
	reset_gh_fixtures
	if "$block" o/one 6 --reason 'only a reason' >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal without --unblock\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'missing --unblock'
	if [ -s "$GH_LOG" ]; then
		printf 'expected no gh calls, got:\n%s\n' "$(cat "$GH_LOG")" >&2
		return 1
	fi
	printf 'ok: refuses a Blocker without an unblock step\n'
}

case_invalid_repo() {
	reset_gh_fixtures
	if "$block" ../evil 6 --reason 'reason' --unblock 'step' >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal for a traversal\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'invalid repository'
	if [ -s "$GH_LOG" ]; then
		printf 'expected no gh calls, got:\n%s\n' "$(cat "$GH_LOG")" >&2
		return 1
	fi
	printf 'ok: refuses a repository traversal\n'
}

case_failure() {
	reset_gh_fixtures
	if GH_FAIL_COMMENT=1 "$block" o/one 6 --reason 'reason' --unblock 'step' >/dev/null 2>&1; then
		printf 'expected a failed comment to fail the script\n' >&2
		return 1
	fi
	if grep -Fq 'issue edit' "$GH_LOG"; then
		printf 'expected no label after a failed comment, got:\n%s\n' "$(cat "$GH_LOG")" >&2
		return 1
	fi
	printf 'ok: a failed comment stops before the label\n'
}

case ${BLOCK_ISSUE_CASE:-all} in
post) case_post ;;
order) case_order ;;
unblock_multiline) case_unblock_multiline ;;
idempotent) case_idempotent ;;
missing) case_missing ;;
invalid_repo) case_invalid_repo ;;
failure) case_failure ;;
all)
	case_post
	case_order
	case_unblock_multiline
	case_idempotent
	case_missing
	case_invalid_repo
	case_failure
	;;
*)
	printf 'unknown case: %s\n' "$BLOCK_ISSUE_CASE" >&2
	exit 2
	;;
esac
