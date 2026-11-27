import importlib.machinery
import importlib.util
import json
import os
import subprocess  # nosec B404 -- the tests run the script and a fake gh with argument lists
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("advisory-review-status")

# Fixture strings from ranokay/factory: CodeRabbit's "Review skipped" comment and
# Copilot's "insufficient remaining quota" review are from
# https://github.com/ranokay/factory/pull/133; the Greptile summary marker is from
# https://github.com/ranokay/factory/pull/133 and /pull/150. "Review rate limited"
# is CodeRabbit's documented status wording; the repository has no rate-limit comment.
FAKE = '''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
s = json.load(open(os.environ["FAKE_STATE"]))
if a[0] != "api":
    print("unexpected gh call: %r" % (a,), file=sys.stderr)
    sys.exit(1)
endpoint = a[-1]
if endpoint.endswith("/pulls/1"):
    result = {"head": {"sha": s.get("head", "abc")}}
elif "/commits/" in endpoint:
    result = {"commit": {"committer": {"date": s["head_date"]}}}
elif "/reviews?" in endpoint:
    result = [s.get("reviews", [])]
elif "/pulls/" in endpoint and "/comments?" in endpoint:
    result = [s.get("inline_comments", [])]
elif "/comments?" in endpoint:
    result = [s.get("comments", [])]
elif "/timeline?" in endpoint:
    result = [s.get("timeline", [])]
else:
    print("unknown endpoint: %s" % endpoint, file=sys.stderr)
    sys.exit(1)
if "--slurp" in a and isinstance(result, dict):
    result = [result]
print(json.dumps(result))
'''

HEAD = "6646394ff039a594da0d5e50bd09273967e5d94f"
HEAD_DATE = "2026-11-24T03:22:45Z"
GREPTILE_REQUEST = {
    "user": {"login": "ranokay"},
    "created_at": "2026-11-24T03:27:54Z",
    "body": "@greptileai\n\n<!-- factory:advisory-review:greptile -->",
}
CODERABBIT_REQUEST = {
    "user": {"login": "ranokay"},
    "created_at": "2026-11-24T03:27:53Z",
    "body": "@coderabbitai review\n\n<!-- factory:advisory-review:coderabbit -->",
}
GREPTILE_SUMMARY = {
    "user": {"login": "greptile-apps[bot]"},
    "created_at": "2026-11-24T03:31:37Z",
    "updated_at": "2026-11-24T03:31:45Z",
    "body": "<!-- greptile_summary -->\n\n<h2>Greptile Overview</h2>",
}
CODERABBIT_SKIP = {
    "user": {"login": "coderabbitai[bot]"},
    "created_at": "2026-11-24T02:20:21Z",
    "updated_at": "2026-11-24T03:22:55Z",
    "body": "<!-- coderabbit-ai-generated:v1 -->\n## Review skipped\nAuto reviews are disabled on this repository. To trigger a single review, invoke the `@coderabbitai review` command.",
}
CODERABBIT_RATE_LIMIT = {
    "user": {"login": "coderabbitai[bot]"},
    "created_at": "2026-11-24T03:23:00Z",
    "updated_at": "2026-11-24T03:23:05Z",
    "body": "<!-- coderabbit-ai-generated:v1 -->\n## Review rate limited\nYou have reached your review limit.",
}
CODERABBIT_PLACEHOLDER = {
    "user": {"login": "coderabbitai[bot]"},
    "created_at": "2026-11-24T03:23:00Z",
    "updated_at": "2026-11-24T03:24:00Z",
    "body": "<!-- coderabbit-ai-generated:v1 -->\n> Currently processing new changes in this PR. This may take a few minutes, please wait...",
}
COPILOT_QUOTA = {
    "user": {"login": "copilot-pull-request-reviewer[bot]"},
    "commit_id": HEAD,
    "submitted_at": "2026-11-24T03:28:08Z",
    "body": "Copilot was unable to review this pull request because the user who requested the review has insufficient remaining quota.",
}
COPILOT_REQUEST = {
    "event": "review_requested",
    "requested_reviewer": {"login": "Copilot"},
    "created_at": "2026-11-24T03:27:52Z",
}
CODERABBIT_SUMMARY_BODY = (
    "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\n"
    "## Summary by CodeRabbit\n\nNo issues found."
)
PR214_HEAD = "110ff399edd572bd0849d2b52cc4217bf0118d27"
PR214_GREPTILE_REQUEST = {
    "user": {"login": "ranokay"},
    "created_at": "2026-11-27T07:13:20Z",
    "body": "@greptileai\n\n<!-- factory:advisory-review:greptile -->",
}
PR214_CODERABBIT_REQUEST = {
    "user": {"login": "ranokay"},
    "created_at": "2026-11-27T07:13:19Z",
    "body": "@coderabbitai review\n\n<!-- factory:advisory-review:coderabbit -->",
}
PR214_EARLIER_COMMENTS = [
    {
        "user": {"login": "greptile-apps[bot]"},
        "created_at": "2026-11-27T02:28:16Z",
        "updated_at": "2026-11-27T02:28:16Z",
        "body": "<!-- greptile_summary -->\n\n<h2>Greptile Overview</h2>",
    },
    {
        "user": {"login": "coderabbitai[bot]"},
        "created_at": "2026-11-27T04:50:52Z",
        "updated_at": "2026-11-27T07:14:00Z",
        "body": CODERABBIT_SUMMARY_BODY,
    },
    {
        "user": {"login": "coderabbitai[bot]"},
        "created_at": "2026-11-27T07:13:25Z",
        "updated_at": "2026-11-27T07:13:25Z",
        "body": "<!-- This is an auto-generated reply by CodeRabbit -->\nCurrently processing new changes in this PR.",
    },
]
PR214_EARLIER_REVIEWS = [
    {
        "user": {"login": "greptile-apps[bot]"},
        "commit_id": "6a892430fa339b94ed92eb5a5e06bb4627ca2899",
        "submitted_at": "2026-11-27T07:10:00Z",
        "body": "",
    },
    {
        "user": {"login": "coderabbitai[bot]"},
        "commit_id": "5e2e9cbaa3a023f30e6232e1a4afcdf3f40ead19",
        "submitted_at": "2026-11-27T07:12:00Z",
        "body": "",
    },
]
PR214_FINAL_INLINE_COMMENTS = [
    {
        "user": {"login": "greptile-apps[bot]"},
        "created_at": "2026-11-27T07:15:37Z",
        "updated_at": "2026-11-27T07:15:37Z",
        "body": "Greptile review finding",
    },
    {
        "user": {"login": "greptile-apps[bot]"},
        "created_at": "2026-11-27T07:17:35Z",
        "updated_at": "2026-11-27T07:17:36Z",
        "body": "Another Greptile review finding",
    },
    {
        "user": {"login": "coderabbitai[bot]"},
        "created_at": "2026-11-27T07:24:27Z",
        "updated_at": "2026-11-27T07:24:27Z",
        "body": "CodeRabbit review finding",
    },
]
PR214_FINISHED_REPLY = {
    "user": {"login": "coderabbitai[bot]"},
    "created_at": "2026-11-27T07:13:25Z",
    "updated_at": "2026-11-27T07:24:40Z",
    "body": "<!-- This is an auto-generated reply by CodeRabbit -->\nReview finished.",
}


