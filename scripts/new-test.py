import shutil
import unittest

from new_profiles_test import NewProfilesTests
from new_test_support import NewCase, OP_VALUE, ROOT, TEMPLATE_TAG, TEST_TAG

__all__ = ["NewCaseTests", "NewProfilesTests"]


class NewCaseTests(NewCase):
    def test_creates_repo_clone_template_profiles_and_settings(self):
        result = self.run_new("one")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        state = self.state()
        self.assertIn("ranokay/one", state["repos"])
        self.assertEqual([project["title"] for project in state["projects"]], ["Ideas"])
        clone = self.clone("one")
        for path in ("AGENTS.md", "PRODUCT.md", "mise.toml", "hk.pkl", ".coderabbit.yaml"):
            self.assertTrue((clone / path).is_file(), path)
        self.assertTrue((clone / "CLAUDE.md").is_symlink())
        self.assertEqual((clone / "CLAUDE.md").readlink().as_posix(), "AGENTS.md")
        agents = (clone / "AGENTS.md").read_text()
        self.assertIn("Profiles: universal", agents)
        mise = (clone / "mise.toml").read_text()
        self.assertIn(f"profiles/universal/mise.toml?ref={TEST_TAG}", mise)
        self.assertIn(f"profiles/universal/tasks.toml?ref={TEST_TAG}", mise)
        self.assertEqual(mise.count("profiles/universal/mise.toml"), 1)
        self.assertEqual(mise.count("profiles/universal/tasks.toml"), 1)
        self.assertNotIn(TEMPLATE_TAG, mise)
        hk = (clone / "hk.pkl").read_text()
        self.assertIn(
            f"https://raw.githubusercontent.com/ranokay/factory/{TEST_TAG}/profiles/universal/hk.pkl",
            hk,
        )
        self.assertEqual(len(self.git("one", "log", "--format=%H").stdout.split()), 1)
        self.assertTrue(any("push" in arguments for arguments in self.git_calls()))
        repo = state["repos"]["ranokay/one"]
        self.assertTrue(repo["repo"]["private"])
        self.assertEqual(repo["repo"]["description"], "one, a Personal Project")
        names = {label["name"] for label in repo["labels"]}
        for label in ("ready-for-agent", "ready-for-human", "enhancement", "blocked"):
            self.assertIn(label, names)
        self.assertNotIn("documentation", names)
        self.assertTrue(repo["repo"]["allow_auto_merge"])
        self.assertEqual(len(repo["rulesets"]), 1)
        ruleset = repo["rulesets"][0]
        self.assertEqual(ruleset["name"], "Factory default branch")
        checks = {
            check["context"]
            for check in ruleset["rules"][-1]["parameters"]["required_status_checks"]
        }
        self.assertEqual(checks, {"Lint", "Security", "Tests", "OCR"})
        self.assertEqual(
            repo["secret_values"]["OPENCODE_ZEN_API_KEY"], OP_VALUE
        )
        self.assertNotIn(OP_VALUE, result.stdout + result.stderr)
        self.assert_handoff(result.stdout)

    def test_second_run_is_idempotent(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        head = self.head("one")
        self.reset_logs()
        second = self.run_new("one")
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertIn("reusing ranokay/one", second.stdout)
        self.assertEqual(self.head("one"), head)
        self.assertFalse(
            any(call[:2] == ["repo", "create"] for call in self.gh_calls()), self.gh_calls()
        )
        self.assertFalse(any("push" in arguments for arguments in self.git_calls()), self.git_calls())
        self.assertEqual(self.state()["repos"]["ranokay/one"]["writes"], [])
        self.assertNotIn(OP_VALUE, second.stdout + second.stderr)

    def test_settings_failure_stops_before_the_handoff(self):
        result = self.run_new("drift-one", extra={"FAKE_MISSING_VENDOR": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("repository settings failed", result.stderr)
        self.assertNotIn("is ready", result.stdout)
        self.assertNotIn("hand-off", result.stdout)

    def test_an_unreadable_repository_is_not_mistaken_for_absent(self):
        result = self.run_new("one", extra={"FAKE_VIEW_ERROR": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot read ranokay/one", result.stderr)
        self.assertFalse(
            any(call[:2] == ["repo", "create"] for call in self.gh_calls()),
            self.gh_calls(),
        )

    def test_profile_chain_writes_every_include(self):
        result = self.run_new("web-one", "--profile", "web")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        clone = self.clone("web-one")
        self.assertIn("Profiles: universal, typescript, web", (clone / "AGENTS.md").read_text())
        mise = (clone / "mise.toml").read_text()
        for profile in ("universal", "typescript", "web"):
            self.assertIn(f"profiles/{profile}/mise.toml?ref={TEST_TAG}", mise)
            self.assertIn(f"profiles/{profile}/tasks.toml?ref={TEST_TAG}", mise)
            self.assertEqual(mise.count(f"profiles/{profile}/mise.toml"), 1)
            self.assertEqual(mise.count(f"profiles/{profile}/tasks.toml"), 1)
        self.assertNotIn(TEMPLATE_TAG, mise)
        self.assertIn(
            f"https://raw.githubusercontent.com/ranokay/factory/{TEST_TAG}/profiles/web/hk.pkl",
            (clone / "hk.pkl").read_text(),
        )

    def test_incompatible_profiles_are_refused(self):
        result = self.run_new("bad", "--profile", "typescript", "--profile", "go")
        self.assertEqual(result.returncode, 1)
        self.assertIn("one Profile chain", result.stderr)
        self.assertEqual(self.state()["repos"], {})

    def test_ensure_ideas_ignores_unused_repository_arguments(self):
        result = self.run_new("--ensure-ideas", "--profile", "bogus")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(len(self.state()["projects"]), 1)

    def test_ensure_ideas_is_idempotent(self):
        first = self.run_new("--ensure-ideas")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertIn("created the Ideas Project", first.stdout)
        self.reset_logs()
        second = self.run_new("--ensure-ideas")
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertIn("reusing the Ideas Project", second.stdout)
        self.assertEqual(len(self.state()["projects"]), 1)
        self.assertFalse(
            any(call[:2] == ["project", "create"] for call in self.gh_calls()),
            self.gh_calls(),
        )

    def test_idea_seeds_a_retry_before_the_rules_exist(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        state = self.state()
        state["repos"]["ranokay/one"]["rulesets"] = []
        self.write_state(state)
        self.seed_idea()
        result = self.run_new("one", "--idea", "PVTI_1")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("promoted", result.stdout)
        product = (self.clone("one") / "PRODUCT.md").read_text()
        self.assertIn("# Rocket", product)
        self.assertIn("Fly to Mars.", product)
        self.assertNotIn("# <repo>", product)
        self.assertEqual(len(self.state()["repos"]["ranokay/one"]["issues"]), 1)

    def test_a_missing_rule_list_still_seeds(self):
        self.seed_idea()
        result = self.run_new(
            "ruleless-one",
            "--idea",
            "PVTI_1",
            extra={"FAKE_RULES_404": "ranokay/ruleless-one"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("# Rocket", (self.clone("ruleless-one") / "PRODUCT.md").read_text())

    def test_idea_promotes_without_seeding_a_protected_repo(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        head = self.head("one")
        self.reset_logs()
        self.seed_idea()
        result = self.run_new("one", "--idea", "PVTI_1")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("requires a pull request", result.stdout)
        self.assertEqual(self.head("one"), head)
        self.assertIn("# <repo>", (self.clone("one") / "PRODUCT.md").read_text())
        self.assertFalse(any("push" in arguments for arguments in self.git_calls()))
        self.assertEqual(len(self.state()["repos"]["ranokay/one"]["issues"]), 1)

    def test_a_reused_clone_is_checked_out_to_main(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        swapped = self.git("one", "checkout", "-b", "feature")
        self.assertEqual(swapped.returncode, 0, swapped.stderr)
        result = self.run_new("one")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        branch = self.git("one", "branch", "--show-current").stdout.strip()
        self.assertEqual(branch, "main")

    def test_a_retry_pushes_a_local_commit_ahead_of_origin(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        (self.clone("one") / "local.txt").write_text("unpublished\n")
        self.git("one", "add", "local.txt")
        self.git("one", "commit", "-qm", "local unpublished seed")
        self.reset_logs()
        result = self.run_new("one")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(any("push" in arguments for arguments in self.git_calls()))

    def test_a_retry_publishes_a_seed_whose_push_failed(self):
        self.seed_idea()
        failed = self.run_new(
            "retry-one", "--idea", "PVTI_1", extra={"FAKE_FAIL_PUSH": "1"}
        )
        self.assertEqual(failed.returncode, 1)
        self.assertIn("push", failed.stderr)
        self.reset_logs()
        retry = self.run_new("retry-one", "--idea", "PVTI_1")
        self.assertEqual(retry.returncode, 0, retry.stderr + retry.stdout)
        self.assertTrue(any("push" in arguments for arguments in self.git_calls()))
        self.assertEqual(
            len(self.state()["repos"]["ranokay/retry-one"]["issues"]), 1
        )
        self.assertNotIn("# <repo>", (self.clone("retry-one") / "PRODUCT.md").read_text())

    def test_factory_root_selects_the_template(self):
        factory = self.root / "factory"
        shutil.copytree(ROOT / "project", factory / "project")
        (factory / "profiles").symlink_to(ROOT / "profiles", target_is_directory=True)
        agents = factory / "project/AGENTS.md"
        agents.write_text(
            agents.read_text().replace("# <repo>", "# <repo>\n\nTemplate marker.")
        )
        result = self.run_new("one", "--factory-root", str(factory))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Template marker.", (self.clone("one") / "AGENTS.md").read_text())

    def test_idea_seeds_and_promotes(self):
        self.seed_idea()
        result = self.run_new("--idea", "PVTI_1")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        state = self.state()
        repo = state["repos"]["ranokay/rocket"]
        self.assertEqual(repo["repo"]["description"], "Rocket")
        product = (self.clone("rocket") / "PRODUCT.md").read_text()
        self.assertIn("# Rocket", product)
        self.assertIn("Fly to Mars.", product)
        self.assertEqual(len(repo["issues"]), 1)
        self.assertEqual(repo["issues"][0]["title"], "Rocket")
        self.assertEqual(repo["issues"][0]["body"], "Fly to Mars.")
        self.assertEqual(state["items"][0]["content"]["type"], "Issue")
        self.assertIn("promoted", result.stdout)
        rerun = self.run_new("--idea", "PVTI_1")
        self.assertEqual(rerun.returncode, 0, rerun.stderr + rerun.stdout)
        self.assertIn("already promoted", rerun.stdout)
        self.assertEqual(len(self.state()["repos"]["ranokay/rocket"]["issues"]), 1)


if __name__ == "__main__":
    unittest.main()
