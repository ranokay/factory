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
	refused "2 Runs already active on this machine" "$run_slot" acquire a/three 3
	check_count 2
	printf 'ok: a third repo is refused\n'
}

case_same() {
	"$run_slot" acquire a/one 1 >/dev/null
	refused "a/one already has a Run" "$run_slot" acquire a/one 2
	refused "A/ONE already has a Run" "$run_slot" acquire A/ONE 3
	check_count 1
	printf 'ok: a second slot for the same repo is refused\n'
}

case_release() {
	"$run_slot" acquire a/one 1 >/dev/null
	"$run_slot" acquire a/two 2 >/dev/null
	"$run_slot" set-thread a/one thread-1
	expect_slot a/one 1 thread-1
	touch "$work/a/one/thread.tmp"
	"$run_slot" release a/one
	"$run_slot" acquire a/three 3 >/dev/null
	expect_slot a/three 3 -
	check_count 2
	printf 'ok: release frees a slot\n'
}

case_traversal() {
	refused "invalid repository" "$run_slot" acquire ../evil 1
	check_count 0
	printf 'ok: a traversal is refused\n'
}

case_permission() {
	mkdir -p "$work/a"
	chmod 500 "$work/a"
	refused "cannot create" "$run_slot" acquire a/one 1
	chmod 700 "$work/a"
	check_count 0
	printf 'ok: a permission failure is not a duplicate slot\n'
}

case_concurrent() {
	for n in 1 2 3 4; do
		(
			if "$run_slot" acquire "o$n/r" "$n" >/dev/null 2>"$work/err$n"; then
				status=0
			else
				status=$?
			fi
			printf '%s\n' "$status" >"$work/status$n"
		) &
	done
	wait
	successes=0
	for n in 1 2 3 4; do
		if [ "$(cat "$work/status$n")" -eq 0 ]; then
			successes=$((successes + 1))
		fi
	done
	if [ "$successes" -ne 2 ]; then
		printf 'expected 2 concurrent winners, found %s\n' "$successes" >&2
		return 1
	fi
	check_count 2
	printf 'ok: concurrent acquires admit exactly two\n'
}

case_orphan() {
	"$run_slot" acquire a/one 1 >/dev/null
	mkdir "$work/.machine/2"
	refused "2 Runs already active on this machine" "$run_slot" acquire a/two 2
	"$run_slot" release a/one
	"$run_slot" acquire a/two 2 >/dev/null
	check_count 1
	expect_slot a/two 2 -
	printf 'ok: a crashed machine slot is pruned\n'
}

check_count() {
	actual=$("$run_slot" list | wc -l | tr -d ' ')
	if [ "$actual" -ne "$1" ]; then
		printf 'expected %s slots, found %s\n' "$1" "$actual" >&2
		return 1
	fi
}

refused() {
	expected=$1
	shift
	if "$@" >/dev/null 2>"$work/stderr"; then
		printf 'expected a refusal: %s\n' "$*" >&2
		return 1
	fi
	if ! grep -Fq "$expected" "$work/stderr"; then
		printf 'expected stderr to contain %s, got: %s\n' "$expected" "$(cat "$work/stderr")" >&2
		return 1
	fi
	return 0
}

expect_slot() {
	line=$(printf '%s\t%s\t%s' "$1" "$2" "$3")
	if ! "$run_slot" list | grep -Fq "$line"; then
		printf 'expected a list line for %s\n' "$line" >&2
		return 1
	fi
}

run_case() {
	work=$test_root/$1
	mkdir "$work"
	RUN_SLOT_DIR=$work
	export RUN_SLOT_DIR
	"$1"
}

test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
case ${RUN_SLOT_CASE:-all} in
two) run_case case_two ;;
third) run_case case_third ;;
same) run_case case_same ;;
release) run_case case_release ;;
traversal) run_case case_traversal ;;
permission) run_case case_permission ;;
concurrent) run_case case_concurrent ;;
orphan) run_case case_orphan ;;
all)
	run_case case_two
	run_case case_third
	run_case case_same
	run_case case_release
	run_case case_traversal
	run_case case_permission
	run_case case_concurrent
	run_case case_orphan
	;;
*)
	printf 'unknown case: %s\n' "$RUN_SLOT_CASE" >&2
	exit 2
	;;
esac
