from new_test_support import NewCase, TEMPLATE_TAG, TEST_TAG

__all__ = ["NewProfilesTests"]


class NewProfilesTests(NewCase):
    def test_an_unreachable_tag_is_not_pinned(self):
        result = self.run_new(
            "one", extra={"FAKE_UNREACHABLE_TAG": "v9.9.10"}
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        mise = (self.clone("one") / "mise.toml").read_text()
        self.assertIn(f"profiles/universal/mise.toml?ref={TEST_TAG}", mise)
        self.assertNotIn("v9.9.10", mise)

    def test_a_tagless_checkout_falls_back_to_the_template_pin(self):
        result = self.run_new(
            "fallback-one", extra={"FAKE_TAG": "", "FAKE_NO_TAGS": "1"}
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        mise = (self.clone("fallback-one") / "mise.toml").read_text()
        self.assertIn(f"profiles/universal/mise.toml?ref={TEMPLATE_TAG}", mise)
        self.assertNotIn(TEST_TAG, mise)

    def test_second_run_keeps_existing_pins_when_the_release_moves(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        head = self.head("one")
        second = self.run_new("one", extra={"FAKE_TAG": "v9.9.10"})
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(self.head("one"), head)
        mise = (self.clone("one") / "mise.toml").read_text()
        self.assertIn(f"profiles/universal/mise.toml?ref={TEST_TAG}", mise)
        self.assertNotIn("v9.9.10", mise)
        self.assertEqual(mise.count("profiles/universal/mise.toml"), 1)
        self.assertIn(TEST_TAG, (self.clone("one") / "hk.pkl").read_text())

    def test_a_missing_hk_keeps_the_existing_pin(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        hk = self.clone("one") / "hk.pkl"
        hk.unlink()
        self.git("one", "add", "-A")
        self.git("one", "commit", "-qm", "remove hk")
        result = self.run_new("one", extra={"FAKE_TAG": "v9.9.10"})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        mise = (self.clone("one") / "mise.toml").read_text()
        self.assertIn(f"profiles/universal/mise.toml?ref={TEST_TAG}", mise)
        self.assertNotIn("v9.9.10", mise)
        self.assertIn(TEST_TAG, hk.read_text())

    def test_include_comments_survive_a_rerun(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        entry = (
            "git::https://github.com/ranokay/factory.git"
            f"//profiles/universal/mise.toml?ref={TEST_TAG}"
        )
        self.edit_clone("one", "mise.toml", f'  "{entry}",', f"  '{entry}', # keep this")
        result = self.run_new("one", extra={"FAKE_TAG": "v9.9.10"})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        mise = (self.clone("one") / "mise.toml").read_text()
        self.assertIn("# keep this", mise)
        self.assertEqual(mise.count("profiles/universal/mise.toml"), 1)
        self.assertNotIn("v9.9.10", mise)

    def test_an_unsupported_pin_with_a_comment_is_not_rewritten(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        entry = (
            "git::https://github.com/ranokay/factory.git"
            f"//profiles/universal/mise.toml?ref={TEST_TAG}"
        )
        self.edit_clone(
            "one",
            "mise.toml",
            f'  "{entry}",\n',
            f'  "{entry}", "other", # keep this\n',
        )
        result = self.run_new("one")
        self.assertEqual(result.returncode, 1)
        self.assertIn("without deleting a comment", result.stderr)
        self.assertIn("# keep this", (self.clone("one") / "mise.toml").read_text())
        self.assertEqual(self.git("one", "status", "--porcelain").stdout, "")

    def test_an_alternate_hk_url_at_the_same_tag_is_kept(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.edit_clone(
            "one",
            "hk.pkl",
            f"https://raw.githubusercontent.com/ranokay/factory/{TEST_TAG}",
            f"https://github.com/ranokay/factory/raw/{TEST_TAG}",
        )
        result = self.run_new("one", extra={"FAKE_TAG": "v9.9.10"})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        hk = (self.clone("one") / "hk.pkl").read_text()
        self.assertIn(f"https://github.com/ranokay/factory/raw/{TEST_TAG}", hk)

    def test_an_unpinned_include_is_replaced_once(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.edit_clone("one", "mise.toml", f"?ref={TEST_TAG}", "")
        result = self.run_new("one")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        mise = (self.clone("one") / "mise.toml").read_text()
        self.assertEqual(mise.count("profiles/universal/mise.toml"), 1)
        self.assertEqual(mise.count("profiles/universal/tasks.toml"), 1)
        self.assertNotIn('profiles/universal/mise.toml"', mise)
        self.assertNotIn('profiles/universal/tasks.toml"', mise)

    def test_divergent_hk_and_mise_pins_fail_loudly(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.edit_clone(
            "one",
            "hk.pkl",
            f"/{TEST_TAG}/profiles/universal/hk.pkl",
            "/v9.9.10/profiles/universal/hk.pkl",
        )
        result = self.run_new("one")
        self.assertEqual(result.returncode, 1)
        self.assertIn("align them", result.stderr)

    def test_a_divergent_hk_pin_leaves_the_clone_clean(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.edit_clone(
            "one",
            "hk.pkl",
            f"/{TEST_TAG}/profiles/universal/hk.pkl",
            "/v9.9.10/profiles/universal/hk.pkl",
        )
        result = self.run_new("one")
        self.assertEqual(result.returncode, 1)
        self.assertIn("align them", result.stderr)
        self.assertEqual(self.git("one", "status", "--porcelain").stdout, "")

    def test_an_hk_pin_fills_a_missing_mise_pin(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.edit_clone("one", "mise.toml", f"?ref={TEST_TAG}", "")
        result = self.run_new("one", extra={"FAKE_TAG": "v9.9.10"})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        mise = (self.clone("one") / "mise.toml").read_text()
        self.assertEqual(mise.count("profiles/universal/mise.toml"), 1)
        self.assertIn(f"profiles/universal/mise.toml?ref={TEST_TAG}", mise)
        self.assertNotIn("v9.9.10", mise)
