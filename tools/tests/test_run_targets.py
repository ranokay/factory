#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 -- the fixture drives the local target CLI
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
TARGETS = TOOLS / "run_targets.py"


class RunTargetsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = dict(os.environ, XDG_CACHE_HOME=str(self.root / "cache"))
        self.primary = json.dumps({"providerInstanceId": "opencode", "model": "opencode-go/hungerg-free", "options": {}})
        self.fallback = json.dumps(
            {"providerInstanceId": "opencode", "model": "opencode-go/hy4-flash-free", "options": {"variant": "high"}}
        )

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- this checkout's target CLI
            [sys.executable, str(TARGETS), *arguments],
            text=True,
            capture_output=True,
            check=False,
            env=self.env,
        )

    def path(self) -> Path:
        return self.root / "cache" / "factory" / "runs" / "o" / "one" / "6" / "targets.json"

    def test_set_preserves_target_order(self):
        result = self.invoke("set", "o/one", "6", self.primary, self.fallback)
        self.assertEqual(result.returncode, 0, result.stderr)
        targets = json.loads(self.path().read_text())["targets"]
        self.assertEqual([target["model"] for target in targets], ["opencode-go/hungerg-free", "opencode-go/hy4-flash-free"])
        self.assertFalse(any(target["tried"] for target in targets))

    def test_mark_advances_the_next_untried_target(self):
        self.assertEqual(self.invoke("set", "o/one", "6", self.primary, self.fallback).returncode, 0)
        marked = self.invoke("mark", "o/one", "6", self.primary)
        self.assertEqual(marked.returncode, 0, marked.stderr)
        following = self.invoke("next", "o/one", "6")
        self.assertEqual(following.returncode, 0, following.stderr)
        self.assertEqual(json.loads(following.stdout)["model"], "opencode-go/hy4-flash-free")

    def test_mark_marks_every_duplicate_target_identity(self):
        self.assertEqual(self.invoke("set", "o/one", "6", self.primary, self.primary, self.fallback).returncode, 0)
        marked = self.invoke("mark", "o/one", "6", self.primary)
        self.assertEqual(marked.returncode, 0, marked.stderr)
        targets = json.loads(self.path().read_text())["targets"]
        self.assertEqual([target["tried"] for target in targets], [True, True, False])
        following = self.invoke("next", "o/one", "6")
        self.assertEqual(json.loads(following.stdout)["model"], "opencode-go/hy4-flash-free")

    def test_invalid_repository_cannot_write_targets(self):
        result = self.invoke("set", "../evil", "6", self.primary)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.path().exists())


if __name__ == "__main__":
    unittest.main()
