#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess  # nosec B404  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- the fixture drives the local guard
import sys
import unittest
from pathlib import Path

from run_guard_fixture import RunGuardFixture

GUARD = Path(__file__).resolve().parents[1] / "run_guard.py"


class RunGuardStopTests(RunGuardFixture):
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

    def test_unblock_remediation_wording_does_not_create_a_model_only_cause(self):
        primary = json.dumps({"providerInstanceId": "opencode", "model": "opencode-go/hungerg-free", "options": {}})
        fallback = json.dumps(
            {"providerInstanceId": "opencode", "model": "opencode-go/hy4-flash-free", "options": {"variant": "high"}}
        )
        self.target_command("set", primary, fallback)
        self.target_command("mark", primary)
        output = self.output(
            self.invoke(self.fallback_command("opencode-go/hungerg-free unavailable quota", "Check the provider quota"))
        )
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("opencode-go/hy4-flash-free", output["hookSpecificOutput"]["permissionDecisionReason"])
        for reason in ("blocked", "tests fail on main"):
            with self.subTest(reason=reason):
                command = self.fallback_command(reason, "Restore the hungerg-free provider quota")
                self.assertEqual(self.output(self.invoke(command)), {})

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
        result = subprocess.run(  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- this checkout's guard
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
