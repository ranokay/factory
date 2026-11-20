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
FAILURES = []


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


def human_size(amount):
    value = float(amount)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.0f} bytes' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024


def worktree_reason(path):
    records = run('git', 'worktree', 'list', '--porcelain', cwd=path).split('\n\n')
    record = next(r.splitlines() for r in records if Path(r.splitlines()[0][9:]).resolve() == path.resolve())
    branch = next((line[7:] for line in record if line.startswith('branch ')), None)
    if branch is None:
        return 'detached HEAD'
    if any(line.startswith('locked') for line in record):
        return 'locked'
    if run('git', 'status', '--porcelain', '--untracked-files=all', '--ignored', cwd=path):
        return 'dirty'
    if run('git', 'rev-list', branch, '--not', '--remotes', cwd=path):
        return 'unpushed commits'
    if time.time() - int(run('git', 'show', '-s', '--format=%ct', branch, cwd=path)) < AGE:
        return 'recent commit'
    remotes = run('git', 'for-each-ref', '--format=%(refname)', '--contains', branch, 'refs/remotes', cwd=path)
    all_remotes = run('git', 'for-each-ref', '--format=%(refname)', 'refs/remotes', cwd=path)
    short = branch.removeprefix('refs/heads/')
    if not remotes and any(ref.endswith('/' + short) for ref in all_remotes.splitlines()):
        return 'remote branch not merged'
    return None


def pruneable(path, reason_for):
    try:
        reason = reason_for(path)
    except (OSError, subprocess.SubprocessError, StopIteration, ValueError, IndexError) as error:
        message = f'cannot inspect {path}: {error}'
        FAILURES.append(message)
        print(f'kept {path}: {message}', flush=True)
        return False
    if reason:
        print(f'kept {path}: {reason}', flush=True)
        return False
    return True


def worktrees():
    root = HOME / '.t3/worktrees'
    for path in sorted(root.glob('*/*')):
        if not path.is_dir() or path.is_symlink():
            continue
        if pruneable(path, worktree_reason):
            yield path


def evidence_reason(path):
    if time.time() - path.stat().st_mtime < AGE:
        return 'recent evidence'
    state = run('gh', 'pr', 'view', path.name, '--repo', f'{path.parent.parent.name}/{path.parent.name}', '--json', 'state', '--jq', '.state')
    if state not in ('CLOSED', 'MERGED'):
        return f'PR is {state}'
    if contains_git(path):
        return 'contains git data'
    return None


def evidence():
    for path in sorted((HOME / '.cache/factory').glob('*/*/*')):
        if not path.is_dir() or path.is_symlink() or not path.name.isdecimal():
            continue
        if pruneable(path, evidence_reason):
            yield path



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


def active_tools():
    installed = json.loads(tool_output('ls', '--json'))
    return {
        (tool, entry['version'])
        for tool, entries in installed.items()
        for entry in entries
        if entry.get('installed') and entry.get('active')
    }


def tool_keep_reason(tool, version, path, active, protected):
    if (tool, version) in active:
        return 'active tool'
    if any(program.startswith(str(path) + '/') or program == str(path) for program in protected):
        return 'used by launch agent'
    if not path.exists():
        return 'install path missing'
    if time.time() - path.stat().st_mtime < AGE:
        return 'recent install'
    return None


def tool_candidates():
    versions = json.loads(tool_output('ls', '--prunable', '--json'))
    for tool, entries in versions.items():
        for entry in entries:
            version = entry['version']
            path = Path(entry['install_path'])
            output = tool_output('prune', '--tools', '--dry-run', f'{tool}@{version}')
            matches = re.findall(r'\[dryrun\]\s+remove (.+)', output)
            if not matches:
                raise RuntimeError(f'cannot enumerate prunable paths for {tool}@{version}')
            paths = [Path(item.strip()).expanduser() for line in matches for item in line.split(', ')]
            if path not in paths:
                raise RuntimeError(f'prune output does not contain {path}')
            yield tool, version, path, paths


