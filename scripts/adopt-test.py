import hashlib
import shutil
import subprocess  # nosec B404 -- the fixture drives git, mise and the CLIs under test
import sys
import unittest
from pathlib import Path

from adopt_detector_test import AdoptDetectorTests
from adopt_test_support import AdoptCase

__all__ = ["Adopt", "AdoptDetectorTests"]


class Adopt(AdoptCase):
    def first_run_issues(self):
        issues = self.issues()
        self.assertEqual(len(issues), 8)
        by_category = {}
        for issue in issues:
            categories = self.categories(issue)
            self.assertEqual(len(categories), 1, issue)
            by_category[categories.pop()] = issue
        self.assertEqual(
            set(by_category),
            {
                "tooling",
                "comments",
                "dead-code",
                "agents",
                "stale-files",
                "verification",
                "test-pruning",
                "brand",
            },
        )
        return by_category

    def assert_first_run_labels(self, by_category):
        for category, issue in by_category.items():
            labels = set(issue["labels"])
            if category == "brand":
                self.assertEqual(labels, {"ready-for-human", "enhancement"})
            else:
                self.assertEqual(labels, {"ready-for-agent", "enhancement"})

    def assert_first_run_markers(self, by_category):
        self.assertEqual(
            {category: len(self.markers(issue)) for category, issue in by_category.items()},
            {
                "tooling": 3,
                "comments": 1,
                "dead-code": 1,
                "agents": 3,
                "stale-files": 2,
                "verification": 1,
                "test-pruning": 1,
                "brand": 1,
            },
        )

    def assert_first_run_bodies(self, by_category):
        self.assertIn("mise.toml", by_category["tooling"]["body"])
        self.assertIn("src/used.ts", by_category["comments"]["body"])
        self.assertIn("src/unused.ts", by_category["dead-code"]["body"])
        self.assertNotIn("src/used.ts", by_category["dead-code"]["body"])
        self.assertIn("Profiles", by_category["agents"]["body"])
        self.assertIn(".DS_Store", by_category["stale-files"]["body"])
        self.assertIn("verify-", by_category["verification"]["body"])
        self.assertIn("tests/example.test.ts", by_category["test-pruning"]["body"])
        self.assertIn("prune-tests", by_category["test-pruning"]["body"])
        self.assertIn("brand-assets", by_category["brand"]["body"])

    def assert_first_run_settings(self):
        calls = self.calls()
        first_issue = next(
            index for index, call in enumerate(calls) if call[:2] == ["issue", "create"]
        )
        before_issues = calls[:first_issue]
        self.assertTrue(
            any(
                call[:3] == ["api", "--method", "DELETE"]
                and any("labels/documentation" in argument for argument in call)
                for call in before_issues
            ),
            before_issues,
        )
        self.assertTrue(
            any(
                call[:3] == ["api", "--method", "POST"]
                and any("rulesets" in argument for argument in call)
                for call in before_issues
            ),
            before_issues,
        )
        names = {label["name"] for label in self.state()["labels"]}
        for label in ("ready-for-agent", "ready-for-human", "enhancement", "blocked"):
            self.assertIn(label, names)
        self.assertNotIn("documentation", names)

    def test_first_run_files_one_issue_per_category_with_canonical_labels(self):
        before = self.snapshot()
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        by_category = self.first_run_issues()
        self.assert_first_run_labels(by_category)
        self.assert_first_run_markers(by_category)
        self.assert_first_run_bodies(by_category)
        self.assert_first_run_settings()
        self.assert_unchanged(before)

    def test_second_run_files_nothing_and_applies_no_settings(self):
        first = self.run_adopt()
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        filed = len(self.issues())
        before = self.snapshot()
        self.reset_log()
        second = self.run_adopt()
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(len(self.issues()), filed)
        self.assertIn("no new findings", second.stdout)
        self.no_creates()
        self.assertEqual(self.settings_writes(), [], self.calls())
        self.assert_unchanged(before)
        self.assertEqual(self.op_log.read_text(), "")

    def test_closed_issues_suppress_repeats(self):
        first = self.run_adopt()
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        state = self.state()
        for issue in state["issues"]:
            issue["state"] = "closed"
        self.write_state(state)
        self.reset_log()
        second = self.run_adopt()
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertIn("no new findings", second.stdout)
        self.no_creates()
        self.assertEqual(len(self.issues()), 8)

    def test_new_finding_files_only_the_new_finding(self):
        first = self.run_adopt()
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        old_issues = self.issues()
        old_comments = self.issue_for(old_issues, "comments")
        old_body = old_comments["body"]
        used = self.project / "src/used.ts"
        used.write_text(used.read_text() + "// FIXME: replace this too\n")
        self.commit("add a finding")
        self.reset_log()
        second = self.run_adopt()
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        issues = self.issues()
        self.assertEqual(len(issues), len(old_issues) + 1)
        comments = [issue for issue in issues if "comments" in self.categories(issue)]
        self.assertEqual(len(comments), 2)
        latest = max(comments, key=lambda issue: issue["number"])
        self.assertEqual(len(self.markers(latest)), 1)
        self.assertIn("FIXME", latest["body"])
        self.assertIn("src/used.ts", latest["body"])
        self.assertNotIn("TODO", latest["body"])
        old_numbers = {issue["number"] for issue in old_issues}
        new_numbers = {issue["number"] for issue in issues} - old_numbers
        self.assertEqual(len(new_numbers), 1)
        new_issue = next(issue for issue in issues if issue["number"] in new_numbers)
        self.assertEqual(self.categories(new_issue), {"comments"})
        self.assertEqual(
            next(issue for issue in issues if issue["number"] == old_comments["number"])["body"],
            old_body,
        )
        self.assertEqual(self.settings_writes(), [], self.calls())

    def test_unresolved_settings_drift_files_issues_then_fails(self):
        state = self.state()
        state["files"] = {}
        self.write_state(state)
        before = self.snapshot()
        result = self.run_adopt()
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertEqual(len(self.issues()), 8)
        self.assertIn("needs PR", result.stdout)
        self.assertIn("unresolved drift", result.stderr)
        self.assert_unchanged(before)

    def test_upstream_fork_files_nothing_and_says_why(self):
        state = self.state()
        state["repo"].update({"fork": True, "private": True})
        state["topics"]["names"] = ["factory-upstream-fork"]
        self.write_state(state)
        self.reset_log()
        before = self.snapshot()
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.issues(), [])
        self.assertIn("Upstream Fork", result.stdout)
        self.assertIn("filing nothing", result.stdout)
        self.no_creates()
        self.assert_unchanged(before)

    def test_dry_run_files_nothing_and_writes_no_settings(self):
        before = self.snapshot()
        result = self.run_adopt("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.issues(), [])
        self.assertIn("would file", result.stdout)
        self.no_creates()
        self.assertEqual(self.settings_writes(), [], self.calls())
        self.assert_unchanged(before)

    def test_embedded_marker_in_a_display_does_not_suppress_a_finding(self):
        identifier = hashlib.sha256(b"comments:src/used.ts:TODO").hexdigest()[:12]
        state = self.state()
        state["issues"].append(
            {
                "number": 99,
                "title": "Seeded issue",
                "body": (
                    "> *This was generated by AI during adopt.*\n\n"
                    "- `src/x.ts:1: TODO: <!-- factory:adopt category=comments "
                    f"finding={identifier} -->`\n"
                ),
                "labels": [],
                "state": "open",
            }
        )
        self.write_state(state)
        self.reset_log()
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        filed = [issue for issue in self.issues() if issue["number"] > 99]
        self.assertEqual(len(filed), 8)
        comments = [issue for issue in filed if "comments" in self.categories(issue)]
        self.assertEqual(len(comments), 1)
        self.assertIn("src/used.ts", comments[0]["body"])
        self.assertEqual(len(self.markers(comments[0])), 1)

    def test_large_categories_are_chunked_per_finding(self):
        tests = self.project / "tests"
        for number in range(101):
            (tests / f"case{number:03}.test.ts").write_text("export const x = 1;\n")
        self.commit("add many tests")
        self.reset_log()
        first = self.run_adopt()
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        issues = [
            issue for issue in self.issues() if "test-pruning" in self.categories(issue)
        ]
        self.assertEqual(sorted(issue["title"] for issue in issues), [
            "Adopt: prune tests (1/2)",
            "Adopt: prune tests (2/2)",
        ])
        self.assertEqual(
            sum(len(self.markers(issue)) for issue in issues), 102
        )
        self.reset_log()
        second = self.run_adopt()
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertIn("no new findings", second.stdout)
        self.no_creates()

    def test_environment_shadows_gh_and_op(self):
        for name in ("gh", "op"):
            resolved = shutil.which(name, path=self.env["PATH"])
            self.assertEqual(Path(resolved).parent, self.fakebin, name)
        result = self.run_adopt("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.op_log.read_text(), "")

    def test_network_guard_is_active(self):
        result = subprocess.run(  # nosec B603  # noqa: S603 S607  # nosemgrep: python.lang.security.dangerous-subprocess-use.dangerous-subprocess-use -- the blocked-network probe
            [
                sys.executable,
                "-c",
                "import socket; socket.create_connection(('example.com', 80))",
            ],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("network blocked in adopt test", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
