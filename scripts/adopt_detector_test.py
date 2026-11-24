import os

from adopt_test_support import AdoptCase


class AdoptDetectorTests(AdoptCase):
    def test_brand_master_suppresses_the_brand_issue(self):
        master = self.project / "assets/brand/icon.svg"
        master.parent.mkdir(parents=True)
        master.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"></svg>\n')
        self.commit("add brand master")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        issues = self.issues()
        self.assertEqual(len(issues), 7)
        self.assertNotIn("brand", {category for issue in issues for category in self.categories(issue)})

    def test_comment_key_uses_the_banned_marker_not_the_prose(self):
        note = self.project / "src/note.ts"
        note.write_text("// NOTE: TODO later\n")
        self.commit("add note")
        first = self.run_adopt()
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        comments = [
            issue for issue in self.issues() if "comments" in self.categories(issue)
        ]
        self.assertEqual(len(comments), 1)
        self.assertEqual(len(self.markers(comments[0])), 2)
        self.assertIn("src/note.ts", comments[0]["body"])
        note.write_text("// REMARK: TODO later\n")
        self.commit("reword note")
        self.reset_log()
        second = self.run_adopt()
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertIn("no new findings", second.stdout)
        self.no_creates()

    def test_local_includes_only_pass_for_the_factory_repo(self):
        (self.project / "mise.toml").write_text(
            'include = ["./profiles/universal/mise.toml"]\n\n'
            '[task_config]\nincludes = ["profiles/universal/tasks.toml"]\n'
        )
        (self.project / "hk.pkl").write_text('amends "profiles/universal/hk.pkl"\n')
        (self.project / "renovate.json").write_text(
            '{"extends": ["github>ranokay/factory"]}\n'
        )
        self.commit("add local Factory includes")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        tooling = self.issue_for(self.issues(), "tooling")
        self.assertEqual(len(self.markers(tooling)), 2)
        self.use_factory_state("Ranokay/Factory")
        result = self.run_adopt(repo="Ranokay/Factory")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        issues = self.issues()
        self.assertEqual(len(issues), 7)
        self.assertNotIn(
            "tooling",
            {category for issue in issues for category in self.categories(issue)},
        )

    def test_local_include_must_name_a_profile_file(self):
        for include in (
            "./profiles/universal/README.md",
            "./profiles/nope/mise.toml",
            "./profiles/universal/tasks.toml",
        ):
            with self.subTest(include=include):
                (self.project / "mise.toml").write_text(
                    f'include = ["{include}"]\n\n'
                    '[task_config]\nincludes = ["./profiles/universal/tasks.toml"]\n'
                )
                (self.project / "hk.pkl").write_text(
                    'amends "profiles/universal/hk.pkl"\n'
                )
                (self.project / "renovate.json").write_text(
                    '{"extends": ["github>ranokay/factory"]}\n'
                )
                self.commit(f"add local include {include}")
                self.use_factory_state()
                result = self.run_adopt(repo="ranokay/factory")
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                tooling = self.issue_for(self.issues(), "tooling")
                self.assertEqual(len(self.markers(tooling)), 1)
                self.assertIn("mise.toml", tooling["body"])

    def test_unrelated_svg_does_not_suppress_the_brand_issue(self):
        master = self.project / "assets/brand/export.svg"
        master.parent.mkdir(parents=True)
        master.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"></svg>\n')
        self.commit("add an export")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        issues = self.issues()
        self.assertEqual(len(issues), 8)
        self.assertIn(
            "brand",
            {category for issue in issues for category in self.categories(issue)},
        )

    def test_wordmark_only_still_requests_an_icon(self):
        master = self.project / "assets/brand/wordmark.svg"
        master.parent.mkdir(parents=True)
        master.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 16"></svg>\n')
        self.commit("add a wordmark")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        issues = self.issues()
        self.assertEqual(len(issues), 8)
        self.assertIn("icon", self.issue_for(issues, "brand")["body"])

    def test_string_includes_are_accepted(self):
        (self.project / "mise.toml").write_text(
            'include = "git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=v0.2.0"\n\n'
            '[task_config]\nincludes = "git::https://github.com/ranokay/factory.git//profiles/universal/tasks.toml?ref=v0.2.0"\n'
        )
        (self.project / "hk.pkl").write_text(
            'amends "https://raw.githubusercontent.com/ranokay/factory/v0.2.0/profiles/universal/hk.pkl"\n'
        )
        (self.project / "renovate.json").write_text(
            '{"extends": ["github>ranokay/factory"]}\n'
        )
        self.commit("add string includes")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        issues = self.issues()
        self.assertEqual(len(issues), 7)
        self.assertNotIn(
            "tooling",
            {category for issue in issues for category in self.categories(issue)},
        )

    def test_git_and_refs_includes_are_accepted(self):
        include = "git::https://github.com/Ranokay/Factory.git//profiles/universal/mise.toml?ref=v0.2.0"
        tasks = "git::https://github.com/Ranokay/Factory.git//profiles/universal/tasks.toml?ref=v0.2.0"
        hk = "https://raw.githubusercontent.com/Ranokay/Factory/refs/tags/v0.2.0/profiles/universal/hk.pkl"
        self.write_tooling([include], [tasks], hk)
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        issues = self.issues()
        self.assertEqual(len(issues), 7)
        self.assertNotIn(
            "tooling",
            {category for issue in issues for category in self.categories(issue)},
        )

    def test_ssh_factory_includes_are_accepted(self):
        include = "git::ssh://git@github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=v0.2.0"
        tasks = "git::ssh://git@github.com/ranokay/factory.git//profiles/universal/tasks.toml?ref=v0.2.0"
        hk = "https://raw.githubusercontent.com/ranokay/factory/v0.2.0/profiles/universal/hk.pkl"
        self.write_tooling([include], [tasks], hk)
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        issues = self.issues()
        self.assertEqual(len(issues), 7)
        self.assertNotIn(
            "tooling",
            {category for issue in issues for category in self.categories(issue)},
        )

    def test_factory_include_must_name_a_profile_file(self):
        include = "git::https://github.com/ranokay/factory.git//profiles/universal/README.md?ref=v0.2.0"
        tasks = "git::https://github.com/ranokay/factory.git//profiles/universal/tasks.toml?ref=v0.2.0"
        hk = "https://raw.githubusercontent.com/ranokay/factory/v0.2.0/profiles/universal/hk.pkl"
        self.write_tooling([include], [tasks], hk)
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        tooling = self.issue_for(self.issues(), "tooling")
        self.assertEqual(len(self.markers(tooling)), 1)
        self.assertIn("mise.toml", tooling["body"])

    def test_blob_and_tree_includes_are_findings(self):
        hk = "https://raw.githubusercontent.com/ranokay/factory/v0.2.0/profiles/universal/hk.pkl"
        tasks = "git::https://github.com/ranokay/factory.git//profiles/universal/tasks.toml?ref=v0.2.0"
        mise = "git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=v0.2.0"
        for includes, task_values in (
            (
                ["https://github.com/ranokay/factory/blob/v0.2.0/profiles/universal/mise.toml"],
                [tasks],
            ),
            (
                [mise],
                ["https://github.com/ranokay/factory/tree/v0.2.0/profiles/universal/tasks.toml"],
            ),
        ):
            with self.subTest(includes=includes, tasks=task_values):
                self.write_tooling(includes, task_values, hk)
                result = self.run_adopt()
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                tooling = self.issue_for(self.issues(), "tooling")
                self.assertIn("mise.toml", tooling["body"])
                self.reset_state()
                self.reset_log()

    def test_binary_reference_is_skipped_without_withholding_dead_code(self):
        (self.project / "assets").mkdir()
        (self.project / "assets/logo.png").write_bytes(
            b"\x89PNG\r\n\x1a\n\x00" + b"x" * 2_000_000
        )
        self.commit("add a binary")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn("dead-code detection skipped", result.stderr)
        issues = self.issues()
        self.assertEqual(len(issues), 8)
        self.assertIn(
            "dead-code",
            {category for issue in issues for category in self.categories(issue)},
        )
        self.assertIn("src/unused.ts", self.issue_for(issues, "dead-code")["body"])

    def test_verification_skill_without_mise_does_not_abort(self):
        skill = self.project / ".agents/skills/verify-counter"
        (skill / "references/features").mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: verify-counter\ndescription: fixture\n---\n"
        )
        (skill / "references/features/feature.md").write_text("feature\n")
        link = self.project / ".claude/skills/verify-counter"
        link.parent.mkdir(parents=True)
        os.symlink("../../.agents/skills/verify-counter", link)
        self.commit("add a verification skill")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        issues = self.issues()
        self.assertEqual(
            {category for issue in issues for category in self.categories(issue)},
            {
                "tooling",
                "comments",
                "dead-code",
                "agents",
                "stale-files",
                "test-pruning",
                "brand",
            },
        )

    def test_unpinned_factory_includes_are_a_finding(self):
        (self.project / "mise.toml").write_text(
            'include = ["git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=main"]\n\n'
            '[task_config]\nincludes = ["git::https://github.com/ranokay/factory.git//profiles/universal/tasks.toml?ref=main"]\n'
        )
        (self.project / "hk.pkl").write_text(
            'amends "https://raw.githubusercontent.com/ranokay/factory/main/profiles/universal/hk.pkl"\n'
        )
        (self.project / "renovate.json").write_text(
            '{"extends": ["github>ranokay/factory"]}\n'
        )
        self.commit("add unpinned includes")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        tooling = self.issue_for(self.issues(), "tooling")
        self.assertIn("mise.toml", tooling["body"])
        self.assertIn("hk.pkl", tooling["body"])
        self.assertNotIn("renovate.json is not extending", tooling["body"])
        self.assertEqual(len(self.markers(tooling)), 2)

    def test_mixed_pinned_and_unpinned_includes_are_a_finding(self):
        pinned = "git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=v0.2.0"
        unpinned = "git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=main"
        tasks = "git::https://github.com/ranokay/factory.git//profiles/universal/tasks.toml?ref=v0.2.0"
        hk = "https://raw.githubusercontent.com/ranokay/factory/v0.2.0/profiles/universal/hk.pkl"
        self.write_tooling([pinned, unpinned], [tasks], hk)
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        tooling = self.issue_for(self.issues(), "tooling")
        self.assertEqual(len(self.markers(tooling)), 1)
        self.assertIn("mise.toml", tooling["body"])
        self.assertNotIn("hk.pkl is not amending", tooling["body"])

    def test_prerelease_factory_tag_is_accepted(self):
        include = "git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=v0.2.0-rc.1"
        tasks = "git::https://github.com/ranokay/factory.git//profiles/universal/tasks.toml?ref=v0.2.0-rc.1"
        hk = "https://raw.githubusercontent.com/ranokay/factory/v0.2.0-rc.1/profiles/universal/hk.pkl"
        self.write_tooling([include], [tasks], hk)
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        issues = self.issues()
        self.assertEqual(len(issues), 7)
        self.assertNotIn(
            "tooling",
            {category for issue in issues for category in self.categories(issue)},
        )

    def test_short_ref_is_not_a_factory_tag(self):
        include = "git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=v1"
        tasks = "git::https://github.com/ranokay/factory.git//profiles/universal/tasks.toml?ref=v1"
        hk = "https://raw.githubusercontent.com/ranokay/factory/v1/profiles/universal/hk.pkl"
        self.write_tooling([include], [tasks], hk)
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        tooling = self.issue_for(self.issues(), "tooling")
        self.assertEqual(len(self.markers(tooling)), 2)
        self.assertIn("mise.toml", tooling["body"])
        self.assertIn("hk.pkl", tooling["body"])

    def test_malformed_tooling_configs_are_findings(self):
        (self.project / "mise.toml").write_text("include = 1\n")
        (self.project / "renovate.json").write_text("[]\n")
        self.commit("add malformed configs")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        tooling = self.issue_for(self.issues(), "tooling")
        self.assertIn("mise.toml", tooling["body"])
        self.assertIn("renovate.json", tooling["body"])

    def test_unreadable_reference_file_warns_and_continues(self):
        (self.project / "huge.txt").write_text("x" * 2_000_001)
        self.commit("add a huge reference")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("skipping it as a reference source", result.stderr)
        self.assertIn("dead-code detection skipped", result.stderr)
        issues = self.issues()
        self.assertEqual(len(issues), 7)
        self.assertNotIn(
            "dead-code",
            {category for issue in issues for category in self.categories(issue)},
        )

    def test_broken_reference_symlink_withholds_dead_code(self):
        os.symlink("missing.txt", self.project / "broken.txt")
        self.commit("add a broken symlink")
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("cannot read broken.txt", result.stderr)
        self.assertIn("dead-code detection skipped", result.stderr)
        issues = self.issues()
        self.assertEqual(len(issues), 7)
        self.assertNotIn(
            "dead-code",
            {category for issue in issues for category in self.categories(issue)},
        )

