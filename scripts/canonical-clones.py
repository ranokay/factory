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



def move_blocker(path, worktrees):
    if not worktrees:
        return None
    paths = [Path(w) for w in worktrees]
    for tree in paths:
        if git(tree, 'status', '--porcelain', '--untracked-files=all'):
            return f'linked worktree has uncommitted work: {tree}'
    result = subprocess.run(['t3', 'thread', 'list', '--project', str(path), '--json'],
                            capture_output=True, text=True)
    if result.returncode:
        t3_root = (Path.home() / '.t3' / 'worktrees').resolve()
        has_t3_tree = any(tree.resolve().is_relative_to(t3_root) for tree in paths)
        if result.stderr.startswith('No T3 Code project matches ') and not has_t3_tree:
            return None
        raise ValueError(f'T3 thread list failed: {result.stderr.strip()}')
    threads = json.loads(result.stdout)
    if not isinstance(threads, list):
        raise ValueError('T3 thread list is not a list')
    active = {'preparing', 'queued', 'starting', 'running', 'waiting'}
    for thread in threads:
        if thread.get('status') in active:
            details = json.loads(subprocess.run(['t3', 'thread', 'show', thread['threadId'], '--json'],
                                                check=True, capture_output=True, text=True).stdout)['thread']
            tree = details.get('worktreePath')
            if tree is None:
                return f"cannot establish worktree for active T3 thread {thread['threadId']}"
            if Path(tree).resolve() in {p.resolve() for p in paths}:
                return f"active T3 thread {thread['threadId']}: {tree}"
        elif thread.get('status') not in {'idle', 'completed', 'failed', 'cancelled', 'interrupted'}:
            raise ValueError(f"unknown T3 thread status: {thread.get('status')}")
    return None


def github_repos(root):
    """Return (identity, path) for each main clone under root with a GitHub origin."""
    repositories = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.is_symlink() or not (path / '.git').exists():
            continue
        try:
            if not is_main_clone(path):
                continue
            key = identity(git(path, 'remote', 'get-url', 'origin'))
        except (OSError, subprocess.CalledProcessError):
            continue
        if not key or not key.startswith('github.com/'):
            continue
        repositories.append((key, path))
    return repositories


def relocate(old, new, trees):
    mapped = [str(new / Path(w).relative_to(old)) if Path(w).is_relative_to(old) else w for w in trees]
    old.rename(new)
    try:
        if mapped:
            git(new, 'worktree', 'repair', *mapped)
            for tree in mapped:
                common = Path(git(tree, 'rev-parse', '--git-common-dir'))
                if not common.is_absolute():
                    common = Path(tree) / common
                if common.resolve() != (new / '.git').resolve():
                    raise ValueError(f'worktree repair verification failed: {tree}')
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        try:
            new.rename(old)
            if trees:
                git(old, 'worktree', 'repair', *trees)
        except (OSError, subprocess.SubprocessError) as rollback:
            raise ValueError(f'move failed: {error}; rollback failed: {rollback}') from error
        raise ValueError(f'move failed and was rolled back: {error}') from error



