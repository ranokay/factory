#!/usr/bin/env python3

from __future__ import annotations

import base64
import contextlib
import gzip
import io
import json
import os
import runpy
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


def encoded_fixtures() -> list[tuple[str, str, str]]:
    keywords = json.loads((TOOLS / "gitleaks-keywords.json").read_text())["keywords"]

    def has_keyword(value: str) -> bool:
        return any(keyword in value.lower() for keyword in keywords)

    fixtures = []
    factories = (
        ("github-pat", lambda: "ghp_" + random_alnum(36)),
        ("context7-key-token", context7_token),
    )
    for rule_id, factory in factories:
        for _ in range(100):
            token = factory()
            encodings = (
                base64.b64encode(token.encode()).decode(),
                token.encode().hex(),
                "".join(f"%{byte:02X}" for byte in token.encode()),
                "".join(f"\\u{ord(char):04x}" for char in token),
            )
            if not any(has_keyword(value) for value in encodings):
                fixtures.extend((rule_id, token, value) for value in encodings)
                break
        else:
            raise AssertionError("cannot generate an encoded fixture without plaintext keywords")
    for _ in range(100):
        nested_token = context7_token()
        nested = base64.b64encode(nested_token.encode()).decode()
        for _ in range(4):
            nested = base64.b64encode(nested.encode()).decode()
        if not has_keyword(nested):
            fixtures.append(("context7-key-token", nested_token, nested))
            break
    else:
        raise AssertionError("cannot generate a nested fixture without plaintext keywords")
    return fixtures