def cache_candidates():
    root = Path(tool_output('cache', 'path').strip()).resolve()
    output = tool_output('cache', 'prune', '--dry-run', '--verbose')
    paths = []
    for line in output.splitlines():
        line = re.sub(r'\x1b\[[0-9;]*m', '', line)
        if 'pruning ' in line:
            candidate = line.split('pruning ', 1)[1].strip()
            if not (candidate.startswith('/') or candidate.startswith('~')):
                if candidate.startswith('old cache files'):
                    continue
                raise RuntimeError(f'unrecognized mise cache output: {line}')
            path = Path(candidate).expanduser().resolve()
            if root not in path.parents or path == root:
                raise RuntimeError(f'unsafe cache path {path}')
            paths.append(path)
        elif not (line.startswith('DEBUG ') or 'cache pruned ' in line or not line.strip()):
            raise RuntimeError(f'unrecognized mise cache output: {line}')
    return paths


def contains_git(path):
    if not path.exists():
        return False
    return any('.git' in dirs or '.git' in files or ('HEAD' in files and 'objects' in dirs and 'refs' in dirs) for _, dirs, files in os.walk(path, followlinks=False))


def prune_tools(dry_run):
    total = 0
    candidates = list(tool_candidates())
    active = active_tools()
    protected = protected_programs()
    for tool, version, install, paths in candidates:
        reason = tool_keep_reason(tool, version, install, active, protected)
        if reason:
            print(f'kept {install}: {reason}')
            continue
        if any(contains_git(path) for path in paths):
            print(f'kept {tool}@{version}: contains git data')
            continue
        if not dry_run:
            reason = tool_keep_reason(tool, version, install, active_tools(), protected_programs())
            if reason:
                print(f'kept {install}: {reason} before pruning')
                continue
        for path in paths:
            amount = size(path) if path.exists() else 0
            print(f'delete {path} {amount} bytes', flush=True)
            total += amount
        if not dry_run:
            tool_output('prune', '--tools', '--yes', f'{tool}@{version}')
    return total


def prune_cache(dry_run):
    paths = cache_candidates()
    if any(contains_git(path) for path in paths):
        raise RuntimeError('mise cache contains git data; refusing cache prune')
    if not dry_run and paths != cache_candidates():
        print('kept mise cache: candidates changed before pruning', flush=True)
        FAILURES.append('mise cache candidates changed before pruning')
        return 0
    total = sum(size(path) for path in paths if path.exists())
    for path in paths:
        print(f'delete {path} {size(path) if path.exists() else 0} bytes', flush=True)
    if not dry_run:
        tool_output('cache', 'prune', '--verbose')
    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--notify', action='store_true')
    args = parser.parse_args()
    total = 0
    for path in worktrees():
        if not args.dry_run and not pruneable(path, worktree_reason):
            continue
        amount = size(path)
        print(f'delete {path} {amount} bytes', flush=True)
        total += amount
        if not args.dry_run:
            run('git', 'worktree', 'remove', str(path), cwd=path)
    for path in evidence():
        if not args.dry_run and not pruneable(path, evidence_reason):
            continue
        amount = size(path)
        print(f'delete {path} {amount} bytes', flush=True)
        total += amount
        if not args.dry_run:
            shutil.rmtree(path)
    total += prune_cache(args.dry_run)
    total += prune_tools(args.dry_run)
    summary = human_size(total)
    print(f'{"would reclaim" if args.dry_run else "reclaimed"} {summary}', flush=True)
    if args.notify and not args.dry_run and sys.platform == 'darwin':
        message = f'Factory GC reclaimed {summary}'
        run('/usr/bin/osascript', '-e', f'display notification "{message}" with title "Factory GC"')
        print(f'notification posted: {message}', flush=True)
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
