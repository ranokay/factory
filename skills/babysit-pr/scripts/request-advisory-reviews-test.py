import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("request-advisory-reviews")
FAKE = '''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
s = json.load(open(os.environ["FAKE_STATE"]))
if a[0] != "api":
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(json.dumps(a) + "\\n")
    sys.exit(0)
endpoint = a[-1]
if "/check-runs?" in endpoint:
    result = {"check_runs": [{"status": "completed", "conclusion": s.get("conclusion", "success"), "details_url": "https://github.com/o/r/actions/runs/42/job/1"}]}
elif "/actions/runs?" in endpoint:
    result = {"workflow_runs": [{"name": "AI Review PR " + str(s.get("run_pr", 1)), "display_title": "AI Review PR " + str(s.get("run_pr", 1)), "head_sha": "abc", "status": "completed" if s.get("complete", True) else "in_progress", "conclusion": s.get("conclusion", "success"), "id": 42}]}
    if s.get("older_completed"):
        result["workflow_runs"].append({"name": "AI Review PR 1", "display_title": "AI Review PR 1", "head_sha": "abc", "status": "completed", "conclusion": "success", "id": 41})
elif "/reviews?" in endpoint:
    result = [s.get("reviews", [])]
elif "/pulls/" in endpoint and "/comments?" in endpoint:
    result = [s.get("inline_comments", [])]
elif "/comments?" in endpoint:
    result = [s.get("comments", [])]
elif "/timeline?" in endpoint:
    result = [s.get("timeline", [])]
else:
    result = {"head": {"sha": "abc", "repo": {"full_name": s.get("head_repo", "o/r")}}, "user": {"login": s.get("author", "ranokay")}}
if "--slurp" in a and isinstance(result, dict): result = [result]
print(json.dumps(result))
'''


class ReviewRequests(unittest.TestCase):
    def invoke(self, state):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "gh").write_text(FAKE)
            (root / "gh").chmod(0o755)
            (root / "state").write_text(json.dumps(state))
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", FAKE_STATE=str(root / "state"), FAKE_LOG=str(root / "log"))
            result = subprocess.run(["python3", str(SCRIPT), "o/r", "1"], env=env, capture_output=True, text=True)
            writes = [json.loads(line) for line in (root / "log").read_text().splitlines()] if (root / "log").exists() else []
            return result, writes

    def test_requests_all_once_and_respects_existing_requests(self):
        result, writes = self.invoke({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(writes), 3)
        state = {"reviews": [{"user": {"login": "copilot-pull-request-reviewer[bot]"}, "state": "COMMENTED"}], "comments": [{"body": "@coderabbitai review\n<!-- factory:advisory-review:coderabbit -->"}, {"body": "@greptileai\n<!-- factory:advisory-review:greptile -->"}]}
        result, writes = self.invoke(state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(writes, [])

    def test_outside_contributor_requires_owner_approval(self):
        result, writes = self.invoke({"author": "other"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(writes, [])
        result, writes = self.invoke({"author": "other", "reviews": [{"user": {"login": "ranokay"}, "state": "APPROVED", "commit_id": "abc"}]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(writes), 3)
        result, writes = self.invoke({"author": "other", "reviews": [{"user": {"login": "ranokay"}, "state": "APPROVED", "commit_id": "old"}]})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(writes, [])

    def test_partial_requests_and_timeline_are_respected(self):
        state = {"timeline": [{"event": "review_requested", "requested_reviewer": {"login": "copilot-pull-request-reviewer[bot]"}}], "comments": [{"body": "<!-- factory:advisory-review:coderabbit -->"}]}
        result, writes = self.invoke(state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(writes), 1)
        self.assertIn("@greptileai", writes[0][-1])

    def test_review_must_complete_on_head_and_forks_need_approval(self):
        result, writes = self.invoke({"complete": False})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(writes, [])

    def test_skipped_review_allows_only_approved_outside_contributor(self):
        state = {"head_repo": "other/r", "conclusion": "skipped"}
        result, writes = self.invoke(state)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(writes, [])
        state["reviews"] = [{"user": {"login": "ranokay"}, "state": "APPROVED", "commit_id": "abc"}]
        result, writes = self.invoke(state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(writes), 3)
        self.assertIn("workflow=skipped", result.stdout)
        result, writes = self.invoke({"head_repo": "other/r"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(writes, [])

    def test_failed_review_conclusion_is_visible(self):
        result, writes = self.invoke({"conclusion": "failure"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("workflow=failure", result.stdout)
        self.assertEqual(len(writes), 3)

    def test_newer_running_or_other_pr_run_does_not_unblock(self):
        for state in ({"complete": False, "older_completed": True}, {"run_pr": 2}):
            with self.subTest(state=state):
                result, writes = self.invoke(state)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(writes, [])

    def test_bot_review_and_comment_prevent_new_requests(self):
        state = {"reviews": [{"user": {"login": "coderabbitai[bot]"}, "state": "COMMENTED"}], "comments": [{"user": {"login": "greptile-apps[bot]"}, "body": "reviewed"}]}
        result, writes = self.invoke(state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(writes), 1)
        self.assertIn("@copilot", writes[0])

    def test_human_mentions_do_not_count_as_factory_requests(self):
        state = {"comments": [{"user": {"login": "ranokay"}, "body": "@coderabbitai review"}, {"user": {"login": "ranokay"}, "body": "@greptile please review"}]}
        result, writes = self.invoke(state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(writes), 3)

    def test_bot_inline_comment_counts_as_activity(self):
        state = {"inline_comments": [{"user": {"login": "greptile-apps[bot]"}, "body": "review finding"}]}
        result, writes = self.invoke(state)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(writes), 2)
        self.assertFalse(any("@greptileai" in str(write) for write in writes))


if __name__ == "__main__":
    unittest.main()
