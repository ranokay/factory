#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 -- the fixture drives the local recorder and git
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
RECORDER = TOOLS / "check_result.py"


class CheckResultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = dict(os.environ, XDG_CACHE_HOME=str(self.root / "cache"))
        self.git("init", "-q")
        self.git("config", "user.name", "Run guard test")
        self.git("config", "user.email", "run-guard@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "README").write_text("fixture\n")
        self.git("add", "README")
        self.git("commit", "-qm", "baseline")

    def git(self, *args: str) -> str:
        result = subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local git with an argument list
            ["git", "-C", str(self.repo), *args],
            text=True,
            capture_output=True,
            check=True,
            env=self.env,
        )
        return result.stdout.strip()

    def record(self, *command: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = subprocess.run(  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- this checkout's recorder
            [sys.executable, str(RECORDER), "--", *command],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
            env=self.env,
        )
        paths = list((self.root / "cache" / "factory" / "check-results").glob("*/*.json"))
        self.assertEqual(len(paths), 1, result.stderr)
        return result, json.loads(paths[0].read_text())

    def test_clean_success_records_a_pass_for_head(self):
        head = self.git("rev-parse", "HEAD")
        result, record = self.record("true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(record["head"], head)
        self.assertTrue(record["passed"])
        self.assertTrue(record["clean_before"])
        self.assertTrue(record["clean_after"])

    def test_red_check_records_a_failure(self):
        result, record = self.record("sh", "-c", "exit 7")
        self.assertEqual(result.returncode, 7)
        self.assertFalse(record["passed"])
        self.assertEqual(record["exit_code"], 7)

    def test_dirty_tree_before_check_cannot_record_a_pass(self):
        (self.repo / "README").write_text("changed\n")
        result, record = self.record("true")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(record["passed"])
        self.assertFalse(record["clean_before"])

    def test_tree_dirtied_by_check_cannot_record_a_pass(self):
        result, record = self.record("sh", "-c", "printf generated > generated")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(record["passed"])
        self.assertTrue(record["clean_before"])
        self.assertFalse(record["clean_after"])

    def test_factory_and_project_template_checks_invoke_the_recorder(self):
        root = TOOLS.parent
        factory = tomllib.loads((root / "mise.toml").read_text())["tasks"]["check"]
        template = tomllib.loads((root / "project" / "mise.toml").read_text())["tasks"]["check"]
        self.assertIn("tools/check_result.py", json.dumps(factory))
        self.assertIn(".agents/tools/run", json.dumps(template))
        self.assertIn("check_result", json.dumps(template))

    def test_project_template_falls_back_without_factory_bootstrap(self):
        root = TOOLS.parent
        run = tomllib.loads((root / "project" / "mise.toml").read_text())["tasks"]["check"]["run"]
        binary = self.root / "bin"
        binary.mkdir()
        log = self.root / "mise.log"
        mise = binary / "mise"
        mise.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >>{log}\n")
        mise.chmod(0o755)
        home = self.root / "home"
        home.mkdir()
        env = dict(self.env, HOME=str(home), PATH=str(binary) + os.pathsep + self.env["PATH"])
        result = subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- the fixture runs the template check with a fake mise
            ["sh", "-c", run],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.read_text().splitlines(), ["run check:lint", "run check:security", "run check:test"])

    def test_failed_recorder_start_cannot_leave_a_stale_pass(self):
        self.assertEqual(self.record("true")[0].returncode, 0)
        path = next((self.root / "cache" / "factory" / "check-results").glob("*/*.json"))
        result = subprocess.run(  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- this checkout's recorder
            [sys.executable, str(RECORDER), "--", "missing-check-command"],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
            env=self.env,
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
