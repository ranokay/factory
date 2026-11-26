import os
import json
import re
import subprocess
import tempfile
import textwrap
import tomllib
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


def assert_rule_shape(test, path):
    test.assertEqual(json.loads(path.read_text()), {
        "rules": [{
            "path": "**/*",
            "rule": "REVIEW.md",
            "merge_system_rule": True,
        }],
    })


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
            "route_severity_below": "${{ inputs.route_severity_below }}",
            "route_categories": "${{ inputs.route_categories }}",
            "resolve_outdated": "true",
            "effort": "${{ inputs.effort }}",
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
        assert_rule_shape(self, path)
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
        self.assertEqual(
            restyle["env"]["OCR_INLINE"], "${{ steps.review.outputs.comments_inline }}"
        )
        self.assertEqual(
            restyle["env"]["OCR_SKIPPED"], "${{ steps.review.outputs.comments_skipped }}"
        )
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

    def test_carry_step_reads_the_previous_summary_before_review(self):
        steps = action_steps(action("ocr-review"))
        names = [item.get("name") for item in steps]
        self.assertLess(names.index("Carry routed findings"), names.index("Review"))
        self.assertLess(names.index("Review"), names.index("Restyle review comments"))
        carry = named_step(steps, "Carry routed findings")
        self.assertEqual(
            carry["env"]["OCR_SCRIPT"], "${{ github.action_path }}/restyle.py"
        )
        self.assertEqual(
            carry["env"]["OCR_CARRIED"], "${{ runner.temp }}/ocr-carried.json"
        )
        self.assertIn('--snapshot "$OCR_CARRIED"', carry["run"])
        restyle = named_step(steps, "Restyle review comments")
        self.assertEqual(restyle["env"]["OCR_CARRIED"], carry["env"]["OCR_CARRIED"])

    def test_carry_step_never_fails_the_job(self):
        carry = named_step(action_steps(action("ocr-review")), "Carry routed findings")
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "fail.py"
            script.write_text("raise SystemExit(1)\n")
            result = run_step(
                carry,
                {
                    "OCR_SCRIPT": str(script),
                    "OCR_CARRIED": str(Path(directory) / "carried.json"),
                },
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::warning::", result.stdout)

    def test_review_inputs_are_unchanged_for_the_checkpoint_fingerprint(self):
        with_ = review_step(action("ocr-review"))["with"]
        self.assertEqual(
            set(with_),
            {
                "ocr_version",
                "llm_url",
                "llm_model",
                "llm_use_anthropic",
                "llm_protocol",
                "llm_auth_token",
                "llm_extra_headers",
                "background",
                "rule",
                "sticky_summary",
                "incremental",
                "checkpoint_range",
                "route_severity_below",
                "route_categories",
                "resolve_outdated",
                "effort",
                "upload_artifacts",
            },
        )

    def test_review_and_restyle_inputs_share_defaults(self):
        source = action("ocr-review")
        inputs = yaml.safe_load(source)["inputs"]
        with_ = review_step(source)["with"]
        restyle = named_step(action_steps(source), "Restyle review comments")
        defaults = {
            "route_severity_below": "medium",
            "route_categories": "style,documentation",
            "effort": "high",
        }
        for name, variable in (
            ("route_severity_below", "OCR_ROUTE_SEVERITY_BELOW"),
            ("route_categories", "OCR_ROUTE_CATEGORIES"),
            ("effort", "OCR_EFFORT"),
        ):
            with self.subTest(input=name):
                self.assertEqual(inputs[name]["default"], defaults[name])
                self.assertEqual(with_[name], "${{ inputs." + name + " }}")
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


class TemplateReviewTests(unittest.TestCase):
    def template(self, name):
        return (ROOT / "project" / ".github" / "workflows" / name).read_text()

    def triggers(self, source):
        parsed = yaml.safe_load(source)
        return parsed.get("on", parsed.get(True))

    def test_gates_caller_names_the_required_contexts(self):
        source = self.template("check.yml")
        parsed = yaml.safe_load(source)
        self.assertEqual(parsed["name"], "Gates")
        self.assertEqual(parsed["permissions"], {"contents": "read"})
        self.assertEqual(
            self.triggers(source),
            {"pull_request": None, "push": {"branches": ["main"]}},
        )
        self.assertEqual(
            parsed["concurrency"],
            {
                "group": "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
                "cancel-in-progress": True,
            },
        )
        jobs = parsed["jobs"]
        self.assertEqual(set(jobs), {"lint", "security", "tests"})
        self.assertEqual(
            [jobs[name]["name"] for name in ("lint", "security", "tests")],
            ["Lint", "Security", "Tests"],
        )
        for name, group in (("lint", "lint"), ("security", "security"), ("tests", "test")):
            with self.subTest(job=name):
                gate = jobs[name]["steps"][-1]
                self.assertEqual(
                    gate["uses"], "ranokay/factory/.github/actions/gates@v0.5.0"
                )
                self.assertEqual(gate["with"]["group"], group)
        self.assertNotIn("@main", source)

    def test_ai_review_caller_names_ocr(self):
        source = self.template("ai-review.yml")
        parsed = yaml.safe_load(source)
        self.assertEqual(parsed["name"], "AI Review")
        assert_review_permissions(self, source)
        assert_draft_guard(self, source)
        self.assertEqual(
            self.triggers(source),
            {
                "pull_request": {
                    "types": ["opened", "synchronize", "reopened", "ready_for_review"]
                }
            },
        )
        self.assertEqual(
            parsed["concurrency"],
            {
                "group": "ai-review-${{ github.event.pull_request.number }}",
                "cancel-in-progress": True,
            },
        )
        job = parsed["jobs"]["review"]
        self.assertEqual(job["name"], "OCR")
        review = job["steps"][-1]
        self.assertEqual(
            review["uses"], "ranokay/factory/.github/actions/ocr-review@v0.5.0"
        )
        self.assertEqual(
            review["with"]["opencode_zen_api_key"],
            "${{ secrets.OPENCODE_ZEN_API_KEY }}",
        )
        self.assertNotIn("@main", source)

    def test_review_files_are_generic(self):
        assert_rule_shape(self, ROOT / "project" / "ai-review-rule.json")
        text = (ROOT / "project" / "REVIEW.md").read_text()
        self.assertNotIn("Factory", text)
        self.assertNotIn("ranokay", text)
        self.assertIn("## Score", text)
        self.assertIn("## Diagram", text)

    def test_template_profiles_carry_the_gate_tasks(self):
        mise = tomllib.loads((ROOT / "project" / "mise.toml").read_text())
        refs = [*mise["include"], *mise["task_config"]["includes"]]
        self.assertTrue(refs)
        tags = {ref.split("?ref=", 1)[1] for ref in refs}
        self.assertEqual(tags, {"v0.5.0"})
        tag = tags.pop()
        hk = (ROOT / "project" / "hk.pkl").read_text()
        self.assertIn(f"/{tag}/profiles/universal/hk.pkl", hk)
        pinned = subprocess.run(  # nosec B603 B607 -- reads the pinned tag from this checkout
            ["git", "-C", str(ROOT), "show", f"{tag}:profiles/universal/tasks.toml"],
            capture_output=True,
            text=True,
            check=False,
        )
        if pinned.returncode != 0:
            self.skipTest(f"{tag} is not fetched in this checkout")
        tasks = tomllib.loads(pinned.stdout)
        for name in ("check:lint", "check:security", "check:test"):
            self.assertIn(name, tasks)

    def test_template_pin_policy_keeps_factory_tags_bumpable(self):
        pinact = yaml.safe_load((ROOT / "project" / ".pinact.yaml").read_text())
        self.assertEqual(pinact["version"], 3)
        self.assertTrue(pinact["rules"][0]["ignore"])
        expr = pinact["rules"][0]["conditions"][0]["expr"]
        self.assertIn('ActionRepoFullName == "ranokay/factory"', expr)
        self.assertIn(r'ActionVersion matches "v\\d+\\.\\d+\\.\\d+"', expr)


if __name__ == "__main__":
    unittest.main()
