#!/usr/bin/env python3

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
GUARD = TOOLS / "secret_guard.py"


def context7_token() -> str:
    return "ctx7" + "sk-" + secrets.token_hex(32)


def random_alnum(length: int) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def fallback_fixtures() -> list[tuple[str, str]]:
    pem_body = base64.b64encode(secrets.token_bytes(64)).decode()
    fixtures = [
        ("private-key", f"-----BEGIN RSA PRIVATE KEY-----\n{pem_body}\n-----END RSA PRIVATE KEY-----"),
        ("aws-access-key-id", "AKIA" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))),
        ("aws-access-key-id", "ASIA" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))),
        ("google-api-key", "AIza" + random_alnum(35)),
        ("openai-api-key", "sk-" + random_alnum(32)),
        ("anthropic-api-key", "sk-ant-" + random_alnum(32)),
        ("literal-credential", "api_token = " + random_alnum(32)),
    ]
    fixtures.extend(("github-token", prefix + random_alnum(36)) for prefix in ("ghp_", "gho_", "ghu_", "ghs_"))
    fixtures.append(("github-token", "github_pat_" + random_alnum(36)))
    fixtures.extend(("slack-token", prefix + random_alnum(24)) for prefix in ("xoxa-", "xoxb-", "xoxp-", "xoxr-", "xoxs-"))
    return fixtures


