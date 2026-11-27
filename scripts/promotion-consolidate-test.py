#!/usr/bin/env python3

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess  # nosec B404 -- the tests drive the CLI under test
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
PASS = SCRIPTS / "promotion-consolidate.py"
PLAN = SCRIPTS / "promotion-consolidation.json"
FIXTURE = SCRIPTS / "fixtures/promotion-consolidate/open-promotions.json"
GH = SCRIPTS / "fixtures/promotion-consolidate/gh"
TRACKING = "<!-- factory:promotion-consolidate-tracking -->"
SUMMARY = "<!-- factory:promotion-consolidate-run date=2026-11-27 -->"
MUTATING = {("issue", "comment"), ("issue", "edit"), ("issue", "close"), ("issue", "create")}

spec = importlib.util.spec_from_file_location("promotion_consolidate", PASS)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture_state():
    issues = json.loads(FIXTURE.read_text())["issues"]
    return {"issues": {str(item["number"]): item for item in issues}, "mutations": 0, "next_comment": 1, "next_issue": 900}


def normalized(state):
    clean = copy.deepcopy(state)
    clean.pop("mutations", None)
    return json.dumps(clean, sort_keys=True, indent=1)


class Pass(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        shutil.copy(GH, gh)
        gh.chmod(0o755)
        self.log = self.root / "gh.log"
        self.state_path = self.root / "state.json"
        self.plan_path = self.root / "plan.json"
        self.plan_path.write_text(PLAN.read_text())
        self.write_state(fixture_state())

    def write_state(self, state):
        self.state_path.write_text(json.dumps(state, sort_keys=True))

    def read_state(self):
        return json.loads(self.state_path.read_text())

    def write_plan(self, plan):
        self.plan_path.write_text(json.dumps(plan))

    def run_pass(self, *extra, fail_after=None, plan=None):
        env = dict(
            os.environ,
            PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            FAKE_GH_STATE=str(self.state_path),
            FAKE_GH_LOG=str(self.log),
        )
        env.pop("FAKE_GH_FAIL_AFTER", None)
        if fail_after is not None:
            env["FAKE_GH_FAIL_AFTER"] = str(fail_after)
        command = [
            sys.executable, str(PASS), "--plan", str(plan or self.plan_path),
            "--repo", "ranokay/factory", "--date", "2026-11-27", *extra,
        ]
        return subprocess.run(  # nosec B603 -- the test runs the pass with an argument list
            command, capture_output=True, text=True, check=False, env=env, timeout=120
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def writes(self):
        found = []
        for args in self.calls():
            if tuple(args[:2]) in MUTATING or (args[0] == "api" and "PATCH" in args):
                found.append(args)
        return found

    def tracking_comments(self, state):
        for item in state["issues"].values():
            if TRACKING in item["body"]:
                return item, item["comments"]
        return None, []


class DryRun(Pass):
    def test_writes_nothing_and_prints_the_summary(self):
        before = self.state_path.read_text()
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.state_path.read_text(), before)
        self.assertIn("Open promotions: 51 before, 44 after this run.", result.stdout)
        self.assertIn("#79 (survivor) absorbs #80, #163, #206", result.stdout)
        self.assertIn("#51 covered by `.gitleaks.toml#context7-key-token`", result.stdout)
        self.assertIn("add needs-triage to #79", result.stdout)
        self.assertNotIn("ready-for-agent", result.stdout.split("Sent to pushback")[1].split("###")[0])


class LiveRun(Pass):
    def test_merges_closes_covers_and_hands_off(self):
        result = self.run_pass()
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.read_state()
        issues = state["issues"]
        survivor = issues["79"]["body"]
        self.assertIn("Cluster members: #79 (survivor), #80, #163, #206.", survivor)
        for source in (80, 163, 206):
            self.assertIn(f"<!-- factory:promotion-occurrence source={source} -->", survivor)
            self.assertIn(f"From #{source}:", survivor)
            self.assertEqual(issues[str(source)]["state"], "CLOSED")
            self.assertIn(f"<!-- factory:promotion-merged into=79 -->", self.comment_bodies(issues[str(source)]))
        self.assertEqual(issues["79"]["state"], "OPEN")
        self.assertEqual(issues["51"]["state"], "CLOSED")
        self.assertIn("<!-- factory:promotion-covered check=.gitleaks.toml#context7-key-token -->", self.comment_bodies(issues["51"]))
        for number in (79, 64, 89, 104, 52):
            self.assertIn("needs-triage", self.labels(issues[str(number)]))
        self.assertFalse(any("ready-for-agent" in args for args in self.calls()))
        tracking, comments = self.tracking_comments(state)
        self.assertIsNotNone(tracking)
        summaries = [c for c in comments if SUMMARY in c["body"]]
        self.assertEqual(len(summaries), 1)
        self.assertIn("Open promotions: 51 before, 44 after this run.", summaries[0]["body"])

    def test_a_second_run_changes_no_issue_and_edits_only_the_summary(self):
        self.assertEqual(self.run_pass().returncode, 0)
        after_first = normalized(self.read_state())
        self.log.unlink()
        result = self.run_pass()
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = self.writes()
        self.assertEqual(len(writes), 1, writes)
        self.assertEqual(writes[0][:2], ["api", "-X"])
        state = self.read_state()
        self.assertEqual(normalized(state), after_first)
        _, comments = self.tracking_comments(state)
        self.assertEqual(len([c for c in comments if SUMMARY in c["body"]]), 1)

    def test_an_interrupted_run_resumes_to_the_same_state(self):
        self.assertEqual(self.run_pass().returncode, 0)
        clean = normalized(self.read_state())
        self.write_state(fixture_state())
        self.log.unlink(missing_ok=True)
        interrupted = self.run_pass(fail_after=5)
        self.assertNotEqual(interrupted.returncode, 0)
        self.assertIn("re-run to finish", interrupted.stderr)
        resumed = self.run_pass()
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(normalized(self.read_state()), clean)

    def comment_bodies(self, item):
        return "\n".join(c["body"] for c in item.get("comments", []))

    def labels(self, item):
        return [label["name"] for label in item["labels"]]


class Refused(Pass):
    def assertRefused(self, plan, message):
        self.write_plan(plan)
        before = self.state_path.read_text()
        result = self.run_pass()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.state_path.read_text(), before)

    def test_unknown_number_is_refused_before_any_write(self):
        plan = json.loads(PLAN.read_text())
        plan["clusters"].append({"members": [9999, 79], "reason": "x"})
        self.assertRefused(plan, "#9999 is not a promotion issue")

    def test_a_number_in_two_places_is_refused(self):
        plan = json.loads(PLAN.read_text())
        plan["covered"][0]["issue"] = 79
        self.assertRefused(plan, "#79 appears in")

    def test_a_covered_check_that_does_not_exist_is_refused(self):
        plan = json.loads(PLAN.read_text())
        plan["covered"][0]["check"]["name"] = "no-such-check"
        self.assertRefused(plan, ".gitleaks.toml does not define the check no-such-check")

    def test_a_free_text_gate_check_cannot_close_anything(self):
        plan = json.loads(PLAN.read_text())
        plan["covered"] = [{"issue": 38, "check": {"file": "hk.pkl", "name": "comment-ban"}, "reason": "x"}]
        self.assertRefused(plan, "hk.pkl does not define the check comment-ban")

    def test_a_closed_issue_that_this_pass_did_not_close_is_refused(self):
        state = fixture_state()
        state["issues"]["80"]["state"] = "CLOSED"
        self.write_state(state)
        self.assertRefused(json.loads(PLAN.read_text()), "#80 is closed and this pass did not close it")

    def test_a_reason_must_be_one_line(self):
        plan = json.loads(PLAN.read_text())
        plan["clusters"][0]["reason"] = "two\nlines"
        self.assertRefused(plan, "cluster 1 needs a one-line reason")


