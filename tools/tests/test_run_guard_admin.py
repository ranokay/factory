#!/usr/bin/env python3

from __future__ import annotations

from run_guard_fixture import RunGuardFixture


class RunGuardAdminTests(RunGuardFixture):
    def test_admin_merge_all_selector_forms_pass_for_a_sync_pr_on_a_personal_fork(self):
        self.gh_stub()
        cases = (
            ("gh pr merge --admin", None),
            ("gh pr merge 42 --admin", "42"),
            ("gh pr merge 42 --admin=1", "42"),
            ("gh pr merge https://github.com/o/one/pull/42 --admin", "https://github.com/o/one/pull/42"),
            ("gh pr merge sync/upstream-2026-11-28 --admin", "sync/upstream-2026-11-28"),
            ("gh -R o/one pr merge 42 --admin", "42"),
            ("gh pr merge 42 -R o/one --admin", "42"),
            ("gh pr merge -R o/one 42 --admin", "42"),
            ("gh pr merge -t subject 42 --admin", "42"),
            ("gh pr merge -b body 42 --admin", "42"),
            ("gh pr merge -F body.md 42 --admin", "42"),
            ("gh pr merge -A author@example.invalid 42 --admin", "42"),
            ('gh pr merge 42 --admin --body "$BODY"', "42"),
        )
        for command, selector in cases:
            with self.subTest(command=command):
                self.assertEqual(self.output(self.invoke(command)), {})
                logged = [line for line in (self.root / "gh.log").read_text().splitlines() if " pr view " in line][-1]
                expected = f"pr view {selector} " if selector else "pr view --json"
                self.assertIn(expected, logged)

    def test_admin_merge_is_denied_for_a_feature_branch(self):
        self.gh_stub(head="feature/example")
        for command in ("gh pr merge --admin", "gh -R o/one pr merge 42 --admin"):
            with self.subTest(command=command):
                output = self.output(self.invoke(command))
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("sync/upstream-", output["hookSpecificOutput"]["permissionDecisionReason"])
        output = self.output(self.invoke("gh pr merge 42 --match-head-commit --admin"))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("sync/upstream-", output["hookSpecificOutput"]["permissionDecisionReason"])
        for flag in ("--admin", "--admin=true", "--admin=1", "--admin=t", "--admin=TRUE"):
            with self.subTest(flag=flag):
                output = self.output(self.invoke(f"gh pr merge 42 {flag}"))
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("sync/upstream-", output["hookSpecificOutput"]["permissionDecisionReason"])
                self.assertIn("without --admin", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_admin_merge_is_denied_without_the_personal_fork_topic(self):
        self.gh_stub(topics="")
        output = self.output(self.invoke("gh pr merge 42 --admin"))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("factory-personal-fork", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_admin_merge_rejects_environment_repository_and_directory_changes(self):
        self.gh_stub()
        for command, message in (
            ("GH_REPO=victim/repo gh pr merge 42 --admin", "GH_REPO"),
            ("env GH_REPO=victim/repo gh pr merge 42 --admin", "GH_REPO"),
            ("export GH_REPO=victim/repo; gh pr merge 42 --admin", "GH_REPO"),
            ("export GH_REPO+=victim/repo; gh pr merge 42 --admin", "GH_REPO"),
            ("declare -x GH_REPO+=victim/repo; gh pr merge 42 --admin", "GH_REPO"),
            ("env GH_REPO+=victim/repo gh pr merge 42 --admin", "GH_REPO"),
            ("declare -x GH_REPO=victim/repo; gh pr merge 42 --admin", "GH_REPO"),
            ("typeset -x GH_REPO=victim/repo; gh pr merge 42 --admin", "GH_REPO"),
            ("set -a; GH_REPO=victim/repo; set +a; gh pr merge 42 --admin", "GH_REPO"),
            ("GH_REPO=victim/repo; gh pr merge 42 --admin", "GH_REPO"),
            ("GH_REPO=o/r+=x gh pr merge 42 --admin", "GH_REPO"),
            ("(cd /tmp); gh pr merge 42 --admin", "subshell"),
            ("cd /tmp & gh pr merge 42 --admin", "background or pipeline"),
            ("cd /tmp | gh pr merge 42 --admin", "background or pipeline"),
            ("cd /ineligible && false && cd /eligible; gh pr merge 42 --admin", "multiple directory changes"),
            ("pushd /plain /recorder; gh pr merge 42 --admin", "directory stack"),
            ("cd; gh pr merge 42 --admin", "bare"),
            ("cd -; gh pr merge 42 --admin", "previous-directory"),
        ):
            with self.subTest(command=command):
                output = self.output(self.invoke(command))
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn(message, output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_cdpath_relative_admin_merge_is_rejected(self):
        self.gh_stub()
        other = self.root / "other"
        other.mkdir()
        self.env["CDPATH"] = str(self.root)
        output = self.output(self.invoke("cd repo; gh pr merge 42 --admin", cwd=other))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("CDPATH", output["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertFalse((self.root / "gh.log").exists())

    def test_later_admin_merge_is_checked(self):
        self.gh_stub()
        command = "gh pr merge 42 --admin; GH_REPO=victim/repo gh pr merge 43 --admin"
        output = self.output(self.invoke(command))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("GH_REPO", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_malformed_admin_topics_block(self):
        self.gh_stub(topics_json='{"repositoryTopics":"invalid"}')
        output = self.output(self.invoke("gh pr merge 42 --admin"))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("repositoryTopics", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_admin_merge_uses_the_changed_checkout(self):
        self.gh_stub()
        fork = self.root / "fork"
        fork.mkdir()
        self.assertEqual(self.output(self.invoke(f"cd {fork} && gh pr merge 42 --admin")), {})
        self.assertIn(f"cwd={fork.resolve()} ", (self.root / "gh.log").read_text())

    def test_pushd_without_change_verifies_the_current_admin_checkout(self):
        self.gh_stub()
        other = self.root / "other"
        other.mkdir()
        self.assertEqual(self.output(self.invoke(f"pushd -n {other}; gh pr merge 42 --admin")), {})
        logged = [line for line in (self.root / "gh.log").read_text().splitlines() if " pr view " in line][-1]
        self.assertIn(f"cwd={self.repo.resolve()} ", logged)
        self.assertNotIn(f"cwd={other.resolve()} ", logged)

    def test_plain_merge_does_not_query_github(self):
        self.gh_stub(head="feature/example", topics="")
        for command in (
            "gh pr merge 42 --merge",
            'gh pr merge 42 --merge --body "$BODY"',
            'gh pr merge 42 --merge --body="$BODY"',
            'gh pr merge "$PR" --merge',
            "gh pr merge $PR --merge",
            "gh pr merge 42 --merge --body '--admin'",
            "gh pr merge 42 --merge --body=--admin",
            "gh pr merge 42 --merge -b --admin",
            "gh pr merge 42 --merge -F --admin",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.output(self.invoke(command)), {})
        self.assertFalse((self.root / "gh.log").exists())


if __name__ == "__main__":
    import unittest

    unittest.main()
