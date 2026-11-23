#!/bin/sh

expect_contains() {
	haystack=$1
	needle=$2
	if ! printf '%s\n' "$haystack" | grep -Fq "$needle"; then
		printf 'expected output to contain %s, got:\n%s\n' "$needle" "$haystack" >&2
		return 1
	fi
}

expect_body() {
	if ! grep -Fq -e "$1" "$GH_BODY"; then
		printf 'expected the body to contain %s, got:\n%s\n' "$1" "$(cat "$GH_BODY")" >&2
		return 1
	fi
}

reset_gh_fixtures() {
	: >"$GH_LOG"
	: >"$GH_LABELS"
	: >"$GH_COMMENTS"
}

init_gh_stub() {
	stub_root=$1
	mkdir -p "$stub_root/bin"
	cat >"$stub_root/bin/gh" <<'STUB'
#!/bin/sh
printf '%s\n' "$*" >>"$GH_LOG"
body=
previous=
for argument in "$@"; do
	[ "$previous" = "--body-file" ] && body=$argument
	previous=$argument
done
case "$1 $2" in
"issue view")
	cat "$GH_LABELS"
	;;
"api "*)
	cat "$GH_COMMENTS"
	;;
"issue comment")
	[ -z "${GH_FAIL_COMMENT:-}" ] || exit 1
	cp "$body" "$GH_BODY"
	printf 'https://example.test/issue/6#issuecomment-1\n'
	;;
"issue create")
	cp "$body" "$GH_BODY"
	printf 'https://example.test/issue/7\n'
	;;
"issue edit")
	exit 0
	;;
*)
	exit 1
	;;
esac
STUB
	chmod +x "$stub_root/bin/gh"
	export GH_LOG="$stub_root/log"
	export GH_BODY="$stub_root/body"
	export GH_LABELS="$stub_root/labels"
	export GH_COMMENTS="$stub_root/comments"
	PATH="$stub_root/bin:$PATH"
	export PATH
	reset_gh_fixtures
}
