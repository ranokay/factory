#!/usr/bin/env python3

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "environment_check.py"


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.git("init", "-q", "-b", "develop")
        self.git("config", "user.name", "Agent test")
        self.git("config", "user.email", "agent@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", "/dev/null")
        self.git("remote", "add", "origin", "https://github.com/example/project.git")
        (self.root / "AGENTS.md").write_text("Read the workflow.\n")
        self.git("add", "AGENTS.md")
        self.git("commit", "-qm", "baseline")
        self.git("update-ref", "refs/remotes/origin/develop", "HEAD")
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def check(self, cwd=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "check", "--cwd", str(cwd or self.root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def state(self, cwd=None, code=0):
        result = self.check(cwd)
        self.assertEqual(result.returncode, code, result.stderr)
        return json.loads(result.stdout)

    def test_subdirectory_uses_its_actual_root(self):
        nested = self.root / "nested"
        nested.mkdir()
        state = self.state(nested)
        self.assertEqual(state["root"], str(self.root))
        self.assertEqual(state["status"], "ok")
        self.assertTrue(state["contains_baseline"])
        self.assertEqual(state["instructions"], ["AGENTS.md"])

    def test_integration_branch_is_the_one_origin_head_names(self):
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.assertEqual(self.state()["integration_branch"], "origin/main")

    def test_checkout_behind_the_integration_branch_is_reported(self):
        self.git("commit", "-q", "--allow-empty", "-m", "newer")
        self.git("update-ref", "refs/remotes/origin/develop", "HEAD")
        self.git("checkout", "-q", "--detach", "HEAD~1")
        state = self.state(code=1)
        self.assertEqual(state["status"], "needs_preflight")
        self.assertIn("origin/develop", state["warnings"][0])

    def test_unset_origin_head_is_reported(self):
        self.git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
        state = self.state(code=1)
        self.assertEqual(state["status"], "needs_preflight")
        self.assertEqual(len(state["warnings"]), 1)

    def test_a_non_git_directory_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.check(Path(directory))
            state = json.loads(result.stdout)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(state["status"], "not_git")


class StartupTests(unittest.TestCase):
    def run_startup(self, payload):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "startup"],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_startup_reports_the_checkout_in_hook_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result = self.run_startup(json.dumps({"cwd": str(root)}))
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
            self.assertIn("Checkout preflight", output["hookSpecificOutput"]["additionalContext"])

    def test_startup_rejects_a_non_object_payload(self):
        result = self.run_startup("[]")
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid hook input", result.stderr)


if __name__ == "__main__":
    unittest.main()
