from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from ocr_restyle_test_support import (
    CHECKPOINT,
    CLEAN_CHECKPOINT,
    CLEAN_CHECKPOINT_2,
    CLEAN_CHECKPOINT_3,
    FINGERPRINT,
    INLINE_BODY,
    STALE_CHECKPOINT,
    SUMMARY_BODY,
    SUMMARY_MARKER,
    FakeGitHub,
    carrier,
    finding,
    ocr_result,
    restyle,
)

RUN1_TO = "abc1234def567890123456789abcdef0123456789"
RUN2_TO = "2222222222222222222222222222222222222222"
RUN3_TO = "3333333333333333333333333333333333333333"


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.github = FakeGitHub()
        self.addCleanup(self.github.close)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.result_path = self.directory / "result.json"
        self.github.seed(SUMMARY_BODY, INLINE_BODY)

    def run_cli(self, environment=None):
        env = {
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
        output = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(output):
            code = restyle.main([])
        return types.SimpleNamespace(
            returncode=code, stdout=output.getvalue(), stderr=""
        )

    def run_snapshot(self):
        path = self.directory / "carried.json"
        env = {
            "GITHUB_API_URL": self.github.url,
            "GITHUB_REPOSITORY": "o/r",
            "PR_NUMBER": "7",
            "GITHUB_TOKEN": "unused",  # nosec B105 -- dummy credential for the local fake server
        }
        output = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(output):
            code = restyle.main(["--snapshot", str(path)])
        return types.SimpleNamespace(returncode=code, stdout=output.getvalue(), path=path)

    def write(self, result):
        self.result_path.write_text(json.dumps(result))

    def start_next_run(self, checkpoint):
        self.github.issue_comments[0]["body"] = (
            f"{SUMMARY_MARKER}\n"
            "✅ **OpenCodeReview**: Review complete: 0 finding(s) across 5 selected item(s).\n\n"
            f"{checkpoint}"
        )

    def next_run_environment(self, carried, run_id, from_sha, to_sha):
        return {
            "GITHUB_RUN_ID": run_id,
            "OCR_RANGE_FROM": from_sha,
            "OCR_RANGE_TO": to_sha,
            "OCR_CARRIED": str(carried),
            "OCR_INLINE": "0",
            "OCR_SKIPPED": "0",
        }

    def raise_routed_finding(self):
        self.github.review_comments.clear()
        self.write(
            ocr_result(
                [finding(path="scripts/a.py", severity="medium", start=10, end=12)]
            )
        )
        result = self.run_cli(environment={"OCR_INLINE": "0", "OCR_SKIPPED": "0"})
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("1 lower-priority findings", self.github.issue_comments[0]["body"])
        snapshot = self.run_snapshot()
        self.assertEqual(snapshot.returncode, 0, snapshot.stdout)
        return snapshot.path

    def run_clean_second_run(self, carried, result=None):
        self.start_next_run(CLEAN_CHECKPOINT_2)
        self.write(
            result
            or ocr_result(
                [], message="Review complete: 0 finding(s) across 5 selected item(s)."
            )
        )
        return self.run_cli(
            environment=self.next_run_environment(carried, "2", RUN1_TO, RUN2_TO)
        )

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
        self.github.fail("PATCH", "/repos/o/r/pulls/comments/2")
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

    def test_a_routed_finding_survives_a_later_clean_checkpointed_run(self):
        carried = self.raise_routed_finding()
        payload = json.loads(carried.read_text())
        self.assertEqual(payload["findings"][0]["head"], RUN1_TO)
        self.assertEqual(len(payload["findings"]), 1)

        result = self.run_clean_second_run(carried)
        self.assertEqual(result.returncode, 0, result.stdout)
        summary = self.github.issue_comments[0]["body"]
        self.assertIn("**🟡 P2 · A finding title** · `scripts/a.py:10-12`", summary)
        self.assertIn("Carried from `def5678..abc1234`", summary)
        self.assertIn(CLEAN_CHECKPOINT_2, summary)
        self.assertIn(SUMMARY_MARKER, summary)

    def test_a_same_line_finding_replaces_a_carried_one_after_remapping(self):
        carried = self.raise_routed_finding()
        self.github.compare = {
            "files": [
                {
                    "filename": "scripts/a.py",
                    "patch": "@@ -5,0 +6,3 @@\n+one\n+two\n+three",
                }
            ]
        }
        fresh = finding(
            path="scripts/a.py",
            severity="medium",
            start=13,
            end=15,
            content="**A fresh finding**\n\nThe fresh explanation.",
        )
        result = self.run_clean_second_run(carried, ocr_result([fresh]))
        self.assertEqual(result.returncode, 0, result.stdout)
        summary = self.github.issue_comments[0]["body"]
        self.assertIn("**🟡 P2 · A fresh finding** · `scripts/a.py:13-15`", summary)
        self.assertNotIn("A finding title", summary)
        self.assertNotIn("Carried from", summary)
        self.assertIn("1 lower-priority findings", summary)

    def test_an_unrouted_finding_on_the_same_line_replaces_a_carried_one(self):
        carried = self.raise_routed_finding()
        self.github.compare = {"files": []}
        raised = {**finding(path="scripts/a.py", start=10, end=12), "severity": "high"}
        self.start_next_run(CLEAN_CHECKPOINT_2)
        self.write(
            ocr_result(
                [raised],
                message="Review complete: 1 finding(s) across 5 selected item(s).",
            )
        )
        result = self.run_cli(
            environment=self.next_run_environment(carried, "2", RUN1_TO, RUN2_TO)
            | {"OCR_INLINE": "0", "OCR_SKIPPED": "1"}
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        summary = self.github.issue_comments[0]["body"]
        self.assertNotIn("A finding title", summary)
        self.assertNotIn("lower-priority findings", summary)
        self.assertEqual(restyle.decode_routed(summary).findings, [])

    def test_a_finding_whose_line_is_modified_is_dropped(self):
        carried = self.raise_routed_finding()
        self.github.compare = {
            "files": [
                {
                    "filename": "scripts/a.py",
                    "patch": (
                        "@@ -8,5 +8,5 @@\n line8\n line9\n"
                        "-old10\n+new10\n line11\n line12"
                    ),
                }
            ]
        }
        result = self.run_clean_second_run(carried)
        self.assertEqual(result.returncode, 0, result.stdout)
        summary = self.github.issue_comments[0]["body"]
        self.assertNotIn("A finding title", summary)
        self.assertNotIn("lower-priority findings", summary)
        self.assertEqual(restyle.decode_routed(summary).findings, [])

    def test_a_finding_below_an_insertion_survives_with_a_shifted_line(self):
        carried = self.raise_routed_finding()
        self.github.compare = {
            "files": [
                {
                    "filename": "scripts/a.py",
                    "patch": "@@ -5,0 +6,3 @@\n+one\n+two\n+three",
                }
            ]
        }
        result = self.run_clean_second_run(carried)
        self.assertEqual(result.returncode, 0, result.stdout)
        summary = self.github.issue_comments[0]["body"]
        self.assertIn("**🟡 P2 · A finding title** · `scripts/a.py:13-15`", summary)
        self.assertIn("Carried from `def5678..abc1234`", summary)
        decoded = restyle.decode_routed(summary)
        self.assertEqual(decoded.findings[0]["head"], RUN2_TO)
        self.assertEqual(decoded.findings[0]["start_line"], 13)

    def test_a_remapped_finding_survives_a_third_clean_run_at_the_same_line(self):
        carried = self.raise_routed_finding()
        self.github.compare = {
            "files": [
                {
                    "filename": "scripts/a.py",
                    "patch": "@@ -5,0 +6,3 @@\n+one\n+two\n+three",
                }
            ]
        }
        self.run_clean_second_run(carried)
        snapshot = self.run_snapshot()
        payload = json.loads(snapshot.path.read_text())
        self.assertEqual(payload["findings"][0]["head"], RUN2_TO)
        self.assertEqual(payload["findings"][0]["start_line"], 13)
        self.assertEqual(payload["findings"][0]["origin"]["to"], RUN1_TO)

        self.github.compare = {"files": []}
        self.start_next_run(CLEAN_CHECKPOINT_3)
        self.write(
            ocr_result(
                [], message="Review complete: 0 finding(s) across 5 selected item(s)."
            )
        )
        result = self.run_cli(
            environment=self.next_run_environment(snapshot.path, "3", RUN2_TO, RUN3_TO)
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        summary = self.github.issue_comments[0]["body"]
        self.assertIn("**🟡 P2 · A finding title** · `scripts/a.py:13-15`", summary)
        self.assertIn("Carried from `def5678..abc1234`", summary)

    def test_duplicate_carried_findings_are_listed_once(self):
        carried = self.raise_routed_finding()
        payload = json.loads(carried.read_text())
        payload["findings"] = payload["findings"] * 2
        carried.write_text(json.dumps(payload))
        result = self.run_clean_second_run(carried)
        self.assertEqual(result.returncode, 0, result.stdout)
        summary = self.github.issue_comments[0]["body"]
        self.assertIn("1 lower-priority findings", summary)
        self.assertEqual(summary.count("A finding title"), 1)

    def test_a_carried_finding_without_a_diff_is_kept_and_names_the_file(self):
        carried = self.raise_routed_finding()
        self.github.compare = {
            "files": [{"filename": "scripts/a.py", "status": "binary"}]
        }
        result = self.run_clean_second_run(carried)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("no usable diff for scripts/a.py", result.stdout)
        self.assertIn("`scripts/a.py:10-12`", self.github.issue_comments[0]["body"])

    def test_a_failed_compare_keeps_the_carried_finding_unremapped(self):
        carried = self.raise_routed_finding()
        self.github.fail_compare = True
        result = self.run_clean_second_run(carried)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("cannot map carried findings", result.stdout)
        self.assertIn("`scripts/a.py:10-12`", self.github.issue_comments[0]["body"])

    def test_malformed_or_missing_carried_state_warns_and_still_rewrites(self):
        self.start_next_run(CLEAN_CHECKPOINT_2)
        self.write(ocr_result([finding(path="scripts/b.py", severity="medium")]))
        for name, value in (("malformed", "{not json"), ("missing", None)):
            with self.subTest(state=name):
                path = self.directory / f"{name}.json"
                if value is not None:
                    path.write_text(value)
                result = self.run_cli(
                    environment={
                        "GITHUB_RUN_ID": "2",
                        "OCR_RANGE_FROM": RUN1_TO,
                        "OCR_RANGE_TO": RUN2_TO,
                        "OCR_CARRIED": str(path),
                        "OCR_INLINE": "0",
                        "OCR_SKIPPED": "0",
                    }
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("cannot read carried findings", result.stdout)
                self.assertIn(
                    "A finding title", self.github.issue_comments[0]["body"]
                )

    def test_same_head_noop_keeps_the_carried_findings(self):
        carried = self.raise_routed_finding()
        original = self.github.issue_comments[0]["body"]
        self.github.patches.clear()
        result = self.run_cli(
            environment={
                "OCR_RANGE_REASON": "same_head_noop",
                "OCR_CARRIED": str(carried),
                "OCR_INLINE": "0",
            }
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("::warning::", result.stdout)
        self.assertEqual(self.github.issue_comments[0]["body"], original)
        self.assertEqual(self.github.patches, [])

    def test_distinct_routed_findings_on_one_line_are_both_listed(self):
        self.github.review_comments.clear()
        self.write(
            ocr_result(
                [
                    finding(path="scripts/a.py", severity="medium", start=10, end=12, content="**First finding**"),
                    finding(path="scripts/a.py", severity="medium", start=10, end=10, content="**Second finding**"),
                ]
            )
        )
        result = self.run_cli(environment={"OCR_INLINE": "0", "OCR_SKIPPED": "0"})
        self.assertEqual(result.returncode, 0, result.stdout)
        summary = self.github.issue_comments[0]["body"]
        self.assertIn("2 lower-priority findings", summary)
        self.assertIn("First finding", summary)
        self.assertIn("Second finding", summary)

    def test_a_failed_compare_keeps_the_old_head_for_a_later_remap(self):
        carried = self.raise_routed_finding()
        self.github.fail_compare = True
        result = self.run_clean_second_run(carried)
        self.assertEqual(result.returncode, 0, result.stdout)
        snapshot = self.run_snapshot()
        kept = json.loads(snapshot.path.read_text())["findings"][0]
        self.assertEqual(kept["head"], RUN1_TO)
        self.assertEqual(kept["start_line"], 10)

        self.github.fail_compare = False
        self.github.compare = {
            "files": [{"filename": "scripts/a.py", "patch": "@@ -5,0 +6,3 @@\n+one\n+two\n+three"}]
        }
        self.start_next_run(CLEAN_CHECKPOINT_3)
        self.write(
            ocr_result([], message="Review complete: 0 finding(s) across 5 selected item(s).")
        )
        result = self.run_cli(
            environment=self.next_run_environment(snapshot.path, "3", RUN2_TO, RUN3_TO)
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("`scripts/a.py:13-15`", self.github.issue_comments[0]["body"])

    def test_a_failed_write_restores_the_carried_marker(self):
        carried = self.directory / "carried.json"
        carried.write_text(
            json.dumps(
                {
                    "findings": [
                        {
                            "path": "scripts/z.py",
                            "start_line": 50,
                            "end_line": 52,
                            "severity": "medium",
                            "content": "**A finding title**",
                            "head": RUN1_TO,
                            "origin": {"run": "0", "from": "def5678", "to": RUN1_TO},
                        }
                    ]
                }
            )
        )
        self.write(ocr_result([finding()]))
        self.github.fail("PATCH", "/repos/o/r/pulls/comments/2")
        result = self.run_cli(environment={"OCR_CARRIED": str(carried)})
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("::warning::", result.stdout)
        restored = restyle.decode_routed(self.github.issue_comments[0]["body"])
        self.assertEqual(restored.findings[0]["path"], "scripts/z.py")
        self.assertEqual(self.github.review_comments[0]["body"], INLINE_BODY)

    def test_a_failed_write_replaces_a_stale_routed_marker_in_the_restored_body(self):
        carried = self.directory / "carried.json"
        carried.write_text(
            json.dumps(
                {
                    "findings": [
                        {
                            "path": "scripts/z.py",
                            "start_line": 50,
                            "end_line": 52,
                            "severity": "medium",
                            "content": "**Carried finding**",
                            "head": RUN1_TO,
                            "origin": {"run": "0", "from": "def5678", "to": RUN1_TO},
                        }
                    ]
                }
            )
        )
        stale = restyle.encode_routed(
            [{"path": "scripts/old.py", "start_line": 1, "end_line": 1, "severity": "low", "content": "**Stale**", "head": RUN1_TO}]
        )
        self.github.issue_comments[0]["body"] = f"{stale}\n{SUMMARY_BODY}"
        self.write(ocr_result([finding()]))
        self.github.fail("PATCH", "/repos/o/r/pulls/comments/2")
        result = self.run_cli(environment={"OCR_CARRIED": str(carried)})
        self.assertEqual(result.returncode, 0, result.stdout)
        restored_body = self.github.issue_comments[0]["body"]
        self.assertEqual(restyle.decode_routed(restored_body).findings[0]["path"], "scripts/z.py")
        self.assertNotIn("scripts/old.py", restored_body)
        self.assertEqual(restored_body.count("ocr-routed:v1"), 1)

    def reset_github(self):
        self.github.issue_comments.clear()
        self.github.review_comments.clear()
        self.github.patches.clear()
        self.github.faults.clear()
        self.github.compare = {"files": []}
        self.github.fail_compare = False
        self.github.seed(SUMMARY_BODY, INLINE_BODY)

    def prepare_later_run(self):
        carried = self.raise_routed_finding()
        payload = json.loads(carried.read_text())
        payload["findings"].append(
            {
                "path": "scripts/c.py",
                "start_line": 60,
                "end_line": 62,
                "severity": "low",
                "category": "style",
                "content": "**Second carried finding**",
                "head": RUN1_TO,
                "origin": {"run": "1", "from": "def56789abcdef0123456789abcdef0123456789", "to": RUN1_TO},
            }
        )
        carried.write_text(json.dumps(payload))
        self.run1_marker = restyle.encode_routed(payload["findings"])
        self.github.review_comments.append(
            {
                "id": 3,
                "body": INLINE_BODY.replace("ocr-1-1-", "ocr-2-1-"),
                "html_url": "https://example.test/pull/7#discussion_r3",
                "path": "scripts/b.py",
                "line": 40,
                "start_line": 40,
            }
        )
        self.start_next_run(CLEAN_CHECKPOINT_2)
        self.write(ocr_result([finding(path="scripts/b.py", severity="high", start=40, end=40)]))
        return carried

    def later_run_environment(self, carried):
        return self.next_run_environment(carried, "2", RUN1_TO, RUN2_TO) | {
            "OCR_INLINE": "1",
            "OCR_SKIPPED": "0",
        }

    def carried_on_summary(self):
        findings = restyle.decode_routed(self.github.issue_comments[0]["body"]).findings
        return [
            (
                item["path"],
                item["start_line"],
                item["end_line"],
                item["head"],
                item["severity"],
                item["content"],
                item["origin"]["from"],
                item["origin"]["run"],
            )
            for item in findings
        ]

    def assert_snapshot_kept(self):
        self.assertEqual(
            self.carried_on_summary(),
            [
                ("scripts/a.py", 10, 12, RUN1_TO, "medium", "**A finding title**\n\nThe explanation names the failure and the line.", "def56789abcdef0123456789abcdef0123456789", "1"),
                ("scripts/c.py", 60, 62, RUN1_TO, "low", "**Second carried finding**", "def56789abcdef0123456789abcdef0123456789", "1"),
            ],
        )
        self.assertIn(self.run1_marker, self.github.issue_comments[0]["body"])

    def test_a_later_failure_keeps_every_snapshotted_finding_on_the_summary(self):
        injections = {
            "summary lookup": lambda github: github.fail("GET", "/repos/o/r/issues/7/comments"),
            "inline lookup": lambda github: github.fail("GET", "/repos/o/r/pulls/7/comments"),
            "summary write": lambda github: github.fail("PATCH", "/repos/o/r/issues/comments/1"),
            "inline write": lambda github: github.fail("PATCH", "/repos/o/r/pulls/comments/3"),
            "compare": lambda github: setattr(github, "fail_compare", True),
        }
        for name, inject in injections.items():
            with self.subTest(failure=name):
                self.reset_github()
                carried = self.prepare_later_run()
                inject(self.github)
                result = self.run_cli(environment=self.later_run_environment(carried))
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assert_snapshot_kept()

        with self.subTest(failure="rewrite"):
            self.reset_github()
            carried = self.prepare_later_run()
            result = self.run_cli(
                environment=self.later_run_environment(carried) | {"OCR_INLINE": "3"}
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("OCR restyle skipped: OCR reported 3 inline", result.stdout)
            self.assert_snapshot_kept()

    def test_a_failed_fallback_write_only_warns(self):
        self.reset_github()
        carried = self.prepare_later_run()
        self.github.fail("PATCH", "/repos/o/r/issues/comments/1", times=2)
        result = self.run_cli(environment=self.later_run_environment(carried))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("cannot keep carried findings on the summary", result.stdout)
        self.assertNotIn("ocr-routed:v1", self.github.issue_comments[0]["body"])
        self.assertEqual(self.github.issue_comments[0]["body"].count("<!-- ocr-summary -->"), 1)

    def test_a_skipped_review_keeps_the_carried_findings_on_the_summary(self):
        self.reset_github()
        carried = self.prepare_later_run()
        skipped = ocr_result([], message="Review skipped.")
        skipped["status"] = "skipped"
        self.write(skipped)
        result = self.run_cli(environment=self.later_run_environment(carried))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_snapshot_kept()

    def test_a_truncated_diff_keeps_the_carried_finding_unremapped_with_a_warning(self):
        carried = self.raise_routed_finding()
        self.github.compare = {
            "files": [{"filename": "scripts/a.py", "patch": "@@ -5,0 +6,3 @@\n+one\n\n+two"}]
        }
        result = self.run_clean_second_run(carried)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("no usable diff for scripts/a.py", result.stdout)
        self.assertIn("`scripts/a.py:10-12`", self.github.issue_comments[0]["body"])

    def test_a_no_newline_marker_does_not_make_the_diff_unusable(self):
        carried = self.raise_routed_finding()
        self.github.compare = {
            "files": [
                {
                    "filename": "scripts/a.py",
                    "patch": "@@ -5,0 +6,3 @@\n+one\n+two\n+three\n\\ No newline at end of file",
                }
            ]
        }
        result = self.run_clean_second_run(carried)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("no usable diff", result.stdout)
        self.assertIn("`scripts/a.py:13-15`", self.github.issue_comments[0]["body"])

    def test_an_entry_without_an_origin_is_dropped_with_a_warning(self):
        carried = self.raise_routed_finding()
        payload = json.loads(carried.read_text())
        payload["findings"].append(
            {
                "path": "scripts/x.py",
                "start_line": 5,
                "end_line": 5,
                "severity": "low",
                "content": "**Originless entry**",
                "head": RUN1_TO,
            }
        )
        carried.write_text(json.dumps(payload))
        result = self.run_clean_second_run(carried)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("carried finding 1 dropped: no origin", result.stdout)
        kept = restyle.decode_routed(self.github.issue_comments[0]["body"]).findings
        self.assertEqual([item["path"] for item in kept], ["scripts/a.py"])

    def test_an_entry_without_a_head_is_dropped_with_a_warning(self):
        carried = self.raise_routed_finding()
        payload = json.loads(carried.read_text())
        payload["findings"].append(
            {
                "path": "scripts/x.py",
                "start_line": 5,
                "end_line": 5,
                "severity": "low",
                "content": "**Headless entry**",
                "head": "",
                "origin": {"run": "0", "from": "def5678", "to": RUN1_TO},
            }
        )
        carried.write_text(json.dumps(payload))
        result = self.run_clean_second_run(carried)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("carried finding 1 dropped: no coordinate head", result.stdout)
        kept = restyle.decode_routed(self.github.issue_comments[0]["body"]).findings
        self.assertEqual([item["path"] for item in kept], ["scripts/a.py"])

    def test_a_routed_finding_without_any_head_is_not_written_with_an_empty_head(self):
        self.github.review_comments.clear()
        headless = ocr_result([finding(path="scripts/a.py", severity="medium", start=10, end=12)])
        headless["manifest"]["input"]["resolved_head"] = ""
        self.write(headless)
        result = self.run_cli(environment={"OCR_INLINE": "0", "OCR_SKIPPED": "0"})
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("routed finding at scripts/a.py:10-12 not carried: no coordinate head", result.stdout)
        self.assertEqual(restyle.decode_routed(self.github.issue_comments[0]["body"]).findings, [])

    def test_a_failed_snapshot_write_leaves_the_previous_snapshot_intact(self):
        target = self.directory / "carried.json"
        target.write_text('{"findings": []}')
        (self.directory / "carried.json.tmp").mkdir()
        result = self.run_snapshot()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(target.read_text(), '{"findings": []}')

    def test_snapshot_reads_the_previous_routed_marker(self):
        carried = self.raise_routed_finding()
        payload = json.loads(carried.read_text())
        self.assertEqual(payload["findings"][0]["head"], RUN1_TO)
        self.assertEqual(payload["findings"][0]["path"], "scripts/a.py")
        self.assertEqual(payload["findings"][0]["origin"]["from"], "def56789abcdef0123456789abcdef0123456789")

    def test_snapshot_without_a_routed_marker_carries_nothing(self):
        result = self.run_snapshot()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("::warning::", result.stdout)
        self.assertEqual(json.loads(result.path.read_text())["findings"], [])

    def test_snapshot_degrades_to_no_findings_when_the_marker_is_malformed(self):
        self.github.issue_comments[:] = [
            {
                "id": 1,
                "body": f"{SUMMARY_MARKER}\n<!-- ocr-routed:v1 not-base64! -->",
                "html_url": "https://example.test/issues/1",
            }
        ]
        result = self.run_snapshot()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("cannot decode the routed-findings marker", result.stdout)
        self.assertEqual(json.loads(result.path.read_text())["findings"], [])

    def test_snapshot_without_a_previous_summary_carries_nothing(self):
        self.github.issue_comments.clear()
        result = self.run_snapshot()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("::warning::", result.stdout)
        self.assertEqual(json.loads(result.path.read_text())["findings"], [])