def state(**extra):
    value = {"head": HEAD, "head_date": HEAD_DATE}
    value.update(extra)
    return value


def pr214_state(**extra):
    value = state(
        head=PR214_HEAD,
        head_date="2026-11-27T07:13:00Z",
        comments=[PR214_CODERABBIT_REQUEST, PR214_GREPTILE_REQUEST, *PR214_EARLIER_COMMENTS],
        reviews=PR214_EARLIER_REVIEWS,
    )
    value.update(extra)
    return value


def load_module():
    loader = importlib.machinery.SourceFileLoader("advisory_status", str(SCRIPT))
    spec = importlib.util.spec_from_loader("advisory_status", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class AdvisoryReviewStatus(unittest.TestCase):
    def invoke(self, value, *arguments):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "gh").write_text(FAKE)
            (root / "gh").chmod(0o755)
            (root / "state").write_text(json.dumps(value))
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", FAKE_STATE=str(root / "state"))
            return subprocess.run(  # nosec B603 B607 -- fixed script path and an argument list  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- the test runs this checkout's script
                ["python3", str(SCRIPT), "o/r", "1", *arguments],
                env=env,
                capture_output=True,
                text=True,
            )

    def test_greptile_request_without_a_summary_is_pending(self):
        result = self.invoke(state(comments=[GREPTILE_REQUEST]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("greptile: pending", result.stdout)
        self.assertIn("pending: greptile", result.stdout)

    def test_greptile_summary_passes(self):
        result = self.invoke(state(comments=[GREPTILE_REQUEST, GREPTILE_SUMMARY]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("greptile: terminal (summary posted)", result.stdout)
        self.assertIn("all advisory reviewers terminal", result.stdout)

    def test_greptile_pre_request_summary_edit_stays_pending(self):
        edited = dict(GREPTILE_SUMMARY, created_at="2026-11-24T03:20:00Z", updated_at="2026-11-24T03:31:45Z")
        result = self.invoke(state(comments=[GREPTILE_REQUEST, edited]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("greptile: pending", result.stdout)

    def test_greptile_inline_comment_after_the_request_passes(self):
        inline = {
            "user": {"login": "greptile-apps[bot]"},
            "created_at": "2026-11-24T03:31:41Z",
            "body": "Review finding",
        }
        result = self.invoke(state(comments=[GREPTILE_REQUEST], inline_comments=[inline]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("greptile: terminal (inline comment)", result.stdout)

    def test_another_reviewers_activity_does_not_answer_greptile(self):
        inline = {
            "user": {"login": "coderabbitai[bot]"},
            "created_at": "2026-11-24T03:31:41Z",
            "body": "CodeRabbit review finding",
        }
        result = self.invoke(state(comments=[GREPTILE_REQUEST], inline_comments=[inline]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("greptile: pending", result.stdout)

    def test_a_review_after_the_request_survives_a_later_push(self):
        review = {
            "user": {"login": "greptile-apps[bot]"},
            "commit_id": "b" * 40,
            "submitted_at": "2026-11-24T03:31:41Z",
            "body": "",
        }
        result = self.invoke(state(head_date="2026-11-24T04:00:00Z", comments=[GREPTILE_REQUEST], reviews=[review]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("greptile: terminal (review posted)", result.stdout)

    def test_a_review_on_the_head_before_the_request_stays_pending(self):
        review = {
            "user": {"login": "greptile-apps[bot]"},
            "commit_id": HEAD,
            "submitted_at": "2026-11-24T03:27:53Z",
            "body": "",
        }
        result = self.invoke(state(comments=[GREPTILE_REQUEST], reviews=[review]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("greptile: pending", result.stdout)

    def test_the_latest_request_is_the_anchor(self):
        later = dict(GREPTILE_REQUEST, created_at="2026-11-24T03:40:00Z")
        result = self.invoke(state(comments=[GREPTILE_REQUEST, later, GREPTILE_SUMMARY]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("greptile: pending", result.stdout)

    def test_an_auto_reviewer_is_anchored_to_the_head(self):
        result = self.invoke(state(comments=[CODERABBIT_SKIP]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coderabbit: terminal (review skipped)", result.stdout)

    def test_an_auto_reviewer_response_before_the_head_stays_pending(self):
        result = self.invoke(state(head_date="2026-11-24T04:00:00Z", comments=[CODERABBIT_SKIP]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("coderabbit: pending", result.stdout)

    def test_a_request_without_a_timestamp_is_an_error(self):
        marker = {"user": {"login": "ranokay"}, "body": "<!-- factory:advisory-review:greptile -->"}
        result = self.invoke(state(comments=[marker]))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("request has no created_at", result.stderr)

    def test_coderabbit_rate_limit_is_terminal(self):
        result = self.invoke(state(comments=[CODERABBIT_RATE_LIMIT]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coderabbit: terminal (rate limit)", result.stdout)

    def test_rate_limit_before_the_request_stays_pending(self):
        result = self.invoke(state(comments=[CODERABBIT_REQUEST, CODERABBIT_RATE_LIMIT]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("coderabbit: pending", result.stdout)

    def test_a_skip_before_the_request_stays_pending(self):
        result = self.invoke(state(comments=[CODERABBIT_REQUEST, CODERABBIT_SKIP]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("coderabbit: pending", result.stdout)

    def test_a_skip_after_the_request_stays_pending(self):
        skip = dict(CODERABBIT_SKIP, created_at="2026-11-24T03:31:00Z", updated_at="2026-11-24T03:31:00Z")
        result = self.invoke(state(comments=[CODERABBIT_REQUEST, skip]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("coderabbit: pending", result.stdout)

    def test_in_progress_placeholder_stays_pending_then_passes(self):
        placeholder = dict(CODERABBIT_PLACEHOLDER, updated_at="2026-11-24T03:28:00Z")
        result = self.invoke(state(comments=[CODERABBIT_REQUEST, placeholder]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("coderabbit: pending", result.stdout)
        completed = dict(placeholder, created_at="2026-11-24T03:31:00Z", updated_at="2026-11-24T03:31:00Z", body=CODERABBIT_SUMMARY_BODY)
        result = self.invoke(state(comments=[CODERABBIT_REQUEST, completed]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coderabbit: terminal (summary posted)", result.stdout)

    def test_an_unrelated_new_code_rabbit_reply_stays_pending(self):
        reply = dict(CODERABBIT_PLACEHOLDER, created_at="2026-11-24T03:31:00Z", updated_at="2026-11-24T03:31:00Z", body="Thanks for the mention.")
        result = self.invoke(state(comments=[CODERABBIT_REQUEST, reply]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("coderabbit: pending", result.stdout)

    def test_coderabbit_finished_reply_counts_only_after_it_is_edited(self):
        reply = {
            "user": {"login": "coderabbitai[bot]"},
            "created_at": "2026-11-24T03:27:55Z",
            "updated_at": "2026-11-24T03:27:55Z",
            "body": "<!-- This is an auto-generated reply by CodeRabbit -->\nReview finished.",
        }
        result = self.invoke(state(comments=[CODERABBIT_REQUEST, reply]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("coderabbit: pending", result.stdout)
        reply["updated_at"] = "2026-11-24T03:31:00Z"
        result = self.invoke(state(comments=[CODERABBIT_REQUEST, reply]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coderabbit: terminal (comment edit)", result.stdout)

    def test_a_same_second_reply_is_terminal(self):
        same_second = dict(CODERABBIT_RATE_LIMIT, updated_at=CODERABBIT_REQUEST["created_at"])
        result = self.invoke(state(comments=[CODERABBIT_REQUEST, same_second]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coderabbit: terminal (rate limit)", result.stdout)

    def test_a_walkthrough_quoting_a_placeholder_is_terminal(self):
        body = CODERABBIT_SUMMARY_BODY + "\n\n" + ("walkthrough line\n" * 100)
        body += "> Currently processing new changes in this PR."
        result = self.invoke(
            state(
                comments=[
                    CODERABBIT_REQUEST,
                    dict(
                        CODERABBIT_PLACEHOLDER,
                        created_at="2026-11-24T03:31:00Z",
                        updated_at="2026-11-24T03:31:00Z",
                        body=body,
                    ),
                ]
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coderabbit: terminal (summary posted)", result.stdout)

    def test_copilot_request_without_a_review_is_pending(self):
        result = self.invoke(state(timeline=[COPILOT_REQUEST]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("copilot: pending", result.stdout)

    def test_copilot_quota_is_terminal(self):
        result = self.invoke(state(timeline=[COPILOT_REQUEST], reviews=[COPILOT_QUOTA]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("copilot: terminal (quota)", result.stdout)

    def test_unrequested_reviewer_is_terminal(self):
        result = self.invoke(state())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("terminal (not requested)"), 3, result.stdout)

    def test_pr_214_earlier_activity_does_not_finish_requested_reviewers(self):
        result = self.invoke(pr214_state())
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("coderabbit: pending", result.stdout)
        self.assertIn("greptile: pending", result.stdout)
        self.assertIn("pending: coderabbit, greptile", result.stdout)

    def test_pr_214_inline_comments_and_summary_edit_finish_requested_reviewers(self):
        value = pr214_state(
            comments=[PR214_CODERABBIT_REQUEST, PR214_GREPTILE_REQUEST, *PR214_EARLIER_COMMENTS[:2], PR214_FINISHED_REPLY],
            inline_comments=PR214_FINAL_INLINE_COMMENTS,
        )
        result = self.invoke(value)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("greptile: terminal (inline comment)", result.stdout)
        self.assertIn("coderabbit: terminal (comment edit)", result.stdout)
        result = self.invoke(value, "--wait", "--timeout-minutes", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("greptile: terminal (inline comment)", result.stdout)
        self.assertIn("coderabbit: terminal (comment edit)", result.stdout)

    def test_wait_times_out_with_named_reviewers(self):
        result = self.invoke(state(comments=[GREPTILE_REQUEST]), "--wait", "--timeout-minutes", "0")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("greptile: pending", result.stdout)
        self.assertIn("timed out after 0m: greptile", result.stdout)

    def test_wait_sleeps_until_terminal(self):
        module = load_module()
        now = [0.0]
        sleeps = []
        replies = iter(
            [
                [{"name": "greptile", "terminal": False, "reason": ""}],
                [{"name": "greptile", "terminal": False, "reason": ""}],
                [{"name": "greptile", "terminal": True, "reason": "summary posted"}],
            ]
        )
        states, timed_out = module.wait_for_terminal(
            lambda: next(replies), 60, 30, lambda: now[0], lambda seconds: (sleeps.append(seconds), now.__setitem__(0, now[0] + seconds))
        )
        self.assertFalse(timed_out)
        self.assertTrue(states[0]["terminal"])
        self.assertEqual(sleeps, [30, 30])

    def test_wait_stops_at_the_deadline(self):
        module = load_module()
        now = [0.0]
        sleeps = []
        states, timed_out = module.wait_for_terminal(
            lambda: [{"name": "greptile", "terminal": False, "reason": ""}],
            30,
            12,
            lambda: now[0],
            lambda seconds: (sleeps.append(seconds), now.__setitem__(0, now[0] + seconds)),
        )
        self.assertTrue(timed_out)
        self.assertEqual(sleeps, [12, 12, 6])

    def test_usage_error_is_exit_2(self):
        result = subprocess.run(  # nosec B603 B607 -- fixed script path and an argument list  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- the test runs this checkout's script
            ["python3", str(SCRIPT), "o/r"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        result = subprocess.run(  # nosec B603 B607 -- fixed script path and an argument list  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- the test runs this checkout's script
            ["python3", str(SCRIPT), "o", "1"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("usage", result.stderr)


if __name__ == "__main__":
    unittest.main()