def github_parent(key):
    if not key.startswith('github.com/ranokay/'):
        return None
    result = subprocess.run(['gh', 'api', f'repos/{key.removeprefix("github.com/")}'],
                            check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    parent = data.get('parent')
    if not data.get('fork') or not isinstance(parent, dict) or not parent.get('full_name'):
        return None
    return 'github.com/' + parent['full_name'].lower()


def upstream_identity(path):
    remotes = git(path, 'remote').splitlines()
    if 'upstream' not in remotes:
        return None
    return identity(git(path, 'remote', 'get-url', 'upstream')) or 'invalid upstream'


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
    parents = {}
    for key in list(groups):
        if not key.startswith('github.com/ranokay/'):
            continue
        if not any(other != key and other.rsplit('/', 1)[-1] == key.rsplit('/', 1)[-1] for other in groups):
            continue
        try:
            parent = github_parent(key)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            continue
        if parent in groups and parent != key:
            parents[key] = parent
            groups[key] += groups.pop(parent)
    targets = {}
    for key in groups:
        targets.setdefault(developer / key.rsplit('/', 1)[-1], []).append(key)
    entries = []
    for key, paths in sorted(groups.items()):
        target = developer / key.rsplit('/', 1)[-1]
        reason = None
        parent = parents.get(key)
        candidates = [p for p in paths if not parent or identity(git(p, 'remote', 'get-url', 'origin')) == key]
        if target in candidates:
            selected = target
        else:
            dev = [p for p in candidates if p.parent == developer]
            named = [p for p in candidates if p.name == target.name]
            selected = dev[0] if len(dev) == 1 else named[0] if len(named) == 1 else candidates[0] if len(candidates) == 1 else None
            if selected is None and parent:
                try:
                    clean = [p for p in candidates if not work(p)]
                    selected = clean[0] if len(clean) == 1 else None
                except (OSError, subprocess.SubprocessError):
                    pass
            if selected is None:
                reason = 'ambiguous canonical clone; decision required'
            elif target.exists() or target.is_symlink():
                reason = 'destination already exists'
        if parent and selected and reason is None:
            try:
                upstream = upstream_identity(selected)
                if upstream and upstream != parent:
                    reason = f'upstream remote differs from fork parent: {upstream}'
            except (OSError, subprocess.SubprocessError) as error:
                reason = f'cannot inspect upstream remote: {error}'
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
        if selected is not None and selected != target and reason is None:
            try:
                reason = move_blocker(selected, clone_worktrees[str(selected)])
            except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
                reason = f'cannot inspect T3 worktrees: {error}'
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
                        'move': selected is not None and selected != target and reason is None, 'duplicates': duplicates, 'linked_worktrees': clone_worktrees, 'parent_identity': parent, 'refusal': reason})
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
        if args.action == 'move' and entry['refusal']:
            failed = True
        if args.action == 'move' and entry['move']:
            old, new = Path(entry['selected']), Path(entry['canonical'])
            try:
                current_worktrees = linked_worktrees(old)
                blocker = move_blocker(old, current_worktrees)
                if blocker:
                    raise ValueError(blocker)
                if new.exists() or new.is_symlink():
                    raise ValueError('destination already exists')
                new.parent.mkdir(parents=True, exist_ok=True)
                relocate(old, new, current_worktrees)
                result['repoint'].append({'old': str(old), 'new': str(new)})
                if entry['parent_identity']:
                    try:
                        if upstream_identity(new) is None:
                            git(new, 'remote', 'add', 'upstream', 'https://' + entry['parent_identity'] + '.git')
                    except (OSError, subprocess.SubprocessError) as error:
                        result['actions'].append(f'moved {old} to {new}, but could not configure upstream: {error}')
                        failed = True
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                result['actions'].append(f'refused move {old}: {error}')
                failed = True
        if (args.action == 'move' and entry['parent_identity'] and not entry['refusal']
                and entry['selected'] == entry['canonical']):
            try:
                canonical = Path(entry['canonical'])
                if upstream_identity(canonical) is None:
                    git(canonical, 'remote', 'add', 'upstream', 'https://' + entry['parent_identity'] + '.git')
                    result['actions'].append(f'added upstream for {canonical}')
            except (OSError, subprocess.SubprocessError) as error:
                result['actions'].append(f'could not add upstream for {canonical}: {error}')
                failed = True
        if args.action == 'move' and not entry['refusal'] and entry['selected']:
            canonical = Path(entry['canonical'])
            if canonical.is_dir() and entry['identity'].startswith('github.com/'):
                try:
                    git(canonical, 'remote', 'set-url', '--push', 'origin',
                        'https://' + entry['identity'] + '.git')
                    git(canonical, 'config', '--local', '--replace-all', 'credential.https://github.com.helper', '')
                    git(canonical, 'config', '--local', '--add', 'credential.https://github.com.helper',
                        '!gh auth git-credential')
                except (OSError, subprocess.SubprocessError) as error:
                    result['actions'].append(f'could not configure HTTPS pushes for {canonical}: {error}')
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
                    if entry['parent_identity'] and upstream_identity(canonical) != entry['parent_identity']:
                        raise ValueError('canonical clone lacks the expected upstream remote')
                    duplicate_identity = identity(git(path, 'remote', 'get-url', 'origin'))
                    if duplicate_identity not in (entry['identity'], entry['parent_identity']):
                        raise ValueError('duplicate clone identity changed')
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
