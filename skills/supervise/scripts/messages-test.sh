#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
skill_dir=$(dirname "$script_dir")
messages=$skill_dir/references/messages.md
supervise=$skill_dir/SKILL.md
stopped_notice=$script_dir/stopped-notice

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

case_targets() {
	for line in \
		'run-targets set' \
		'run-targets mark' \
		'GLM alone when DeepSeek is absent' \
		'targets.json'; do
		expect_file "$supervise" "$line"
	done
	printf 'ok: the Supervisor records ordered targets for the fallback guard\n'
}

case_waiting() {
	for line in \
		'gh run watch <id> --exit-status' \
		'gh event listen' \
		'scripts/stopped-notice' \
		'checks pending' \
		'resume after checks finish'; do
		expect_file "$supervise" "$line"
		expect_file "$messages" "$line"
	done
	printf '%s\n' 'supervise · notice · task · stopped thread · human decision' 'unblock: ranokay approves' | "$stopped_notice"
	if printf '%s\n' 'supervise · notice · task · stopped thread · checks pending' | "$stopped_notice"; then
		printf 'stopped-notice accepted pending checks\n' >&2
		return 1
	fi
	if printf '%s\n' 'supervise · notice · task · stopped thread · waiting' 'unblock: resume after checks finish' | "$stopped_notice"; then
		printf 'stopped-notice accepted a deferred check wait\n' >&2
		return 1
	fi
	if printf '%s\n' 'supervise · notice · task · stopped thread · required checks are pending' | "$stopped_notice"; then
		printf 'stopped-notice accepted reordered pending-check wording\n' >&2
		return 1
	fi
	if printf '%s\n' 'supervise · notice · task · stopped thread · waiting' 'unblock: resume after checks finish;' | "$stopped_notice"; then
		printf 'stopped-notice accepted a punctuated deferred wait\n' >&2
		return 1
	fi
	if printf '%s\n' 'supervise · notice · task · stopped thread · waiting' 'unblock: resume once checks finish' | "$stopped_notice"; then
		printf 'stopped-notice accepted a reordered deferred wait\n' >&2
		return 1
	fi
	printf 'ok: PR forks wait for checks and stopped notices reject deferred waits\n'
}

case ${MESSAGES_CASE:-all} in
headers) case_headers ;;
objection) case_objection ;;
skill) case_skill ;;
targets) case_targets ;;
waiting) case_waiting ;;
all)
	case_headers
	case_objection
	case_skill
	case_targets
	case_waiting
	;;
*)
	printf 'unknown case: %s\n' "$MESSAGES_CASE" >&2
	exit 2
	;;
esac
