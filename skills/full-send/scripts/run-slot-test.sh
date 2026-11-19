#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
run_slot=$script_dir/run-slot

case_two() {
	"$run_slot" acquire a/one 1 >/dev/null
	"$run_slot" acquire a/two 2 >/dev/null
	check_count 2
	printf 'ok: two slots succeed\n'
}

case_third() {
	"$run_slot" acquire a/one 1 >/dev/null
	"$run_slot" acquire a/two 2 >/dev/null
	refused "$run_slot" acquire a/three 3
	check_count 2
	printf 'ok: a third repo is refused\n'
}

case_same() {
	"$run_slot" acquire a/one 1 >/dev/null
	refused "$run_slot" acquire a/one 2
	check_count 1
	printf 'ok: a second slot for the same repo is refused\n'
}

case_release() {
	"$run_slot" acquire a/one 1 >/dev/null
	"$run_slot" acquire a/two 2 >/dev/null
	"$run_slot" release a/one
	"$run_slot" acquire a/three 3 >/dev/null
	check_count 2
	printf 'ok: release frees a slot\n'
}

case_traversal() {
	refused "$run_slot" acquire ../evil 1
	check_count 0
	printf 'ok: a traversal is refused\n'
}

check_count() {
	actual=$("$run_slot" list | wc -l | tr -d ' ')
	if [ "$actual" -ne "$1" ]; then
		printf 'expected %s slots, found %s\n' "$1" "$actual" >&2
		return 1
	fi
}

refused() {
	if "$@" >/dev/null 2>&1; then
		printf 'expected a refusal: %s\n' "$*" >&2
		return 1
	fi
	return 0
}

run_case() {
	work=$(mktemp -d)
	RUN_SLOT_DIR=$work
	export RUN_SLOT_DIR
	if "$1"; then
		status=0
	else
		status=$?
	fi
	rm -rf "$work"
	return "$status"
}

case ${RUN_SLOT_CASE:-all} in
two) run_case case_two ;;
third) run_case case_third ;;
same) run_case case_same ;;
release) run_case case_release ;;
traversal) run_case case_traversal ;;
all)
	run_case case_two
	run_case case_third
	run_case case_same
	run_case case_release
	run_case case_traversal
	;;
*)
	printf 'unknown case: %s\n' "$RUN_SLOT_CASE" >&2
	exit 2
	;;
esac
