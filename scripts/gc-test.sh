#!/bin/sh
set -eu
root=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
python_bin=$(mise which python)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export HOME="$tmp/home"
export XDG_CONFIG_HOME="$HOME/.config"
export MISE_CONFIG_DIR="$tmp/config"
export MISE_DATA_DIR="$tmp/data"
export MISE_STATE_DIR="$tmp/state"
export MISE_CACHE_DIR="$tmp/cache"
mkdir -p "$HOME/.t3/worktrees/example" "$MISE_CACHE_DIR/old-tool" "$MISE_CONFIG_DIR" "$tmp/remote.git"
printf 'cached\n' >"$MISE_CACHE_DIR/old-tool/file"
touch -t 202001010000 "$MISE_CACHE_DIR/old-tool/file"
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
run_gc() {
	(cd "$tmp" && uv run --offline --no-project --no-config --python "$python_bin" python "$root/scripts/gc.py" "$@")
}
run_gc --dry-run >"$tmp/dry"
if grep -E '^delete .*(unpushed|dirty|locked)' "$tmp/dry"; then exit 1; fi
grep '^delete .*stale' "$tmp/dry"
grep '^delete .*old-tool' "$tmp/dry"
grep '^would reclaim' "$tmp/dry"
run_gc >"$tmp/run"
grep '^reclaimed' "$tmp/run"
test -d "$HOME/.t3/worktrees/example/unpushed"
git -C "$HOME/.t3/worktrees/example/unpushed" log -1 --format=%s | grep '^unpushed$'
test ! -e "$HOME/.t3/worktrees/example/stale"
test ! -e "$MISE_CACHE_DIR/old-tool"

test -f "$HOME/.t3/worktrees/example/dirty/untracked"
test -d "$HOME/.t3/worktrees/example/locked"

mkdir "$HOME/.t3/worktrees/example/broken"
if run_gc --dry-run >"$tmp/broken"; then
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
if (
	PATH="$tmp/bin:$PATH"
	run_gc --dry-run >"$tmp/badtime"
); then
	exit 1
fi
grep 'kept .*badtime.*cannot inspect' "$tmp/badtime"