class SecretGuardTests(unittest.TestCase):
    def invoke(
        self,
        mode: str,
        payload: dict | str,
        *,
        env: dict[str, str] | None = None,
        guard: Path = GUARD,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(guard), mode],
            input=payload if isinstance(payload, str) else json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def test_malformed_input_fails_closed_without_echoing_it(self):
        token = context7_token()
        result = self.invoke("prompt", f'{{"prompt":"{token}"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid JSON input", result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_gitleaks_blocks_a_prompt_without_echoing_the_fixture(self):
        token = context7_token()
        result = self.invoke("prompt", {"prompt": f"Use this credential: {token}"})
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["decision"], "block")
        self.assertIn("context7-key-token", output["reason"])
        self.assertTrue(output["hookSpecificOutput"]["suppressOriginalPrompt"])
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_gitleaks_blocks_serialised_input_from_any_tool(self):
        token = context7_token()
        result = self.invoke(
            "tool",
            {"tool_name": "Write", "tool_input": {"path": "credential.txt", "content": f"token={token}"}},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("context7-key-token", output["permissionDecisionReason"])
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_opencode_mode_reports_a_block_to_the_plugin(self):
        token = context7_token()
        result = self.invoke("opencode", {"tool": "bash", "input": {"command": f"echo {token}"}})
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["block"])
        self.assertIn("context7-key-token", output["message"])
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_missing_gitleaks_blocks_a_context7_fixture_with_the_fallback(self):
        token = context7_token()
        with tempfile.TemporaryDirectory() as empty_path:
            env = os.environ.copy()
            env["PATH"] = empty_path
            result = self.invoke("prompt", {"prompt": token}, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["decision"], "block")
        self.assertIn("context7-key-token", output["reason"])
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_missing_gitleaks_blocks_common_token_shapes_without_echoing_them(self):
        with tempfile.TemporaryDirectory() as empty_path:
            env = os.environ.copy()
            env["PATH"] = empty_path
            for rule_id, token in fallback_fixtures():
                with self.subTest(rule_id=rule_id, token=token[:12]):
                    result = self.invoke("prompt", {"prompt": f"Credential: {token}"}, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    output = json.loads(result.stdout)
                    self.assertEqual(output["decision"], "block")
                    self.assertIn(rule_id, output["reason"])
                    self.assertNotIn(token, result.stdout + result.stderr)

    def test_failing_gitleaks_uses_the_fallback_without_echoing_its_output(self):
        token = context7_token()
        with tempfile.TemporaryDirectory() as bin_dir:
            gitleaks = Path(bin_dir) / "gitleaks"
            gitleaks.write_text("#!/bin/sh\ninput=$(cat)\nprintf '%s\\n' \"$input\"\nprintf '%s\\n' \"$input\" >&2\nexit 2\n")
            gitleaks.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = bin_dir
            result = self.invoke("tool", {"tool_name": "Bash", "tool_input": {"command": f"echo {token}"}}, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("context7-key-token", output["permissionDecisionReason"])
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_timed_out_gitleaks_uses_the_fallback(self):
        token = context7_token()
        with tempfile.TemporaryDirectory() as bin_dir:
            gitleaks = Path(bin_dir) / "gitleaks"
            gitleaks.write_text("#!/bin/sh\n/bin/sleep 10\n")
            gitleaks.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = bin_dir
            result = self.invoke("prompt", {"prompt": token}, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("context7-key-token", json.loads(result.stdout)["reason"])
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_missing_gitleaks_warns_when_the_fallback_passes_a_keyword_input(self):
        with tempfile.TemporaryDirectory() as empty_path:
            env = os.environ.copy()
            env["PATH"] = empty_path
            result = self.invoke("prompt", {"prompt": "Check the API configuration."}, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        warning = json.loads(result.stdout)["systemMessage"]
        self.assertIn("gitleaks not found", warning)
        self.assertIn("fallback scan passed", warning)
        self.assertEqual(result.stderr, "")

    def test_input_without_keywords_skips_gitleaks(self):
        cases = (
            ("prompt", {"prompt": "Summarise the current branch."}),
            ("tool", {"tool_name": "Bash", "tool_input": {"command": "git status --short"}}),
        )
        for mode, payload in cases:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as bin_dir:
                marker = Path(bin_dir) / "spawned"
                gitleaks = Path(bin_dir) / "gitleaks"
                gitleaks.write_text('#!/bin/sh\n: >"$GUARD_MARKER"\nexit 2\n')
                gitleaks.chmod(0o755)
                env = os.environ.copy()
                env["PATH"] = bin_dir
                env["GUARD_MARKER"] = str(marker)
                result = self.invoke(mode, payload, env=env)
                spawned = marker.exists()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")
                self.assertFalse(spawned)

    def test_input_with_a_keyword_runs_gitleaks(self):
        with tempfile.TemporaryDirectory() as bin_dir:
            marker = Path(bin_dir) / "spawned"
            gitleaks = Path(bin_dir) / "gitleaks"
            gitleaks.write_text('#!/bin/sh\n: >"$GUARD_MARKER"\nexit 0\n')
            gitleaks.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = bin_dir
            env["GUARD_MARKER"] = str(marker)
            result = self.invoke("prompt", {"prompt": "Check the API configuration."}, env=env)
            spawned = marker.exists()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertTrue(spawned)

    def test_missing_or_invalid_keyword_catalog_always_runs_gitleaks(self):
        for catalog in (None, "not json"):
            with self.subTest(catalog=catalog), tempfile.TemporaryDirectory() as case_dir:
                root = Path(case_dir)
                tools = root / "tools"
                tools.mkdir()
                guard = tools / "secret_guard.py"
                shutil.copy2(GUARD, guard)
                if catalog is not None:
                    (tools / "gitleaks-keywords.json").write_text(catalog)
                bin_dir = root / "bin"
                bin_dir.mkdir()
                marker = root / "spawned"
                gitleaks = bin_dir / "gitleaks"
                gitleaks.write_text('#!/bin/sh\n: >"$GUARD_MARKER"\nexit 0\n')
                gitleaks.chmod(0o755)
                env = os.environ.copy()
                env["PATH"] = str(bin_dir)
                env["GUARD_MARKER"] = str(marker)
                result = self.invoke("prompt", {"prompt": "Summarise the current branch."}, env=env, guard=guard)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertTrue(marker.exists())

    def test_ordinary_input_passes_in_every_mode(self):
        cases = (
            ("prompt", {"prompt": "Summarise the current branch."}),
            ("tool", {"tool_name": "Bash", "tool_input": {"command": "git status --short"}}),
            ("opencode", {"tool": "bash", "input": {"command": "git status --short"}}),
        )
        for mode, payload in cases:
            with self.subTest(mode=mode):
                result = self.invoke(mode, payload)
                self.assertEqual(result.returncode, 0, result.stderr)
                if mode == "opencode":
                    self.assertEqual(json.loads(result.stdout), {"block": False})
                else:
                    self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
