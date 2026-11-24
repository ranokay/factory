from __future__ import annotations

import json
import os
import subprocess  # nosec B404 -- the tests run the local script with argv
import tempfile
import unittest
from pathlib import Path

from ocr_restyle_test_support import (
    CHECKPOINT,
    CLEAN_CHECKPOINT,
    FINGERPRINT,
    INLINE_BODY,
    SCRIPT,
    STALE_CHECKPOINT,
    SUMMARY_BODY,
    SUMMARY_MARKER,
    FakeGitHub,
    carrier,
    finding,
    ocr_result,
)


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.github = FakeGitHub()
        self.addCleanup(self.github.close)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.result_path = Path(directory.name) / "result.json"
        self.github.seed(SUMMARY_BODY, INLINE_BODY)

    def run_cli(self, environment=None):
        env = {
            **os.environ,
            "GITHUB_API_URL": self.github.url,
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_ATTEMPT": "1",
            "PR_NUMBER": "7",
            "GITHUB_TOKEN": "unused",  # nosec B105 -- dummy credential for the local fake server
            "OCR_RESULT": str(self.result_path),
            "OCR_RANGE_REASON": "full",
            "OCR_INLINE": "1",
            "OCR_SKIPPED": "0",
            "OCR_ROUTE_SEVERITY_BELOW": "medium",
            "OCR_ROUTE_CATEGORIES": "style,documentation",
            "OCR_EFFORT": "high",
            "OCR_MODEL": "deepseek-v4.1-flash",
            "OCR_VERSION": "1.14.0",
        }
        env.update(environment or {})
        # The audit rule flags the variable first argument; this test runs
        # the script with argv and no shell. See
        # https://semgrep.dev/r/python.lang.security.audit.dangerous-subprocess-use-audit
        return subprocess.run(  # nosec B603, B607 -- local script, argv, no shell
            ["python3", str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def write(self, result):
        self.result_path.write_text(json.dumps(result))

    def test_end_to_end_rewrites_summary_and_inline(self):
        comments = [
            {**finding(path="scripts/a.py", start=10, end=12), "severity": "high"},
            finding(path="scripts/b.py", severity="medium", start=118, end=118),
            carrier(),
        ]
        self.write(ocr_result(comments))
        result = self.run_cli(environment={"OCR_INLINE": "1", "OCR_SKIPPED": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.github.patches), 2, self.github.patches)
        summary = self.github.issue_comments[0]["body"]
        self.assertIn("## AI Review · 2/5 · Blocking issues", summary)
        self.assertIn("[A finding title]", summary)
        self.assertIn("1 lower-priority findings (in summary only)", summary)
        self.assertIn("<details><summary>Diagram</summary>", summary)
        self.assertIn(CHECKPOINT, summary)
        inline = self.github.review_comments[0]["body"]
        self.assertTrue(inline.startswith(FINGERPRINT))
        self.assertIn("**🟠 P1 · A finding title**", inline)
        self.assertIn("The explanation names the failure and the line.", inline)
        self.assertIn("<details><summary>Suggested change</summary>", inline)
        self.assertIn("```suggestion", inline)
        self.assertNotIn("### 📄", inline)
        self.assertNotIn("**Suggestion:**", inline)
        self.assertNotIn("Fix with agent prompt", inline)

    def test_forced_api_failure_restores_every_original(self):
        self.write(ocr_result([finding()]))
        self.github.fail_path = "/repos/o/r/pulls/comments/2"
        self.github.fail_once = True
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::warning::", result.stdout)
        self.assertEqual(self.github.issue_comments[0]["body"], SUMMARY_BODY)
        self.assertEqual(self.github.review_comments[0]["body"], INLINE_BODY)

    def test_bad_json_is_a_noop(self):
        self.result_path.write_text("{not json")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning::", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_unexpected_shape_is_a_noop(self):
        self.write({"comments": [1], "status": "complete"})
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning::", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_unknown_severity_is_a_warning_noop(self):
        self.write(ocr_result([finding(severity="urgent")]))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning::", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_empty_severity_is_a_warning_noop(self):
        self.write(ocr_result([finding(severity="")]))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning::", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_inline_count_mismatch_is_a_warning_noop(self):
        self.write(ocr_result([finding()]))
        result = self.run_cli(environment={"OCR_INLINE": "2"})
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning::", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_skipped_count_mismatch_is_a_warning_noop(self):
        self.write(
            ocr_result([finding(content="**Another finding**\n\nA different failure.")])
        )
        result = self.run_cli(environment={"OCR_SKIPPED": "5"})
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning::", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_unknown_route_severity_is_a_warning_noop(self):
        self.write(ocr_result([finding()]))
        result = self.run_cli(
            environment={"OCR_ROUTE_SEVERITY_BELOW": "critical-ish"}
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning::", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_unknown_route_category_is_a_warning_noop(self):
        self.write(ocr_result([finding()]))
        result = self.run_cli(environment={"OCR_ROUTE_CATEGORIES": "style,typo"})
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning::", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_same_head_noop_writes_nothing(self):
        self.write(ocr_result([finding()]))
        result = self.run_cli(environment={"OCR_RANGE_REASON": "same_head_noop"})
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("::warning::", result.stdout)
        self.assertIn("nothing to rewrite", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_skipped_run_writes_nothing(self):
        self.write({"status": "skipped", "comments": []})
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("::warning::", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_missing_summary_is_a_warning_noop(self):
        self.github.issue_comments.clear()
        self.write(ocr_result([finding()]))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("::warning::", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_clean_run_rewrites_a_summary_without_a_run_tag(self):
        clean = (
            f"{SUMMARY_MARKER}\n"
            "✅ **OpenCodeReview**: Review complete: 0 finding(s) across 1 selected item(s).\n\n"
            f"{CLEAN_CHECKPOINT}"
        )
        self.github.issue_comments[:] = [
            {"id": 1, "body": clean, "html_url": "https://example.test/issues/1"}
        ]
        self.write(ocr_result([], message="Review complete: 0 finding(s) across 1 selected item(s)."))
        result = self.run_cli(environment={"OCR_INLINE": "0", "OCR_SKIPPED": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.github.patches), 1, self.github.patches)
        summary = self.github.issue_comments[0]["body"]
        self.assertIn("## AI Review · 5/5 · Safe to merge", summary)
        self.assertIn("Review complete: 0 finding(s) across 1 selected item(s).", summary)
        self.assertIn(CLEAN_CHECKPOINT, summary)
        self.assertNotIn("| | Finding | Where |", summary)

    def test_summary_from_another_run_is_a_warning_noop(self):
        stale = f"{SUMMARY_MARKER}\n✅ **OpenCodeReview**: Review complete.\n\n{STALE_CHECKPOINT}"
        self.github.issue_comments[:] = [
            {"id": 1, "body": stale, "html_url": "https://example.test/issues/1"}
        ]
        self.write(ocr_result([finding()]))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("no sticky summary comment for this run", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_summary_lookup_ignores_a_malformed_candidate(self):
        self.github.issue_comments.insert(
            0,
            {
                "id": 3,
                "body": f"{SUMMARY_MARKER}\n<!-- ocr-checkpoint:v1 not-base64! -->",
                "html_url": "https://example.test/issues/3",
            },
        )
        self.write(ocr_result([finding()]))
        result = self.run_cli(environment={"OCR_INLINE": "1", "OCR_SKIPPED": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("::warning::", result.stdout)
        summary = next(c for c in self.github.issue_comments if c["id"] == 1)["body"]
        self.assertIn("## AI Review · 2/5 · Blocking issues", summary)
        self.assertEqual(
            [p for p in self.github.patches if "/issues/comments/3" in p[0]], []
        )

    def test_missing_range_reason_is_a_warning_noop(self):
        self.write(ocr_result([finding()]))
        result = self.run_cli(environment={"OCR_RANGE_REASON": ""})
        self.assertEqual(result.returncode, 0)
        self.assertIn("OCR_RANGE_REASON is missing", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_missing_counts_are_a_warning_noop(self):
        self.write(ocr_result([finding()]))
        for name in ("OCR_INLINE", "OCR_SKIPPED"):
            with self.subTest(name=name):
                result = self.run_cli(environment={name: ""})
                self.assertEqual(result.returncode, 0)
                self.assertIn(f"{name} is missing", result.stdout)
                self.assertEqual(self.github.patches, [])

    def test_two_summaries_with_the_same_run_tag_are_a_warning_noop(self):
        self.github.issue_comments.append(
            {
                "id": 3,
                "body": SUMMARY_BODY,
                "html_url": "https://example.test/issues/3",
            }
        )
        self.write(ocr_result([finding()]))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("more than one sticky summary carries this run's tag", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_run_tag_beats_a_checkpoint_only_candidate(self):
        self.github.issue_comments.append(
            {
                "id": 3,
                "body": f"{SUMMARY_MARKER}\n✅ Looks good.\n\n{CLEAN_CHECKPOINT}",
                "html_url": "https://example.test/issues/3",
            }
        )
        self.write(ocr_result([finding()]))
        result = self.run_cli(environment={"OCR_INLINE": "1", "OCR_SKIPPED": "0"})
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("::warning::", result.stdout)
        self.assertEqual(
            [p for p in self.github.patches if "/issues/comments/3" in p[0]], []
        )
        self.assertTrue(any("/issues/comments/1" in p[0] for p in self.github.patches))

    def test_two_checkpoint_candidates_are_a_warning_noop(self):
        self.github.issue_comments[:] = [
            {
                "id": 4,
                "body": f"{SUMMARY_MARKER}\n✅ One.\n\n{CLEAN_CHECKPOINT}",
                "html_url": "https://example.test/issues/4",
            },
            {
                "id": 3,
                "body": f"{SUMMARY_MARKER}\n✅ Two.\n\n{CLEAN_CHECKPOINT}",
                "html_url": "https://example.test/issues/3",
            },
        ]
        self.write(ocr_result([finding()]))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("more than one sticky summary matches this run", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_a_lone_malformed_candidate_is_named_in_the_warning(self):
        self.github.issue_comments[:] = [
            {
                "id": 3,
                "body": f"{SUMMARY_MARKER}\n<!-- ocr-checkpoint:v1 not-base64! -->",
                "html_url": "https://example.test/issues/3",
            }
        ]
        self.write(ocr_result([finding()]))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertIn("1 malformed candidate(s) ignored", result.stdout)
        self.assertEqual(self.github.patches, [])

    def test_inline_comment_without_an_id_is_a_warning_noop(self):
        self.github.review_comments[0]["id"] = None
        self.write(ocr_result([finding()], message="Review complete: 1 finding(s)."))
        result = self.run_cli(environment={"OCR_INLINE": "1", "OCR_SKIPPED": "0"})
        self.assertEqual(result.returncode, 0)
        self.assertIn("review comment has no id", result.stdout)
        self.assertEqual(self.github.patches, [])
