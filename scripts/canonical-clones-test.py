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
        output = self.cli('move').stdout
        self.assertIn('ambiguous canonical clone', output)
        self.assertFalse((self.developer / 'repo').exists())

    def test_collision(self):
        self.repo(self.source, 'one', 'https://github.com/a/repo')
        self.repo(self.source, 'two', 'https://github.com/b/repo')
        self.assertIn('repository name collision', self.cli('move').stdout)
        self.assertFalse((self.developer / 'repo').exists())

    def test_occupied_destination(self):
        old = self.repo(self.source, 'repo')
        (self.developer / 'repo').mkdir()
        self.assertIn('destination already exists', self.cli('move').stdout)
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
        self.assertFalse(entry['move'])
        self.assertIn(str(linked), entry['refusal'])
        self.assertIn(f'linked worktree of {main}: {linked}', self.cli().stdout)
        self.cli('remove', '--approve-path', str(linked))
        self.assertTrue(linked.exists())
        moved = json.loads(self.cli('move', '--json').stdout)
        moved_paths = {pair['old'] for pair in moved['repoint']}
        for repo in report['repositories']:
            self.assertEqual(repo['move'], repo['selected'] in moved_paths)
        self.assertIn(str(movable), moved_paths)
        self.assertTrue(main.exists())
        self.git(linked, 'status', '--porcelain')

    def test_linked_worktree_refused(self):
        old = self.repo(self.source, 'repo')
        linked = self.root / 'linked'
        self.git(old, 'worktree', 'add', '-b', 'linked', str(linked))
        result = self.cli('move', check=False)
        self.assertIn('linked worktrees', result.stdout)
        self.assertTrue(old.exists())
        self.git(linked, 'status', '--porcelain')


if __name__ == '__main__':
    unittest.main()
