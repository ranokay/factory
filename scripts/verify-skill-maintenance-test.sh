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
export GH_QUERY_LOG="$tmp/gh-queries.log"
export GH_STATE="$tmp/gh-state"
mkdir -p "$tmp/remotes" "$tmp/repos" "$tmp/closed-root" "$GH_STATE" "$tmp/bin"
: >"$GH_LOG"
: >"$GH_QUERY_LOG"

cat >"$tmp/bin/gh" <<'STUB'
#!/bin/sh
set -eu
repo=
all=$*
case "$1 $2" in
"api -X")
	page=1
	per_page=
	query=
	while [ "$#" -gt 0 ]; do
		case $1 in
		q=*) query=${1#q=} ;;
		per_page=*) per_page=${1#per_page=} ;;
		page=*) page=${1#page=} ;;
		esac
		shift
	done
	printf 'search %s per_page %s page %s\n' "$query" "$per_page" "$page" >>"$GH_QUERY_LOG"
	repo=${query#repo:}
	repo=${repo%% *}
	state=open
	case $query in *"state:closed"*) state=closed ;; esac
	file="$GH_STATE/$(printf '%s' "$repo" | tr '/' '_')-$state-page$page.json"
	[ -f "$file" ] && cat "$file" || printf '{"items":[],"total_count":0}\n'
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

set_origin_head() {
	git -C "$1" symbolic-ref refs/remotes/origin/HEAD "refs/remotes/origin/$2"
}

write_issue_page() {
	python3 - "$GH_STATE" "$1" "$2" "$3" "$4" "$5" <<'PAGE'
import json
import sys
from pathlib import Path

root, repo, state, number, title, body = sys.argv[1:7]
closed = None if state == "open" else "2026-11-20T00:00:00Z"
item = {"number": int(number), "title": title, "body": body, "closed_at": closed}
path = Path(root) / f"{repo.replace('/', '_')}-{state}-page1.json"
path.write_text(json.dumps({"total_count": 1, "items": [item]}))
PAGE
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
set_origin_head "$tmp/repos/changed" trunk

new_remote unchanged main
clone_repo unchanged "$tmp/repos"
printf 'one\n' >"$tmp/repos/unchanged/app.txt"
commit_all "$tmp/repos/unchanged" "change the app"
write_skill "$tmp/repos/unchanged" unchanged
commit_all "$tmp/repos/unchanged" "update verify skill"
set_github_origin "$tmp/repos/unchanged" unchanged
set_origin_head "$tmp/repos/unchanged" main

new_remote open main
clone_repo open "$tmp/repos"
printf 'one\n' >"$tmp/repos/open/app.txt"
write_skill "$tmp/repos/open" open
commit_all "$tmp/repos/open" "add verify skill"
printf 'two\n' >>"$tmp/repos/open/app.txt"
commit_all "$tmp/repos/open" "change the app"
set_github_origin "$tmp/repos/open" open
set_origin_head "$tmp/repos/open" main
open_head=$(git -C "$tmp/repos/open" rev-parse HEAD)
write_issue_page "ranokay/open" open 7 "Maintain verify-open skill" "Keep this open. <!-- factory:verify-maintenance head=$open_head -->"

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
set_origin_head "$tmp/closed-root/closed" trunk
closed_head=$(git -C "$tmp/closed-root/closed" rev-parse HEAD)
write_issue_page "ranokay/closed" closed 9 "Maintain verify-closed skill" "Clean pass. <!-- factory:verify-maintenance head=$closed_head -->"

new_remote no-head main
clone_repo no-head "$tmp/no-head-root"
printf 'one\n' >"$tmp/no-head-root/no-head/app.txt"
write_skill "$tmp/no-head-root/no-head" no-head
commit_all "$tmp/no-head-root/no-head" "add verify skill"
printf 'two\n' >>"$tmp/no-head-root/no-head/app.txt"
commit_all "$tmp/no-head-root/no-head" "change the app"
set_github_origin "$tmp/no-head-root/no-head" no-head
set_origin_head "$tmp/no-head-root/no-head" main
write_issue_page "ranokay/no-head" closed 11 "Maintain verify-no-head skill" "No head recorded. <!-- factory:verify-maintenance -->"

new_remote no-default main
clone_repo no-default "$tmp/no-default-root"
printf 'one\n' >"$tmp/no-default-root/no-default/app.txt"
commit_all "$tmp/no-default-root/no-default" "add the app"
write_skill "$tmp/no-default-root/no-default" no-default
commit_all "$tmp/no-default-root/no-default" "add verify skill"
git -C "$tmp/no-default-root/no-default" checkout -qb feature
printf 'two\n' >>"$tmp/no-default-root/no-default/app.txt"
commit_all "$tmp/no-default-root/no-default" "feature work"
git -C "$tmp/no-default-root/no-default" fetch -q origin
git -C "$tmp/no-default-root/no-default" branch --set-upstream-to=origin/feature feature
set_github_origin "$tmp/no-default-root/no-default" no-default
set_origin_head "$tmp/no-default-root/no-default" main
git -C "$tmp/no-default-root/no-default" remote set-head origin -d

new_remote fetch-fail main
clone_repo fetch-fail "$tmp/fetch-root"
printf 'one\n' >"$tmp/fetch-root/fetch-fail/app.txt"
write_skill "$tmp/fetch-root/fetch-fail" fetch-fail
commit_all "$tmp/fetch-root/fetch-fail" "add verify skill"
printf 'two\n' >>"$tmp/fetch-root/fetch-fail/app.txt"
commit_all "$tmp/fetch-root/fetch-fail" "change the app"
set_github_origin "$tmp/fetch-root/fetch-fail" fetch-fail
set_origin_head "$tmp/fetch-root/fetch-fail" main
git -C "$tmp/fetch-root/fetch-fail" config http.proxy http://127.0.0.1:1
git -C "$tmp/fetch-root/fetch-fail" config https.proxy http://127.0.0.1:1

new_remote stale trunk
clone_repo stale "$tmp/stale-root"
printf 'one\n' >"$tmp/stale-root/stale/app.txt"
commit_all "$tmp/stale-root/stale" "add the app"
stale_base=$(git -C "$tmp/stale-root/stale" rev-parse HEAD)
write_skill "$tmp/stale-root/stale" stale
commit_all "$tmp/stale-root/stale" "add verify skill"
printf 'two\n' >>"$tmp/stale-root/stale/app.txt"
commit_all "$tmp/stale-root/stale" "change the app"
stale_head=$(git -C "$tmp/stale-root/stale" rev-parse HEAD)
set_github_origin "$tmp/stale-root/stale" stale
set_origin_head "$tmp/stale-root/stale" trunk
git -C "$tmp/stale-root/stale" reset -q --hard "$stale_base"
git -C "$tmp/stale-root/stale" update-ref -d refs/remotes/origin/trunk
git -C "$tmp/stale-root/stale" remote set-url origin "git@github.com:ranokay/stale.git"
printf '#!/bin/sh\nexec git-upload-pack "%s"\n' "$tmp/remotes/stale.git" >"$tmp/bin/stale-ssh"
chmod +x "$tmp/bin/stale-ssh"

new_remote paged main
clone_repo paged "$tmp/paged-root"
printf 'one\n' >"$tmp/paged-root/paged/app.txt"
write_skill "$tmp/paged-root/paged" paged
commit_all "$tmp/paged-root/paged" "add verify skill"
printf 'two\n' >>"$tmp/paged-root/paged/app.txt"
commit_all "$tmp/paged-root/paged" "change the app"
paged_head=$(git -C "$tmp/paged-root/paged" rev-parse HEAD)
set_github_origin "$tmp/paged-root/paged" paged
set_origin_head "$tmp/paged-root/paged" main
python3 - "$GH_STATE" "$paged_head" <<'PAGED'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
head = sys.argv[2]


def item(number, body):
	return {"number": number, "title": f"Maintain verification skills ({number})", "body": body, "closed_at": None}


for index, numbers in enumerate([range(1, 101), range(101, 201), range(201, 251)], start=1):
	items = [item(number, "unrelated maintenance text") for number in numbers]
	if index == 3:
		marker = f"Run `/maintain-verification-skill`.\n\n<!-- factory:verify-maintenance head={head} -->\n"
		items.insert(0, item(999, marker))
	path = root / f"ranokay_paged-open-page{index}.json"
	path.write_text(json.dumps({"total_count": 251, "items": items}))
PAGED

new_remote flooded main
clone_repo flooded "$tmp/flooded-root"
printf 'one\n' >"$tmp/flooded-root/flooded/app.txt"
write_skill "$tmp/flooded-root/flooded" flooded
commit_all "$tmp/flooded-root/flooded" "add verify skill"
printf 'two\n' >>"$tmp/flooded-root/flooded/app.txt"
commit_all "$tmp/flooded-root/flooded" "change the app"
set_github_origin "$tmp/flooded-root/flooded" flooded
set_origin_head "$tmp/flooded-root/flooded" main
python3 - "$GH_STATE" <<'FLOODED'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
for page in range(1, 11):
	items = [
		{"number": page * 1000 + number, "title": f"Issue {number}", "body": "unrelated", "closed_at": "2026-11-20T00:00:00Z"}
		for number in range(100)
	]
	(root / f"ranokay_flooded-closed-page{page}.json").write_text(json.dumps({"total_count": 1000, "items": items}))
FLOODED

new_remote partial main
clone_repo partial "$tmp/partial-root"
printf 'one\n' >"$tmp/partial-root/partial/app.txt"
write_skill "$tmp/partial-root/partial" partial
commit_all "$tmp/partial-root/partial" "add verify skill"
printf 'two\n' >>"$tmp/partial-root/partial/app.txt"
commit_all "$tmp/partial-root/partial" "change the app"
set_github_origin "$tmp/partial-root/partial" partial
set_origin_head "$tmp/partial-root/partial" main
python3 - "$GH_STATE" <<'PARTIAL'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
item = {"number": 1, "title": "Unrelated", "body": "unrelated", "closed_at": None}
(root / "ranokay_partial-open-page1.json").write_text(
	json.dumps({"total_count": 1, "incomplete_results": True, "items": [item]})
)
PARTIAL

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
GIT_SSH_COMMAND="$tmp/bin/stale-ssh" run_maintenance --root "$tmp/stale-root" >"$tmp/stale-run"
[ "$(grep -c '^create ranokay/stale$' "$GH_LOG" || true)" -eq 1 ] || fail "a stale checkout did not file from the fetched default branch"
grep -q "<!-- factory:verify-maintenance head=$stale_head -->" "$GH_LOG" || fail "the stale checkout filed a stale head"
[ ! -d "$tmp/stale-root/stale/.agents/skills/verify-stale" ] || fail "discovery checked the skill out into the worktree"
[ -z "$(git -C "$tmp/stale-root/stale" status --porcelain)" ] || fail "discovery dirtied the worktree"
git -C "$tmp/stale-root/stale" rev-parse --verify --quiet refs/remotes/origin/trunk >/dev/null || fail "the maintenance run did not fetch"

: >"$GH_LOG"
if run_maintenance --root "$tmp/no-default-root" --no-fetch --dry-run >"$tmp/no-default-out" 2>"$tmp/no-default-err"; then
	cat "$tmp/no-default-out" "$tmp/no-default-err" >&2
	fail "a clone without origin/HEAD filed maintenance for its feature branch"
fi
grep -q 'cannot resolve origin/HEAD' "$tmp/no-default-err" || fail "a missing origin/HEAD did not name the problem"
grep -q 'git remote set-head origin --auto' "$tmp/no-default-err" || fail "a missing origin/HEAD did not print the fix"
[ ! -s "$GH_LOG" ] || fail "a missing origin/HEAD called the GitHub create boundary"

: >"$GH_LOG"
: >"$GH_QUERY_LOG"
run_maintenance --root "$tmp/paged-root" --no-fetch --dry-run >"$tmp/paged-dry"
grep -q 'open ranokay/paged' "$tmp/paged-dry" || fail "a marker issue past the first page was not found"
if grep -q 'would file ranokay/paged' "$tmp/paged-dry"; then
	fail "a marker issue past the first page did not block a duplicate"
fi
grep -q '"factory:verify-maintenance"' "$GH_QUERY_LOG" || fail "the marker was not sent as a quoted search phrase"
grep -q 'repo:ranokay/paged' "$GH_QUERY_LOG" || fail "the search did not scope to the repository"
grep -q 'is:issue' "$GH_QUERY_LOG" || fail "the search did not ask for issues"
grep -q 'state:open' "$GH_QUERY_LOG" || fail "the search did not ask for the open state"
grep -q 'page 3' "$GH_QUERY_LOG" || fail "the search stopped before the last page"
: >"$GH_LOG"
run_maintenance --root "$tmp/paged-root" --no-fetch >"$tmp/paged-run"
[ ! -s "$GH_LOG" ] || fail "a marker issue past the first page allowed a duplicate filing"

: >"$GH_LOG"
: >"$GH_QUERY_LOG"
if run_maintenance --root "$tmp/flooded-root" --no-fetch --dry-run >"$tmp/flooded-out" 2>"$tmp/flooded-err"; then
	cat "$tmp/flooded-out" "$tmp/flooded-err" >&2
	fail "a search that filled every page returned success"
fi
grep -q 'filled all 10 pages' "$tmp/flooded-err" || fail "a search that filled every page did not fail loudly"
grep -q 'per_page 100' "$GH_QUERY_LOG" || fail "the search did not ask for 100 results per page"
grep -q 'page 10' "$GH_QUERY_LOG" || fail "the search did not page through every result"
[ ! -s "$GH_LOG" ] || fail "a search that filled every page filed an issue"

: >"$GH_LOG"
if run_maintenance --root "$tmp/partial-root" --no-fetch >"$tmp/partial-out" 2>"$tmp/partial-err"; then
	cat "$tmp/partial-out" "$tmp/partial-err" >&2
	fail "an incomplete search returned success"
fi
grep -q 'returned incomplete results' "$tmp/partial-err" || fail "an incomplete search did not fail loudly"
[ ! -s "$GH_LOG" ] || fail "an incomplete search filed an issue"

: >"$GH_LOG"
if GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/usr/bin/false run_maintenance --root "$tmp/fetch-root" --dry-run >"$tmp/fetch-out" 2>"$tmp/fetch-err"; then
	cat "$tmp/fetch-out" "$tmp/fetch-err" >&2
	fail "a failed fetch returned success"
fi
grep -q 'git fetch failed' "$tmp/fetch-err" || fail "a failed fetch did not name the error"
[ ! -s "$GH_LOG" ] || fail "a failed fetch filed an issue"

echo "verify-skill-maintenance-test: changed files one issue, unchanged, third-party and clean-close file none, open issue blocks a duplicate including past the first page"
