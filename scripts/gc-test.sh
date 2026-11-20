#!/bin/sh
set -eu
root=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
base_path=$PATH
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
PATH="$tmp/bin:$base_path"
if run_gc --dry-run >"$tmp/badtime"; then
	exit 1
fi
PATH=$base_path
grep 'kept .*badtime.*cannot inspect' "$tmp/badtime"

fake_root="$tmp/fake/install"
mkdir -p "$fake_root/fake/1.0" "$fake_root/recent/1.0" "$tmp/fake/bin" "$tmp/tool-home"
printf 'fake\n' >"$fake_root/fake/1.0/file"
printf 'recent\n' >"$fake_root/recent/1.0/file"
touch -t 202001010000 "$fake_root/fake/1.0/file"
cat >"$tmp/fake/bin/mise" <<STUB
#!/bin/sh
case "\$1 \$2" in
"ls --json")
	printf '{"fake":[{"version":"1.0","install_path":"$fake_root/fake/1.0","installed":true,"active":true}],"recent":[{"version":"1.0","install_path":"$fake_root/recent/1.0","installed":true,"active":false}]}\n'
	;;
"ls --prunable")
	printf '{"fake":[{"version":"1.0","install_path":"$fake_root/fake/1.0","installed":true,"active":false}],"recent":[{"version":"1.0","install_path":"$fake_root/recent/1.0","installed":true,"active":false}]}\n'
	;;
"cache path")
	printf '%s\n' "$tmp/cache"
	;;
"cache prune")
	printf 'INFO  cache pruned 0 files, 0 B\n'
	;;
*)
	printf '[dryrun] remove %s\n' "$fake_root/fake/1.0"
	;;
esac
STUB
chmod +x "$tmp/fake/bin/mise"
PATH="$tmp/fake/bin:$base_path"
HOME="$tmp/tool-home" run_gc --dry-run >"$tmp/tools"
PATH=$base_path
grep 'kept .*fake/1.0: active tool' "$tmp/tools"
grep 'kept .*recent/1.0: recent install' "$tmp/tools"
if grep '^delete .*fake/1.0' "$tmp/tools"; then exit 1; fi
