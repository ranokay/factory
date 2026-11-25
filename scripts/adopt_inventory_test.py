import json

from adopt_test_support import AdoptCase


class AdoptInventoryTests(AdoptCase):
    def write_skills_lock(self):
        (self.project / "skills.lock").write_text(
            json.dumps(
                {
                    "version": 1,
                    "skills": [
                        {
                            "name": "vendor",
                            "source": "https://example.invalid/skills.git",
                            "path": "skills/vendor",
                            "commit": "0" * 40,
                            "patches": [],
                            "files": [],
                        }
                    ],
                }
            )
        )

    def test_vendored_skill_files_are_not_inventoried(self):
        vendored = self.project / "skills/vendor/scripts/helper.test.ts"
        vendored.parent.mkdir(parents=True)
        vendored.write_text("// TODO: upstream follows\n")
        own = self.project / "skills/own/scripts/helper.test.ts"
        own.parent.mkdir(parents=True)
        own.write_text("// TODO: ours\n")
        self.write_skills_lock()
        self.commit("add skills")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        issues = self.issues()
        comments = self.issue_for(issues, "comments")
        self.assertNotIn("skills/vendor/scripts/helper.test.ts", comments["body"])
        self.assertIn("skills/own/scripts/helper.test.ts", comments["body"])
        pruning = self.issue_for(issues, "test-pruning")
        self.assertNotIn("skills/vendor/scripts/helper.test.ts", pruning["body"])
        self.assertIn("skills/own/scripts/helper.test.ts", pruning["body"])

    def test_test_fixtures_are_not_inventoried(self):
        fixtures = self.project / "tests/fixtures"
        fixtures.mkdir(parents=True)
        (fixtures / "todo.sh").write_text("#!/bin/sh\n# TODO: fixture input\n")
        (fixtures / "case.test.ts").write_text("export const x = 1;\n")
        self.commit("add test fixtures")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        issues = self.issues()
        comments = self.issue_for(issues, "comments")
        self.assertNotIn("tests/fixtures/todo.sh", comments["body"])
        self.assertEqual(len(self.markers(comments)), 1)
        pruning = self.issue_for(issues, "test-pruning")
        self.assertNotIn("tests/fixtures/case.test.ts", pruning["body"])
        self.assertEqual(len(self.markers(pruning)), 1)

    def test_fixture_references_still_keep_dead_code_alive(self):
        (self.project / "src/orphan.ts").write_text("export const orphan = 1;\n")
        usage = self.project / "tests/fixtures/usage.ts"
        usage.parent.mkdir(parents=True)
        usage.write_text('import { orphan } from "../../src/orphan";\n')
        self.commit("add a fixture reference")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        dead = self.issue_for(self.issues(), "dead-code")
        self.assertNotIn("src/orphan.ts", dead["body"])
        self.assertIn("src/unused.ts", dead["body"])

    def test_non_object_skills_lock_stops_the_inventory(self):
        (self.project / "skills.lock").write_text("[]\n")
        self.commit("add a list skills lock")
        result = self.run_adopt()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("skills.lock", result.stderr)
        self.assertEqual(self.issues(), [])

    def test_source_fixtures_are_still_inventoried(self):
        fixtures = self.project / "src/fixtures"
        fixtures.mkdir(parents=True)
        (fixtures / "seed.ts").write_text("// TODO: owned source\n")
        (fixtures / "seed.test.ts").write_text("export const x = 1;\n")
        self.commit("add source fixtures")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        issues = self.issues()
        comments = self.issue_for(issues, "comments")
        self.assertIn("src/fixtures/seed.ts", comments["body"])
        pruning = self.issue_for(issues, "test-pruning")
        self.assertIn("src/fixtures/seed.test.ts", pruning["body"])

    def test_malformed_skills_lock_stops_the_inventory(self):
        (self.project / "skills.lock").write_text("{not json\n")
        self.commit("add a malformed skills lock")
        result = self.run_adopt()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("skills.lock", result.stderr)
        self.assertEqual(self.issues(), [])

    def test_invalid_skills_lock_entry_stops_the_inventory(self):
        (self.project / "skills.lock").write_text(
            '{"version": 1, "skills": [{"name": "vendor"}]}\n'
        )
        self.commit("add an incomplete skills lock")
        result = self.run_adopt()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("skills.lock", result.stderr)
        self.assertEqual(self.issues(), [])
