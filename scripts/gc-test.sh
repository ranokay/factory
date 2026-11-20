#!/bin/sh
set -eu
root=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export HOME="$tmp/home"
mkdir -p "$HOME/.t3/worktrees/example" "$tmp/remote.git"
git init -q --bare --initial-branch=main "$tmp/remote.git"
git clone -q "$tmp/remote.git" "$tmp/repo"
git -C "$tmp/repo" config user.name Test
git -C "$tmp/repo" config user.email test@example.com
printf 'base\n' >"$tmp/repo/file"
git -C "$tmp/repo" add file
GIT_AUTHOR_DATE="2020-01-01T00:00:00Z" GIT_COMMITTER_DATE="2020-01-01T00:00:00Z" git -C "$tmp/repo" commit -qm base
git -C "$tmp/repo" push -q origin HEAD
git -C "$tmp/repo" worktree add -qb unpushed "$HOME/.t3/worktrees/example/unpushed"
git -C "$HOME/.t3/worktrees/example/unpushed" config user.name Test
git -C "$HOME/.t3/worktrees/example/unpushed" config user.email test@example.com
printf 'work\n' >>"$HOME/.t3/worktrees/example/unpushed/file"
git -C "$HOME/.t3/worktrees/example/unpushed" commit -qam unpushed
git -C "$tmp/repo" worktree add -qb stale "$HOME/.t3/worktrees/example/stale"
git -C "$tmp/repo" worktree add -qb dirty "$HOME/.t3/worktrees/example/dirty"
printf 'uncommitted\n' >"$HOME/.t3/worktrees/example/dirty/untracked"
git -C "$tmp/repo" worktree add -qb locked "$HOME/.t3/worktrees/example/locked"
git -C "$tmp/repo" worktree lock "$HOME/.t3/worktrees/example/locked"
git -C "$tmp/repo" commit --allow-empty -qm newer
GC_TEST_WORKTREES_ONLY=1 python3 "$root/scripts/gc.py" --dry-run >"$tmp/dry"
if grep -E '^delete .*(unpushed|dirty|locked)' "$tmp/dry"; then exit 1; fi
grep '^delete .*stale' "$tmp/dry"
GC_TEST_WORKTREES_ONLY=1 python3 "$root/scripts/gc.py" >"$tmp/run"
test -d "$HOME/.t3/worktrees/example/unpushed"
git -C "$HOME/.t3/worktrees/example/unpushed" log -1 --format=%s | grep '^unpushed$'
test ! -e "$HOME/.t3/worktrees/example/stale"

test -f "$HOME/.t3/worktrees/example/dirty/untracked"
test -d "$HOME/.t3/worktrees/example/locked"

mkdir "$HOME/.t3/worktrees/example/broken"
if GC_TEST_WORKTREES_ONLY=1 python3 "$root/scripts/gc.py" --dry-run >"$tmp/broken"; then
	exit 1
fi
grep 'kept .*broken.*cannot inspect' "$tmp/broken"

rmdir "$HOME/.t3/worktrees/example/broken"
git -C "$tmp/repo" worktree add -qb badtime "$HOME/.t3/worktrees/example/badtime" origin/main
REAL_GIT=$(command -v git)
export REAL_GIT
mkdir "$tmp/bin"
cat >"$tmp/bin/git" <<'STUB'
#!/bin/sh
if [ "$1" = show ]; then
	printf 'invalid timestamp\n'
	exit 0
fi
exec "$REAL_GIT" "$@"
STUB
chmod +x "$tmp/bin/git"
if PATH="$tmp/bin:$PATH" GC_TEST_WORKTREES_ONLY=1 python3 "$root/scripts/gc.py" --dry-run >"$tmp/badtime"; then
	exit 1
fi
grep 'kept .*badtime.*cannot inspect' "$tmp/badtime"
