#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess  # nosec B404  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- fixture local tools
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
GUARD = TOOLS / "run_guard.py"


class RunGuardFixture(unittest.TestCase):
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
        result = subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local fixture subprocess
            ["git", "-C", str(self.repo), *args],
            text=True,
            capture_output=True,
            check=True,
            env=self.env,
        )
        return result.stdout.strip()

    def invoke(self, command: str, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local fixture subprocess
            [sys.executable, str(GUARD), "tool"],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd or self.repo)}),
            text=True,
            capture_output=True,
            check=False,
            cwd=cwd or self.repo,
            env=self.env,
        )

    def invoke_mode(self, mode: str, payload: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local fixture subprocess
            [sys.executable, str(GUARD), mode],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            cwd=self.repo,
            env=self.env,
        )

    def output(self, result: subprocess.CompletedProcess[str]) -> dict:
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else {}

    def write_mise(self, command: str):
        (self.repo / "mise.toml").write_text(f'[tasks.check]\nrun = {json.dumps(command)}\n')
        self.git("add", "mise.toml")
        self.git("commit", "-qm", "check task")

    def record_check(self, *command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local fixture subprocess
            [sys.executable, str(TOOLS / "check_result.py"), "--", *command],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
            env=self.env,
        )

    def gh_stub(
        self,
        *,
        head: str = "sync/upstream-2026-11-28",
        topics: str = "factory-personal-fork",
        topics_json: str | None = None,
        pr_json: str | None = None,
    ):
        binary = self.root / "bin"
        binary.mkdir(exist_ok=True)
        script = binary / "gh"
        script.write_text(
            "#!/bin/sh\n"
            "printf 'cwd=%s %s\\n' \"$PWD\" \"$*\" >>\"$GH_LOG\"\n"
            "case \"$1 $2\" in\n"
            "  'pr view') [ -z \"${GH_PR_FAIL:-}\" ] || { printf '%s\\n' \"${GH_PR_ERROR:-no pull requests found for branch}\" >&2; exit 1; }; if [ -n \"${GH_PR_JSON:-}\" ]; then printf '%s\\n' \"$GH_PR_JSON\"; else printf '{\"number\":42,\"headRefName\":\"%s\",\"url\":\"https://github.com/o/one/pull/42\"}\\n' \"$GH_HEAD\"; fi ;;\n"
            "  'pr checks') printf '%s\\n' \"$GH_CHECKS\"; exit \"$GH_CHECKS_CODE\" ;;\n"
            "  'repo view') if [ -n \"${GH_TOPICS_JSON:-}\" ]; then printf '%s\\n' \"$GH_TOPICS_JSON\"; else printf '{\"repositoryTopics\":[{\"name\":\"%s\"}]}\\n' \"$GH_TOPICS\"; fi ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n"
        )
        script.chmod(0o755)
        self.env["PATH"] = str(binary) + os.pathsep + self.env["PATH"]
        self.env["GH_HEAD"] = head
        self.env["GH_TOPICS"] = topics
        self.env["GH_TOPICS_JSON"] = topics_json or ""
        self.env["GH_LOG"] = str(self.root / "gh.log")
        self.env["GH_PR_FAIL"] = ""
        self.env["GH_PR_ERROR"] = "no pull requests found for branch"
        self.env["GH_PR_JSON"] = pr_json or ""
        self.env["GH_CHECKS"] = "[]"
        self.env["GH_CHECKS_CODE"] = "0"

    def target_command(self, command: str, *targets: str) -> None:
        result = subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local fixture subprocess
            [sys.executable, str(TOOLS / "run_targets.py"), command, "o/one", "6", *targets],
            text=True,
            capture_output=True,
            check=False,
            env=self.env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def fallback_command(self, reason: str, unblock: str = "Launch the next target") -> str:
        return f"skills/supervise/scripts/block-issue o/one 6 --reason '{reason}' --unblock '{unblock}'"
