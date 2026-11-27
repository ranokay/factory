import json
import os
import re
import subprocess  # nosec B404 -- the tests drive the CLI under test
import sys
from promotion_consolidate_github import PROMOTION_LIST_LIMIT, TRACKING_LIST_LIMIT
from promotion_consolidate_render import MAX_PROPOSAL
from promotion_consolidate_test_support import PASS, PLAN, NAMED_CLUSTERS, FIXTURE_COVERED, fixture_state, normalized, without_summaries, proposal_members, Pass


class DryRun(Pass):
    def test_writes_nothing_and_prints_the_summary(self):
        before = self.state_path.read_text()
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.state_path.read_text(), before)
        self.assertIn("Open promotions: 51 before, 45 after this run.", result.stdout)
        self.assertIn("#79 (survivor) absorbs #80, #163, #206", result.stdout)
        self.assertIn("Closed as covered: none.", result.stdout)
        self.assertIn("add needs-triage to #79", result.stdout)
        self.assertNotIn("factory:promotion-consolidate-run id=", result.stdout.split("DRY RUN")[0])

    def test_dry_run_posts_no_summary_comment(self):
        result = self.run_pass("--dry-run", run_id="R-dry")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(args[:2] == ["issue", "comment"] for args in self.writes()))


class LiveRun(Pass):
    def test_merges_closes_and_hands_off(self):
        result = self.run_pass(run_id="R1")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.read_state()
        issues = state["issues"]
        survivor = issues["79"]["body"]
        self.assertIn("Cluster members: #79 (survivor), #80, #163, #206.", survivor)
        for source in (80, 163, 206):
            self.assertIn(f"<!-- factory:promotion-occurrence source={source} -->", survivor)
            self.assertIn(f"From #{source}:", survivor)
            self.assertEqual(issues[str(source)]["state"], "CLOSED")
            self.assertIn("<!-- factory:promotion-merged into=79 -->", self.comment_bodies(issues[str(source)]))
        self.assertEqual(issues["79"]["state"], "OPEN")
        self.assertEqual(issues["51"]["state"], "OPEN")
        for number in (79, 64, 89, 104, 51):
            self.assertIn("needs-triage", self.labels(issues[str(number)]))
        self.assertFalse(any("ready-for-agent" in args for args in self.calls()))
        self.assertEqual(len(self.summaries(state)), 1)
        self.assertIn("Open promotions: 51 before, 45 after this run.", self.summaries(state)[0]["body"])

    def test_two_consecutive_runs_leave_two_summary_comments(self):
        self.assertEqual(self.run_pass(run_id="R1").returncode, 0)
        self.log.unlink()
        self.assertEqual(self.run_pass(run_id="R2").returncode, 0)
        writes = self.writes()
        self.assertEqual(len(writes), 1, writes)
        self.assertEqual(writes[0][:2], ["issue", "comment"])
        state = self.read_state()
        self.assertEqual(len(self.summaries(state)), 2)
        self.assertIn("id=R1 -->", self.summaries(state)[0]["body"])
        self.assertIn("id=R2 -->", self.summaries(state)[1]["body"])

    def test_rerunning_the_same_run_id_posts_nothing(self):
        self.assertEqual(self.run_pass(run_id="R1").returncode, 0)
        self.log.unlink()
        self.assertEqual(self.run_pass(run_id="R1").returncode, 0)
        self.assertEqual(self.writes(), [])
        self.assertEqual(len(self.summaries(self.read_state())), 1)

    def test_an_interrupted_run_resumes_and_posts_one_comment_for_itself(self):
        self.assertEqual(self.run_pass(run_id="R1").returncode, 0)
        clean = normalized(self.read_state())
        self.write_state(fixture_state())
        self.log.unlink(missing_ok=True)
        interrupted = self.run_pass(fail_after=5, run_id="R1")
        self.assertNotEqual(interrupted.returncode, 0)
        self.assertIn("re-run to finish", interrupted.stderr)
        resumed = self.run_pass(run_id="R1")
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        state = self.read_state()
        self.assertEqual(normalized(without_summaries(state)), normalized(without_summaries(json.loads(clean))))
        self.assertEqual(len(self.summaries(state)), 1)
        self.assertIn("Open promotions: 49 before, 45 after this run.", self.summaries(state)[0]["body"])

    def test_a_covered_entry_closes_only_with_its_defined_check(self):
        plan = json.loads(PLAN.read_text())
        plan["covered"] = [FIXTURE_COVERED]
        self.write_plan(plan)
        result = self.run_pass(run_id="R1")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.read_state()
        self.assertEqual(state["issues"]["52"]["state"], "CLOSED")
        self.assertIn("<!-- factory:promotion-covered check=hk.pkl#gitleaks-test -->", self.comment_bodies(state["issues"]["52"]))
        self.assertIn("https://github.com/ranokay/factory/blob/main/hk.pkl", self.comment_bodies(state["issues"]["52"]))
        self.assertIn("Open promotions: 51 before, 44 after this run.", self.summaries(state)[0]["body"])


