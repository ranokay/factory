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
MUTATING = {("issue", "comment"), ("issue", "edit"), ("issue", "close"), ("issue", "create")}
WORKFLOW = """name: CI
on: push
jobs:
  lint:
    runs-on: ubuntu-latest
    env:
      TOKEN:
        value: abc
    steps:
      - name: Typecheck
        uses: actions/checkout@v4
        with:
          image:
            tag: x
"""
NAMED_CLUSTERS = [[79, 80, 163, 206], [104, 105], [64, 109], [89, 211]]
FIXTURE_COVERED = {"issue": 52, "check": {"file": "hk.pkl", "name": "gitleaks-test"}, "reason": "Test-only mapping that exercises the covered path."}

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


def without_summaries(state):
    clean = copy.deepcopy(state)
    for item in clean["issues"].values():
        item["comments"] = [c for c in item.get("comments", []) if "factory:promotion-consolidate-run id=" not in c["body"]]
    return clean


def proposal_members(stdout):
    section = stdout.split("Proposed clusters (not applied)")[1].split("Confirm a proposal")[0]
    groups = []
    for line in section.splitlines():
        match = re.match(r"- (.*?) \(shared terms:|- not proposed, \d+ members.*?: (.*)$", line)
        if match:
            text = match.group(1) or match.group(2)
            groups.append([int(n.strip().lstrip("#")) for n in text.split(",")])
    return groups


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
        self.work = self.root / "work"
        self.make_integration_clone()
        self.write_state(fixture_state())

    def make_integration_clone(self):
        remote = self.root / "remote.git"
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.test"]
        subprocess.run(["git", "init", "--bare", "--quiet", "-b", "main", str(remote)], check=True)  # nosec B603 B607
        self.work.mkdir()
        for name in (".gitleaks.toml", "hk.pkl"):
            shutil.copy(ROOT / name, self.work / name)
        (self.work / ".github" / "workflows").mkdir(parents=True)
        (self.work / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW)
        git = ["git", "-C", str(self.work)]
        subprocess.run([*git, "init", "--quiet", "-b", "main"], check=True)  # nosec B603 B607
        subprocess.run([*git, "add", "."], check=True)  # nosec B603 B607
        subprocess.run([*git, *identity, "commit", "--quiet", "-m", "fixture"], check=True)  # nosec B603 B607
        subprocess.run([*git, "remote", "add", "origin", str(remote)], check=True)  # nosec B603 B607
        subprocess.run([*git, "push", "--quiet", "origin", "main"], check=True)  # nosec B603 B607

    def write_state(self, state):
        self.state_path.write_text(json.dumps(state, sort_keys=True))

    def read_state(self):
        return json.loads(self.state_path.read_text())

    def write_plan(self, plan):
        self.plan_path.write_text(json.dumps(plan))

    def run_pass(self, *extra, fail_after=None, plan=None, run_id=None):
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
            "--repo", "ranokay/factory", "--date", "2026-11-27", "--root", str(self.work), *extra,
        ]
        if run_id is not None:
            command += ["--run-id", run_id]
        return subprocess.run(  # nosec B603 -- the test runs the pass with an argument list
            command, capture_output=True, text=True, check=False, env=env, timeout=120
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def writes(self):
        return [args for args in self.calls() if tuple(args[:2]) in MUTATING or (args[0] == "api" and "PATCH" in args)]

    def tracking(self, state):
        for item in state["issues"].values():
            if TRACKING in item["body"]:
                return item
        return None

    def summaries(self, state):
        tracking = self.tracking(state)
        if tracking is None:
            return []
        return [c for c in tracking["comments"] if "factory:promotion-consolidate-run id=" in c["body"]]

    def comment_bodies(self, item):
        return "\n".join(c["body"] for c in item.get("comments", []))

    def labels(self, item):
        return [label["name"] for label in item["labels"]]


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
        plan["covered"] = [dict(FIXTURE_COVERED, issue=79)]
        self.assertRefused(plan, "#79 appears in")

    def test_a_covered_check_that_does_not_exist_is_refused(self):
        plan = json.loads(PLAN.read_text())
        plan["covered"] = [dict(FIXTURE_COVERED, check={"file": "hk.pkl", "name": "no-such-check"})]
        self.assertRefused(plan, "hk.pkl does not define the check no-such-check")

    def test_a_free_text_gate_check_cannot_close_anything(self):
        plan = json.loads(PLAN.read_text())
        plan["covered"] = [{"issue": 38, "check": {"file": "hk.pkl", "name": "comment-ban"}, "reason": "x"}]
        self.assertRefused(plan, "hk.pkl does not define the check comment-ban")

    def test_a_closed_issue_that_this_pass_did_not_close_is_refused(self):
        state = fixture_state()
        state["issues"]["80"]["state"] = "CLOSED"
        self.write_state(state)
        self.assertRefused(json.loads(PLAN.read_text()), "#80 is closed and this pass did not close it")

    def test_a_check_that_is_only_local_is_refused(self):
        plan = json.loads(PLAN.read_text())
        plan["covered"] = [dict(FIXTURE_COVERED, check={"file": "scripts/promotion-consolidate.py", "name": "run"})]
        self.assertRefused(plan, "from origin/main")

    def test_a_cluster_that_is_not_an_object_is_refused(self):
        plan = json.loads(PLAN.read_text())
        plan["clusters"] = ["x"]
        self.assertRefused(plan, "cluster 1 must be an object")

    def test_a_reason_must_be_one_line(self):
        plan = json.loads(PLAN.read_text())
        plan["clusters"][0]["reason"] = "two\nlines"
        self.assertRefused(plan, "cluster 1 needs a one-line reason")

    def test_a_run_id_with_spaces_is_refused(self):
        result = self.run_pass(run_id="bad id")
        self.assertEqual(result.returncode, 1)
        self.assertIn("run id", result.stderr)


class StrictPlan(Pass):
    def test_a_cluster_of_non_numbers_is_refused_not_a_traceback(self):
        plan = json.loads(PLAN.read_text())
        plan["clusters"] = [{"members": ["a", "b"], "reason": "x"}]
        self.write_plan(plan)
        result = self.run_pass()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.writes(), [])

    def test_a_missing_plan_key_is_refused(self):
        self.write_plan({"clusters": []})
        result = self.run_pass()
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs a covered list", result.stderr)
        self.assertEqual(self.writes(), [])

    def test_a_promotion_without_a_correction_section_refuses_the_run(self):
        state = fixture_state()
        state["issues"]["80"]["body"] = "Just a note, no template."
        self.write_state(state)
        result = self.run_pass()
        self.assertEqual(result.returncode, 1)
        self.assertIn("#80 has no ### Correction section", result.stderr)
        self.assertEqual(self.writes(), [])

    def test_a_rerun_of_a_finished_backlog_reports_no_change(self):
        self.assertEqual(self.run_pass(run_id="R1").returncode, 0)
        result = self.run_pass("--dry-run", run_id="R2")
        self.assertIn("Open promotions: 45 before, 45 after this run.", result.stdout)


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
        self.assertTrue(all(len(group) <= module.MAX_PROPOSAL for group in groups), groups)
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


