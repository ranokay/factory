#!/usr/bin/env python3
"""Inventory and migrate clones; T3 repointing is performed separately."""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], check=True, text=True, capture_output=True).stdout.strip()


def identity(url):
    if re.match(r'^[^/@:]+@[^/:]+:', url):
        userhost, path = url.split(':', 1)
        host = userhost.split('@')[-1].lower()
    else:
        parsed = urlsplit(url)
        if parsed.scheme not in ('https', 'ssh') or not parsed.hostname:
            return None
        host, path = parsed.hostname.lower(), parsed.path
    parts = path.strip('/').removesuffix('.git').split('/')
    if len(parts) != 2 or not all(parts) or any(p in ('.', '..') for p in parts):
        return None
    if host == 'github.com':
        parts = [p.lower() for p in parts]
    return '/'.join([host, *parts])


def work(path):
    reasons = []
    if git(path, 'status', '--porcelain', '--untracked-files=all'):
        reasons.append('uncommitted or untracked changes')
    if git(path, 'stash', 'list'):
        reasons.append('stashes')
    if git(path, 'rev-list', '--branches', 'HEAD', '--not', '--remotes'):
        reasons.append('local commits not on remote-tracking branches')
    branches = git(path, 'for-each-ref', '--format=%(refname:short)%00%(upstream)', 'refs/heads')
    if any(not line.split('\0', 1)[1] for line in branches.splitlines()):
        reasons.append('local branches without upstream')
    return reasons


def linked_worktrees(path):
    records = git(path, 'worktree', 'list', '--porcelain', '-z').split('\0')
    return [record[9:] for record in records if record.startswith('worktree ')
            and Path(record[9:]).resolve() != path.resolve()]


def is_main_clone(path):
    common = Path(git(path, 'rev-parse', '--git-common-dir'))
    if not common.is_absolute():
        common = path / common
    return common.resolve() == (path / '.git').resolve()