class HandoffOrder(Pass):
    def test_the_handoff_marker_is_written_before_the_label(self):
        result = self.run_pass(run_id="R1")
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = self.writes()
        marker = next(i for i, a in enumerate(writes) if a[:3] == ["issue", "comment", "79"])
        label = next(i for i, a in enumerate(writes) if a[:3] == ["issue", "edit", "79"] and "--add-label" in a)
        self.assertLess(marker, label)

    def test_a_stop_at_any_point_is_finished_by_a_rerun(self):
        self.assertEqual(self.run_pass(run_id="R1").returncode, 0)
        clean = json.dumps(without_summaries(self.read_state()), sort_keys=True)
        for stop in range(1, 25):
            self.write_state(fixture_state())
            self.log.unlink(missing_ok=True)
            self.run_pass(fail_after=stop, run_id="R1")
            resumed = self.run_pass(run_id="R1")
            self.assertEqual(resumed.returncode, 0, f"stop {stop}: {resumed.stderr}")
            resumed_state = json.dumps(without_summaries(self.read_state()), sort_keys=True)
            self.assertEqual(resumed_state, clean, f"stop {stop} left a different state")


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


class Proposals(Pass):
    def test_the_empty_plan_proposes_the_named_clusters_as_components(self):
        self.write_plan({"clusters": [], "covered": []})
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        groups = proposal_members(result.stdout)
        self.assertTrue(all(len(group) <= MAX_PROPOSAL for group in groups), groups)
        recovered = [
            named for named in NAMED_CLUSTERS
            if any(set(named) <= set(group) for group in groups)
        ]
        self.assertGreaterEqual(len(recovered), 3, recovered)

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
        self.assertIn("#1001, #1002 (shared terms:", result.stdout)
        self.assertEqual(self.read_state()["issues"]["1001"]["state"], "OPEN")
        self.assertEqual(self.read_state()["issues"]["1002"]["state"], "OPEN")


class TrackingLookup(Pass):
    def test_refuses_when_the_listing_reaches_its_cap(self):
        state = fixture_state()
        for number in range(2000, 2000 + TRACKING_LIST_LIMIT):
            state["issues"][str(number)] = {
                "number": number, "title": "unrelated", "body": "", "state": "OPEN",
                "createdAt": "2026-11-27T00:00:00Z", "labels": [], "comments": [],
            }
        self.write_state(state)
        self.write_plan({"clusters": [], "covered": []})
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"{TRACKING_LIST_LIMIT}-issue cap", result.stderr)


class PromotionCap(Pass):
    def test_refuses_when_the_promotion_listing_reaches_its_cap(self):
        state = fixture_state()
        for number in range(3000, 3000 + PROMOTION_LIST_LIMIT - len(state["issues"])):
            state["issues"][str(number)] = {
                "number": number, "title": "unrelated", "body": "x", "state": "OPEN",
                "createdAt": "2026-11-27T00:00:00Z", "labels": [], "comments": [],
            }
        self.write_state(state)
        self.write_plan({"clusters": [], "covered": []})
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"{PROMOTION_LIST_LIMIT}-issue cap", result.stderr)


class MissingField(Pass):
    def test_a_gh_listing_without_a_field_is_named_not_a_traceback(self):
        stub_bin = self.root / "stub"
        stub_bin.mkdir()
        stub = stub_bin / "gh"
        stub.write_text("#!/usr/bin/env python3\nprint('[{\"number\": 1, \"state\": \"OPEN\"}]')\n")
        stub.chmod(0o755)
        self.write_plan({"clusters": [], "covered": []})
        env = dict(os.environ, PATH=f"{stub_bin}{os.pathsep}{os.environ['PATH']}")
        result = subprocess.run(  # nosec B603 -- the test runs the pass with an argument list  # nosemgrep -- the test runs git and the pass with fixed argument lists
            [sys.executable, str(PASS), "--plan", str(self.plan_path), "--repo", "ranokay/factory", "--dry-run", "--root", str(self.work)],
            capture_output=True, text=True, check=False, env=env, timeout=120,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("without the expected field", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class FetchAndGate(Pass):
    def test_a_run_whose_root_is_not_a_checkout_is_refused_before_any_write(self):
        result = self.run_pass("--root", str(self.root / "not-a-checkout"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot fetch origin main", result.stderr)
        self.assertEqual(self.writes(), [])
