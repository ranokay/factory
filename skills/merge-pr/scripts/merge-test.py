import json
import os
import subprocess  # nosec B404 -- the tests run the merge script with a fake gh
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MERGE = ROOT / "skills/merge-pr/scripts/merge"
SHA = "b" * 40

GH = '''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ["FAKE_LOG"]).open("a") as handle:
    handle.write(json.dumps(args) + "\\n")
state = json.loads(Path(os.environ["FAKE_STATE"]).read_text())
if args[:1] == ["api"]:
    if state.get("api_fail"):
        sys.exit(1)
    print(json.dumps({
        "user": {"login": state.get("author", "ranokay")},
        "head": {
            "sha": state.get("head", "b" * 40),
            "repo": None if state.get("deleted") else {"full_name": state.get("head_repo", "o/r")},
        },
    }))
elif args[:2] == ["pr", "merge"]:
    if state.get("merge_fail"):
        sys.exit(1)
    print("merged")
else:
    sys.exit(1)
'''


class Merge(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        binary = self.root / "bin"
        binary.mkdir()
        (binary / "gh").write_text(GH)
        (binary / "gh").chmod(0o755)
        self.log = self.root / "log"
        self.state = self.root / "state.json"
        self.env = dict(
            os.environ,
            PATH=f"{binary}:{os.environ['PATH']}",
            FAKE_LOG=str(self.log),
            FAKE_STATE=str(self.state),
        )
        self.reset()

    def tearDown(self):
        self.temporary.cleanup()

    def reset(self, **state):
        self.state.write_text(json.dumps(state))
        self.log.write_text("")

    def merge(self, *arguments):
        # nosemgrep
        return subprocess.run(  # nosec B603 -- this checkout's merge script
            [sys.executable, str(MERGE), "o/r", "7", "--head", SHA, *arguments],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    def test_guard_copy_matches_the_babysit_pr_skill(self):
        self.assertEqual(
            (ROOT / "skills/babysit-pr/scripts/merge_guard.py").read_text(),
            (ROOT / "skills/merge-pr/scripts/merge_guard.py").read_text(),
        )

    def test_factory_squash_pins_the_head(self):
        result = self.merge("--squash")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2, calls)
        self.assertEqual(calls[0][0], "api")
        self.assertEqual(
            calls[1], ["pr", "merge", "7", "-R", "o/r", "--match-head-commit", SHA, "--squash"]
        )

    def test_merge_commit_uses_merge(self):
        result = self.merge("--merge-commit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[1][-1], "--merge")

    def test_factory_bot_is_allowed(self):
        self.reset(author="renovate[bot]")
        result = self.merge("--squash")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls()), 2)

    def test_outside_contributor_is_refused_before_merging(self):
        for state in ({"author": "other"}, {"head_repo": "other/r"}, {"deleted": True}):
            with self.subTest(state=state):
                self.reset(**state)
                result = self.merge("--squash")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Outside Contributor PR", result.stderr)
                self.assertEqual(len(self.calls()), 1, self.calls())

    def test_reading_the_pr_failure_refuses(self):
        self.reset(api_fail=True)
        result = self.merge("--squash")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reading o/r#7 failed", result.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_merge_failure_is_not_reported_as_merged(self):
        self.reset(merge_fail=True)
        result = self.merge("--squash")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("merging o/r#7 failed", result.stderr)
        self.assertNotIn("merged o/r#7", result.stdout)

    def test_usage_errors_refuse_without_calling_gh(self):
        for arguments in (("--squash", "--merge-commit"), ()):
            with self.subTest(arguments=arguments):
                result = self.merge(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.calls(), [])

    def test_short_head_is_refused(self):
        # nosemgrep
        result = subprocess.run(  # nosec B603 -- this checkout's merge script
            [sys.executable, str(MERGE), "o/r", "7", "--head", "abc", "--squash"],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("40-character", result.stderr)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
