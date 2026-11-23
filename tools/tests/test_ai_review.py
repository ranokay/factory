import os
import json
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return (ROOT / ".github" / "workflows" / name).read_text()


def step(source, name):
    match = re.search(
        rf"(?ms)^      - name: {re.escape(name)}\n(.*?)(?=^      - name: |\Z)", source
    )
    if match is None:
        raise AssertionError(f"missing step: {name}")
    return match.group(1)


def run_step(source, environment):
    match = re.search(r"(?ms)^        run: \|\n((?:^          .*\n)+)", source)
    if match is None:
        raise AssertionError("missing shell command")
    return subprocess.run(
        ["bash", "-c", textwrap.dedent(match.group(1))],
        env={**os.environ, **environment}, capture_output=True, text=True, check=False,
    )


def assert_review_permissions(test, source):
    parsed = yaml.safe_load(source)
    test.assertEqual(parsed["permissions"], {})
    test.assertEqual(set(parsed["jobs"]), {"review"})
    test.assertEqual(parsed["jobs"]["review"]["permissions"], {
        "contents": "write", "issues": "read", "pull-requests": "write",
    })


def review_step(source):
    for item in yaml.safe_load(source)["jobs"]["review"]["steps"]:
        if item.get("id") == "review":
            return item
    raise AssertionError("missing review step")


def assert_draft_guard(test, callee, caller):
    condition = yaml.safe_load(callee)["jobs"]["review"]["if"]
    test.assertIn(
        "github.event.pull_request.head.repo.full_name == github.repository", condition
    )
    test.assertIn("github.event.pull_request.draft == false", condition)
    test.assertNotIn("if", yaml.safe_load(caller)["jobs"]["review"])


def assert_failure_propagation(test, source):
    jobs = yaml.safe_load(source)["jobs"]
    test.assertEqual(set(jobs), {"review"})
    for job in jobs.values():
        for item in [job, *job.get("steps", [])]:
            test.assertNotIn("continue-on-error", item)
            if "if" in item:
                test.assertIsInstance(item["if"], str)
                test.assertNotRegex(item["if"], r"(?i)\b(?:always|failure)\s*\(")


