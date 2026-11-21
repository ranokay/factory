import os
import re
import subprocess
import textwrap
import unittest
from pathlib import Path

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


class AiReviewWorkflowTests(unittest.TestCase):
    def test_secret_jobs_allow_only_same_repository_pull_requests(self):
        caller = workflow("ai-review-caller.yml")
        reusable = workflow("ai-review.yml")
        self.assertRegex(caller, r"(?m)^  pull_request:$")
        self.assertNotIn("pull_request_target:", caller + reusable)
        for source in (caller, reusable):
            condition = re.search(r"(?m)^    if: (.+)$", source).group(1)
            operands = re.split(r"\s*==\s*", condition)
            self.assertEqual(len(operands), 2)
            for head, expected in (("ranokay/factory", True), ("outside/fork", False)):
                context = {
                    "github.event.pull_request.head.repo.full_name": head,
                    "github.repository": "ranokay/factory",
                }
                self.assertEqual(context[operands[0]] == context[operands[1]], expected)

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
        self.assertNotEqual(run_step(check, {"SUMMARY": ""}).returncode, 0)
        for failures in ("0", "1"):
            result = run_step(check, {"SUMMARY": "https://example.test/comment", "FAILED": failures})
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
