#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
file_promotion=$script_dir/file-promotion

test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

export GH_LOG="$test_root/log"
export GH_BODY="$test_root/body"
: >"$GH_LOG"

mkdir -p "$test_root/bin"
cat >"$test_root/bin/gh" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"$GH_LOG"
case "$1 $2" in
"issue create")
	body=
	previous=
	for argument in "$@"; do
		[ "$previous" = "--body-file" ] && body=$argument
		previous=$argument
	done
	cp "$body" "$GH_BODY"
	printf 'https://example.test/issue/7\n'
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
		printf 'expected the issue body to contain %s, got:\n%s\n' "$1" "$(cat "$GH_BODY")" >&2
		return 1
	fi
}

case_gate_check() {
	: >"$GH_LOG"
	output=$("$file_promotion" o/one --title 'Gate skipped tests' --correction 'Skipped tests escape Gates.' --gate-check 'A universal-Profile hk step fails on skip markers.' --label needs-triage)
	expect_contains "$output" 'https://example.test/issue/7'
	expect_body '### Correction'
	expect_body 'Skipped tests escape Gates.'
	expect_body '### Gate check'
	expect_body 'A universal-Profile hk step fails on skip markers.'
	if ! grep -Fq 'issue create -R o/one --title Gate skipped tests' "$GH_LOG"; then
		printf 'expected an issue create with the title, got:\n%s\n' "$(cat "$GH_LOG")" >&2
		return 1
	fi
	if ! grep -Fq -- '--label promotion --label needs-triage' "$GH_LOG"; then
		printf 'expected promotion and needs-triage labels, got:\n%s\n' "$(cat "$GH_LOG")" >&2
		return 1
	fi
	printf 'ok: names a Gate check and applies the labels\n'
}

case_no_check() {
	: >"$GH_LOG"
	"$file_promotion" o/one --title 'Explicit exception' --correction 'A person must decide.' --no-check 'No deterministic check exists for this decision.' >/dev/null
	expect_body 'No check possible: No deterministic check exists for this decision.'
	printf 'ok: states why no check is possible\n'
}

case_neither() {
	: >"$GH_LOG"
	if "$file_promotion" o/one --title 'No check' --correction 'Missing the field.' >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal without a Gate check\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'missing --gate-check or --no-check'
	if [ -s "$GH_LOG" ]; then
		printf 'expected no issue, got:\n%s\n' "$(cat "$GH_LOG")" >&2
		return 1
	fi
	printf 'ok: refuses a promotion with no Gate check and no reason\n'
}

case_both() {
	: >"$GH_LOG"
	if "$file_promotion" o/one --title 'Both' --correction 'Both fields.' --gate-check 'a check' --no-check 'a reason' >/dev/null 2>"$test_root/err"; then
		printf 'expected a refusal with both fields\n' >&2
		return 1
	fi
	expect_contains "$(cat "$test_root/err")" 'use either --gate-check or --no-check'
	printf 'ok: refuses both fields at once\n'
}

case ${FILE_PROMOTION_CASE:-all} in
gate_check) case_gate_check ;;
no_check) case_no_check ;;
neither) case_neither ;;
both) case_both ;;
all)
	case_gate_check
	case_no_check
	case_neither
	case_both
	;;
*)
	printf 'unknown case: %s\n' "$FILE_PROMOTION_CASE" >&2
	exit 2
	;;
esac
