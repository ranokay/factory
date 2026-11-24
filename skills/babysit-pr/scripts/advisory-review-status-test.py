import importlib.machinery
import importlib.util
import json
import os
import subprocess
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
    "body": "<!-- coderabbit-ai-generated:v1 -->\n<!-- This is an auto-generated comment: skip review by coderabbit.ai -->\n> ## Review skipped\n> Auto reviews are disabled on this repository.",
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
}


def state(**extra):
    value = {"head": HEAD, "head_date": HEAD_DATE}
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
            return subprocess.run(
                ["python3", str(SCRIPT), "o/r", "1", *arguments],
                env=env,
                capture_output=True,
                text=True,
            )

    def test_greptile_pending_blocks(self):
        result = self.invoke(state(comments=[GREPTILE_REQUEST]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("greptile: pending", result.stdout)
        self.assertIn("pending: greptile", result.stdout)

    def test_greptile_summary_passes(self):
        result = self.invoke(state(comments=[GREPTILE_REQUEST, GREPTILE_SUMMARY]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("greptile: terminal (summary posted)", result.stdout)
        self.assertIn("all advisory reviewers terminal", result.stdout)

    def test_greptile_review_on_an_older_head_stays_pending(self):
        review = {
            "user": {"login": "greptile-apps[bot]"},
            "commit_id": "b" * 40,
            "submitted_at": "2026-11-24T03:31:41Z",
            "body": "",
        }
        result = self.invoke(state(head_date="2026-11-24T04:00:00Z", comments=[GREPTILE_REQUEST], reviews=[review]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("greptile: pending", result.stdout)

    def test_coderabbit_skip_is_terminal_even_when_created_earlier(self):
        result = self.invoke(state(comments=[CODERABBIT_REQUEST, CODERABBIT_SKIP]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coderabbit: terminal (review skipped)", result.stdout)

    def test_coderabbit_rate_limit_is_terminal(self):
        result = self.invoke(state(comments=[CODERABBIT_REQUEST, CODERABBIT_RATE_LIMIT]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coderabbit: terminal (rate limit)", result.stdout)

    def test_stale_rate_limit_does_not_make_the_current_head_terminal(self):
        result = self.invoke(
            state(head_date="2026-11-24T04:00:00Z", comments=[CODERABBIT_REQUEST, CODERABBIT_RATE_LIMIT])
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("coderabbit: pending", result.stdout)

    def test_in_progress_placeholder_stays_pending_then_passes(self):
        result = self.invoke(state(comments=[CODERABBIT_REQUEST, CODERABBIT_PLACEHOLDER]))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("coderabbit: pending", result.stdout)
        completed = dict(CODERABBIT_PLACEHOLDER, body="## Summary by CodeRabbit\n\nNo issues found.")
        result = self.invoke(state(comments=[CODERABBIT_REQUEST, completed]))
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


    def test_wait_retries_a_transient_read_error(self):
        module = load_module()
        now = [0.0]
        sleeps = []
        calls = [0]

        def check():
            calls[0] += 1
            if calls[0] == 1:
                raise ValueError("temporary gh failure")
            return [{"name": "greptile", "terminal": True, "reason": "summary posted"}]

        states, timed_out = module.wait_for_terminal(
            check, 60, 30, lambda: now[0], lambda seconds: (sleeps.append(seconds), now.__setitem__(0, now[0] + seconds))
        )
        self.assertFalse(timed_out)
        self.assertTrue(states[0]["terminal"])
        self.assertEqual(sleeps, [30])

    def test_usage_error_is_exit_2(self):
        result = subprocess.run(["python3", str(SCRIPT), "o/r"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == "__main__":
    unittest.main()
