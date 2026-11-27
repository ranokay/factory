import json
from promotion_consolidate_text import COVERED_PREFIX, MERGED_PREFIX
from promotion_consolidate_test_support import PLAN, FIXTURE_COVERED, fixture_state, proposal_members, Pass


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


class ClosedMembers(Pass):
    def test_a_closed_member_merged_into_another_survivor_is_refused(self):
        def close_80_into_163(state):
            state["issues"]["80"]["state"] = "CLOSED"
            state["issues"]["80"]["comments"] = [
                {"id": 99, "body": f"{MERGED_PREFIX}163 -->", "url": "https://example.test/80#issuecomment-99",
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
                {"id": 98, "body": f"{COVERED_PREFIX}hk.pkl#gitleaks-test -->", "url": "https://example.test/52#issuecomment-98",
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
