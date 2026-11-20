#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
receipt=$script_dir/process-receipt

test_root=$(mktemp -d)
sleeper=
stop_sleeper() {
	if [ -n "$sleeper" ]; then
		pkill -P "$sleeper" 2>/dev/null || true
		kill "$sleeper" 2>/dev/null || true
		wait "$sleeper" 2>/dev/null || true
		sleeper=
	fi
}
trap 'stop_sleeper; rm -rf "$test_root"' EXIT

# shellcheck source=skills/supervise/scripts/test-lib.sh
# shellcheck disable=SC1091
. "$script_dir/test-lib.sh"

export XDG_STATE_HOME="$test_root/state"
work=$test_root/work
mkdir -p "$work" "$test_root/bin"
sims=$test_root/sims
: >"$sims"
cat >"$test_root/bin/xcrun" <<'STUB'
#!/bin/sh
[ -z "${XCRUN_FAIL:-}" ] || exit 1
cat "${XCRUN_OUT:?}"
STUB
chmod +x "$test_root/bin/xcrun"
XCRUN_OUT=$sims
export XCRUN_OUT
PATH=$test_root/bin:$PATH
export PATH

wait_for_check_status() {
	expected=$1
	n=0
	while [ "$n" -lt 50 ]; do
		if "$receipt" check "$work" >/dev/null 2>&1; then
			actual=0
		else
			actual=1
		fi
		[ "$actual" = "$expected" ] && return 0
		sleep 0.1
		n=$((n + 1))
	done
	printf 'timed out waiting for check to exit %s\n' "$expected" >&2
	return 1
}

case_clean() {
	"$receipt" baseline "$work" >/dev/null
	output=$(cd "$work" && "$receipt" check "$work")
	expect_contains "$output" 'processes: none'
	expect_contains "$output" 'simulators: none'
	printf 'ok: a clean worktree prints none, including its own shell\n'
}

case_cwd() {
	"$receipt" baseline "$work" >/dev/null
	(cd "$work" && exec sh -c 'sleep 30') 2>/dev/null &
	sleeper=$!
	if ! wait_for_check_status 1; then
		return 1
	fi
	if output=$("$receipt" check "$work"); then
		printf 'expected a running process to fail the receipt\n' >&2
		return 1
	fi
	expect_contains "$output" 'sleep'
	stop_sleeper
	wait_for_check_status 0
	printf 'ok: a process with its cwd in the worktree is reported\n'
}

case_argv() {
	"$receipt" baseline "$work" >/dev/null
	sh -c 'sleep 30; :' "$work/worker" 2>/dev/null &
	sleeper=$!
	if ! wait_for_check_status 1; then
		return 1
	fi
	if output=$("$receipt" check "$work"); then
		printf 'expected an argv match to fail the receipt\n' >&2
		return 1
	fi
	expect_contains "$output" 'sleep'
	stop_sleeper
	wait_for_check_status 0
	printf 'ok: a process whose command line names the worktree is reported\n'
}

case_no_baseline() {
	rm -rf "$XDG_STATE_HOME"
	if output=$("$receipt" check "$work" 2>&1); then
		printf 'expected a refusal without a baseline\n' >&2
		return 1
	fi
	expect_contains "$output" 'no baseline'
	printf 'ok: check refuses without a baseline\n'
}

case_simulator() {
	printf '    iPhone 16 (AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA) (Booted)\n' >"$sims"
	"$receipt" baseline "$work" >/dev/null
	output=$("$receipt" check "$work")
	expect_contains "$output" 'simulator AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA: pre-existing'
	printf '    iPad (BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB) (Booted)\n' >"$sims"
	if output=$("$receipt" check "$work" 2>&1); then
		printf 'expected a newly booted simulator to fail the receipt\n' >&2
		return 1
	fi
	expect_contains "$output" 'simulator BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB: new'
	printf 'ok: a simulator booted after the baseline fails the receipt\n'
}

case_simulator_unknown() {
	: >"$sims"
	"$receipt" baseline "$work" >/dev/null
	export XCRUN_FAIL=1
	if output=$("$receipt" check "$work" 2>&1); then
		printf 'expected a failed xcrun to fail the receipt\n' >&2
		unset XCRUN_FAIL
		return 1
	fi
	expect_contains "$output" 'simulators: unknown'
	if XCRUN_FAIL=1 "$receipt" baseline "$work" >/dev/null 2>&1; then
		printf 'expected a failed xcrun to fail the baseline\n' >&2
		unset XCRUN_FAIL
		return 1
	fi
	unset XCRUN_FAIL
	printf 'ok: a failed xcrun is reported instead of none\n'
}

case_git_state() {
	rm -rf "$work"
	git init -q "$work"
	"$receipt" baseline "$work" >/dev/null
	if [ ! -f "$work/.git/process-receipt" ]; then
		printf 'expected the baseline inside the git directory\n' >&2
		return 1
	fi
	output=$(cd "$work" && "$receipt" check "$work")
	expect_contains "$output" 'processes: none'
	printf 'ok: the baseline lives in the git directory and dies with the worktree\n'
}

case ${PROCESS_RECEIPT_CASE:-all} in
clean) case_clean ;;
cwd) case_cwd ;;
argv) case_argv ;;
no_baseline) case_no_baseline ;;
simulator) case_simulator ;;
simulator_unknown) case_simulator_unknown ;;
git_state) case_git_state ;;
all)
	case_clean
	case_cwd
	case_argv
	case_no_baseline
	case_simulator
	case_simulator_unknown
	case_git_state
	;;
*)
	printf 'unknown case: %s\n' "$PROCESS_RECEIPT_CASE" >&2
	exit 2
	;;
esac
