#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
GUARD = TOOLS / "run_guard.py"


class RunGuardTests(unittest.TestCase):
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
        result = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            text=True,
            capture_output=True,
            check=True,
            env=self.env,
        )
        return result.stdout.strip()

    def invoke(self, command: str, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(GUARD), "tool"],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd or self.repo)}),
            text=True,
            capture_output=True,
            check=False,
            cwd=cwd or self.repo,
            env=self.env,
        )

    def invoke_mode(self, mode: str, payload: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
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

    def test_push_outside_a_recorder_repo_is_allowed(self):
        self.write_mise("echo no recorder")
        for command in ("git push", f"git -C {self.repo} push", "echo ready && git push"):
            with self.subTest(command=command):
                self.assertEqual(self.output(self.invoke(command)), {})

    def test_wrapper_introspection_does_not_require_a_check_record(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        for command in (
            "command -v git push",
            "command -V git push",
            "command -pv git push",
            "command -p -v git push",
            "sudo -l git push",
            "sudo -v git push",
            "sudo -nl git push",
            "sudo -nv git push",
            "sudo --help git push",
            "env --help git push",
            "env --version git push",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.output(self.invoke(command)), {})

    def test_shorthand_check_task_scope_is_respected(self):
        (self.repo / "mise.toml").write_text('[tasks]\ncheck = "echo no recorder"\n')
        self.git("add", "mise.toml")
        self.git("commit", "-qm", "shorthand check")
        self.assertEqual(self.output(self.invoke("git push")), {})
        (self.repo / "mise.toml").write_text('[tasks]\ncheck = "~/.agents/tools/run check_result -- true"\n')
        self.git("add", "mise.toml")
        self.git("commit", "-qm", "shorthand recorder")
        output = self.output(self.invoke("git push"))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_push_inside_a_recorder_repo_requires_a_pass_record(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        result = self.invoke("git push")
        output = self.output(result)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("mise run check", output["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn(self.git("rev-parse", "HEAD"), output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_unreadable_recorder_configuration_blocks_push(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        for text in (
            "not toml",
            'tasks = "invalid"\n',
            "[tasks.check]\nrun = 1979-05-27\n",
            "[tasks.check]\nrun = [1979-05-27]\n",
        ):
            with self.subTest(text=text):
                (self.repo / "mise.toml").write_text(text)
                output = self.output(self.invoke("git push"))
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("mise.toml", output["hookSpecificOutput"]["permissionDecisionReason"])

    def record_check(self, *command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(TOOLS / "check_result.py"), "--", *command],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
            env=self.env,
        )

    def test_recorded_pass_allows_git_c_and_compound_pushes(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        recorded = self.record_check("true")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        for command in ("git push", f"git -C {self.repo} push", "echo ready && git push"):
            with self.subTest(command=command):
                self.assertEqual(self.output(self.invoke(command)), {})

    def test_git_c_and_compound_pushes_require_a_pass_record(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        self.env["RUN_GUARD_REPO"] = str(self.repo)
        other = self.root / "other"
        other.mkdir()
        for command in (
            f"git -C {self.repo} push",
            "echo ready && git push",
            "cd /missing || git push",
            "cd /tmp | git push",
            "(cd /tmp); git push",
            '(cd "$RUN_GUARD_REPO"; git push)',
            "echo x | cd /tmp && git push",
            f"git -C {self.repo} -C . push",
            f"cd -P {self.repo} && git push",
            f"cd -LP {self.repo} && git push",
            f"cd -- {self.repo} && git push",
            'cd "$RUN_GUARD_REPO" && git push',
            'git -C "$RUN_GUARD_REPO" push',
            "command -p git push",
            "command -- git push",
            "sudo -u root git push",
            "sudo -nu root git push",
            "sudo -uroot git push",
            "sudo -nuroot git push",
            "sudo --user root git push",
            "sudo --user=root git push",
            "sudo --us root git push",
            "sudo --chd /tmp git push",
            "sudo -n -- git push",
            "env -S 'git push'",
            "env -S'git push'",
            "env --split-string='git push'",
            "env -vS'git push'",
            "env -ivS'git push'",
            "echo ok; env -S 'x\\'; git push",
            "echo ok; env -S'x\\'; git push",
            "echo ok; env --split-string='x\\'; git push",
            "echo ok; env -vS 'x\\'; git push",
            "env -i git push",
            "env -v git push",
            "env -iv git push",
            "env -0 git push",
            "env -u FOO git push",
            "env -uFOO git push",
            "env -uS git push",
            "env -iu FOO git push",
            "env -iuFOO git push",
            "env -C /tmp git push",
            "env -C/tmp git push",
            "env -P /usr/bin git push",
            "env -P/usr/bin git push",
            "env --unset FOO git push",
            "env --unset=FOO git push",
            "env --chdir /tmp git push",
            "env --chdir=/tmp git push",
            "env --block-signal=TERM git push",
            "env --list-signal-handling git push",
            "env -- git push",
            "env FOO=bar git push",
        ):
            with self.subTest(command=command):
                output = self.output(
                    self.invoke(
                        command,
                        cwd=other
                        if "-C ." in command or '(cd "$RUN_GUARD_REPO";' in command or "cd -LP" in command
                        else self.repo,
                    )
                )
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_recorded_red_check_blocks_push(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        recorded = self.record_check("sh", "-c", "exit 9")
        self.assertEqual(recorded.returncode, 9)
        output = self.output(self.invoke("git push"))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def gh_stub(self, *, head: str = "sync/upstream-2026-11-28", topics: str = "factory-personal-fork"):
        binary = self.root / "bin"
        binary.mkdir(exist_ok=True)
        script = binary / "gh"
        script.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >>\"$GH_LOG\"\n"
            "case \"$1 $2\" in\n"
            "  'pr view') [ -z \"${GH_PR_FAIL:-}\" ] || { printf '%s\\n' \"${GH_PR_ERROR:-no pull requests found for branch}\" >&2; exit 1; }; printf '{\"number\":42,\"headRefName\":\"%s\",\"url\":\"https://github.com/o/one/pull/42\"}\\n' \"$GH_HEAD\" ;;\n"
            "  'pr checks') printf '%s\\n' \"$GH_CHECKS\"; exit \"$GH_CHECKS_CODE\" ;;\n"
            "  'repo view') printf '{\"repositoryTopics\":[\"%s\"]}\\n' \"$GH_TOPICS\" ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n"
        )
        script.chmod(0o755)
        self.env["PATH"] = str(binary) + os.pathsep + self.env["PATH"]
        self.env["GH_HEAD"] = head
        self.env["GH_TOPICS"] = topics
        self.env["GH_LOG"] = str(self.root / "gh.log")
        self.env["GH_PR_FAIL"] = ""
        self.env["GH_PR_ERROR"] = "no pull requests found for branch"
        self.env["GH_CHECKS"] = "[]"
        self.env["GH_CHECKS_CODE"] = "0"

    def test_admin_merge_all_selector_forms_pass_for_a_sync_pr_on_a_personal_fork(self):
        self.gh_stub()
        for command in (
            "gh pr merge --admin",
            "gh pr merge 42 --admin",
            "gh pr merge 42 --admin=1",
            "gh pr merge https://github.com/o/one/pull/42 --admin",
            "gh pr merge sync/upstream-2026-11-28 --admin",
            "gh -R o/one pr merge 42 --admin",
            "gh pr merge 42 -R o/one --admin",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.output(self.invoke(command)), {})

    def test_admin_merge_is_denied_for_a_feature_branch(self):
        self.gh_stub(head="feature/example")
        for flag in ("--admin", "--admin=true", "--admin=1", "--admin=t", "--admin=TRUE"):
            with self.subTest(flag=flag):
                output = self.output(self.invoke(f"gh pr merge 42 {flag}"))
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("sync/upstream-", output["hookSpecificOutput"]["permissionDecisionReason"])
                self.assertIn("without --admin", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_admin_merge_is_denied_without_the_personal_fork_topic(self):
        self.gh_stub(topics="")
        output = self.output(self.invoke("gh pr merge 42 --admin"))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("factory-personal-fork", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_plain_merge_does_not_query_github(self):
        self.gh_stub(head="feature/example", topics="")
        self.assertEqual(self.output(self.invoke("gh pr merge 42 --merge")), {})
        self.assertFalse((self.root / "gh.log").exists())

    def test_stop_blocks_each_pending_required_run(self):
        self.gh_stub()
        self.env["GH_CHECKS"] = json.dumps(
            [
                {"name": "Lint", "state": "PENDING", "bucket": "pending", "link": "https://github.com/o/one/actions/runs/11"},
                {"name": "Tests", "state": "PENDING", "bucket": "pending", "link": "https://github.com/o/one/actions/runs/12"},
            ]
        )
        self.env["GH_CHECKS_CODE"] = "8"
        output = self.output(self.invoke_mode("stop", {"cwd": str(self.repo), "stop_hook_active": False}))
        self.assertEqual(output["decision"], "block")
        self.assertIn("gh run watch 11 --exit-status", output["reason"])
        self.assertIn("gh run watch 12 --exit-status", output["reason"])

    def test_stop_allows_a_branch_without_a_pr(self):
        self.gh_stub()
        self.env["GH_PR_FAIL"] = "1"
        self.assertEqual(self.output(self.invoke_mode("stop", {"cwd": str(self.repo)})), {})

    def test_unavailable_stop_facts_block_once_then_warn_and_allow(self):
        self.gh_stub()
        self.env["GH_PR_FAIL"] = "1"
        self.env["GH_PR_ERROR"] = "network unavailable"
        self.env["GH_CHECKS_CODE"] = "1"
        first = self.output(self.invoke_mode("stop", {"cwd": str(self.repo), "stop_hook_active": False}))
        self.assertEqual(first["decision"], "block")
        repeat = self.output(self.invoke_mode("stop", {"cwd": str(self.repo), "stop_hook_active": True}))
        self.assertNotIn("decision", repeat)
        self.assertIn("warning", repeat["systemMessage"].lower())

    def test_pending_checks_keep_blocking_on_a_repeat_stop(self):
        self.gh_stub()
        self.env["GH_CHECKS"] = json.dumps(
            [{"name": "Lint", "state": "PENDING", "bucket": "pending", "link": "https://github.com/o/one/actions/runs/11"}]
        )
        self.env["GH_CHECKS_CODE"] = "8"
        output = self.output(self.invoke_mode("stop", {"cwd": str(self.repo), "stop_hook_active": True}))
        self.assertEqual(output["decision"], "block")

    def target_command(self, command: str, *targets: str) -> None:
        result = subprocess.run(
            [sys.executable, str(TOOLS / "run_targets.py"), command, "o/one", "6", *targets],
            text=True,
            capture_output=True,
            check=False,
            env=self.env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def fallback_command(self, reason: str) -> str:
        return f"skills/supervise/scripts/block-issue o/one 6 --reason '{reason}' --unblock 'Launch the next target'"

    def test_model_only_blocker_with_untried_fallback_is_refused(self):
        primary = json.dumps({"providerInstanceId": "opencode", "model": "opencode-go/hungerg-free", "options": {}})
        fallback = json.dumps(
            {"providerInstanceId": "opencode", "model": "opencode-go/hy4-flash-free", "options": {"variant": "high"}}
        )
        self.target_command("set", primary, fallback)
        self.target_command("mark", primary)
        output = self.output(self.invoke(self.fallback_command("opencode-go/hungerg-free unavailable quota")))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("opencode-go/hy4-flash-free", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_model_only_blocker_with_every_fallback_tried_is_allowed(self):
        primary = json.dumps({"providerInstanceId": "opencode", "model": "opencode-go/hungerg-free", "options": {}})
        fallback = json.dumps(
            {"providerInstanceId": "opencode", "model": "opencode-go/hy4-flash-free", "options": {"variant": "high"}}
        )
        self.target_command("set", primary, fallback)
        self.target_command("mark", primary)
        self.target_command("mark", fallback)
        self.assertEqual(self.output(self.invoke(self.fallback_command("opencode-go/hungerg-free unavailable quota"))), {})

    def test_later_block_issue_violation_in_a_compound_command_is_checked(self):
        primary = json.dumps({"providerInstanceId": "opencode", "model": "opencode-go/hungerg-free", "options": {}})
        fallback = json.dumps(
            {"providerInstanceId": "opencode", "model": "opencode-go/hy4-flash-free", "options": {"variant": "high"}}
        )
        self.target_command("set", primary, fallback)
        self.target_command("mark", primary)
        allowed = "skills/supervise/scripts/block-issue o/one 65 --reason 'ordinary failure' --unblock 'Fix it'"
        command = allowed + "; " + self.fallback_command("opencode-go/hungerg-free unavailable quota")
        output = self.output(self.invoke(command))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_mixed_cause_blocker_is_allowed(self):
        primary = json.dumps({"providerInstanceId": "opencode", "model": "opencode-go/hungerg-free", "options": {}})
        fallback = json.dumps(
            {"providerInstanceId": "opencode", "model": "opencode-go/hy4-flash-free", "options": {"variant": "high"}}
        )
        self.target_command("set", primary, fallback)
        self.target_command("mark", primary)
        command = self.fallback_command("opencode-go/hungerg-free unavailable quota; mise run check failed")
        self.assertEqual(self.output(self.invoke(command)), {})

    def test_blocker_without_targets_file_is_allowed(self):
        command = self.fallback_command("opencode-go/hungerg-free unavailable quota")
        self.assertEqual(self.output(self.invoke(command)), {})

    def test_malformed_targets_file_warns_and_fails_open(self):
        path = self.root / "cache" / "factory" / "runs" / "o" / "one" / "6" / "targets.json"
        path.parent.mkdir(parents=True)
        path.write_text("not json")
        result = self.invoke(self.fallback_command("opencode-go/hungerg-free unavailable quota"))
        self.assertEqual(self.output(result), {})
        self.assertIn("Factory Run guard warning", result.stderr)

    def test_admin_merge_with_unavailable_github_facts_blocks(self):
        self.gh_stub()
        self.env["GH_PR_FAIL"] = "1"
        self.env["GH_PR_ERROR"] = "network unavailable"
        output = self.output(self.invoke("gh pr merge 42 --admin"))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("network unavailable", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_opencode_tool_payload_returns_a_rule_block(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        result = self.invoke_mode("opencode", {"tool": "bash", "input": {"command": "git push"}, "cwd": str(self.repo)})
        output = self.output(result)
        self.assertTrue(output["block"])
        self.assertIn("mise run check", output["message"])

    def test_opencode_idle_pending_and_unavailable_results(self):
        self.gh_stub()
        self.env["GH_CHECKS"] = json.dumps(
            [{"name": "Lint", "state": "PENDING", "bucket": "pending", "link": "https://github.com/o/one/actions/runs/11"}]
        )
        self.env["GH_CHECKS_CODE"] = "8"
        pending = self.output(self.invoke_mode("opencode-idle", {"cwd": str(self.repo), "repeat": False}))
        self.assertTrue(pending["block"])
        self.assertIn("gh run watch 11 --exit-status", pending["message"])
        self.env["GH_PR_FAIL"] = "1"
        self.env["GH_PR_ERROR"] = "network unavailable"
        first = self.output(self.invoke_mode("opencode-idle", {"cwd": str(self.repo), "repeat": False}))
        self.assertTrue(first["block"])
        repeat = self.output(self.invoke_mode("opencode-idle", {"cwd": str(self.repo), "repeat": True}))
        self.assertFalse(repeat["block"])
        self.assertIn("warning", repeat["warning"].lower())

    def test_internal_crash_warns_and_fails_open(self):
        result = subprocess.run(
            [sys.executable, str(GUARD), "tool"],
            input="not json",
            text=True,
            capture_output=True,
            check=False,
            cwd=self.repo,
            env=self.env,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Factory Run guard warning", result.stderr)


if __name__ == "__main__":
    unittest.main()