class Policy(Pass):
    def test_skips_wontfix_and_unmarked_queued_issues_and_takes_the_next(self):
        state = fixture_state()
        state["issues"]["52"]["labels"].append({"name": "wontfix"})
        state["issues"]["65"]["labels"].append({"name": "needs-triage"})
        self.write_state(state)
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        sent = result.stdout.split("Sent to pushback")[1].split("### Proposed")[0]
        self.assertNotIn("#52 ", sent)
        self.assertNotIn("#65 ", sent)
        self.assertEqual(len(re.findall(r"^- #", sent, re.M)), 5)

    def test_a_recorded_verdict_is_not_sent_again(self):
        state = fixture_state()
        state["issues"]["79"]["comments"] = [
            {
                "id": 50,
                "body": "<!-- factory:pushback verdict=go -->\nVerdict: go",
                "url": "https://example.test/79#issuecomment-50",
                "author": {"login": "ranokay"},
                "createdAt": "2026-11-20T00:00:00Z",
            }
        ]
        self.write_state(state)
        result = self.run_pass("--dry-run")
        sent = result.stdout.split("Sent to pushback")[1].split("### Proposed")[0]
        self.assertNotIn("#79 ", sent)

    def test_ties_go_to_the_oldest_issue(self):
        state = {"issues": {}, "mutations": 0, "next_comment": 1, "next_issue": 900}
        for number, created in ((7, "2026-11-02T00:00:00Z"), (3, "2026-11-01T00:00:00Z")):
            state["issues"][str(number)] = {
                "number": number, "title": f"Reject widget {number}", "body": "### Correction\n\nx\n\n### Gate check\n\nx",
                "state": "OPEN", "createdAt": created, "labels": [{"name": "promotion"}], "comments": [],
            }
        self.write_state(state)
        self.write_plan({"clusters": [], "covered": []})
        result = self.run_pass("--dry-run", "--top", "1")
        self.assertIn("- #3 (score 2)", result.stdout)
        self.assertNotIn("- #7 (score", result.stdout)

    def test_near_titles_are_proposed_and_never_applied(self):
        state = fixture_state()
        state["issues"]["1001"] = {
            "number": 1001, "title": "Reject mise runtime tools in a Profile", "body": "### Correction\n\nx\n\n### Gate check\n\nx",
            "state": "OPEN", "createdAt": "2026-11-27T00:00:00Z", "labels": [{"name": "promotion"}], "comments": [],
        }
        state["issues"]["1002"] = {
            "number": 1002, "title": "Reject mise runtime pins in a Profile", "body": "### Correction\n\nx\n\n### Gate check\n\nx",
            "state": "OPEN", "createdAt": "2026-11-27T00:00:00Z", "labels": [{"name": "promotion"}], "comments": [],
        }
        self.write_state(state)
        result = self.run_pass()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("- #1001, #1002 (shared title words: mise, profile, reject, runtime)", result.stdout)
        self.assertEqual(self.read_state()["issues"]["1001"]["state"], "OPEN")
        self.assertEqual(self.read_state()["issues"]["1002"]["state"], "OPEN")


