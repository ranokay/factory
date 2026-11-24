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


def action(name):
    return (ROOT / ".github" / "actions" / name / "action.yml").read_text()


def action_steps(source):
    return yaml.safe_load(source)["runs"]["steps"]


def named_step(steps, name):
    for item in steps:
        if item.get("name") == name:
            return item
    raise AssertionError(f"missing step: {name}")


def run_step(source, environment):
    return subprocess.run(
        ["bash", "-c", textwrap.dedent(source["run"])],
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
    for item in action_steps(source):
        if item.get("id") == "review":
            return item
    raise AssertionError("missing review step")


def assert_draft_guard(test, source):
    job = yaml.safe_load(source)["jobs"]["review"]
    condition = job["if"]
    test.assertIn(
        "github.event.pull_request.head.repo.full_name == github.repository", condition
    )
    test.assertIn("github.event.pull_request.draft == false", condition)
    test.assertFalse(any("if" in item for item in job["steps"]))


def assert_failure_propagation(test, items):
    for item in items:
        test.assertNotIn("continue-on-error", item)
        if "if" in item:
            test.assertIsInstance(item["if"], str)
            test.assertNotRegex(item["if"], r"(?i)\b(?:always|failure)\s*\(")


def workflow_items(source):
    jobs = yaml.safe_load(source)["jobs"]
    return jobs, [item for job in jobs.values() for item in [job, *job.get("steps", [])]]


class AiReviewWorkflowTests(unittest.TestCase):
    def test_review_permissions_grant_only_the_review_needs(self):
        source = workflow("ai-review.yml")
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
        with_ = review_step(action("ocr-review"))["with"]
        expected = {
            "ocr_version": "1.14.0",
            "rule": "${{ steps.rule.outputs.path }}",
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
        headers = review_step(action("ocr-review"))["with"]["llm_extra_headers"]
        self.assertNotIn("github.run_", headers)
        self.assertIn("github.repository_id", headers)
        self.assertIn("github.event.pull_request.number", headers)

    def test_checkpoint_context_ignores_the_editable_pr_body(self):
        script = named_step(action_steps(action("ocr-review")), "Gather review context")["with"]["script"]
        self.assertNotIn("pr.body", script)
        self.assertIn("issue.body", script)

    def test_rule_path_resolves_and_rejects_missing_files(self):
        path = ROOT / "ai-review-rule.json"
        self.assertEqual(json.loads(path.read_text()), {
            "rules": [{
                "path": "**/*",
                "rule": "REVIEW.md",
                "merge_system_rule": True,
            }],
        })
        self.assertTrue((ROOT / "REVIEW.md").is_file())
        rule_step = named_step(action_steps(action("ocr-review")), "Resolve rule path")
        self.assertEqual(rule_step["env"]["RULE"], "${{ inputs.rule }}")
        self.assertEqual(rule_step["env"]["DEFAULT_RULE"], "${{ format('{0}/ai-review-rule.json', github.workspace) }}")
        self.assertEqual(rule_step["env"]["WORKSPACE"], "${{ github.workspace }}")
        self.assertEqual(review_step(action("ocr-review"))["with"]["rule"], "${{ steps.rule.outputs.path }}")
        source = yaml.safe_load(workflow("ai-review.yml"))
        self.assertNotIn("rule", source["jobs"]["review"]["steps"][-1]["with"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            cases = (
                ({"RULE": "", "DEFAULT_RULE": str(path)}, True, f"path={path}\n"),
                ({"RULE": "", "DEFAULT_RULE": str(Path(directory) / "missing.json")}, True, ""),
                ({"RULE": "ai-review-rule.json", "DEFAULT_RULE": ""}, True, f"path={path}\n"),
                ({"RULE": str(Path(directory) / "missing.json"), "DEFAULT_RULE": ""}, False, ""),
            )
            for environment, succeeds, expected in cases:
                with self.subTest(environment=environment):
                    output.write_text("")
                    result = run_step(rule_step, {**environment, "WORKSPACE": str(ROOT), "GITHUB_OUTPUT": str(output)})
                    if succeeds:
                        self.assertEqual(result.returncode, 0, result.stderr)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("rule input does not exist", result.stdout)
                    self.assertEqual(output.read_text(), expected)

    def test_draft_pull_requests_skip_review_until_ready(self):
        assert_draft_guard(self, workflow("ai-review.yml"))

    def test_review_failures_propagate(self):
        source = workflow("ai-review.yml")
        jobs, items = workflow_items(source)
        self.assertEqual(set(jobs), {"review"})
        assert_failure_propagation(self, items)
        assert_failure_propagation(self, action_steps(action("ocr-review")))
        mutated = source.replace("      - name: Review\n", "      - name: Review\n        continue-on-error: true\n")
        self.assertNotEqual(mutated, source)
        _, items = workflow_items(mutated)
        with self.assertRaises(AssertionError):
            assert_failure_propagation(self, items)
        mutated = source.replace("  review:\n", "  review:\n    continue-on-error: true\n")
        _, items = workflow_items(mutated)
        with self.assertRaises(AssertionError):
            assert_failure_propagation(self, items)
        mutated = re.sub(r"(?m)^    if: .+$", "    if: always()", source)
        _, items = workflow_items(mutated)
        with self.assertRaises(AssertionError):
            assert_failure_propagation(self, items)
        review = action("ocr-review")
        mutated = review.replace("    - name: Require published review\n", "    - name: Require published review\n      if: failure()\n")
        with self.assertRaises(AssertionError):
            assert_failure_propagation(self, action_steps(mutated))

    def test_automatic_review_event_routing(self):
        source = workflow("ai-review.yml")
        self.assertIn("run-name: AI Review PR ${{ github.event.pull_request.number }}", source)
        match = re.search(r"(?m)^    types: \[([^]]+)\]$", source)
        actions = {action.strip() for action in match.group(1).split(",")}
        for action, starts in (
            ("opened", True), ("reopened", True),
            ("ready_for_review", True), ("synchronize", True),
            ("closed", False), ("edited", False),
        ):
            with self.subTest(action=action):
                self.assertEqual(action in actions, starts)

    def test_secret_jobs_allow_only_same_repository_pull_requests(self):
        source = workflow("ai-review.yml")
        triggers = source.split("on:\n", 1)[1].split("permissions:", 1)[0]
        self.assertEqual(re.findall(r"(?m)^  ([a-z_]+):", triggers), ["pull_request"])
        self.assertNotIn("pull_request_target:", source + action("ocr-review"))
        job = yaml.safe_load(source)["jobs"]["review"]
        condition = job["if"]
        self.assertTrue(condition.startswith("github.event.pull_request.head.repo.full_name == github.repository"))
        self.assertIn("github.event.pull_request.draft == false", condition)
        authors = json.loads((ROOT / "repo-settings.json").read_text())["factory_authors"]
        self.assertEqual(re.findall(r"pull_request\.user\.login == '([^']+)'", condition), authors)
        self.assertEqual(
            job["steps"][-1]["with"]["opencode_zen_api_key"],
            "${{ secrets.OPENCODE_ZEN_API_KEY }}",
        )

    def test_restyle_step_follows_review_and_uses_the_action_directory(self):
        source = action("ocr-review")
        steps = action_steps(source)
        names = [item.get("name") for item in steps]
        self.assertLess(names.index("Review"), names.index("Restyle review comments"))
        restyle = named_step(steps, "Restyle review comments")
        self.assertEqual(restyle["env"]["OCR_SCRIPT"], "${{ github.action_path }}/restyle.py")
        self.assertNotIn("github.workspace", restyle["env"]["OCR_SCRIPT"])
        self.assertTrue((ROOT / ".github/actions/ocr-review/restyle.py").is_file())

    def test_restyle_step_never_fails_the_job(self):
        restyle = named_step(action_steps(action("ocr-review")), "Restyle review comments")
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "fail.py"
            script.write_text("raise SystemExit(1)\n")
            result = run_step(restyle, {"OCR_SCRIPT": str(script)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::warning::", result.stdout)

    def test_restyle_inputs_mirror_the_review_literals(self):
        source = action("ocr-review")
        inputs = yaml.safe_load(source)["inputs"]
        with_ = review_step(source)["with"]
        restyle = named_step(action_steps(source), "Restyle review comments")
        for name, variable in (
            ("route_severity_below", "OCR_ROUTE_SEVERITY_BELOW"),
            ("route_categories", "OCR_ROUTE_CATEGORIES"),
            ("effort", "OCR_EFFORT"),
        ):
            with self.subTest(input=name):
                self.assertEqual(inputs[name]["default"], with_[name])
                self.assertEqual(restyle["env"][variable], "${{ inputs." + name + " }}")

    def test_missing_key_fails_before_review(self):
        source = action("ocr-review")
        check = named_step(action_steps(source), "Require Console key")
        self.assertEqual(check["env"]["ZEN_KEY"], "${{ inputs.opencode_zen_api_key }}")
        self.assertNotIn("continue-on-error", check)
        self.assertLess(source.index("name: Require Console key"), source.index("name: Review\n"))
        self.assertNotEqual(run_step(check, {"ZEN_KEY": ""}).returncode, 0)
        self.assertEqual(run_step(check, {"ZEN_KEY": "fixture"}).returncode, 0)

    def test_summary_check_does_not_fail_on_findings_that_could_not_be_inlined(self):
        check = named_step(action_steps(action("ocr-review")), "Require published review")
        self.assertEqual(check["env"]["SUMMARY"], "${{ steps.review.outputs.summary_comment_url }}")
        self.assertEqual(check["env"]["RANGE_REASON"], "${{ steps.review.outputs.range_reason }}")
        self.assertEqual(check["env"]["RESULT_PATH"], "/tmp/ocr-result.json")  # nosec B108 -- the OCR action writes its result there
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