class PromotionCap(Pass):
    def test_refuses_when_the_promotion_listing_reaches_its_cap(self):
        state = fixture_state()
        for number in range(3000, 3000 + module.PROMOTION_LIST_LIMIT - len(state["issues"])):
            state["issues"][str(number)] = {
                "number": number, "title": "unrelated", "body": "x", "state": "OPEN",
                "createdAt": "2026-11-27T00:00:00Z", "labels": [], "comments": [],
            }
        self.write_state(state)
        self.write_plan({"clusters": [], "covered": []})
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"{module.PROMOTION_LIST_LIMIT}-issue cap", result.stderr)


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
            [sys.executable, str(PASS), "--plan", str(self.plan_path), "--repo", "ranokay/factory", "--dry-run", "--root", str(self.work)],
            capture_output=True, text=True, check=False, env=env, timeout=120,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("without the expected field", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


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


class TrustedMarkers(Pass):
    def copied(self, number, body, author="mallory", state_edit=None):
        state = fixture_state()
        if state_edit:
            state_edit(state)
        state["issues"][str(number)].setdefault("comments", []).append(
            {"id": 77, "body": body, "url": f"https://example.test/{number}#issuecomment-77",
              "author": {"login": author}, "createdAt": "2026-11-20T00:00:00Z"}
        )
        self.write_state(state)

    def test_a_copied_merged_marker_on_a_closed_issue_is_ignored(self):
        def close_80(state):
            state["issues"]["80"]["state"] = "CLOSED"
        self.copied(80, f"{module.MERGED_PREFIX}79 -->", state_edit=close_80)
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("#80 is closed and this pass did not close it as a duplicate of survivor #79", result.stderr)

    def test_a_copied_covered_marker_on_a_closed_issue_is_ignored(self):
        def close_52(state):
            state["issues"]["52"]["state"] = "CLOSED"
        self.copied(52, f"{module.COVERED_PREFIX}hk.pkl#gitleaks-test -->", state_edit=close_52)
        plan = json.loads(PLAN.read_text())
        plan["covered"] = [FIXTURE_COVERED]
        self.write_plan(plan)
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("#52 is closed and this pass did not close it as covered by hk.pkl#gitleaks-test", result.stderr)

    def test_a_copied_handoff_marker_does_not_stop_the_handoff(self):
        self.copied(79, module.PUSHBACK_MARKER)
        result = self.run_pass(run_id="R1")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.read_state()
        self.assertIn("needs-triage", [l["name"] for l in state["issues"]["79"]["labels"]])
        own = [c for c in state["issues"]["79"]["comments"] if module.PUSHBACK_MARKER in c["body"] and c["author"]["login"] == "ranokay"]
        self.assertEqual(len(own), 1)

    def test_a_copied_verdict_from_another_account_is_not_a_verdict(self):
        self.copied(79, "<!-- factory:pushback verdict=go -->\nVerdict: go")
        result = self.run_pass(run_id="R1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("needs-triage", [l["name"] for l in self.read_state()["issues"]["79"]["labels"]])

    def test_a_nested_steps_key_does_not_hide_the_real_steps(self):
        plan = json.loads(PLAN.read_text())
        plan["covered"] = [dict(FIXTURE_COVERED, issue=52, check={"file": ".github/workflows/ci.yml", "name": "Typecheck"})]
        self.write_plan(plan)
        anchor = "    runs-on: ubuntu-latest\n"
        assert WORKFLOW.count(anchor) == 1
        workflow = WORKFLOW.replace(anchor, anchor + "    strategy:\n      matrix:\n        steps:\n          - 1\n")
        self.assertNotEqual(workflow, WORKFLOW)
        (self.work / ".github" / "workflows" / "ci.yml").write_text(workflow)
        git = ["git", "-C", str(self.work)]
        subprocess.run([*git, "add", "."], check=True)  # nosec B603 B607
        subprocess.run([*git, "-c", "user.name=test", "-c", "user.email=test@example.test", "commit", "--quiet", "-m", "nested"], check=True)  # nosec B603 B607
        subprocess.run([*git, "push", "--quiet", "--force", "origin", "main"], check=True)  # nosec B603 B607
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("covered by `.github/workflows/ci.yml#Typecheck`", result.stdout)

    def test_a_copied_summary_marker_does_not_suppress_the_run_summary(self):
        self.write_state(fixture_state())
        first = self.run_pass(run_id="R1")
        self.assertEqual(first.returncode, 0, first.stderr)
        state = self.read_state()
        tracking = self.tracking(state)
        tracking["comments"] = [
            c for c in tracking["comments"] if "id=R1 -->" not in c["body"]
        ] + [{"id": 88, "body": "<!-- factory:promotion-consolidate-run id=R2 -->", "url": "https://example.test/x#issuecomment-88", "author": {"login": "mallory"}, "createdAt": "2026-11-20T00:00:00Z"}]
        self.write_state(state)
        self.log.unlink(missing_ok=True)
        second = self.run_pass(run_id="R2")
        self.assertEqual(second.returncode, 0, second.stderr)
        own = [c for c in self.tracking(self.read_state())["comments"] if "id=R2 -->" in c["body"] and c["author"]["login"] == "ranokay"]
        self.assertEqual(len(own), 1)

    def test_a_deleted_account_author_is_untrusted_not_a_crash(self):
        state = fixture_state()
        state["issues"]["950"] = {
            "number": 950, "title": "Promotion backlog consolidation (tracking)",
            "body": f"{module.TRACKING_MARKER}\nghost copy", "state": "OPEN",
            "createdAt": "2026-11-20T00:00:00Z", "labels": [], "comments": [], "author": None,
        }
        self.write_state(state)
        result = self.run_pass(run_id="R1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.read_state()["issues"]["950"]["comments"], [])

    def test_a_title_cannot_carry_a_summary_marker(self):
        state = fixture_state()
        state["issues"]["79"]["title"] = "Reject runtimes <!-- factory:promotion-consolidate-run id=R2 -->"
        self.write_state(state)
        self.assertEqual(self.run_pass(run_id="R1").returncode, 0)
        summary = self.summaries(self.read_state())[0]["body"]
        self.assertEqual(summary.count("<!-- factory:promotion-consolidate-run id="), 1)

    def test_a_copied_tracking_issue_is_not_adopted(self):
        state = fixture_state()
        state["issues"]["950"] = {
            "number": 950, "title": "Promotion backlog consolidation (tracking)",
            "body": f"{module.TRACKING_MARKER}\nmallory's copy", "state": "OPEN",
            "createdAt": "2026-11-20T00:00:00Z", "labels": [], "comments": [], "author": {"login": "mallory"},
        }
        self.write_state(state)
        result = self.run_pass(run_id="R1")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.read_state()
        self.assertEqual(state["issues"]["950"]["comments"], [])
        own = [i for i in state["issues"].values() if module.TRACKING_MARKER in i["body"] and i["author"]["login"] == "ranokay"]
        self.assertEqual(len(own), 1)


class ClosedMembers(Pass):
    def test_a_closed_member_merged_into_another_survivor_is_refused(self):
        def close_80_into_163(state):
            state["issues"]["80"]["state"] = "CLOSED"
            state["issues"]["80"]["comments"] = [
                {"id": 99, "body": f"{module.MERGED_PREFIX}163 -->", "url": "https://example.test/80#issuecomment-99",
                  "author": {"login": "ranokay"}, "createdAt": "2026-11-20T00:00:00Z"}
            ]
        self.write_state(self._with(close_80_into_163))
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("#80 is closed and this pass did not close it as a duplicate of survivor #79", result.stderr)
        self.assertEqual(self.writes(), [])

    def test_a_closed_issue_covered_elsewhere_is_not_a_cluster_member(self):
        def close_52_covered(state):
            state["issues"]["52"]["state"] = "CLOSED"
            state["issues"]["52"]["comments"] = [
                {"id": 98, "body": f"{module.COVERED_PREFIX}hk.pkl#gitleaks-test -->", "url": "https://example.test/52#issuecomment-98",
                  "author": {"login": "ranokay"}, "createdAt": "2026-11-20T00:00:00Z"}
            ]
        plan = json.loads(PLAN.read_text())
        plan["clusters"].append({"members": [79, 52], "reason": "x"})
        self.write_plan(plan)
        self.write_state(self._with(close_52_covered))
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("#52 is closed", result.stderr)
        self.assertEqual(self.writes(), [])

    def _with(self, edit):
        state = fixture_state()
        edit(state)
        return state


class WorkflowNames(Pass):
    def test_only_job_ids_and_step_names_resolve(self):
        for name in ("lint", "Typecheck"):
            plan = json.loads(PLAN.read_text())
            plan["covered"] = [dict(FIXTURE_COVERED, issue=52, check={"file": ".github/workflows/ci.yml", "name": name})]
            self.write_plan(plan)
            result = self.run_pass("--dry-run")
            self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")
            self.assertIn(f"#52 covered by `.github/workflows/ci.yml#{name}`", result.stdout)

    def test_a_with_key_or_an_env_var_does_not_resolve(self):
        for name in ("image", "TOKEN", "runs-on"):
            plan = json.loads(PLAN.read_text())
            plan["covered"] = [dict(FIXTURE_COVERED, issue=52, check={"file": ".github/workflows/ci.yml", "name": name})]
            self.write_plan(plan)
            result = self.run_pass("--dry-run")
            self.assertEqual(result.returncode, 1, f"{name}: {result.stdout}")
            self.assertIn(f".github/workflows/ci.yml does not define the check {name}", result.stderr)


class GateBounding(Pass):
    def test_appended_occurrences_do_not_move_proposal_weights(self):
        def two(bodies):
            state = {"issues": {}, "mutations": 0, "next_comment": 1, "next_issue": 900}
            for number, body in bodies:
                state["issues"][str(number)] = {
                    "number": number, "title": f"Widget {number}", "body": body, "state": "OPEN",
                    "createdAt": "2026-11-27T00:00:00Z", "labels": [{"name": "promotion"}], "comments": [],
                }
            return state
        plain = "### Correction\n\nalpha beta\n\n### Gate check\n\nnothing shared here"
        other = "### Correction\n\ngamma delta\n\n### Gate check\n\nquorumzeta widgetfoo rarebar"
        appended = plain + "\n\n### Merged occurrences\n\n- quorumzeta widgetfoo rarebar"
        self.write_state(two([(1, plain), (2, other)]))
        self.write_plan({"clusters": [], "covered": []})
        without = self.run_pass("--dry-run")
        self.write_state(two([(1, appended), (2, other)]))
        with_junk = self.run_pass("--dry-run")
        self.assertEqual(without.returncode, 0, without.stderr)
        self.assertEqual(with_junk.returncode, 0, with_junk.stderr)
        self.assertEqual(proposal_members(without.stdout), [])
        self.assertEqual(proposal_members(with_junk.stdout), [])


class FetchAndGate(Pass):
    def test_a_run_whose_root_is_not_a_checkout_is_refused_before_any_write(self):
        result = self.run_pass("--root", str(self.root / "not-a-checkout"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot fetch origin main", result.stderr)
        self.assertEqual(self.writes(), [])


class Occurrences(Pass):
    def test_every_line_of_a_correction_is_kept_as_an_occurrence(self):
        state = fixture_state()
        state["issues"]["900"] = {
            "number": 900, "title": "Reject widget", "createdAt": "2026-11-27T00:00:00Z", "state": "OPEN",
            "body": "### Correction\n\nTwo cases:\n- first\n  continued here\n- second\n\n### Gate check\n\ng",
            "labels": [{"name": "promotion"}], "comments": [],
        }
        self.write_state(state)
        self.write_plan({"clusters": [], "covered": []})
        result = self.run_pass("--dry-run", "--top", "10")
        self.assertIn("- #900 (score 4)", result.stdout)

    def test_a_human_note_in_a_survivor_refuses_the_run(self):
        state = fixture_state()
        state["issues"]["79"]["body"] += "\n\n<!-- factory:promotion-links -->\nCluster members: #79.\n<!-- /factory:promotion-links -->\n\n### Merged occurrences\n\nA human note.\n"
        self.write_state(state)
        result = self.run_pass()
        self.assertEqual(result.returncode, 1)
        self.assertIn("text outside the merge blocks", result.stderr)
        self.assertEqual(self.writes(), [])


if __name__ == "__main__":
    unittest.main()
