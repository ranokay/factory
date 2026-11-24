# /// script
# requires-python = ">=3.12"
# dependencies = ["hypothesis==6.170.17"]
# ///
"""Tests for the OCR comment restyler.

The property tests generate OCR-shaped summaries and inline bodies: every
hidden OCR marker in the input must survive the rewrite, and a second rewrite
must change nothing. The fixture tests cover each score tier, the layout, the
routing rules and the failure path, including a forced API failure that must
restore every body already written.
"""

import importlib.util
import json
import os
import subprocess  # nosec B404 -- the tests run the local script with argv
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from hypothesis import HealthCheck, given, settings, strategies as st

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github/actions/ocr-review/restyle.py"
SPEC = importlib.util.spec_from_file_location("ocr_restyle", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
restyle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restyle)

SUMMARY_MARKER = "<!-- ocr-summary -->"
RUN_MARKER = "<!-- ocr-summary-run:1-1 -->"
CHECKPOINT = "<!-- ocr-checkpoint:v1 eyJ2IjoxfQ== -->"
FINGERPRINT = "<!-- ocr-1-1-abcdef0123456789 -->"

SUMMARY_BODY = f"""\
{SUMMARY_MARKER}
{RUN_MARKER}
🔍 **OpenCodeReview** found **4** issue(s) in this PR.

---

### 📄 `scripts/verify-skill-maintenance.py` (L118-L118)

⚠️ GitHub could not post this as an inline comment: Routed to summary (severity medium · category bug)

**Older issues allow duplicate filings**

The marker search stops at the first page.

---

### 📄 `scripts/verify-skill.py` (L86-L86)

⚠️ GitHub could not post this as an inline comment: Routed to summary (severity medium · category style)

**Cleanup is opt-in for probe calls**

The probe leaves its temporary directory behind.

{CHECKPOINT}"""

INLINE_BODY = f"""\
{FINGERPRINT}
**A finding title**

The explanation names the failure and the line.

**Suggestion:**
```suggestion
subprocess.run(["git", "fetch"], check=True)
```"""


def finding(path="scripts/a.py", severity="high", category="bug", start=10, end=12, content=None):
    return {
        "path": path,
        "content": content
        or "**A finding title**\n\nThe explanation names the failure and the line.",
        "suggestion_code": "subprocess.run(['git', 'fetch'], check=True)",
        "existing_code": "pass",
        "start_line": start,
        "end_line": end,
        "category": category,
        "severity": severity,
    }


def carrier():
    return {
        "path": "REVIEW.md",
        "content": "**Diagram**\n\n```mermaid\nflowchart LR\n  A[action] --> B[script]\n```",
        "start_line": 1,
        "end_line": 1,
        "category": "documentation",
        "severity": "low",
    }


def ocr_result(comments, message=None, warnings=None):
    return {
        "status": "complete",
        "message": message or f"Review complete: {len(comments)} finding(s) across 5 selected item(s).",
        "comments": comments,
        "warnings": warnings or [],
        "manifest": {
            "input": {
                "resolved_base": "def56789abcdef0123456789abcdef0123456789",
                "resolved_head": "abc1234def567890123456789abcdef0123456789",
            },
            "execution": {"ocr_version": "v1.14.0", "model": "deepseek-v4.1-flash"},
        },
    }


def summary_for(result, links=None, policy=None, body=SUMMARY_BODY):
    return restyle.rewrite_summary(
        body,
        result,
        links or {},
        {"head": "abc1234def567890123456789abcdef0123456789", "from": "def56789abcdef0123456789abcdef0123456789", "to": "abc1234def567890123456789abcdef0123456789", "version": "v1.14.0", "model": "deepseek-v4.1-flash", "effort": "high"},
        policy or restyle.build_policy("medium", "style,documentation"),
    )


