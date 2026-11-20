#!/bin/sh
set -eu
root=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export HOME="$tmp/home"
mkdir -p "$HOME/.t3/worktrees/example" "$tmp/remote.git"
git init -q --bare "$tmp/remote.git"
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
