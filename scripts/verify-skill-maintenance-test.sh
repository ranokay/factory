#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
python=$(mise -C "$root" which python)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/factory-verify-maintenance.XXXXXX")
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

fail() {
	printf 'verify-skill-maintenance-test: %s\n' "$*" >&2
	exit 1
}

export GH_LOG="$tmp/gh.log"
export GH_STATE="$tmp/gh-state"
mkdir -p "$tmp/remotes" "$tmp/repos" "$tmp/closed-root" "$GH_STATE" "$tmp/bin"
: >"$GH_LOG"

cat >"$tmp/bin/gh" <<'STUB'
#!/bin/sh
set -eu
repo=
state=
all=$*
case "$1 $2" in
"issue list")
	while [ "$#" -gt 0 ]; do
		case $1 in
		--repo) repo=$2; shift 2 ;;
		--state) state=$2; shift 2 ;;
		*) shift ;;
		esac
	done
	file="$GH_STATE/$(printf '%s' "$repo" | tr '/' '_')-$state.json"
	[ -f "$file" ] && cat "$file" || printf '[]\n'
	;;
"issue create")
	while [ "$#" -gt 0 ]; do
		case $1 in
		--repo) repo=$2; shift 2 ;;
		*) shift ;;
		esac
	done
	printf 'create %s\n' "$repo" >>"$GH_LOG"
	printf '%s\n' "$all" >>"$GH_LOG"
	printf 'https://github.com/%s/issues/1\n' "$repo"
	;;
*)
	printf 'gh stub: unexpected call: %s\n' "$*" >&2
	exit 2
	;;
esac
STUB
chmod +x "$tmp/bin/gh"
PATH="$tmp/bin:$PATH"
export PATH

git_config() {
	git -C "$1" config user.name "verify-maintenance test"
	git -C "$1" config user.email "verify-maintenance@example.invalid"
	git -C "$1" config commit.gpgsign false
}

new_remote() {
	git init -q --bare --initial-branch="$2" "$tmp/remotes/$1.git"
}

clone_repo() {
	git clone -q "$tmp/remotes/$1.git" "$2/$1"
	git_config "$2/$1"
}

commit_all() {
	git -C "$1" add -A
	git -C "$1" commit -qm "$2"
	git -C "$1" push -q origin "HEAD:$(git -C "$1" branch --show-current)"
}

write_skill() {
	mkdir -p "$1/.agents/skills/verify-$2"
	printf -- '---\nname: verify-%s\ndescription: fixture\n---\n' "$2" >"$1/.agents/skills/verify-$2/SKILL.md"
}

set_github_origin() {
	git -C "$1" remote set-url origin "https://github.com/ranokay/$2.git"
}

new_remote changed trunk
clone_repo changed "$tmp/repos"
printf 'one\n' >"$tmp/repos/changed/app.txt"
write_skill "$tmp/repos/changed" changed
commit_all "$tmp/repos/changed" "add verify skill"
printf 'two\n' >>"$tmp/repos/changed/app.txt"
commit_all "$tmp/repos/changed" "change the app"
changed_head=$(git -C "$tmp/repos/changed" rev-parse HEAD)
set_github_origin "$tmp/repos/changed" changed

new_remote unchanged main
clone_repo unchanged "$tmp/repos"
printf 'one\n' >"$tmp/repos/unchanged/app.txt"
commit_all "$tmp/repos/unchanged" "change the app"
write_skill "$tmp/repos/unchanged" unchanged
commit_all "$tmp/repos/unchanged" "update verify skill"
set_github_origin "$tmp/repos/unchanged" unchanged

new_remote open main
clone_repo open "$tmp/repos"
printf 'one\n' >"$tmp/repos/open/app.txt"
write_skill "$tmp/repos/open" open
commit_all "$tmp/repos/open" "add verify skill"
printf 'two\n' >>"$tmp/repos/open/app.txt"
commit_all "$tmp/repos/open" "change the app"
set_github_origin "$tmp/repos/open" open
open_head=$(git -C "$tmp/repos/open" rev-parse HEAD)
printf '[{"number":7,"title":"Maintain verify-open skill","body":"Keep this open.\\n\\n<!-- factory:verify-maintenance head=%s -->\\n","closedAt":null}]\n' "$open_head" >"$GH_STATE/ranokay_open-open.json"

new_remote other main
clone_repo other "$tmp/repos"
printf 'one\n' >"$tmp/repos/other/app.txt"
write_skill "$tmp/repos/other" other
commit_all "$tmp/repos/other" "add verify skill"
printf 'two\n' >>"$tmp/repos/other/app.txt"
commit_all "$tmp/repos/other" "change the app"
git -C "$tmp/repos/other" remote set-url origin https://github.com/other/repo.git

new_remote closed trunk
clone_repo closed "$tmp/closed-root"
printf 'one\n' >"$tmp/closed-root/closed/app.txt"
write_skill "$tmp/closed-root/closed" closed
commit_all "$tmp/closed-root/closed" "add verify skill"
printf 'two\n' >>"$tmp/closed-root/closed/app.txt"
commit_all "$tmp/closed-root/closed" "change the app"
set_github_origin "$tmp/closed-root/closed" closed
closed_head=$(git -C "$tmp/closed-root/closed" rev-parse HEAD)
printf '[{"number":9,"title":"Maintain verify-closed skill","body":"Clean pass.\\n\\n<!-- factory:verify-maintenance head=%s -->\\n","closedAt":"2026-11-20T00:00:00Z"}]\n' "$closed_head" >"$GH_STATE/ranokay_closed-closed.json"

