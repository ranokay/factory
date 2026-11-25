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

    def test_documentation_prose_and_directives_are_not_commented_out_code(self):
        docs = self.project / "src/Docs.swift"
        docs.parent.mkdir(parents=True, exist_ok=True)
        docs.write_text(
            "/// The failure text stored on a delivery row. It never includes a credential;\n"
            "/**\n"
            " * A block documentation line;\n"
            " */\n"
            "// Measured: elapsedTime 174.6 reported at the resume, read 8.5 s later\n"
            "// while Music said 183.3. The position must not stay at 174.6.\n"
        )
        inner = self.project / "src/lib.rs"
        inner.write_text("//! return cached;\n")
        probing = self.project / "src/Probing.swift"
        probing.write_text(
            "droplet.openURL(url) // NOSONAR: public licence link\n"
        )
        config = self.project / "src/config.yaml"
        config.write_text(
            "# yaml-language-server: $schema=https://example.com/schema.json\n"
        )
        self.commit("add documentation and directives")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        comments = self.issue_for(self.issues(), "comments")
        for path in (
            "src/Docs.swift",
            "src/lib.rs",
            "src/Probing.swift",
            "src/config.yaml",
        ):
            self.assertNotIn(path, comments["body"])
        self.assertIn("src/used.ts", comments["body"])

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

    def test_upstream_fork_applies_settings_despite_a_bad_lock(self):
        (self.project / "skills.lock").write_text("{not json\n")
        self.commit("add a malformed skills lock")
        state = self.state()
        state["repo"].update({"fork": True, "private": True})
        state["topics"]["names"] = ["factory-upstream-fork"]
        self.write_state(state)
        self.reset_log()
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.issues(), [])
        self.assertIn("Upstream Fork", result.stdout)
        self.assertTrue(self.settings_writes())

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
