#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
GUARD = TOOLS / "command_guard.py"


class CommandGuardTests(unittest.TestCase):
    def check_command(self, command: str, *, shell: str = "/bin/zsh", env: dict[str, str] | None = None) -> dict:
        result = subprocess.run(
            [sys.executable, str(GUARD)],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command, "shell": shell}}),
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else {}

    def test_unquoted_filter_globs_are_denied(self):
        for command in (
            "grep -R foo . --include=*.py | head",
            "grep --exclude *.qml foo .",
            "grep --exclude-dir=build* foo .",
            "rg -g *.py foo",
            "rg -g*.py foo",
            "rg --glob=[ab].py foo",
            "command grep --include=*.py foo .",
            "FOO=bar grep --include=*.py foo .",
        ):
            with self.subTest(command=command):
                output = self.check_command(command)
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_refusal_carries_a_command_that_passes(self):
        for command, corrected in (
            ("grep -R foo . --include=*.py | head", "grep -R foo . --include='*.py' | head"),
            ("grep --exclude *.qml foo .", "grep --exclude '*.qml' foo ."),
            ("rg -g*.py foo", "rg -g'*.py' foo"),
            ("rg -g *.py -g *.md foo", "rg -g '*.py' -g '*.md' foo"),
            ("curl -s https://example.com/a?b=c", "curl -s 'https://example.com/a?b=c'"),
            ('echo "${PIPESTATUS[0]}"', "bash -c 'echo \"${PIPESTATUS[0]}\"'"),
        ):
            with self.subTest(command=command):
                reason = self.check_command(command)["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertTrue(reason.endswith(f"Run this instead:\n{corrected}"), reason)
                self.assertEqual(self.check_command(corrected), {})

    def test_refusal_omits_a_correction_it_cannot_build_safely(self):
        for command in ("rg -g $DIR/*.py foo", "curl https://example.com/$PAGE?x=1"):
            with self.subTest(command=command):
                reason = self.check_command(command)["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertNotIn("Run this instead", reason)

    def test_quoted_escaped_and_non_filter_globs_are_allowed(self):
        for command in (
            "grep --include='*.py' foo .",
            'grep "--include=*.py" foo .',
            r"grep --include=\*.py foo .",
            "rg -g '*.py' foo",
            "rg -g'*.py' foo",
            "grep --include=readme.txt foo .",
            "grep -- '--include=*.py' .",
            "echo '--include=*.py'",
            "python3 -c 'print(\"grep --include=*.py\")'",
            "echo safe # grep --include=*.py",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.check_command(command), {})

    def test_rtk_proxy_filters_keep_their_quote_provenance(self):
        for command in (
            "rtk proxy grep -R foo . --include=*.py |head",
            "rtk proxy rg -g *.py foo",
            "command rtk proxy grep --exclude-dir=build* foo .",
        ):
            with self.subTest(command=command):
                output = self.check_command(command)
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        for command in (
            "rtk proxy grep -R foo . --include='*.py' |head",
            "rtk proxy rg -g '*.py' foo",
            "rtk proxy echo 'grep --include=*.py'",
            "rtk other proxy grep --include=*.py foo .",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.check_command(command), {})

    def test_heredoc_bodies_are_ignored_but_following_commands_are_checked(self):
        self.assertEqual(self.check_command("cat <<'PY'\ngrep --include=*.py\nPY\n"), {})
        self.assertEqual(self.check_command("cat <<-'PY'\n\t${PIPESTATUS[0]}\n\tPY\n"), {})
        output = self.check_command("cat <<'PY'\ntext\nPY\ngrep --include=*.py foo .")
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_unquoted_heredoc_expansions_require_bash(self):
        for body in ("${PIPESTATUS[0]}", "'${PIPESTATUS[0]}'", r"\\${PIPESTATUS[0]}", "${PIPE\\\nSTATUS[0]}"):
            command = "cat <<EOF\n" + body + "\nEOF\n"
            with self.subTest(body=body):
                output = self.check_command(command)
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertEqual(self.check_command(command, shell="/bin/bash"), {})
        for command in (
            "cat <<'EOF'\n${PIPESTATUS[0]}\nEOF\n",
            'cat <<"EOF"\n${PIPESTATUS[0]}\nEOF\n',
            "cat <<EOF''\n${PIPESTATUS[0]}\nEOF\n",
            "cat <<\\EOF\n${PIPESTATUS[0]}\nEOF\n",
            "cat <<EOF\n\\${PIPESTATUS[0]}\nEOF\n",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.check_command(command), {})

    def test_known_env_options_preserve_search_filter_checks(self):
        for command in (
            "env -u UNUSED_GUARD_VALUE rg -g *.py anything",
            "env --unset=UNUSED_GUARD_VALUE rg -g *.py anything",
            "env --unset UNUSED_GUARD_VALUE rg -g *.py anything",
            "env -uUNUSED_GUARD_VALUE rg -g *.py anything",
            "/usr/bin/env -i FOO=bar -- rtk proxy grep --include=*.py anything .",
            "env -C /tmp --chdir=/ rg -g *.py anything",
        ):
            with self.subTest(command=command):
                output = self.check_command(command)
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        for command in (
            "env -u UNUSED_GUARD_VALUE rg -g '*.py' anything",
            "env --unset=UNUSED_GUARD_VALUE echo 'rg -g *.py'",
            "env --unsupported-option rg -g '*.py' anything",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.check_command(command), {})
        output = self.check_command("cat <<< text\ngrep --include=*.py foo .")
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_direct_gate_output_pipelines_require_status_capture(self):
        for command in ("mise run check | tail -n 20", "mise run test-strict | tee gate.log | head"):
            with self.subTest(command=command):
                output = self.check_command(command)
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.check_command("set -o pipefail; mise run check | tail -n 20"), {})
        self.assertEqual(self.check_command("mise run check | tee gate.log\nrc=$pipestatus[1]\nexit $rc"), {})
        self.assertEqual(self.check_command("echo 'mise run check | tail -n 20'"), {})
        self.assertEqual(self.check_command("mise run fmt | tail -n 20"), {})

    def test_pipestatus_requires_explicit_bash_execution_shell(self):
        for command in ('echo "${PIPESTATUS[0]}"', "status=${PIPESTATUS[0]}"):
            with self.subTest(command=command):
                output = self.check_command(command)
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertEqual(self.check_command(command, shell="/bin/bash"), {})
        self.assertEqual(self.check_command("bash -c 'exit ${PIPESTATUS[0]}'"), {})
        self.assertEqual(self.check_command("echo '${PIPESTATUS[0]}'"), {})
        self.assertEqual(self.check_command(r"echo \${PIPESTATUS[0]}"), {})
        self.assertEqual(self.check_command("echo '${PIPESTATUS_EXTRA}'"), {})

    def test_unquoted_url_globs_and_queries_are_denied_in_zsh(self):
        for command in (
            "gh api repos/o/r/contents/docs/a.md?ref=main --jq .path",
            "curl -s https://example.com/a?b=c",
            "curl https://example.com/files/*.zip",
            "cd /tmp && curl -o x https://example.com/x?y=1",
        ):
            with self.subTest(command=command):
                output = self.check_command(command)
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertEqual(self.check_command(command, shell="/bin/bash"), {})
        for command in (
            "gh api 'repos/o/r/contents/docs/a.md?ref=main'",
            'curl -s "https://example.com/a?b=c"',
            r"curl https://example.com/a\?b=c",
            "curl https://example.com/plain/path",
            "ls file?.txt",
            "cat <<'EOF'\nhttps://example.com/a?b=c\nEOF\n",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.check_command(command), {})

    @unittest.skipUnless(sys.platform == "darwin", "the SDK lookup only applies on macOS")
    def test_bare_swiftc_outside_apple_toolchains_is_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "swiftc"
            fake.write_text("#!/bin/sh\n")
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            env = {key: value for key, value in os.environ.items() if key != "SDKROOT"}
            env["PATH"] = directory + os.pathsep + "/usr/bin:/bin"
            for command in ("swiftc a.swift -o a", "cd /tmp && swiftc -parse-as-library a.swift"):
                with self.subTest(command=command):
                    output = self.check_command(command, env=env)
                    self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
            for command in (
                "xcrun -sdk macosx swiftc a.swift -o a",
                "SDKROOT=/sdk swiftc a.swift",
                "/Applications/Xcode.app/Contents/Developer/usr/bin/swiftc a.swift",
                "echo swiftc",
            ):
                with self.subTest(command=command):
                    self.assertEqual(self.check_command(command, env=env), {})
            self.assertEqual(self.check_command("swiftc a.swift", env={**env, "SDKROOT": "/sdk"}), {})
            apple_env = {**env, "PATH": "/usr/bin:/bin"}
            self.assertEqual(self.check_command("swiftc a.swift", env=apple_env), {})

    def test_missing_or_unrelated_payload_fields_do_not_override_permission(self):
        for payload in (
            {},
            [],
            {"tool_name": "Read"},
            {"tool_name": "Bash"},
            {"tool_name": "Bash", "tool_input": {}},
            {"tool_name": "Bash", "tool_input": {"command": None}},
        ):
            with self.subTest(payload=payload):
                result = subprocess.run(
                    [sys.executable, str(GUARD)], input=json.dumps(payload), text=True, capture_output=True, check=False
                )
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")




if __name__ == "__main__":
    unittest.main()
