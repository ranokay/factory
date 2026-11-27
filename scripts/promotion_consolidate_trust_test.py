import json
import subprocess  # nosec B404 -- the tests drive the CLI under test
from promotion_consolidate_test_support import PLAN, WORKFLOW, FIXTURE_COVERED, module, fixture_state, Pass


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
        self.assertEqual(WORKFLOW.count(anchor), 1)
        workflow = WORKFLOW.replace(anchor, anchor + "    strategy:\n      matrix:\n        steps:\n          - 1\n")
        self.assertNotEqual(workflow, WORKFLOW)
        (self.work / ".github" / "workflows" / "ci.yml").write_text(workflow)
        git = ["git", "-C", str(self.work)]
        subprocess.run([*git, "add", "."], check=True)  # nosec B603 B607  # nosemgrep -- the test runs git and the pass with fixed argument lists
        subprocess.run([*git, "-c", "user.name=test", "-c", "user.email=test@example.test", "commit", "--quiet", "-m", "nested"], check=True)  # nosec B603 B607  # nosemgrep -- the test runs git and the pass with fixed argument lists
        subprocess.run([*git, "push", "--quiet", "--force", "origin", "main"], check=True)  # nosec B603 B607  # nosemgrep -- the test runs git and the pass with fixed argument lists
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

    def test_a_null_comment_author_is_untrusted_not_a_crash(self):
        state = fixture_state()
        state["issues"]["79"]["comments"] = [
            {"id": 66, "body": f"{module.PUSHBACK_MARKER}\nghost", "url": "https://example.test/79#issuecomment-66",
              "author": None, "createdAt": "2026-11-20T00:00:00Z"}
        ]
        self.write_state(state)
        result = self.run_pass(run_id="R1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("needs-triage", [l["name"] for l in self.read_state()["issues"]["79"]["labels"]])

    def test_a_title_cannot_carry_a_summary_marker(self):
        state = fixture_state()
        state["issues"]["79"]["title"] = "Reject runtimes <!-- factory:promotion-consolidate-run id=R2 --> <!<!--x-->-->"
        self.write_state(state)
        self.assertEqual(self.run_pass(run_id="R1").returncode, 0)
        summary = self.summaries(self.read_state())[0]["body"]
        self.assertIn("#79 (score", summary)
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


class CompletionAndReshapes(Pass):
    def merged(self, number, comment_author="ranokay"):
        return {"id": 300 + number, "body": f"{module.MERGED_PREFIX}79 -->",
                "url": f"https://example.test/{number}#issuecomment-{300 + number}",
                "author": {"login": comment_author}, "createdAt": "2026-11-20T00:00:00Z"}

    def test_a_builtin_step_resolves_as_a_check(self):
        plan = json.loads(PLAN.read_text())
        plan["covered"] = [dict(FIXTURE_COVERED, issue=52, check={"file": "hk.pkl", "name": "gitleaks"})]
        self.write_plan(plan)
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("covered by `hk.pkl#gitleaks`", result.stdout)

    def test_a_completed_cluster_is_skipped_not_refused(self):
        state = fixture_state()
        state["issues"]["79"]["state"] = "CLOSED"
        for number in (80, 163, 206):
            state["issues"][str(number)]["state"] = "CLOSED"
            state["issues"][str(number)]["comments"] = [self.merged(number)]
        self.write_state(state)
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#79 (survivor) is complete", result.stdout)
        self.assertEqual(self.writes(), [])

    def test_a_closed_survivor_with_an_open_source_is_still_refused(self):
        state = fixture_state()
        state["issues"]["79"]["state"] = "CLOSED"
        state["issues"]["80"]["state"] = "CLOSED"
        state["issues"]["80"]["comments"] = [self.merged(80)]
        self.write_state(state)
        result = self.run_pass("--dry-run")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("#79 is closed, so it cannot be used here", result.stderr)

    def test_an_accepted_reshape_survives_the_merge_and_settles(self):
        state = fixture_state()
        state["issues"]["79"]["body"] += "\n\n## Reshaped scope\n\nNarrow it to runtimes only."
        self.write_state(state)
        first = self.run_pass(run_id="R1")
        self.assertEqual(first.returncode, 0, first.stderr)
        body = self.read_state()["issues"]["79"]["body"]
        self.assertIn("## Reshaped scope", body)
        self.assertIn("Narrow it to runtimes only.", body)
        self.log.unlink(missing_ok=True)
        second = self.run_pass(run_id="R2")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse(any(a[:3] == ["issue", "edit", "79"] for a in self.writes()))

    def test_a_reshape_placed_above_the_merge_blocks_does_not_duplicate_them(self):
        self.assertEqual(self.run_pass(run_id="R1").returncode, 0)
        state = self.read_state()
        body = state["issues"]["79"]["body"]
        head, managed = body.split("<!-- factory:promotion-links -->", 1)
        state["issues"]["79"]["body"] = head + "## Reshaped scope\n\nNarrow it.\n\n<!-- factory:promotion-links -->" + managed
        self.write_state(state)
        self.assertEqual(self.run_pass(run_id="R2").returncode, 0)
        body = self.read_state()["issues"]["79"]["body"]
        self.assertEqual(body.count("<!-- factory:promotion-links -->"), 1)
        self.assertEqual(body.count("<!-- factory:promotion-occurrence source=80 -->"), 1)
        self.assertIn("Narrow it.", body)

    def test_an_empty_reshape_above_the_merge_heading_does_not_duplicate_the_blocks(self):
        self.assertEqual(self.run_pass(run_id="R1").returncode, 0)
        state = self.read_state()
        body = state["issues"]["79"]["body"]
        state["issues"]["79"]["body"] = body.replace("### Merged occurrences", "## Reshaped scope\n### Merged occurrences", 1)
        self.write_state(state)
        self.assertEqual(self.run_pass(run_id="R2").returncode, 0)
        body = self.read_state()["issues"]["79"]["body"]
        self.assertEqual(body.count("### Merged occurrences"), 1)
        self.assertEqual(body.count("<!-- factory:promotion-occurrence source=80 -->"), 1)

    def test_a_near_miss_heading_is_not_a_reshape(self):
        self.assertEqual(self.run_pass(run_id="R1").returncode, 0)
        state = self.read_state()
        state["issues"]["79"]["body"] += "\n\n## Reshaped scopes\n\nNot a reshape."
        self.write_state(state)
        result = self.run_pass("--dry-run", run_id="R2")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("survivor #79 has text outside the merge blocks", result.stderr)

    def test_a_source_keeps_the_blocks_it_absorbed_earlier(self):
        plan = json.loads(PLAN.read_text())
        plan["clusters"] = [{"members": [79, 163], "reason": "x"}]
        self.write_plan(plan)
        state = fixture_state()
        state["issues"]["163"]["body"] += (
            "\n\n<!-- factory:promotion-occurrence source=999 -->\nFrom #999:\n- older case\n"
            "<!-- /factory:promotion-occurrence -->"
        )
        self.write_state(state)
        result = self.run_pass(run_id="R1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<!-- factory:promotion-occurrence source=999 -->", self.read_state()["issues"]["79"]["body"])