new_remote no-head main
clone_repo no-head "$tmp/no-head-root"
printf 'one\n' >"$tmp/no-head-root/no-head/app.txt"
write_skill "$tmp/no-head-root/no-head" no-head
commit_all "$tmp/no-head-root/no-head" "add verify skill"
printf 'two\n' >>"$tmp/no-head-root/no-head/app.txt"
commit_all "$tmp/no-head-root/no-head" "change the app"
set_github_origin "$tmp/no-head-root/no-head" no-head
printf '[{"number":11,"title":"Maintain verify-no-head skill","body":"No head recorded.\\n\\n<!-- factory:verify-maintenance -->\\n","closedAt":"2026-11-20T00:00:00Z"}]\n' >"$GH_STATE/ranokay_no-head-closed.json"

new_remote fetch-fail main
clone_repo fetch-fail "$tmp/fetch-root"
printf 'one\n' >"$tmp/fetch-root/fetch-fail/app.txt"
write_skill "$tmp/fetch-root/fetch-fail" fetch-fail
commit_all "$tmp/fetch-root/fetch-fail" "add verify skill"
printf 'two\n' >>"$tmp/fetch-root/fetch-fail/app.txt"
commit_all "$tmp/fetch-root/fetch-fail" "change the app"
set_github_origin "$tmp/fetch-root/fetch-fail" fetch-fail
git -C "$tmp/fetch-root/fetch-fail" config http.proxy http://127.0.0.1:1
git -C "$tmp/fetch-root/fetch-fail" config https.proxy http://127.0.0.1:1

run_maintenance() {
	uv run --offline --no-project --no-config --python "$python" python "$root/scripts/verify-skill-maintenance.py" "$@"
}

: >"$GH_LOG"
run_maintenance --root "$tmp/repos" --no-fetch --dry-run >"$tmp/dry"
[ ! -s "$GH_LOG" ] || fail "dry-run called the GitHub create boundary"
grep -q 'would file ranokay/changed' "$tmp/dry" || fail "dry-run did not report the changed repo"
grep -q 'unchanged ranokay/unchanged' "$tmp/dry" || fail "dry-run did not skip the unchanged repo"
grep -q 'open ranokay/open' "$tmp/dry" || fail "dry-run did not skip the open issue"
run_maintenance --root "$tmp/closed-root" --no-fetch --dry-run >"$tmp/closed-dry"
grep -q 'unchanged ranokay/closed' "$tmp/closed-dry" || fail "a clean close at the current head still re-filed"

: >"$GH_LOG"
run_maintenance --root "$tmp/repos" --no-fetch >"$tmp/run"
[ "$(grep -c '^create ranokay/changed$' "$GH_LOG" || true)" -eq 1 ] || fail "changed repo did not file exactly one issue"
[ "$(grep -c '^create ' "$GH_LOG" || true)" -eq 1 ] || fail "an unchanged, open-issue or third-party repo filed an issue"
grep -q -- '--label ready-for-agent' "$GH_LOG" || fail "the issue is not ready-for-agent"
grep -q '/maintain-verification-skill' "$GH_LOG" || fail "the issue does not name the maintenance skill"
grep -q "<!-- factory:verify-maintenance head=$changed_head -->" "$GH_LOG" || fail "the issue did not record the filed head"
grep -q "Last maintenance" "$GH_LOG" || fail "the issue does not name the last maintenance"
grep -q "filed ranokay/changed" "$tmp/run" || fail "the command did not report the filed issue"

printf 'three\n' >>"$tmp/closed-root/closed/app.txt"
git -C "$tmp/closed-root/closed" add -A
git -C "$tmp/closed-root/closed" commit -qm "change the app after a clean pass"
git -C "$tmp/closed-root/closed" push -q "$tmp/remotes/closed.git" trunk
git -C "$tmp/closed-root/closed" update-ref refs/remotes/origin/trunk HEAD
closed_after=$(git -C "$tmp/closed-root/closed" rev-parse HEAD)
: >"$GH_LOG"
run_maintenance --root "$tmp/closed-root" --no-fetch >"$tmp/closed-run"
[ "$(grep -c '^create ranokay/closed$' "$GH_LOG" || true)" -eq 1 ] || fail "an app commit after a clean close did not file"
grep -q "<!-- factory:verify-maintenance head=$closed_after -->" "$GH_LOG" || fail "the new issue did not record the new head"

: >"$GH_LOG"
run_maintenance --root "$tmp/no-head-root" --no-fetch >"$tmp/no-head-run" 2>"$tmp/no-head-err"
grep -q 'has no head marker' "$tmp/no-head-err" || fail "a closed marker issue without a head did not warn"
[ "$(grep -c '^create ranokay/no-head$' "$GH_LOG" || true)" -eq 1 ] || fail "a closed marker without a head did not fall back to the skill commit"

: >"$GH_LOG"
if GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/usr/bin/false run_maintenance --root "$tmp/fetch-root" >"$tmp/fetch-out" 2>"$tmp/fetch-err"; then
	cat "$tmp/fetch-out" "$tmp/fetch-err" >&2
	fail "a failed fetch returned success"
fi
grep -q 'git fetch failed' "$tmp/fetch-err" || fail "a failed fetch did not name the error"
[ ! -s "$GH_LOG" ] || fail "a failed fetch filed an issue"

echo "verify-skill-maintenance-test: changed files one issue, unchanged, third-party and clean-close file none, open issue blocks a duplicate"