class TrackingLookup(Pass):
    def test_refuses_when_the_listing_reaches_its_cap(self):
        state = fixture_state()
        for number in range(2000, 2000 + module.TRACKING_LIST_LIMIT):
            state["issues"][str(number)] = {
                "number": number, "title": "unrelated", "body": "", "state": "OPEN",
                "createdAt": "2026-11-27T00:00:00Z", "labels": [], "comments": [],
            }
        self.write_state(state)
        self.write_plan({"clusters": [], "covered": []})
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"{module.TRACKING_LIST_LIMIT}-issue cap", result.stderr)


class MissingField(Pass):
    def test_a_gh_listing_without_a_field_is_named_not_a_traceback(self):
        stub_bin = self.root / "stub"
        stub_bin.mkdir()
        stub = stub_bin / "gh"
        stub.write_text("#!/usr/bin/env python3\nprint('[{\"number\": 1, \"state\": \"OPEN\"}]')\n")
        stub.chmod(0o755)
        self.write_plan({"clusters": [], "covered": []})
        env = dict(os.environ, PATH=f"{stub_bin}{os.pathsep}{os.environ['PATH']}")
        result = subprocess.run(  # nosec B603 -- the test runs the pass with an argument list
            [sys.executable, str(PASS), "--plan", str(self.plan_path), "--repo", "ranokay/factory", "--dry-run"],
            capture_output=True, text=True, check=False, env=env, timeout=120,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("without the expected field", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class CheckFiles(unittest.TestCase):
    def test_each_format_is_matched_by_its_definition(self):
        module.check_defined(ROOT, {"file": ".gitleaks.toml", "name": "context7-key-token"})
        module.check_defined(ROOT, {"file": "hk.pkl", "name": "gitleaks-test"})
        with self.assertRaises(module.Refusal):
            module.check_defined(ROOT, {"file": "hk.pkl", "name": "gitleaks"})
        with self.assertRaises(module.Refusal):
            module.check_defined(ROOT, {"file": "../outside.toml", "name": "x"})
        with self.assertRaises(module.Refusal):
            module.check_defined(ROOT, {"file": "README.md", "name": "x"})


if __name__ == "__main__":
    unittest.main()
