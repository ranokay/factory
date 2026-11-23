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
	if [ "$first" != '**Unblock step:** Raise the timeout in mise.toml' ]; then
		printf 'expected the unblock step as the first content line, got %s\n' "$first" >&2
		return 1
	fi
	printf 'ok: puts the unblock step first\n'
}

case_reason_multiline() {
	reset_gh_fixtures
	if "$block" o/one 6 --reason "$(printf 'one\ntwo')" --unblock 'step' >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal for a multi-line reason\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'must be one line'
	if [ -s "$GH_LOG" ]; then
		printf 'expected no gh calls, got:\n%s\n' "$(cat "$GH_LOG")" >&2
		return 1
	fi
	printf 'ok: refuses a multi-line reason\n'
}

case_trailing_newline() {
	reset_gh_fixtures
	"$block" o/one 6 --reason 'reason' --unblock "$(printf 'step\n')" >/dev/null
	if ! grep -Fxq '**Unblock step:** step' "$GH_BODY"; then
		printf 'expected the exact unblock line, got:\n%s\n' "$(cat "$GH_BODY")" >&2
		return 1
	fi
	printf 'ok: accepts a trailing newline on a one-line value\n'
}

case_evidence_multiline() {
	reset_gh_fixtures
	if "$block" o/one 6 --reason 'reason' --unblock 'step' --evidence "$(printf 'one\ntwo')" >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal for multi-line evidence\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'must be one line'
	printf 'ok: refuses multi-line evidence\n'
}

case_digit_value() {
	reset_gh_fixtures
	"$block" o/one 6 --reason 'reason' --unblock 'step' --evidence '10.0.0.1' >/dev/null
	expect_body '- 10.0.0.1'
	printf 'ok: accepts a digit-leading evidence value\n'
}

case_summary_tag() {
	reset_gh_fixtures
	if "$block" o/one 6 --reason 'reason' --unblock 'step' --detail '</summary>' >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal for a summary tag\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'cannot contain a details block'
	printf 'ok: refuses a summary tag in details\n'
}

case_reason_mention() {
	reset_gh_fixtures
	"$block" o/one 6 --reason 'ping @alice' --unblock 'step' >/dev/null
	expect_body 'ping &#64;alice'
	if grep -Fq '@alice' "$GH_BODY"; then
		printf 'expected the reason mention to be escaped\n' >&2
		return 1
	fi
	printf 'ok: escapes a mention in the reason\n'
}

case_reason_newline() {
	reset_gh_fixtures
	"$block" o/one 6 --reason "$(printf 'reason\n')" --unblock 'step' >/dev/null
	if ! grep -Fxq '@ranokay reason' "$GH_BODY"; then
		printf 'expected the exact reason line, got:\n%s\n' "$(cat "$GH_BODY")" >&2
		return 1
	fi
	printf 'ok: strips a trailing newline from the reason\n'
}

case_leading_marker() {
	reset_gh_fixtures
	if "$block" o/one 6 --reason 'reason' --unblock 'step' --evidence '- https://example.test/evidence' >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal for an evidence list marker\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'must not start with a list marker'
	printf 'ok: refuses an evidence list marker\n'
}

case_detail_breakout() {
	reset_gh_fixtures
	if "$block" o/one 6 --reason 'reason' --unblock 'step' --detail 'before </details> after' >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal for a details breakout\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'cannot contain a details block'
	printf 'ok: refuses a details breakout\n'
}

case_no_detail() {
	reset_gh_fixtures
	"$block" o/one 6 --reason 'reason' --unblock 'step' >/dev/null
	if grep -Fq '<details>' "$GH_BODY"; then
		printf 'expected no details block without --detail\n' >&2
		return 1
	fi
	printf 'ok: omits details without --detail\n'
}

case_empty_evidence() {
	reset_gh_fixtures
	if "$block" o/one 6 --reason 'reason' --unblock 'step' --evidence '' >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal for empty evidence\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'must not be empty'
	if [ -s "$GH_LOG" ]; then
		printf 'expected no gh calls, got:\n%s\n' "$(cat "$GH_LOG")" >&2
		return 1
	fi
	printf 'ok: refuses empty evidence\n'
}

case_finding_marker() {
	reset_gh_fixtures
	if "$block" o/one 6 --reason 'reason' --unblock 'step' --finding '* item' >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal for a finding list marker\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'must not start with a list marker'
	printf 'ok: refuses a finding list marker\n'
}

case_blocker_marker() {
	reset_gh_fixtures
	if "$block" o/one 6 --reason 'reason' --unblock 'step' --detail '<!-- factory:blocker -->' >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal for a marker in details\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'cannot contain an HTML comment'
	printf 'ok: refuses the Blocker marker in details\n'
}

case_detail_attributes() {
	reset_gh_fixtures
	if "$block" o/one 6 --reason 'reason' --unblock 'step' --detail '</details >' >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal for an attribute close tag\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'cannot contain a details block'
	printf 'ok: refuses a details tag variant\n'
}

case_detail_mention() {
	reset_gh_fixtures
	"$block" o/one 6 --reason 'reason' --unblock 'step' --detail 'Context for @alice' >/dev/null
	expect_body 'Context for &#64;alice'
	if grep -Fq '@alice' "$GH_BODY"; then
		printf 'expected the detail mention to be escaped\n' >&2
		return 1
	fi
	printf 'ok: escapes mentions inside details\n'
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
reason_multiline) case_reason_multiline ;;
trailing_newline) case_trailing_newline ;;
evidence_multiline) case_evidence_multiline ;;
digit_value) case_digit_value ;;
summary_tag) case_summary_tag ;;
reason_mention) case_reason_mention ;;
reason_newline) case_reason_newline ;;
leading_marker) case_leading_marker ;;
detail_breakout) case_detail_breakout ;;
detail_mention) case_detail_mention ;;
no_detail) case_no_detail ;;
empty_evidence) case_empty_evidence ;;
finding_marker) case_finding_marker ;;
blocker_marker) case_blocker_marker ;;
detail_attributes) case_detail_attributes ;;
idempotent) case_idempotent ;;
missing) case_missing ;;
invalid_repo) case_invalid_repo ;;
failure) case_failure ;;
all)
	case_post
	case_order
	case_unblock_multiline
	case_reason_multiline
	case_trailing_newline
	case_evidence_multiline
	case_digit_value
	case_summary_tag
	case_reason_mention
	case_reason_newline
	case_leading_marker
	case_detail_breakout
	case_detail_mention
	case_no_detail
	case_empty_evidence
	case_finding_marker
	case_blocker_marker
	case_detail_attributes
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
