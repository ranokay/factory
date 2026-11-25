#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
skill_dir=$(dirname "$script_dir")
messages=$skill_dir/references/messages.md
supervise=$skill_dir/SKILL.md

expect_file() {
	if ! grep -Fq -e "$2" "$1"; then
		printf 'expected %s in %s\n' "$2" "$1" >&2
		return 1
	fi
}

case_headers() {
	for header in \
		'supervise · launch' \
		'supervise · plan <n>/2' \
		'supervise · round <n>/4 · reviewed <head SHA> · open <finding IDs, or none>' \
		'supervise · answers · after <head SHA>' \
		'supervise · approved <head SHA>' \
		'supervise · wake · finding <ID> · <head SHA>' \
		'supervise · wake · protected <PR URL>' \
		'supervise · wake · done <PR URL> · <head SHA>' \
		'supervise · objection <reason>' \
		'supervise · ruling objection · proceed|reshape|park · <reason>' \
		'supervise · ruling <ID> · fix|drop|escalate · <reason>' \
		'supervise · hold <PR URL> · waiting for ranokay' \
		'supervise · merge <PR URL> · <head SHA>' \
		'supervise · block <PR URL> · <reason>'; do
		expect_file "$messages" "$header"
	done
	printf 'ok: every header, including the objection, is documented\n'
}

case_objection() {
	for line in \
		'## Objection' \
		'supervise · objection <reason>' \
		'proceed' \
		'reshape' \
		'park' \
		'not a Blocker' \
		'does not count as an attempt'; do
		expect_file "$messages" "$line"
	done
	printf 'ok: the objection has its own header and ruling\n'
}

case_skill() {
	for line in \
		'supervise · objection <reason>' \
		'proceed' \
		'reshape' \
		'park' \
		'not a Blocker' \
		'does not count as an attempt'; do
		expect_file "$supervise" "$line"
	done
	printf 'ok: the Supervisor rules objections and does not count them as attempts\n'
}

case ${MESSAGES_CASE:-all} in
headers) case_headers ;;
objection) case_objection ;;
skill) case_skill ;;
all)
	case_headers
	case_objection
	case_skill
	;;
*)
	printf 'unknown case: %s\n' "$MESSAGES_CASE" >&2
	exit 2
	;;
esac
