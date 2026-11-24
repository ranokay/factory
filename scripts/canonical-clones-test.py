import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

SCRIPT = Path(__file__).with_name('canonical-clones.py')
spec = importlib.util.spec_from_file_location('clones', SCRIPT)
clones = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clones)


def run(*args, cwd=None, check=True, input=None):
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True, input=input)


class ClonesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.developer = self.root / 'developer'
        self.source.mkdir()
        self.developer.mkdir()
        bindir = self.root / 'bin'
        bindir.mkdir()
        t3 = bindir / 't3'
        t3.write_text('#!/bin/sh\necho "No T3 Code project matches fixture" >&2\nexit 1\n')
        t3.chmod(0o755)
        env = patch.dict(os.environ, {'PATH': str(bindir) + os.pathsep + os.environ.get('PATH', '')})
        env.start()
        self.addCleanup(env.stop)

    def git(self, path, *args):
        return run('git', '-C', str(path), *args).stdout.strip()

    def repo(self, parent, name, remote='https://github.com/Owner/Repo.git'):
        path = parent / name
        run('git', 'init', '-q', str(path))
        self.git(path, 'config', 'user.email', 'fixture@example.com')
        self.git(path, 'config', 'user.name', 'Fixture')
        (path / 'file').write_text('one')
        self.git(path, 'add', 'file')
        self.git(path, 'commit', '-qm', 'initial')
        if remote:
            self.git(path, 'remote', 'add', 'origin', remote)
            branch = self.git(path, 'branch', '--show-current')
            self.git(path, 'update-ref', 'refs/remotes/origin/' + branch, 'HEAD')
            self.git(path, 'branch', '--set-upstream-to', 'origin/' + branch)
        return path

    def cli(self, *args, check=True):
        return run('python3', str(SCRIPT), *args, '--source-root', str(self.source), '--developer-root', str(self.developer), check=check)

    def test_github_repos_lists_only_main_clones_with_a_github_origin(self):
        canonical = self.repo(self.developer, 'repo')
        self.repo(self.developer, 'unknown', None)
        linked = self.developer / 'repo-linked'
        self.git(canonical, 'worktree', 'add', '-q', '-b', 'linked', str(linked))
        self.assertEqual(clones.github_repos(self.developer), [('github.com/owner/repo', canonical)])

    def test_identity_and_unknown(self):
        self.assertEqual(clones.identity('git@github.com:OWNER/Repo.git'), clones.identity('https://github.com/owner/repo'))
        self.assertNotEqual(clones.identity('https://github.com/owner/repo'), clones.identity('https://github.com/other/repo'))
        self.repo(self.source, 'unknown', None)
        self.assertIn('decision required', self.cli().stdout)

    def test_work_and_remove(self):
        self.repo(self.developer, 'repo')
        duplicate = self.repo(self.source, 'copy')
        (duplicate / 'file').write_text('changed')
        (duplicate / 'extra').write_text('new')
        self.assertIn('uncommitted or untracked', self.cli().stdout)
        self.assertNotEqual(self.cli('remove', '--approve-path', str(duplicate), check=False).returncode, 0)
        self.assertTrue(duplicate.exists())
        self.git(duplicate, 'add', 'file', 'extra')
        self.git(duplicate, 'stash')
        self.assertEqual(clones.work(duplicate), ['stashes'])
        self.git(duplicate, 'stash', 'drop')
        self.git(duplicate, 'branch', 'local')
        self.assertIn('local branches without upstream', clones.work(duplicate))
        self.git(duplicate, 'branch', '-D', 'local')
        (duplicate / 'file').write_text('commit')
        self.git(duplicate, 'commit', '-qam', 'local')
        self.assertIn('local commits not on remote-tracking branches', clones.work(duplicate))

    def test_per_path_and_non_tty(self):
        self.repo(self.developer, 'repo')
        a = self.repo(self.source, 'a')
        b = self.repo(self.source, 'b')
        self.assertNotEqual(self.cli('remove', check=False).returncode, 0)
        self.assertTrue(a.exists() and b.exists())
        self.cli('remove', '--approve-path', str(a))
        self.assertFalse(a.exists())
        self.assertTrue(b.exists())

    def test_recheck_after_approval(self):
        self.repo(self.developer, 'repo')
        duplicate = self.repo(self.source, 'copy')
        def approve(_prompt):
            (duplicate / 'new').write_text('late change')
            return 'y'
        argv = ['canonical-clones', 'remove', '--source-root', str(self.source), '--developer-root', str(self.developer)]
        with patch('sys.argv', argv), patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=approve), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(clones.main(), 1)
        self.assertTrue(duplicate.exists())

    def test_canonical_cannot_resolve_to_parent_repo(self):
        canonical = self.repo(self.developer, 'repo')
        duplicate = self.repo(self.source, 'copy')
        run('git', 'init', '-q', str(self.developer))
        self.git(self.developer, 'remote', 'add', 'origin', 'https://github.com/owner/repo')
        def approve(_prompt):
            shutil.rmtree(canonical / '.git')
            return 'y'
        argv = ['canonical-clones', 'remove', '--source-root', str(self.source), '--developer-root', str(self.developer)]
        with patch('sys.argv', argv), patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=approve), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(clones.main(), 1)
        self.assertTrue(duplicate.exists())

    def test_linked_duplicate_kept(self):
        self.repo(self.developer, 'repo')
        duplicate = self.repo(self.source, 'copy')
        self.git(duplicate, 'worktree', 'add', '-b', 'linked', str(self.root / 'linked'))
        result = self.cli('remove', '--approve-path', str(duplicate), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(duplicate.exists())

    def test_remove_requires_canonical_copy(self):
        self.repo(self.source, 'repo')
        duplicate = self.repo(self.source, 'copy')
        result = self.cli('remove', '--approve-path', str(duplicate), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(duplicate.exists())

    def test_detached_unpublished_commit(self):
        self.repo(self.developer, 'repo')
        duplicate = self.repo(self.source, 'copy')
        self.git(duplicate, 'checkout', '--detach')
        (duplicate / 'file').write_text('detached')
        self.git(duplicate, 'commit', '-qam', 'detached')
        result = self.cli('remove', '--approve-path', str(duplicate), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(duplicate.exists())

    def test_ambiguous_selection(self):
        self.repo(self.source, 'a')
        self.repo(self.source, 'b')
        output = self.cli('move', check=False).stdout
        self.assertIn('ambiguous canonical clone', output)
        self.assertFalse((self.developer / 'repo').exists())

    def test_collision(self):
        self.repo(self.source, 'one', 'https://github.com/a/repo')
        self.repo(self.source, 'two', 'https://github.com/b/repo')
        result = self.cli('move', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('repository name collision', result.stdout)
        self.assertFalse((self.developer / 'repo').exists())

    def test_occupied_destination(self):
        old = self.repo(self.source, 'repo')
        (self.developer / 'repo').mkdir()
        self.assertIn('destination already exists', self.cli('move', check=False).stdout)
        self.assertTrue(old.exists())

    def test_move_and_pairs(self):
        old = self.repo(self.source, 'repo')
        output = self.cli('move').stdout
        new = self.developer / 'repo'
        self.assertIn(f'{old} -> {new}', output)
        self.assertTrue(new.exists())
        new.rename(old)
        data = json.loads(self.cli('move', '--json').stdout)
        self.assertEqual(data['repoint'], [{'old': str(old), 'new': str(new)}])

    def test_move_configures_github_pushes_without_changing_fetch_or_other_hosts(self):
        moved_url = 'git@github.com:example/moved.git'
        canonical_url = 'git@github.com:example/canonical.git'
        other_url = 'https://gitlab.example/team/other.git'
        self.repo(self.source, 'moved', moved_url)
        canonical = self.repo(self.developer, 'canonical', canonical_url)
        other = self.repo(self.developer, 'other', other_url)
        other_push = 'ssh://git@gitlab.example/team/other.git'
        self.git(other, 'remote', 'set-url', '--push', 'origin', other_push)
        self.git(other, 'config', '--local', 'credential.https://github.com.helper', '!custom-helper')
        self.cli('move')
        moved = self.developer / 'moved'
        for path, fetch, push in ((moved, moved_url, 'https://github.com/example/moved.git'),
                                  (canonical, canonical_url, 'https://github.com/example/canonical.git')):
            self.assertEqual(self.git(path, 'remote', 'get-url', 'origin'), fetch)
            self.assertEqual(self.git(path, 'remote', 'get-url', '--push', 'origin'), push)
            result = run('git', '-C', str(path), 'config', '--local', '--get-all', 'credential.https://github.com.helper')
            self.assertEqual(result.stdout.splitlines(), ['', '!gh auth git-credential'])
        self.assertEqual(self.git(other, 'remote', 'get-url', 'origin'), other_url)
        self.assertEqual(self.git(other, 'remote', 'get-url', '--push', 'origin'), other_push)
        result = run('git', '-C', str(other), 'config', '--local', '--get-all', 'credential.https://github.com.helper', check=False)
        self.assertEqual(result.stdout.splitlines(), ['!custom-helper'])

    def test_failed_duplicate_worktree_inspection_is_not_clean(self):
        self.repo(self.developer, 'repo')
        duplicate = self.repo(self.source, 'copy')
        original = clones.linked_worktrees
        def inspect(path):
            if path == duplicate:
                raise OSError('fixture failure')
            return original(path)
        with patch.object(clones, 'linked_worktrees', side_effect=inspect):
            result = clones.inventory([self.source, self.developer], self.developer)
        entry = result['repositories'][0]['duplicates'][0]
        self.assertIn('worktree inspection failed: fixture failure', entry['unpublished'])
        with patch.object(clones, 'linked_worktrees', side_effect=inspect), patch.object(clones, 'work', side_effect=OSError('status failure')):
            result = clones.inventory([self.source, self.developer], self.developer)
        reasons = result['repositories'][0]['duplicates'][0]['unpublished']
        self.assertIn('worktree inspection failed: fixture failure', reasons)
        self.assertIn('inspection failed: status failure', reasons)

    def test_source_linked_worktree_and_report_move_parity(self):
        main = self.repo(self.source, 'repo')
        linked = self.source / 'linked'
        self.git(main, 'worktree', 'add', '-b', 'linked', str(linked))
        movable = self.repo(self.source, 'other', 'https://github.com/example/other')
        report = json.loads(self.cli('report', '--json').stdout)
        entry = next(e for e in report['repositories'] if e['selected'] == str(main))
        self.assertEqual(entry['paths'], [str(main)])
        self.assertEqual(entry['duplicates'], [])
        self.assertIn(str(linked), entry['linked_worktrees'][str(main)])
        self.assertTrue(entry['move'])
        self.assertIsNone(entry['refusal'])
        self.assertIn(f'linked worktree of {main}: {linked}', self.cli().stdout)
        self.cli('remove', '--approve-path', str(linked))
        self.assertTrue(linked.exists())
        moved = json.loads(self.cli('move', '--json').stdout)
        moved_paths = {pair['old'] for pair in moved['repoint']}
        for repo in report['repositories']:
            self.assertEqual(repo['move'], repo['selected'] in moved_paths)
        self.assertIn(str(movable), moved_paths)
        self.assertFalse(main.exists())
        self.git(linked, 'status', '--porcelain')

    def test_move_repairs_two_linked_worktrees(self):
        old = self.repo(self.source, 'repo')
        trees = [self.root / 'one', self.root / 'two']
        for index, tree in enumerate(trees):
            self.git(old, 'worktree', 'add', '-b', f'branch{index}', str(tree))
        result = json.loads(self.cli('move', '--json').stdout)
        new = self.developer / 'repo'
        self.assertEqual(result['repoint'], [{'old': str(old), 'new': str(new)}])
        for tree in trees:
            self.assertIn(str(new), self.git(tree, 'worktree', 'list'))
            self.assertEqual(Path(self.git(tree, 'rev-parse', '--git-common-dir')).resolve(), (new / '.git').resolve())

    def test_move_exit_for_dirty_worktree_and_inspection_failure(self):
        old = self.repo(self.source, 'repo')
        tree = self.root / 'linked'
        self.git(old, 'worktree', 'add', '-b', 'linked', str(tree))
        (tree / 'new').write_text('dirty')
        result = self.cli('move', '--json', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('uncommitted work', json.loads(result.stdout)['repositories'][0]['refusal'])
        self.assertTrue(old.exists())
        (tree / 'new').unlink()
        argv = ['canonical-clones', 'move', '--source-root', str(self.source), '--developer-root', str(self.developer)]
        with patch('sys.argv', argv), patch.object(clones, 'linked_worktrees', side_effect=OSError('fixture failure')), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(clones.main(), 1)
        self.assertTrue(old.exists())

    def test_t3_running_and_dirty_refuse(self):
        old = self.repo(self.source, 'repo')
        tree = self.root / '.t3/worktrees/repo/tree'
        tree.parent.mkdir(parents=True)
        self.git(old, 'worktree', 'add', '-b', 'linked', str(tree))
        real_run = subprocess.run
        def fake_run(args, **kwargs):
            if args[0] == 't3':
                if args[2] == 'list':
                    data = [{'threadId': 'test', 'status': 'running'}]
                else:
                    data = {'thread': {'worktreePath': str(tree)}}
                return subprocess.CompletedProcess(args, 0, json.dumps(data), '')
            return real_run(args, **kwargs)
        with patch.object(Path, 'home', return_value=self.root), patch.object(subprocess, 'run', side_effect=fake_run):
            self.assertIn('active T3 thread', clones.move_blocker(old, [str(tree)]))
            (tree / 'new').write_text('dirty')
            self.assertIn('uncommitted work', clones.move_blocker(old, [str(tree)]))
            (tree / 'new').unlink()
            external = self.root / 'elsewhere'
            self.git(old, 'worktree', 'add', '-b', 'elsewhere', str(external))
            def fake_external(args, **kwargs):
                if args[0] == 't3':
                    data = [{'threadId': 'test', 'status': 'running'}] if args[2] == 'list' else {'thread': {'worktreePath': str(external)}}
                    return subprocess.CompletedProcess(args, 0, json.dumps(data), '')
                return real_run(args, **kwargs)
            with patch.object(subprocess, 'run', side_effect=fake_external):
                self.assertIn('active T3 thread', clones.move_blocker(old, [str(external)]))

    def test_upstream_failure_reports_completed_move(self):
        fork = self.repo(self.source, 'demo', 'https://github.com/ranokay/demo')
        self.repo(self.source, 'upstream', 'https://github.com/other/demo')
        real_git = clones.git
        def fail_add(path, *args):
            if args[:3] == ('remote', 'add', 'upstream'):
                raise OSError('fixture failure')
            return real_git(path, *args)
        argv = ['canonical-clones', 'move', '--json', '--source-root', str(self.source), '--developer-root', str(self.developer)]
        with patch('sys.argv', argv), patch.object(clones, 'github_parent', return_value='github.com/other/demo'), patch.object(clones, 'git', side_effect=fail_add), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(clones.main(), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['repoint'][0]['old'], str(fork))
        self.assertTrue((self.developer / 'demo').exists())
        self.assertIn('moved ', result['actions'][0])
        self.assertIn('could not configure upstream', result['actions'][0])

    def test_fork_parent_collision_migrates_and_preserves_unpushed(self):
        fork = self.repo(self.source, 'fork', 'https://github.com/ranokay/demo')
        extra = self.repo(self.source, 'issue', 'https://github.com/ranokay/demo')
        upstream = self.repo(self.source, 'upstream', 'https://github.com/other/demo')
        (extra / 'file').write_text('unpublished')
        self.git(extra, 'commit', '-qam', 'unpublished')
        argv = ['canonical-clones', 'move', '--json', '--source-root', str(self.source), '--developer-root', str(self.developer)]
        with patch('sys.argv', argv), patch.object(clones, 'github_parent', return_value='github.com/other/demo'), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(clones.main(), 0)
        data = json.loads(output.getvalue())
        self.assertEqual(len(data['repositories']), 1)
        self.assertEqual(data['repoint'][0]['old'], str(fork))
        self.assertEqual(clones.upstream_identity(self.developer / 'demo'), 'github.com/other/demo')
        self.assertTrue(extra.exists())
        self.assertTrue(upstream.exists())
        entry = data['repositories'][0]
        self.assertTrue(next(x for x in entry['duplicates'] if x['path'] == str(extra))['unpublished'])
        argv = ['canonical-clones', 'remove', '--approve-path', str(upstream), '--source-root', str(self.source), '--developer-root', str(self.developer)]
        with patch('sys.argv', argv), patch.object(clones, 'github_parent', return_value='github.com/other/demo'), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(clones.main(), 0)
        self.assertFalse(upstream.exists())
        self.assertTrue(extra.exists())



if __name__ == '__main__':
    unittest.main()