class AiReviewWorkflowTests(unittest.TestCase):
    def test_review_permissions_grant_only_the_review_needs(self):
        for name in ("ai-review.yml", "ai-review-caller.yml"):
            with self.subTest(workflow=name):
                source = workflow(name)
                assert_review_permissions(self, source)
                assert_review_permissions(self, source.replace("permissions: {}", "permissions: {} # deny defaults"))
                mutated = source.replace("      contents: write\n", "      actions: write\n      contents: write\n")
                self.assertNotEqual(mutated, source)
                with self.assertRaises(AssertionError):
                    assert_review_permissions(self, mutated)
                mutated = source.replace("permissions: {}\n", "permissions:\n  contents: write\n", 1)
                self.assertNotEqual(mutated, source)
                with self.assertRaises(AssertionError):
                    assert_review_permissions(self, mutated)

    def test_review_uses_high_signal_inputs(self):
        with_ = review_step(workflow("ai-review.yml"))["with"]
        expected = {
            "ocr_version": "1.14.0",
            "rule": "${{ github.workspace }}/ai-review-rule.json",
            "sticky_summary": "true",
            "incremental": "true",
            "checkpoint_range": "true",
            "route_severity_below": "medium",
            "route_categories": "style,documentation",
            "resolve_outdated": "true",
            "effort": "high",
        }
        for name, value in expected.items():
            with self.subTest(input=name):
                self.assertEqual(with_.get(name), value)
        self.assertNotIn("max_tokens_budget", with_)

    def test_checkpoint_session_header_is_stable_across_pushes(self):
        headers = review_step(workflow("ai-review.yml"))["with"]["llm_extra_headers"]
        self.assertNotIn("github.run_", headers)
        self.assertIn("github.repository_id", headers)
        self.assertIn("github.event.pull_request.number", headers)

    def test_checkpoint_context_ignores_the_editable_pr_body(self):
        script = step(workflow("ai-review.yml"), "Gather review context")
        self.assertNotIn("pr.body", script)
        self.assertIn("PR: ${pr.title}", script)
        self.assertIn("issue.body", script)

    def test_rule_file_points_at_review_markdown_through_an_absolute_path(self):
        path = ROOT / "ai-review-rule.json"
        self.assertEqual(json.loads(path.read_text()), {
            "rules": [{
                "path": "**/*",
                "rule": "REVIEW.md",
                "merge_system_rule": True,
            }],
        })
        self.assertTrue((ROOT / "REVIEW.md").is_file())
        self.assertEqual(
            review_step(workflow("ai-review.yml"))["with"]["rule"],
            "${{ github.workspace }}/" + path.name,
        )

    def test_draft_pull_requests_skip_review_until_ready(self):
        callee = workflow("ai-review.yml")
        caller = workflow("ai-review-caller.yml")
        assert_draft_guard(self, callee, caller)

    def test_review_failures_propagate(self):
        for name in ("ai-review.yml", "ai-review-caller.yml"):
            with self.subTest(workflow=name):
                assert_failure_propagation(self, workflow(name))
        source = workflow("ai-review.yml")
        mutated = source.replace("      - name: Review\n", "      - name: Review\n        continue-on-error: true\n")
        self.assertNotEqual(mutated, source)
        with self.assertRaises(AssertionError):
            assert_failure_propagation(self, mutated)
        mutated = source.replace("      - name: Review\n", "      - name: Review\n        'continue-on-error': true\n")
        with self.assertRaises(AssertionError):
            assert_failure_propagation(self, mutated)
        mutated = source.replace("  review:\n", "  review:\n    continue-on-error: true\n")
        with self.assertRaises(AssertionError):
            assert_failure_propagation(self, mutated)
        mutated = re.sub(r"(?m)^    if: .+$", "    if: always()", source)
        with self.assertRaises(AssertionError):
            assert_failure_propagation(self, mutated)
        mutated = source.replace("      - name: Require published review\n", "      - name: Require published review\n        if: failure()\n")
        with self.assertRaises(AssertionError):
            assert_failure_propagation(self, mutated)
        mutated = source.replace("      - name: Require published review\n", "      - name: Require published review\n        if: >+\n          ${{ failure() }}\n")
        with self.assertRaises(AssertionError):
            assert_failure_propagation(self, mutated)

    def test_automatic_review_event_routing(self):
        caller = workflow("ai-review-caller.yml")
        self.assertIn("run-name: AI Review PR ${{ github.event.pull_request.number }}", caller)
        match = re.search(r"(?m)^    types: \[([^]]+)\]$", caller)
        actions = {action.strip() for action in match.group(1).split(",")}
        for action, starts in (
            ("opened", True), ("reopened", True),
            ("ready_for_review", True), ("synchronize", True),
            ("closed", False), ("edited", False),
        ):
            with self.subTest(action=action):
                self.assertEqual(action in actions, starts)

    def test_secret_jobs_allow_only_same_repository_pull_requests(self):
        caller = workflow("ai-review-caller.yml")
        reusable = workflow("ai-review.yml")
        triggers = caller.split("on:\n", 1)[1].split("permissions:", 1)[0]
        self.assertEqual(re.findall(r"(?m)^  ([a-z_]+):", triggers), ["pull_request"])
        self.assertNotIn("pull_request_target:", caller + reusable)
        self.assertNotIn("if", yaml.safe_load(caller)["jobs"]["review"])
        authors = json.loads((ROOT / "repo-settings.json").read_text())["factory_authors"]
        job = reusable.split("  review:\n", 1)[1]
        condition = re.search(r"(?m)^    if: (.+)$", job).group(1)
        self.assertTrue(condition.startswith("github.event.pull_request.head.repo.full_name == github.repository"))
        self.assertEqual(re.findall(r"pull_request\.user\.login == '([^']+)'", condition), authors)

    def test_missing_key_fails_before_review(self):
        source = workflow("ai-review.yml")
        check = step(source, "Require Console key")
        self.assertIn("ZEN_KEY: ${{ secrets.OPENCODE_ZEN_API_KEY }}", check)
        self.assertNotIn("continue-on-error:", check)
        self.assertLess(source.index("name: Require Console key"), source.index("name: Review\n"))
        self.assertNotEqual(run_step(check, {"ZEN_KEY": ""}).returncode, 0)
        self.assertEqual(run_step(check, {"ZEN_KEY": "fixture"}).returncode, 0)

    def test_summary_check_does_not_fail_on_findings_that_could_not_be_inlined(self):
        check = step(workflow("ai-review.yml"), "Require published review")
        self.assertIn("SUMMARY: ${{ steps.review.outputs.summary_comment_url }}", check)
        self.assertIn("RANGE_REASON: ${{ steps.review.outputs.range_reason }}", check)
        self.assertIn("RESULT_PATH: /tmp/ocr-result.json", check)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            env = {"SUMMARY": "https://example.test/comment", "RANGE_REASON": "", "RESULT_PATH": str(path)}
            for status in ("complete", "skipped"):
                path.write_text(json.dumps({"status": status}))
                for failures in ("0", "1"):
                    result = run_step(check, {**env, "FAILED": failures})
                    self.assertEqual(result.returncode, 0, result.stderr)
            path.write_text(json.dumps({"status": "failed"}))
            self.assertNotEqual(run_step(check, env).returncode, 0)
            path.write_text("invalid json")
            self.assertNotEqual(run_step(check, env).returncode, 0)
            path.write_text(json.dumps({"status": "complete"}))
            self.assertNotEqual(run_step(check, {**env, "SUMMARY": ""}).returncode, 0)
            for status in ("complete", "skipped"):
                path.write_text(json.dumps({"status": status}))
                result = run_step(check, {**env, "SUMMARY": "", "RANGE_REASON": "same_head_noop"})
                self.assertEqual(result.returncode, 0, result.stderr)
            path.write_text(json.dumps({"status": "failed"}))
            self.assertNotEqual(
                run_step(check, {**env, "SUMMARY": "", "RANGE_REASON": "same_head_noop"}).returncode, 0
            )


if __name__ == "__main__":
    unittest.main()