class FakeGitHub:
    def __init__(self):
        self.issue_comments = []
        self.review_comments = []
        self.patches = []
        self.fail_path = ""
        self.fail_once = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/repos/o/r/issues/7/comments":
                    outer.reply(self, outer.issue_comments)
                elif path == "/repos/o/r/pulls/7/comments":
                    outer.reply(self, outer.review_comments)
                else:
                    outer.reply(self, {"message": "not found"}, 404)

            def do_PATCH(self):
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if outer.fail_once and path == outer.fail_path:
                    outer.fail_once = False
                    outer.reply(self, {"message": "forced failure"}, 500)
                    return
                outer.patches.append((path, payload["body"]))
                if path.startswith("/repos/o/r/issues/comments/"):
                    ident = int(path.rsplit("/", 1)[1])
                    for comment in outer.issue_comments:
                        if comment["id"] == ident:
                            comment["body"] = payload["body"]
                elif path.startswith("/repos/o/r/pulls/comments/"):
                    ident = int(path.rsplit("/", 1)[1])
                    for comment in outer.review_comments:
                        if comment["id"] == ident:
                            comment["body"] = payload["body"]
                else:
                    outer.reply(self, {"message": "not found"}, 404)
                    return
                outer.reply(self, {"id": ident})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @staticmethod
    def reply(handler, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    @property
    def url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def seed(self):
        self.issue_comments.append(
            {"id": 1, "body": SUMMARY_BODY, "html_url": "https://example.test/issues/1"}
        )
        self.review_comments.append(
            {
                "id": 2,
                "body": INLINE_BODY,
                "html_url": "https://example.test/pull/7#discussion_r2",
                "path": "scripts/a.py",
                "line": 12,
                "start_line": 10,
            }
        )


class RestyleTests(unittest.TestCase):
    def test_score_tiers(self):
        def severities(*values):
            return [
                {
                    "path": f"f{index}.py",
                    "content": "**Title**\n\nExplanation.",
                    "start_line": 1,
                    "end_line": 1,
                    "category": "bug",
                    "severity": value,
                }
                for index, value in enumerate(values)
            ]

        cases = (
            ((), 5),
            (("low",), 5),
            (("low", "low"), 5),
            (("medium",), 4),
            (("medium", "medium"), 3),
            (("medium", "medium", "medium"), 3),
            (("high",), 2),
            (("high", "medium", "medium"), 2),
            (("critical",), 1),
            (("critical", "high", "medium"), 1),
        )
        for values, expected in cases:
            with self.subTest(severities=values):
                self.assertEqual(restyle.score(severities(*values)), expected)

    def test_carrier_is_excluded_from_the_score(self):
        self.assertEqual(restyle.score([carrier(), finding(severity="medium")]), 4)

    def test_verdicts(self):
        self.assertEqual(restyle.verdict(5), "Safe to merge")
        self.assertEqual(restyle.verdict(4), "Merge after fixes")
        self.assertEqual(restyle.verdict(3), "Merge after fixes")
        self.assertEqual(restyle.verdict(2), "Blocking issues")
        self.assertEqual(restyle.verdict(1), "Blocking issues")

    def test_unknown_severity_is_p3_and_does_not_lower_the_score(self):
        comment = finding(severity="", category="")
        self.assertEqual(restyle.badge_of(comment), "⚪ P3")
        self.assertEqual(restyle.score([comment]), 5)

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

    def test_inline_layout(self):
        comment = finding()
        body = restyle.rewrite_inline(INLINE_BODY, comment)
        self.assertTrue(body.startswith(FINGERPRINT))
        self.assertIn("**🟠 P1 · A finding title**", body)
        self.assertIn("The explanation names the failure and the line.", body)
        self.assertIn("<details><summary>Suggested change</summary>", body)
        self.assertIn("```suggestion", body)
        self.assertNotIn("### 📄", body)
        self.assertNotIn("**Suggestion:**", body)
        self.assertNotIn("Fix with agent prompt", body)

    def test_routing_matches_ocr(self):
        policy = restyle.build_policy("medium", "style,documentation")
        self.assertTrue(restyle.is_routed(finding(severity="medium", category="bug"), policy))
        self.assertTrue(restyle.is_routed(finding(severity="low", category="bug"), policy))
        self.assertTrue(restyle.is_routed(finding(severity="high", category="documentation"), policy))
        self.assertTrue(restyle.is_routed(finding(severity="high", category="style"), policy))
        self.assertFalse(restyle.is_routed(finding(severity="high", category="bug"), policy))
        self.assertFalse(restyle.is_routed(finding(severity="critical", category="security"), policy))
        self.assertFalse(restyle.is_routed(finding(severity="", category=""), policy))


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.github = FakeGitHub()
        self.addCleanup(self.github.close)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.result_path = Path(directory.name) / "result.json"
        self.github.seed()

    def run_cli(self, *arguments, environment=None):
        env = {
            **os.environ,
            "GITHUB_API_URL": self.github.url,
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_ATTEMPT": "1",
            "PR_NUMBER": "7",
            "GITHUB_TOKEN": "test-token",
            "OCR_RESULT": str(self.result_path),
            "OCR_ROUTE_SEVERITY_BELOW": "medium",
            "OCR_ROUTE_CATEGORIES": "style,documentation",
            "OCR_EFFORT": "high",
            "OCR_MODEL": "deepseek-v4.1-flash",
            "OCR_VERSION": "1.14.0",
        }
        env.update(environment or {})
        return subprocess.run(  # nosec B603 -- local script, argv, no shell
            [sys.executable, str(SCRIPT), *arguments],
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
        result = self.run_cli()
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
        self.assertNotIn("### 📄", inline)

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

    def test_dry_run_prints_without_writing(self):
        self.write(ocr_result([finding()]))
        result = self.run_cli("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--- issue 1 ---", result.stdout)
        self.assertIn("## AI Review · 2/5 · Blocking issues", result.stdout)
        self.assertEqual(self.github.patches, [])


@st.composite
def ocr_comments(draw):
    return draw(
        st.lists(
            st.fixed_dictionaries(
                {
                    "path": st.sampled_from(["a.py", "b/c.py", "REVIEW.md"]),
                    "content": st.text(min_size=1, max_size=160).filter(lambda value: value.strip()),
                    "severity": st.sampled_from(["critical", "high", "medium", "low", "", None]),
                    "category": st.sampled_from(
                        ["bug", "security", "style", "documentation", "", None]
                    ),
                    "start_line": st.integers(min_value=0, max_value=200),
                    "end_line": st.integers(min_value=0, max_value=200),
                }
            ),
            max_size=6,
        )
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


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    original=st.lists(st.one_of(st.text(max_size=40), MARKERS), max_size=8).map("\n".join),
    comments=ocr_comments(),
    severity_below=st.sampled_from(["", "medium", "low"]),
    categories=st.sampled_from(["", "style,documentation", "bug"]),
)
def test_summary_preserves_markers_and_is_idempotent(
    original, comments, severity_below, categories
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
    meta = {"head": "a" * 40, "from": "b" * 40, "to": "a" * 40, "version": "v1.0.0", "model": "m", "effort": "high"}
    first = restyle.rewrite_summary(body, result, links, meta, policy)
    for marker in restyle.markers(body):
        assert marker in first
    assert restyle.rewrite_summary(first, result, links, meta, policy) == first


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    original=st.lists(st.one_of(st.text(max_size=40), MARKERS), max_size=8).map("\n".join),
    comment=st.fixed_dictionaries(
        {
            "path": st.sampled_from(["a.py", "b/c.py"]),
            "content": st.text(min_size=1, max_size=160).filter(lambda value: value.strip()),
            "severity": st.sampled_from(["critical", "high", "medium", "low", "", None]),
            "category": st.sampled_from(["bug", "style", "documentation", "", None]),
            "start_line": st.integers(min_value=0, max_value=200),
            "end_line": st.integers(min_value=0, max_value=200),
        }
    ),
)
def test_inline_preserves_markers_and_is_idempotent(original, comment):
    body = "<!-- ocr-1-1-abcdef0123456789 -->\n" + original
    first = restyle.rewrite_inline(body, comment)
    for marker in restyle.markers(body):
        assert marker in first
    assert restyle.rewrite_inline(first, comment) == first


if __name__ == "__main__":
    unittest.main()
