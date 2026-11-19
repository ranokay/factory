#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
block=$script_dir/block-issue

test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

export GH_LOG="$test_root/log"
export GH_BODY="$test_root/body"
export GH_LABELS="$test_root/labels"
export GH_COMMENTS="$test_root/comments"
: >"$GH_LOG"
: >"$GH_LABELS"
: >"$GH_COMMENTS"

mkdir -p "$test_root/bin"
cat >"$test_root/bin/gh" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"$GH_LOG"
case "$1 $2" in
"issue view")
	cat "$GH_LABELS"
	;;
"api "*)
	cat "$GH_COMMENTS"
	;;
"issue comment")
	[ -z "${GH_FAIL_COMMENT:-}" ] || exit 1
	body=
	previous=
	for argument in "$@"; do
		[ "$previous" = "--body-file" ] && body=$argument
		previous=$argument
	done
	cp "$body" "$GH_BODY"
	printf 'https://example.test/issue/6#issuecomment-1\n'
	;;
"issue edit")
	exit 0
	;;
*)
	exit 1
	;;
esac
EOF
chmod +x "$test_root/bin/gh"
PATH=$test_root/bin:$PATH
export PATH

expect_contains() {
	haystack=$1
	needle=$2
	if ! printf '%s\n' "$haystack" | grep -Fq "$needle"; then
		printf 'expected output to contain %s, got:\n%s\n' "$needle" "$haystack" >&2
		return 1
	fi
}

expect_body() {
	if ! grep -Fq "$1" "$GH_BODY"; then
		printf 'expected the comment body to contain %s, got:\n%s\n' "$1" "$(cat "$GH_BODY")" >&2
		return 1
	fi
}

reset_fixtures() {
	: >"$GH_LOG"
	: >"$GH_LABELS"
	: >"$GH_COMMENTS"
}

case_post() {
	reset_fixtures
	output=$("$block" o/one 6 --reason 'Worker failed the Gate three times' --unblock 'Raise the timeout in mise.toml' --evidence 'https://example.test/evidence' --finding 'F1: the Gate still fails')
	expect_contains "$output" 'https://example.test/issue/6#issuecomment-1'
	expect_body '@ranokay Worker failed the Gate three times'
	expect_body '**Unblock step:** Raise the timeout in mise.toml'
	expect_body '**Last evidence:** https://example.test/evidence'
	expect_body '**Open findings:** F1: the Gate still fails'
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

case_idempotent() {
	reset_fixtures
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
	reset_fixtures
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

case_failure() {
	reset_fixtures
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
idempotent) case_idempotent ;;
missing) case_missing ;;
failure) case_failure ;;
all)
	case_post
	case_idempotent
	case_missing
	case_failure
	;;
*)
	printf 'unknown case: %s\n' "$BLOCK_ISSUE_CASE" >&2
	exit 2
	;;
esac
