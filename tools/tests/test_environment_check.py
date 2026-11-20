"""Checkout identity is observed from the requested working directory."""

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("environment_check", Path(__file__).parents[1] / "environment_check.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LowDiskTests(unittest.TestCase):
    def free_bytes(self, free):
        return unittest.mock.patch.object(MODULE.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(0, 0, free))

    def test_warns_below_the_threshold_with_the_free_space(self):
        with self.free_bytes(34 * MODULE.GIB):
            self.assertIn("34 GiB free", MODULE.low_disk_warning(Path("/")))

    def test_stays_silent_at_the_threshold(self):
        with self.free_bytes(MODULE.LOW_DISK_BYTES):
            self.assertEqual(MODULE.low_disk_warning(Path("/")), "")

    def test_a_missing_path_is_not_a_warning(self):
        self.assertEqual(MODULE.low_disk_warning(Path("/nonexistent/agent-test")), "")


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

    def test_subdirectory_uses_its_actual_root(self):
        nested = self.root / "nested"
        nested.mkdir()
        state = MODULE.checkout(nested)
        self.assertEqual(state["root"], str(self.root))
        self.assertEqual(state["status"], "ok")
        self.assertTrue(state["contains_baseline"])

    def test_integration_branch_is_the_one_origin_head_names(self):
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.assertEqual(MODULE.checkout(self.root)["integration_branch"], "origin/main")

    def test_checkout_behind_the_integration_branch_is_reported(self):
        self.git("commit", "-q", "--allow-empty", "-m", "newer")
        self.git("update-ref", "refs/remotes/origin/develop", "HEAD")
        self.git("checkout", "-q", "--detach", "HEAD~1")
        state = MODULE.checkout(self.root)
        self.assertEqual(state["status"], "needs_preflight")
        self.assertIn("origin/develop", state["warnings"][0])

    def test_unset_origin_head_is_reported(self):
        self.git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
        state = MODULE.checkout(self.root)
        self.assertEqual(state["status"], "needs_preflight")
        self.assertEqual(len(state["warnings"]), 1)

    def test_startup_hook_reports_the_checkout(self):
        payload = MODULE.hook_output("SessionStart", "Checkout preflight: ok")
        self.assertEqual(payload["hookSpecificOutput"]["additionalContext"], "Checkout preflight: ok")

    def test_non_repository_has_a_distinct_result(self):
        self.assertEqual(MODULE.checkout(self.root.parent)["status"], "not_git")


if __name__ == "__main__":
    unittest.main()