def long_mixed_fixture() -> tuple[str, str]:
    token = context7_token()
    value = "".join(f"\\\\u{ord(char):04x}" for char in token)
    value = "".join(f"%{ord(char):02X}" for char in value)
    value = value.encode().hex()
    value = "".join(f"%{ord(char):02x}" for char in value)
    value = " ".join(f"U+{ord(char):04X}" for char in value)
    return token, f"payload {value} end"


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

    def fake_gitleaks(self, directory: Path, script: str, marker: Path | None = None) -> dict[str, str]:
        gitleaks = directory / "gitleaks"
        gitleaks.write_text(script)
        gitleaks.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = str(directory)
        if marker is not None:
            env["GUARD_MARKER"] = str(marker)
        return env

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

    def test_encoded_fixtures_force_real_gitleaks_and_block_without_echoing(self):
        for rule_id, token, encoded in encoded_fixtures():
            with self.subTest(rule_id=rule_id, encoding=encoded[:12]):
                result = self.invoke("prompt", {"prompt": f"payload {encoded}"})
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual(output["decision"], "block")
                self.assertIn(rule_id, output["reason"])
                self.assertNotIn(token, result.stdout + result.stderr)
                self.assertNotIn(encoded, result.stdout + result.stderr)

    def test_long_mixed_encoding_forces_gitleaks_across_fragment_boundaries(self):
        token, payload = long_mixed_fixture()
        self.assertGreater(len(payload.encode()), 4096)
        result = self.invoke("prompt", {"prompt": payload})
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["decision"], "block")
        self.assertIn("context7-key-token", output["reason"])
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertNotIn(payload, result.stdout + result.stderr)

    def test_every_upstream_content_positive_is_sent_to_gitleaks(self):
        with gzip.open(TOOLS / "gitleaks-rule-samples.json.gz", "rt") as source:
            fixture = json.load(source)
        texts = [
            base64.b64decode(sample["text"]).decode()
            for sample in fixture["samples"]
            if "path" not in sample
        ]
        self.assertGreater(len(texts), 6000)
        with tempfile.TemporaryDirectory() as bin_dir:
            scan_log = Path(bin_dir) / "scans"
            env = self.fake_gitleaks(
                Path(bin_dir),
                '#!/bin/sh\ncat >/dev/null\nprintf \'scan\\n\' >>"$GUARD_LOG"\nexit 0\n',
            )
            env["GUARD_LOG"] = str(scan_log)

            previous_argv = sys.argv
            previous_stdin = sys.stdin
            previous_environ = os.environ.copy()
            try:
                os.environ.clear()
                os.environ.update(env)
                for text in texts:
                    sys.argv = [str(GUARD), "prompt"]
                    sys.stdin = io.StringIO(json.dumps({"prompt": text}))
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        try:
                            runpy.run_path(str(GUARD), run_name="__main__")
                            code = 0
                        except SystemExit as error:
                            code = error.code
                    self.assertEqual(code, 0, stderr.getvalue())
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertEqual(stderr.getvalue(), "")
            finally:
                sys.argv = previous_argv
                sys.stdin = previous_stdin
                os.environ.clear()
                os.environ.update(previous_environ)
            self.assertEqual(len(scan_log.read_text().splitlines()), len(texts))

    def test_ordinary_keyword_false_positives_skip_gitleaks(self):
        cases = (
            ("prompt", {"prompt": "Add a new task to mise.toml"}),
            ("prompt", {"prompt": "ask me before you push, they said so."}),
            ("prompt", {"prompt": "What does the auth module do?"}),
            ("prompt", {"prompt": "Read /Users/ranokay/Developer/factory/skills/supervise/SKILL.md and follow it"}),
            (
                "tool",
                {
                    "tool_name": "Edit",
                    "tool_input": {
                        "file_path": "/Users/ranokay/Developer/factory/tools/command_guard.py",
                        "old_string": "return None",
                        "new_string": "return key",
                    },
                },
            ),
        )
        with tempfile.TemporaryDirectory() as bin_dir:
            marker = Path(bin_dir) / "spawned"
            env = self.fake_gitleaks(Path(bin_dir), '#!/bin/sh\ncat >/dev/null\n: >"$GUARD_MARKER"\nexit 0\n', marker)
            for mode, payload in cases:
                with self.subTest(mode=mode, payload=payload):
                    marker.unlink(missing_ok=True)
                    result = self.invoke(mode, payload, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertFalse(marker.exists())

    def test_representative_ordinary_inputs_skip_gitleaks(self):
        cases = (
            ("prompt", {"prompt": "Reply with exactly OK."}),
            ("prompt", {"prompt": "Read /Users/ranokay/Developer/factory/AGENTS and follow it"}),
            (
                "prompt",
                {"prompt": "Rename factory_command_guard_prefilter to FactoryCommandGuardPrefilter without changing behavior."},
            ),
            ("tool", {"tool_name": "Read", "tool_input": {"file_path": "/Users/ranokay/Developer/factory/scripts/bootstrap.py"}}),
            (
                "tool",
                {
                    "tool_name": "Edit",
                    "tool_input": {
                        "file_path": "/Users/ranokay/Developer/factory/tools/command_guard.py",
                        "old_string": "return None",
                        "new_string": "return []",
                    },
                },
            ),
            (
                "tool",
                {"tool_name": "Write", "tool_input": {"file_path": "/Users/ranokay/Developer/factory/notes", "content": "ordinary text"}},
            ),
            (
                "tool",
                {"tool_name": "Bash", "tool_input": {"command": "sed -n '1,20p' /Users/ranokay/Developer/factory/scripts/bootstrap.py"}},
            ),
        )
        with tempfile.TemporaryDirectory() as bin_dir:
            marker = Path(bin_dir) / "spawned"
            env = self.fake_gitleaks(Path(bin_dir), '#!/bin/sh\ncat >/dev/null\n: >"$GUARD_MARKER"\nexit 0\n', marker)
            for mode, payload in cases:
                with self.subTest(mode=mode, payload=payload):
                    marker.unlink(missing_ok=True)
                    result = self.invoke(mode, payload, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertFalse(marker.exists())

    def test_prefilter_skip_agrees_with_real_gitleaks(self):
        corpus = [
            ("Summarise the current branch.", False),
            ("git status --short", False),
            ("x" * 4097, True),
            ("Read /Users/ranokay/Developer/factory/skills/supervise/SKILL.md and follow it", False),
            ('{"file_path":"/Users/ranokay/Developer/factory/notes.txt","content":"ordinary text"}', False),
            ("curl --version", True),
            ("apiToken = " + random_alnum(32), True),
            ("A" * 15, False),
            ("A" * 31, False),
            ("41" * 16, False),
            ("QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=", False),
            ("////////////////", False),
            ("00" * 16, False),
            (base64.b64encode(b"\x00" * 12).decode(), False),
            ("%00", False),
            ("%41", False),
            ("U+0041", False),
            (base64.b64encode(b"ordinary text").decode(), False),
            ("%61%70%69", False),
            ("\\u0061\\u0070\\u0069", False),
            ("617069" + "41" * 13, False),
            (base64.b64encode(b"api ordinary text").decode(), False),
            *((encoded, True) for _, _, encoded in encoded_fixtures()),
        ]
        with tempfile.TemporaryDirectory() as bin_dir:
            marker = Path(bin_dir) / "spawned"
            env = self.fake_gitleaks(Path(bin_dir), '#!/bin/sh\ncat >/dev/null\n: >"$GUARD_MARKER"\nexit 0\n', marker)
            for text, expected_scan in corpus:
                with self.subTest(text=text[:24], expected_scan=expected_scan):
                    marker.unlink(missing_ok=True)
                    guarded = self.invoke("prompt", {"prompt": text}, env=env)
                    self.assertEqual(guarded.returncode, 0, guarded.stderr)
                    scanned = marker.exists()
                    self.assertEqual(scanned, expected_scan)
                    real = subprocess.run(
                        [
                            "gitleaks",
                            "stdin",
                            "--config",
                            str(TOOLS.parent / ".gitleaks.toml"),
                            "--redact",
                            "--report-format",
                            "json",
                            "--report-path",
                            "-",
                            "--no-banner",
                        ],
                        input=text,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    if not scanned:
                        self.assertEqual(real.returncode, 0, real.stdout + real.stderr)

    def test_failing_gitleaks_uses_the_fallback_without_echoing_its_output(self):
        token = context7_token()
        with tempfile.TemporaryDirectory() as bin_dir:
            env = self.fake_gitleaks(
                Path(bin_dir),
                "#!/bin/sh\ninput=$(cat)\nprintf '%s\\n' \"$input\"\nprintf '%s\\n' \"$input\" >&2\nexit 2\n",
            )
            result = self.invoke("tool", {"tool_name": "Bash", "tool_input": {"command": f"echo {token}"}}, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn("context7-key-token", output["permissionDecisionReason"])
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_timed_out_gitleaks_uses_the_fallback(self):
        token = context7_token()
        with tempfile.TemporaryDirectory() as bin_dir:
            env = self.fake_gitleaks(Path(bin_dir), "#!/bin/sh\n/bin/sleep 10\n")
            result = self.invoke("prompt", {"prompt": token}, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("context7-key-token", json.loads(result.stdout)["reason"])
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_missing_gitleaks_warns_when_the_fallback_passes_a_keyword_input(self):
        with tempfile.TemporaryDirectory() as empty_path:
            env = os.environ.copy()
            env["PATH"] = empty_path
            result = self.invoke("prompt", {"prompt": "curl --version"}, env=env)
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
                env = self.fake_gitleaks(Path(bin_dir), '#!/bin/sh\n: >"$GUARD_MARKER"\nexit 2\n', marker)
                result = self.invoke(mode, payload, env=env)
                spawned = marker.exists()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")
                self.assertFalse(spawned)

    def test_matching_rule_regex_runs_gitleaks(self):
        with tempfile.TemporaryDirectory() as bin_dir:
            marker = Path(bin_dir) / "spawned"
            env = self.fake_gitleaks(Path(bin_dir), '#!/bin/sh\n: >"$GUARD_MARKER"\nexit 0\n', marker)
            result = self.invoke("prompt", {"prompt": "apiToken = " + random_alnum(32)}, env=env)
            spawned = marker.exists()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertTrue(spawned)

    def test_missing_invalid_or_stale_keyword_catalog_always_runs_gitleaks(self):
        for catalog in (None, "not json", "stale"):
            with self.subTest(catalog=catalog), tempfile.TemporaryDirectory() as case_dir:
                root = Path(case_dir)
                tools = root / "tools"
                tools.mkdir()
                guard = tools / "secret_guard.py"
                shutil.copy2(GUARD, guard)
                if catalog == "stale":
                    stale = json.loads((TOOLS / "gitleaks-keywords.json").read_text())
                    stale["gitleaks_version"] = "8.30.2"
                    stale["source"] = "https://raw.githubusercontent.com/gitleaks/gitleaks/v8.30.2/config/gitleaks.toml"
                    (tools / "gitleaks-keywords.json").write_text(json.dumps(stale))
                elif catalog is not None:
                    (tools / "gitleaks-keywords.json").write_text(catalog)
                profile = root / "profiles" / "universal" / "mise.toml"
                profile.parent.mkdir(parents=True)
                profile.write_text('[tools]\ngitleaks = "8.30.1"\n')
                bin_dir = root / "bin"
                bin_dir.mkdir()
                marker = root / "spawned"
                env = self.fake_gitleaks(bin_dir, '#!/bin/sh\n: >"$GUARD_MARKER"\nexit 0\n', marker)
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
