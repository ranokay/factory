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
grep -E '^would reclaim [1-9][0-9]* bytes' "$tmp/dry"
run_gc >"$tmp/run"
grep -E '^reclaimed [1-9][0-9]* bytes' "$tmp/run"
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
mkdir -p "$fake_root/fake/1.0" "$fake_root/recent/1.0" "$fake_root/stale/1.0" "$tmp/fake/bin" "$tmp/tool-home"
printf 'fake\n' >"$fake_root/fake/1.0/file"
printf 'recent\n' >"$fake_root/recent/1.0/file"
printf 'stale\n' >"$fake_root/stale/1.0/file"
touch -t 202001010000 "$fake_root/fake/1.0" "$fake_root/fake/1.0/file" "$fake_root/stale/1.0" "$fake_root/stale/1.0/file"
cat >"$tmp/fake/bin/mise" <<STUB
#!/bin/sh
case "\$1 \$2" in
"ls --json")
	count=0
	[ -f "$tmp/fake/count" ] && count=\$(cat "$tmp/fake/count")
	count=\$((count + 1))
	printf '%s\n' "\$count" >"$tmp/fake/count"
	stale_active=false
	[ "\${FAKE_MODE:-}" = flip ] && [ "\$count" -ge 2 ] && stale_active=true
	printf '{"fake":[{"version":"1.0","install_path":"$fake_root/fake/1.0","installed":true,"active":true}],"recent":[{"version":"1.0","install_path":"$fake_root/recent/1.0","installed":true,"active":false}],"stale":[{"version":"1.0","install_path":"$fake_root/stale/1.0","installed":true,"active":%s}],"missing":[{"version":"1.0","install_path":"$fake_root/missing/1.0","installed":true,"active":false}]}\n' "\$stale_active"
	;;
"ls --prunable")
	printf '{"fake":[{"version":"1.0","install_path":"$fake_root/fake/1.0","installed":true,"active":false}],"recent":[{"version":"1.0","install_path":"$fake_root/recent/1.0","installed":true,"active":false}],"stale":[{"version":"1.0","install_path":"$fake_root/stale/1.0","installed":true,"active":false}],"missing":[{"version":"1.0","install_path":"$fake_root/missing/1.0","installed":true,"active":false}]}\n'
	;;
"cache path")
	printf '%s\n' "$tmp/cache"
	;;
"cache prune")
	printf 'INFO  cache pruned 0 files, 0 B\n'
	;;
"prune --tools")
	case \$3 in
	--dry-run)
		case \$4 in
		fake@1.0) printf '[dryrun] remove %s\n' "$fake_root/fake/1.0" ;;
		recent@1.0) printf '[dryrun] remove %s\n' "$fake_root/recent/1.0" ;;
		stale@1.0) printf '[dryrun] remove %s\n' "$fake_root/stale/1.0" ;;
		missing@1.0) printf '[dryrun] remove %s\n' "$fake_root/missing/1.0" ;;
		esac
		;;
	--yes)
		printf '%s\n' "\$4" >>"$tmp/prune.log"
		;;
	esac
	;;
*)
	exit 1
	;;
esac
STUB
chmod +x "$tmp/fake/bin/mise"
PATH="$tmp/fake/bin:$base_path"
HOME="$tmp/tool-home" run_gc --dry-run >"$tmp/tools"
PATH=$base_path
grep 'kept .*fake/1.0: active tool' "$tmp/tools"
grep 'kept .*recent/1.0: recent install' "$tmp/tools"
grep 'kept .*missing/1.0: install path missing' "$tmp/tools"
grep '^delete .*stale/1.0' "$tmp/tools"
rm -f "$tmp/fake/count" "$tmp/prune.log"
PATH="$tmp/fake/bin:$base_path"
HOME="$tmp/tool-home" run_gc >"$tmp/tools-run"
PATH=$base_path
grep 'stale@1.0' "$tmp/prune.log"
rm -f "$tmp/fake/count"
: >"$tmp/prune.log"
PATH="$tmp/fake/bin:$base_path"
FAKE_MODE=flip HOME="$tmp/tool-home" run_gc >"$tmp/tools-flip"
PATH=$base_path
grep 'kept .*stale/1.0: active tool before pruning' "$tmp/tools-flip"
if grep -q 'stale@1.0' "$tmp/prune.log"; then exit 1; fi

evidence_home="$tmp/evidence-home"
mkdir -p "$evidence_home/.cache/factory/owner/repo/1" "$evidence_home/.cache/factory/owner/repo/2" "$tmp/evidence-bin"
printf 'evidence\n' >"$evidence_home/.cache/factory/owner/repo/1/file"
touch -t 202001010000 "$evidence_home/.cache/factory/owner/repo/1/file" "$evidence_home/.cache/factory/owner/repo/1"
printf 'recent\n' >"$evidence_home/.cache/factory/owner/repo/2/file"
cat >"$tmp/evidence-bin/mise" <<STUB
#!/bin/sh
case "\$1 \$2" in
"ls --json" | "ls --prunable")
	printf '{}\n'
	;;
"cache path")
	printf '%s\n' "$tmp/cache"
	;;
"cache prune")
	printf 'INFO  cache pruned 0 files, 0 B\n'
	;;
*)
	exit 1
	;;
esac
STUB
cat >"$tmp/evidence-bin/gh" <<'STUB'
#!/bin/sh
if [ "$1 $2" = "pr view" ]; then
	printf 'MERGED\n'
	exit 0
fi
exit 1
STUB
chmod +x "$tmp/evidence-bin/mise" "$tmp/evidence-bin/gh"
PATH="$tmp/evidence-bin:$base_path"
HOME="$evidence_home" run_gc --dry-run >"$tmp/evidence-dry"
PATH=$base_path
grep '^delete .*repo/1' "$tmp/evidence-dry"
grep 'kept .*repo/2: recent evidence' "$tmp/evidence-dry"
if grep '^delete .*repo/2' "$tmp/evidence-dry"; then exit 1; fi
PATH="$tmp/evidence-bin:$base_path"
HOME="$evidence_home" run_gc >"$tmp/evidence-run"
PATH=$base_path
test ! -e "$evidence_home/.cache/factory/owner/repo/1"
