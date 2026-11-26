from __future__ import annotations

import base64
import json
import unittest

from hypothesis import HealthCheck, given, settings, strategies as st

from ocr_restyle_test_support import (
    CLEAN_CHECKPOINT,
    INLINE_BODY,
    RUN_MARKER,
    SUMMARY_BODY,
    SUMMARY_MARKER,
    carrier,
    finding,
    ocr_result,
    restyle,
    summary_for,
)


class RestyleTests(unittest.TestCase):
    def test_each_score_tier_appears_in_the_header(self):
        cases = (
            ((), 5, "Safe to merge"),
            (("low",), 5, "Safe to merge"),
            (("low", "low"), 5, "Safe to merge"),
            (("medium",), 4, "Merge after fixes"),
            (("medium", "medium"), 3, "Merge after fixes"),
            (("medium", "medium", "medium"), 3, "Merge after fixes"),
            (("high",), 2, "Blocking issues"),
            (("high", "medium", "medium"), 2, "Blocking issues"),
            (("critical",), 1, "Blocking issues"),
            (("critical", "high", "medium"), 1, "Blocking issues"),
        )
        for values, expected, verdict in cases:
            with self.subTest(severities=values):
                comments = [
                    finding(
                        path=f"f{index}.py",
                        severity=value,
                        start=index + 1,
                        end=index + 1,
                    )
                    for index, value in enumerate(values)
                ]
                body = summary_for(ocr_result(comments))
                self.assertIn(f"## AI Review · {expected}/5 · {verdict}", body)

    def test_carrier_is_excluded_from_the_score(self):
        body = summary_for(ocr_result([carrier(), finding(severity="medium")]))
        self.assertIn("## AI Review · 4/5 · Merge after fixes", body)
        self.assertNotIn("REVIEW.md", body)

    def test_summary_layout_links_inline_and_collapses_routed(self):
        comments = [
            finding(),
            finding(path="scripts/b.py", severity="medium", start=118, end=118),
            finding(path="scripts/c.py", severity="medium", category="style", start=86, end=86),
            carrier(),
        ]
        result = ocr_result(comments)
        body = summary_for(
            result, {0: "https://github.com/o/r/pull/7#discussion_r2"}
        )
        self.assertIn("## AI Review · 2/5 · Blocking issues", body)
        self.assertIn("Review complete: 3 finding(s) across 5 selected item(s).", body)
        self.assertIn(
            "| 🟠 P1 | [A finding title](https://github.com/o/r/pull/7#discussion_r2) | `scripts/a.py:10-12` |",
            body,
        )
        self.assertIn("2 lower-priority findings (in summary only)", body)
        self.assertIn("**🟡 P2 · A finding title** · `scripts/b.py:118`", body)
        self.assertNotIn("GitHub could not post this as an inline comment", body)
        self.assertNotIn("REVIEW.md", body)
        self.assertIn("<details><summary>Diagram</summary>", body)
        self.assertIn("flowchart LR", body)
        self.assertIn(
            "<sub>Reviewed `abc1234` (range `def5678..abc1234`) · OCR 1.14.0 · deepseek-v4.1-flash · effort high</sub>",
            body,
        )
        for marker in restyle.markers(SUMMARY_BODY):
            self.assertIn(marker, body)

    def test_inline_matching_uses_content_to_disambiguate_titles(self):
        first = finding(content="**Same title**\n\nFirst explanation.")
        second = finding(
            path="scripts/b.py",
            start=20,
            end=20,
            content="**Same title**\n\nSecond explanation.",
        )
        posted = [
            {"body": "**Same title**\n\nSecond explanation.", "html_url": "u2"},
            {"body": "**Same title**\n\nFirst explanation.", "html_url": "u1"},
        ]
        links, pairs = restyle.match_inline(ocr_result([first, second]), posted)
        self.assertEqual(links, {0: "u1", 1: "u2"})
        self.assertEqual(len(pairs), 2)

    def test_inline_matching_uses_path_to_disambiguate_duplicate_content(self):
        content = "**Same title**\n\nSame explanation."
        first = finding(path="scripts/a.py", content=content)
        second = finding(path="scripts/b.py", start=20, end=20, content=content)
        posted = [
            {"body": content, "html_url": "u2", "path": "scripts/b.py", "line": 20},
            {"body": content, "html_url": "u1", "path": "scripts/a.py", "line": 12},
        ]
        links, _ = restyle.match_inline(ocr_result([first, second]), posted)
        self.assertEqual(links, {0: "u1", 1: "u2"})

    def test_inline_matching_prefers_the_matching_line(self):
        content = "**Same title**\n\nSame explanation."
        comment = finding(path="scripts/a.py", start=20, end=20, content=content)
        posted = [
            {"body": content, "html_url": "u12", "path": "scripts/a.py", "line": 12},
            {"body": content, "html_url": "u20", "path": "scripts/a.py", "line": 20},
        ]
        links, _ = restyle.match_inline(ocr_result([comment]), posted)
        self.assertEqual(links, {0: "u20"})

    def test_inline_matching_does_not_link_on_a_title_alone(self):
        comment = finding(content="**Same title**\n\nUnique explanation.")
        posted = [{"body": "**🟠 P1 · Same title**", "html_url": "u1"}]
        links, pairs = restyle.match_inline(ocr_result([comment]), posted)
        self.assertEqual(links, {})
        self.assertEqual(pairs, [])

    def test_empty_content_links_only_an_unambiguous_title(self):
        comment = {**finding(), "content": ""}
        body = "**🟠 P1 · Finding**"
        links, _ = restyle.match_inline(
            ocr_result([comment]), [{"body": body, "html_url": "u1"}]
        )
        self.assertEqual(links, {0: "u1"})
        links, _ = restyle.match_inline(
            ocr_result([comment]),
            [{"body": body, "html_url": "u1"}, {"body": body, "html_url": "u2"}],
        )
        self.assertEqual(links, {})

    def test_inline_matching_keeps_marker_like_content(self):
        content = "**Run tag**\n\nSee `<!-- ocr-summary-run:1-1 -->` for the tag."
        posted = [{"body": content, "html_url": "u1"}]
        links, _ = restyle.match_inline(ocr_result([finding(content=content)]), posted)
        self.assertEqual(links, {0: "u1"})

    def test_checkpoint_run_rejects_duplicate_markers(self):
        with self.assertRaises(restyle.RewriteError):
            restyle.checkpoint_run(f"{CLEAN_CHECKPOINT}\n{CLEAN_CHECKPOINT}")

    def test_checkpoint_run_rejects_a_malformed_payload(self):
        with self.assertRaises(restyle.RewriteError):
            restyle.checkpoint_run("<!-- ocr-checkpoint:v1 not-base64! -->")

    def test_missing_message_adds_no_sentence(self):
        result = ocr_result([finding()])
        del result["message"]
        body = summary_for(result, {0: "https://example.test/comment"})
        self.assertIn("## AI Review · 2/5 · Blocking issues", body)
        self.assertNotIn("Review complete.", body)

    def test_clean_run_gets_a_score_and_no_table(self):
        result = ocr_result([], message="No comments generated. Looks good to me.")
        body = summary_for(result)
        self.assertIn("## AI Review · 5/5 · Safe to merge", body)
        self.assertIn("No comments generated. Looks good to me.", body)
        self.assertNotIn("| | Finding | Where |", body)
        self.assertNotIn("<details><summary>Diagram</summary>", body)

    def test_real_posting_failure_keeps_the_short_reason(self):
        result = ocr_result(
            [finding(severity="critical", start=20, end=20)],
            message="Review complete: 1 finding(s) across 2 selected item(s).",
        )
        original = (
            f"{SUMMARY_MARKER}\n{RUN_MARKER}\n"
            "### 📄 `scripts/a.py` (L20-L20)\n\n"
            "⚠️ GitHub could not post this as an inline comment: HTTP 422 outside the diff\n\n"
            "**A finding title**\n\nThe explanation names the failure and the line."
        )
        body = summary_for(result, body=original)
        self.assertIn("⚠️ Not posted inline: HTTP 422 outside the diff", body)
        self.assertNotIn("GitHub could not post this as an inline comment", body)

    def test_mermaid_inside_a_real_finding_stays_inline(self):
        comment = finding(
            content="**Real finding**\n\nThe flow is below.\n\n```mermaid\nflowchart LR\n  A --> B\n```",
        )
        result = ocr_result([comment])
        body = summary_for(result, {0: "https://example.test/comment"})
        inline = restyle.rewrite_inline(INLINE_BODY, comment)
        self.assertNotIn("<details><summary>Diagram</summary>", body)
        self.assertIn("```mermaid", inline)

    def test_carrier_with_no_mermaid_renders_no_diagram(self):
        comment = carrier()
        comment["content"] = "**Diagram**\n\nNo flow changed."
        result = ocr_result([comment], message="Review complete: 1 finding(s) across 1 selected item(s).")
        body = summary_for(result)
        self.assertIn("## AI Review · 5/5 · Safe to merge", body)
        self.assertIn("Review complete: 0 finding(s) across 1 selected item(s).", body)
        self.assertNotIn("<details><summary>Diagram</summary>", body)
        self.assertNotIn("REVIEW.md", body)

    def test_routing_sends_policy_findings_to_the_details(self):
        cases = (
            ("medium", "bug", True),
            ("low", "bug", True),
            ("high", "documentation", True),
            ("high", "style", True),
            ("high", "bug", False),
            ("critical", "security", False),
            ("high", "", False),
        )
        for severity, category, routed in cases:
            with self.subTest(severity=severity, category=category):
                links = {} if routed else {0: "https://example.test/comment"}
                body = summary_for(
                    ocr_result([finding(severity=severity, category=category)]), links
                )
                if routed:
                    self.assertIn("<details><summary>1 lower-priority findings", body)
                else:
                    self.assertIn("https://example.test/comment", body)
                    self.assertNotIn("lower-priority findings", body)
                self.assertNotIn("⚠️ Not posted inline", body)

    def test_incremental_skip_is_not_a_posting_failure(self):
        body = summary_for(ocr_result([finding()]))
        self.assertNotIn("A finding title", body)
        self.assertNotIn("⚠️ Not posted inline", body)
        self.assertIn(
            "1 finding(s) were already covered by another review thread.", body
        )

    def test_no_line_finding_keeps_its_reason(self):
        original = (
            f"{SUMMARY_MARKER}\n{RUN_MARKER}\n"
            "### 📄 `scripts/a.py`\n\n"
            "⚠️ GitHub could not post this as an inline comment: "
            "No line information provided\n\n"
            "**A finding title**\n\nThe explanation names the failure and the line."
        )
        body = summary_for(ocr_result([finding(start=0, end=0)]), body=original)
        self.assertIn("⚠️ Not posted inline: No line information provided", body)
        self.assertIn("1 finding(s) not posted inline", body)
        self.assertIn("The explanation names the failure and the line.", body)

    def test_warnings_keep_their_content(self):
        body = summary_for(
            ocr_result(
                [finding()],
                warnings=[
                    {"file": "a.py", "type": "timeout", "message": "the model timed out"}
                ],
            ),
            {0: "https://example.test/comment"},
        )
        self.assertIn("<details><summary>1 warnings</summary>", body)
        self.assertIn("`a.py` (`timeout`) the model timed out", body)

    def test_suggestion_requires_existing_code(self):
        comment = finding()
        comment["existing_code"] = ""
        body = restyle.rewrite_inline(INLINE_BODY, comment)
        self.assertNotIn("<details><summary>Suggested change</summary>", body)

    def test_routed_marker_round_trips(self):
        carried = {
            "path": "scripts/a.py",
            "start_line": 10,
            "end_line": 12,
            "severity": "medium",
            "content": "**A finding title**",
            "head": "abc1234",
            "origin": {"from": "def5678", "to": "abc1234"},
        }
        marker = restyle.encode_routed([carried])
        decoded = restyle.decode_routed(f"<!-- ocr-summary -->\n{marker}")
        self.assertEqual(decoded.findings, [carried])

    def test_routed_marker_validation_rejects_bad_payloads(self):
        def encoded(payload):
            raw = json.dumps(payload).encode("utf-8")
            return f"<!-- ocr-routed:v1 {base64.b64encode(raw).decode('ascii')} -->"

        bad_payloads = [
            [],
            {"findings": "nope"},
            {"findings": [{"start_line": 3, "severity": "low", "head": "a"}]},
            {"findings": [{"path": "a.py", "start_line": 3, "severity": "urgent", "head": "a"}]},
            {"findings": [{"path": "a.py", "start_line": 3, "severity": "low"}]},
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(restyle.RewriteError):
                    restyle.decode_routed(encoded(payload))
        with self.assertRaises(restyle.RewriteError):
            restyle.decode_routed("<!-- ocr-routed:v1 not-base64! -->")


def comment_strategy():
    return st.fixed_dictionaries(
        {
            "path": st.sampled_from(["a.py", "b/c.py", "REVIEW.md"]),
            "content": st.text(min_size=1, max_size=160).filter(lambda value: value.strip()),
            "severity": st.sampled_from(["critical", "high", "medium", "low"]),
            "category": st.sampled_from(
                ["bug", "security", "style", "documentation", "", None]
            ),
            "start_line": st.integers(min_value=0, max_value=200),
            "end_line": st.integers(min_value=0, max_value=200),
        }
    )


MARKERS = st.sampled_from(
    [
        "<!-- ocr-summary -->",
        "<!-- ocr-summary-run:1-1 -->",
        "<!-- ocr-checkpoint:v1 eyJ2IjoxfQ== -->",
        "<!-- ocr-review-run:1-1 -->",
        "<!-- ocr-1-1-abcdef0123456789 -->",
    ]
)


class PropertyTests(unittest.TestCase):
    @settings(
        max_examples=80,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(
        original=st.lists(st.one_of(st.text(max_size=40), MARKERS), max_size=8).map(
            "\n".join
        ),
        comments=st.lists(comment_strategy(), max_size=6),
        severity_below=st.sampled_from(["", "medium", "low"]),
        categories=st.sampled_from(["", "style,documentation", "bug"]),
    )
    def test_summary_preserves_markers_and_is_idempotent(
        self, original, comments, severity_below, categories
    ):
        body = "<!-- ocr-summary -->\n" + original
        result = {
            "status": "complete",
            "message": "Review complete: 2 finding(s) across 3 selected item(s).",
            "comments": comments,
            "warnings": [],
            "manifest": {"input": {}, "execution": {}},
        }
        policy = restyle.build_policy(severity_below, categories)
        links = {
            index: f"https://example.test/#discussion_r{index}"
            for index in range(len(comments))
        }
        meta = {
            "head": "a" * 40,
            "from": "b" * 40,
            "to": "a" * 40,
            "version": "v1.0.0",
            "model": "m",
            "effort": "high",
        }
        first = restyle.rewrite_summary(body, result, links, meta, policy)
        for marker in restyle.markers(body):
            self.assertIn(marker, first)
        self.assertEqual(restyle.rewrite_summary(first, result, links, meta, policy), first)

    @settings(
        max_examples=80,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    @given(
        original=st.lists(st.one_of(st.text(max_size=40), MARKERS), max_size=8).map(
            "\n".join
        ),
        comment=comment_strategy(),
    )
    def test_inline_preserves_markers_and_is_idempotent(self, original, comment):
        body = "<!-- ocr-1-1-abcdef0123456789 -->\n" + original
        first = restyle.rewrite_inline(body, comment)
        for marker in restyle.markers(body):
            self.assertIn(marker, first)
        self.assertEqual(restyle.rewrite_inline(first, comment), first)
