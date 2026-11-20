#!/usr/bin/env python3
import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
AGE = 30 * 86400


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


def size(path):
    if path.is_symlink() or path.is_file():
        return path.lstat().st_size
    total = 0
    for base, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            total += (Path(base) / name).lstat().st_size
    return total


def worktrees():
    root = HOME / '.t3/worktrees'
    for path in sorted(root.glob('*/*')):
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            records = run('git', 'worktree', 'list', '--porcelain', cwd=path).split('\n\n')
            record = next(r.splitlines() for r in records if Path(r.splitlines()[0][9:]).resolve() == path.resolve())
            branch = next(line[7:] for line in record if line.startswith('branch '))
            if any(line.startswith('locked') for line in record):
                raise ValueError('locked')
            if run('git', 'status', '--porcelain', '--untracked-files=all', '--ignored', cwd=path):
                raise ValueError('dirty')
            if run('git', 'rev-list', branch, '--not', '--remotes', cwd=path):
                raise ValueError('unpushed commits')
            if time.time() - int(run('git', 'show', '-s', '--format=%ct', branch, cwd=path)) < AGE:
                raise ValueError('recent commit')
            remotes = run('git', 'for-each-ref', '--format=%(refname)', '--contains', branch, 'refs/remotes', cwd=path)
            all_remotes = run('git', 'for-each-ref', '--format=%(refname)', 'refs/remotes', cwd=path)
            short = branch.removeprefix('refs/heads/')
            if not remotes and any(ref.endswith('/' + short) for ref in all_remotes.splitlines()):
                raise ValueError('remote branch not merged')
            yield path
        except (OSError, subprocess.SubprocessError, StopIteration, ValueError) as error:
            print(f'kept {path}: {error}', flush=True)


def evidence():
    for path in sorted((HOME / '.cache/factory').glob('*/*/*')):
        if not path.is_dir() or path.is_symlink() or not path.name.isdecimal():
            continue
        try:
            if time.time() - path.stat().st_mtime < AGE:
                continue
            state = run('gh', 'pr', 'view', path.name, '--repo', f'{path.parent.parent.name}/{path.parent.name}', '--json', 'state', '--jq', '.state')
            if state not in ('CLOSED', 'MERGED'):
                print(f'kept {path}: PR is {state}')
                continue
            if contains_git(path):
                print(f'kept {path}: contains git data')
                continue
            yield path
        except (OSError, subprocess.SubprocessError) as error:
            print(f'kept {path}: {error}', flush=True)



def tool_output(*args):
    result = subprocess.run(['mise', *args], capture_output=True, text=True, check=True)
    return result.stdout + result.stderr


def protected_programs():
    programs = []
    for plist in (HOME / 'Library/LaunchAgents').glob('*.plist'):
        try:
            data = plistlib.loads(plist.read_bytes())
            programs.extend(str(value) for value in data.get('ProgramArguments', []))
        except (OSError, ValueError, TypeError):
            raise RuntimeError(f'cannot inspect launch agent {plist}') from None
    return programs


def tool_candidates():
    versions = json.loads(tool_output('ls', '--prunable', '--json'))
    protected = protected_programs()
    for tool, entries in versions.items():
        for entry in entries:
            version = entry['version']
            path = Path(entry['install_path'])
            if any(program.startswith(str(path) + '/') or program == str(path) for program in protected):
                print(f'kept {path}: used by launch agent')
                continue
            output = tool_output('prune', '--tools', '--dry-run', f'{tool}@{version}')
            matches = re.findall(r'\[dryrun\]\s+remove (.+)', output)
            if not matches:
                raise RuntimeError(f'cannot enumerate prunable paths for {tool}@{version}')
            paths = [Path(item.strip()).expanduser() for line in matches for item in line.split(', ')]
            if path not in paths:
                raise RuntimeError(f'prune output does not contain {path}')
            yield tool, version, paths


def cache_candidates():
    root = Path(tool_output('cache', 'path').strip()).resolve()
    output = tool_output('cache', 'prune', '--dry-run', '--verbose')
    paths = []
    for line in output.splitlines():
        if 'pruning ' in line:
            path = Path(line.split('pruning ', 1)[1]).resolve()
            if root not in path.parents or path == root:
                raise RuntimeError(f'unsafe cache path {path}')
            paths.append(path)
        elif not (line.startswith('DEBUG ARGS:') or 'cache pruned ' in line or not line.strip()):
            raise RuntimeError(f'unrecognized mise cache output: {line}')
    return paths


def contains_git(path):
    if not path.exists():
        return False
    return any('.git' in dirs or '.git' in files or ('HEAD' in files and 'objects' in dirs and 'refs' in dirs) for _, dirs, files in os.walk(path, followlinks=False))


def prune_tools(dry_run):
    total = 0
    for tool, version, paths in tool_candidates():
        if any(contains_git(path) for path in paths):
            print(f'kept {tool}@{version}: contains git data')
            continue
        for path in paths:
            amount = size(path) if path.exists() else 0
            print(f'delete {path} {amount} bytes', flush=True)
            total += amount
        if not dry_run:
            current = list(tool_candidates())
            if (tool, version, paths) not in current:
                raise RuntimeError(f'{tool}@{version} changed before pruning')
            tool_output('prune', '--tools', '--yes', f'{tool}@{version}')
    return 0 if dry_run else total


def prune_cache(dry_run):
    paths = cache_candidates()
    if any(contains_git(path) for path in paths):
        raise RuntimeError('mise cache contains git data; refusing cache prune')
    total = sum(size(path) for path in paths if path.exists())
    for path in paths:
        print(f'delete {path} {size(path) if path.exists() else 0} bytes', flush=True)
    if not dry_run:
        if paths != cache_candidates():
            raise RuntimeError('mise cache candidates changed before pruning')
        tool_output('cache', 'prune', '--verbose')
    return 0 if dry_run else total

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    total = 0
    candidates = [(p, 'worktree') for p in worktrees()]
    if not os.environ.get('GC_TEST_WORKTREES_ONLY'):
        candidates.extend((p, 'evidence') for p in evidence())
    for path, kind in candidates:
        amount = size(path)
        print(f'delete {path} {amount} bytes', flush=True)
        if not args.dry_run:
            if kind == 'worktree':
                if path not in set(worktrees()):
                    print(f'kept {path}: safety state changed', flush=True)
                    continue
                run('git', 'worktree', 'remove', str(path), cwd=path)
            else:
                if path not in set(evidence()):
                    print(f'kept {path}: safety state changed', flush=True)
                    continue
                shutil.rmtree(path)
            total += amount
    if not os.environ.get('GC_TEST_WORKTREES_ONLY'):
        total += prune_tools(args.dry_run)
        total += prune_cache(args.dry_run)
    print(f'reclaimed {total} bytes', flush=True)
    if not args.dry_run and sys.platform == 'darwin':
        message = f'Factory GC reclaimed {total} bytes'
        run('/usr/bin/osascript', '-e', f'display notification "{message}" with title "Factory GC"')
        print(f'notification posted: {message}', flush=True)


if __name__ == '__main__':
    main()
