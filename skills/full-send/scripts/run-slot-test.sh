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
	refused "cannot create $work/a/one" "$run_slot" acquire a/one 1
	chmod 700 "$work/a"
	check_count 0
	printf 'ok: a permission failure is not a duplicate slot\n'
}

case_concurrent() {
	for n in 1 2 3 4; do
		(
			"$run_slot" acquire "o$n/r" "$n" >/dev/null 2>"$work/err$n"
		) &
	done
	wait
	check_count 2
	printf 'ok: concurrent acquires admit exactly two\n'
}

case_orphan() {
	"$run_slot" acquire a/one 1 >/dev/null
	mkdir "$work/.machine/2"
	touch -t 200001010000 "$work/.machine/2"
	refused "2 Runs already active on this machine" "$run_slot" acquire a/two 2
	"$run_slot" release a/one
	if [ -d "$work/.machine/2" ]; then
		printf 'expected the crashed machine slot to be pruned\n' >&2
		return 1
	fi
	if [ -d "$work/a" ]; then
		printf 'expected the owner directory to be released\n' >&2
		return 1
	fi
	"$run_slot" acquire a/two 2 >/dev/null
	check_count 1
	expect_slot a/two 2 -
	printf 'ok: a crashed machine slot is pruned\n'
}

case_owner_permission() {
	mkdir "$work/.machine"
	chmod 500 "$work"
	refused "cannot create $work/a" "$run_slot" acquire a/one 1
	chmod 700 "$work"
	check_count 0
	printf 'ok: an owner directory failure names the path\n'
}

check_count() {
	slot_list=$("$run_slot" list)
	if [ -n "$slot_list" ]; then
		actual=$(printf '%s\n' "$slot_list" | wc -l | tr -d ' ')
	else
		actual=0
	fi
	if [ "$actual" -ne "$1" ]; then
		printf 'expected %s slots, found %s\n' "$1" "$actual" >&2
		printf 'run-slot list:\n%s\n' "$slot_list" >&2
		printf 'slot directory tree:\n' >&2
		find "$RUN_SLOT_DIR" -ls >&2
		for error_file in "$work"/err1 "$work"/err2 "$work"/err3 "$work"/err4; do
			if [ -f "$error_file" ]; then
				printf 'stderr from %s:\n' "${error_file##*/}" >&2
				cat "$error_file" >&2
			fi
		done
		return 1
	fi
}

refused() {
	expected=$1
	shift
	if "$@" >/dev/null 2>"$stderr_file"; then
		printf 'expected a refusal: %s\n' "$*" >&2
		return 1
	fi
	if ! grep -Fq "$expected" "$stderr_file"; then
		printf 'expected stderr to contain %s, got: %s\n' "$expected" "$(cat "$stderr_file")" >&2
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
stderr_file=$test_root/stderr
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
owner_permission) run_case case_owner_permission ;;
all)
	run_case case_two
	run_case case_third
	run_case case_same
	run_case case_release
	run_case case_traversal
	run_case case_permission
	run_case case_concurrent
	run_case case_orphan
	run_case case_owner_permission
	;;
*)
	printf 'unknown case: %s\n' "$RUN_SLOT_CASE" >&2
	exit 2
	;;
esac