def inventory(roots, developer):
    groups, unknown, linked = {}, [], []
    for root in dict.fromkeys(roots):
        if not root.exists():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_dir() or path.is_symlink() or not (path / '.git').exists():
                continue
            try:
                if not is_main_clone(path):
                    linked.append(path)
                    continue
                url = git(path, 'remote', 'get-url', 'origin')
                key = identity(url)
            except subprocess.CalledProcessError:
                key = None
            if key is None:
                unknown.append(str(path))
            else:
                groups.setdefault(key, []).append(path)
    targets = {}
    for key in groups:
        targets.setdefault(developer / key.rsplit('/', 1)[-1], []).append(key)
    entries = []
    for key, paths in sorted(groups.items()):
        target = developer / key.rsplit('/', 1)[-1]
        reason = None
        if target in paths:
            selected = target
        else:
            dev = [p for p in paths if p.parent == developer]
            named = [p for p in paths if p.name == target.name]
            selected = dev[0] if len(dev) == 1 else named[0] if len(named) == 1 else paths[0] if len(paths) == 1 else None
            if selected is None:
                reason = 'ambiguous canonical clone; decision required'
            elif target.exists() or target.is_symlink():
                reason = 'destination already exists'
        if len(targets[target]) > 1:
            reason = 'repository name collision: ' + ', '.join(targets[target])
        clone_worktrees, inspection_errors = {}, {}
        for path in paths:
            try:
                clone_worktrees[str(path)] = linked_worktrees(path)
            except (OSError, subprocess.SubprocessError) as error:
                clone_worktrees[str(path)] = []
                inspection_errors[str(path)] = f'worktree inspection failed: {error}'
                if path == selected:
                    reason = f'worktree inspection failed: {error}'
        if selected is not None and selected != target and clone_worktrees[str(selected)] and reason is None:
            reason = 'linked worktrees require manual migration: ' + ', '.join(clone_worktrees[str(selected)])
        duplicates = []
        for path in paths:
            if path != selected:
                try:
                    reasons = work(path)
                    if clone_worktrees[str(path)]:
                        reasons.append('linked worktrees')
                except (OSError, subprocess.SubprocessError) as error:
                    reasons = [f'inspection failed: {error}']
                if str(path) in inspection_errors:
                    reasons.append(inspection_errors[str(path)])
                duplicates.append({'path': str(path), 'unpublished': reasons})
        entries.append({'identity': key, 'paths': [str(p) for p in paths], 'canonical': str(target), 'selected': str(selected) if selected else None,
                        'move': selected is not None and selected != target and reason is None, 'duplicates': duplicates, 'linked_worktrees': clone_worktrees, 'refusal': reason})
    unknown_worktrees = {}
    for path in unknown:
        try:
            unknown_worktrees[path] = linked_worktrees(Path(path))
        except (OSError, subprocess.SubprocessError):
            unknown_worktrees[path] = []
    known = {Path(w).resolve() for entry in entries for paths in entry['linked_worktrees'].values() for w in paths}
    known.update(Path(w).resolve() for paths in unknown_worktrees.values() for w in paths)
    return {'repositories': entries, 'no_origin': unknown, 'no_origin_worktrees': unknown_worktrees,
            'unmatched_worktrees': [str(p) for p in linked if p.resolve() not in known]}



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=['report', 'move', 'remove'], default='report')
    parser.add_argument('--source-root', type=Path, default=Path.home() / 'Downloads/workspaces')
    parser.add_argument('--developer-root', type=Path, default=Path.home() / 'Developer')
    parser.add_argument('--approve-path', action='append', default=[])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    source, developer = args.source_root.absolute(), args.developer_root.absolute()
    if args.action != 'remove' and args.approve_path:
        parser.error('--approve-path is only valid for remove')
    if args.action == 'remove' and not args.approve_path and not sys.stdin.isatty():
        parser.error('remove requires a TTY or --approve-path')
    result = inventory([source, developer], developer)
    result['repoint'] = []
    result['actions'] = []
    failed = False
    for entry in result['repositories']:
        if args.action == 'move' and entry['move']:
            old, new = Path(entry['selected']), Path(entry['canonical'])
            try:
                current_worktrees = linked_worktrees(old)
                if current_worktrees:
                    raise ValueError('linked worktrees require manual migration: ' + ', '.join(current_worktrees))
                if new.exists() or new.is_symlink():
                    raise ValueError('destination already exists')
                new.parent.mkdir(parents=True, exist_ok=True)
                old.rename(new)
                result['repoint'].append({'old': str(old), 'new': str(new)})
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                result['actions'].append(f'refused move {old}: {error}')
                failed = True
        if args.action == 'remove' and not entry['refusal']:
            for duplicate in entry['duplicates']:
                path = Path(duplicate['path'])
                approved = str(path) in args.approve_path
                if not args.approve_path and not duplicate['unpublished']:
                    approved = input(f'Remove {path}? [y/N] ').strip().lower() == 'y'
                if not approved:
                    continue
                try:
                    canonical = Path(entry['canonical'])
                    if (not canonical.is_dir() or Path(git(canonical, 'rev-parse', '--show-toplevel')).resolve() != canonical.resolve()
                            or identity(git(canonical, 'remote', 'get-url', 'origin')) != entry['identity']):
                        raise ValueError('canonical clone is absent or has changed identity')
                    reasons = work(path)
                    if not is_main_clone(path) or linked_worktrees(path):
                        reasons.append('linked worktrees')
                    if reasons:
                        raise ValueError(', '.join(reasons))
                    shutil.rmtree(path)
                    result['actions'].append(f'removed {path}')
                except (OSError, subprocess.SubprocessError, ValueError) as error:
                    result['actions'].append(f'refused removal {path}: {error}')
                    failed = True
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for entry in result['repositories']:
            print(f"{entry['identity']}: canonical {entry['canonical']}")
            print(f"  clones: {', '.join(entry['paths'])}")
            print(f"  selected: {entry['selected']}; move: {entry['move']}; refusal: {entry['refusal']}")
            for clone, paths in entry['linked_worktrees'].items():
                for path in paths:
                    print(f'  linked worktree of {clone}: {path}')
            for duplicate in entry['duplicates']:
                print(f"  {'duplicate' if entry['selected'] else 'unresolved candidate'} {duplicate['path']}: {', '.join(duplicate['unpublished']) or 'clean'}")
        for path in result['unmatched_worktrees']:
            print(f'linked worktree with main clone outside inventory: {path}')
        for path in result['no_origin']:
            print(f'no usable origin; decision required: {path}')
            for linked_path in result['no_origin_worktrees'][path]:
                print(f'  linked worktree of {path}: {linked_path}')
        for action in result['actions']:
            print(action)
        if args.action == 'move':
            print('T3 repoint: apply these pairs through T3 project tools; this command does not update projects:')
            for pair in result['repoint']:
                print(f"  {pair['old']} -> {pair['new']}")
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
