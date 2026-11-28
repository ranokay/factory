#!/usr/bin/env python3

from __future__ import annotations

import subprocess  # nosec B404  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local git fixtures
from run_guard_fixture import RunGuardFixture


class RunGuardTests(RunGuardFixture):
    def test_push_outside_a_recorder_repo_is_allowed(self):
        self.write_mise("echo no recorder")
        for command in (
            "git push",
            f"git -C {self.repo} push",
            "echo ready && git push",
            "cd; pwd",
            "popd; echo ok",
            "cd /tmp | pwd",
            "(cd /tmp); echo ok",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.output(self.invoke(command)), {})

    def test_wrapper_introspection_does_not_require_a_check_record(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        for command in (
            "command -v git push",
            "command -V git push",
            "command -pv git push",
            "command -p -v git push",
            "command --help git push",
            "command -p --help git push",
            "sudo -l git push",
            "sudo -v git push",
            "sudo -nl git push",
            "sudo -nv git push",
            "sudo --help git push",
            "env --help git push",
            "env --version git push",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.output(self.invoke(command)), {})

    def test_env_required_equals_options_reject_bare_forms(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        for command in (
            "env --argv0 git push",
            "env --env0-from envfile git push",
            "env --arg git push",
            "env --env envfile git push",
            "env --quoting-style shell git push",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.output(self.invoke(command)), {})

    def test_benign_dynamic_commands_are_allowed(self):
        for command in (
            '"$EDITOR" file',
            "$(which git) status",
            'env -S "$FLAGS git status"',
            'some-tool "$X" --reason y --unblock z',
            'echo "git push is guarded" "$X"',
            'echo "block-issue wording" "$X"',
            'git commit -m "gh pr merge --admin" "$X"',
            'gh pr view "$NUMBER"',
            'gh label pr --admin "$X"',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.output(self.invoke(command)), {})

    def test_dynamic_guarded_actions_are_denied(self):
        for command in (
            "env -S $'git push'",
            'env -S "${CMD} git push"',
            "env -S '${CMD} git push'",
            "$(which git) push",
            "$(which gh) pr merge 42 --admin",
            "CMD=push && git \"$CMD\"",
            "CMD='git push'; $CMD",
            "CMD='git push' bash -c '$CMD'",
            "FLAGS='git push'; env -S \"$FLAGS\"",
            "echo `git push`",
            'echo "$(git push)"',
            "eval 'git push'",
            "eval git push",
            "eval gh pr merge 42 --admin",
            "CMD='git push'; eval '$CMD'",
            "CMD='pr merge 42 --admin'; gh $CMD",
            "eval 'cd /other'; git push",
            'sh -c "echo ok && /usr/bin/git push"',
            'sh -c "echo ok && /usr/bin/gh -R ranokay/factory pr merge 42 --admin"',
            "CMD='gh pr merge 42 --admin'; $CMD",
            "gh pr \"$SUB\" --admin",
            "gh pr merge sync/x \"--admin=$T\"",
            "A=--admin; gh pr merge \"$A\"",
            "gh -R o/r pr merge 42 \"$A\"",
            "git \"pu\"\"sh\" \"$REF\"",
            "git -c \"$CFG\" push",
            "git \"$OPT\" commit",
            "export REF=\"$X\" && git push origin \"$REF\"",
            "GH=gh; ${GH} pr merge 42 --admin",
            'env -S "${GH} pr merge 42 --admin"',
            "$(which block-issue) o/one 6",
            "BI=block-issue; ${BI} o/one 6 --reason quota --unblock fallback",
            'env -S "${BI} o/one 6 --reason quota --unblock fallback"',
            "sudo -s 'echo hi; git push'",
            "nohup --help git push",
        ):
            with self.subTest(command=command):
                output = self.output(self.invoke(command))
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("dynamic command", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_shorthand_check_task_scope_is_respected(self):
        (self.repo / "mise.toml").write_text('[tasks]\ncheck = "echo no recorder"\n')
        self.git("add", "mise.toml")
        self.git("commit", "-qm", "shorthand check")
        self.assertEqual(self.output(self.invoke("git push")), {})
        (self.repo / "mise.toml").write_text('[tasks]\ncheck = "~/.agents/tools/run check_result -- true"\n')
        self.git("add", "mise.toml")
        self.git("commit", "-qm", "shorthand recorder")
        output = self.output(self.invoke("git push"))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_push_inside_a_recorder_repo_requires_a_pass_record(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        result = self.invoke("git push")
        output = self.output(result)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("mise run check", output["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn(self.git("rev-parse", "HEAD"), output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_unreadable_recorder_configuration_blocks_push(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        for text in (
            "not toml",
            'tasks = "invalid"\n',
            "[tasks.check]\nrun = 1979-05-27\n",
            "[tasks.check]\nrun = [1979-05-27]\n",
        ):
            with self.subTest(text=text):
                (self.repo / "mise.toml").write_text(text)
                output = self.output(self.invoke("git push"))
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("mise.toml", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_recorded_pass_allows_git_c_and_compound_pushes(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        recorded = self.record_check("true")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        for command in (
            "git push",
            f"git -C {self.repo} push",
            "echo ready && git push",
            'git log --grep="$MSG" && git push',
            'git -c key="$VALUE" && git push',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.output(self.invoke(command)), {})

    def test_git_c_and_compound_pushes_require_a_pass_record(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        self.env["RUN_GUARD_REPO"] = str(self.repo)
        other = self.root / "other"
        other.mkdir()
        for command in (
            f"git -C {self.repo} push",
            "git --namespace=foo push",
            "git --config-env=X=Y push",
            "git --no-pager -c k=v push",
            "echo ready && git push",
            "cd /missing || git push",
            "cd /tmp | git push",
            "(cd /tmp); git push",
            '(cd "$RUN_GUARD_REPO"; git push)',
            "echo x | cd /tmp && git push",
            f"git -C {self.repo} -C . push",
            f"cd -P {self.repo} && git push",
            f"cd -LP {self.repo} && git push",
            f"cd -- {self.repo} && git push",
            'cd "$RUN_GUARD_REPO" && git push',
            'git -C "$RUN_GUARD_REPO" push',
            "command -p git push",
            "command -- git push",
            "exec git push",
            "exec -a foo git push",
            "exec -cl git push",
            "sudo -u root git push",
            "sudo -nu root git push",
            "sudo -uroot git push",
            "sudo -nuroot git push",
            "sudo --user root git push",
            "sudo --user=root git push",
            "sudo --us root git push",
            f"sudo --chd {self.repo} git push",
            f"sudo -D {self.repo} git push",
            f"sudo --chdir={self.repo} git push",
            "sudo -n -- git push",
            "sudo -s 'git push'",
            "sudo -i 'git push'",
            "sudo -s -- 'git push'",
            "sudo --she 'git push'",
            "sudo --logi 'git push'",
            "env -S 'git push'",
            "env -S'git push'",
            "env --split-string='git push'",
            "env --split 'git push --force'",
            "env --split='git push'",
            "env --unse FOO git push",
            "env --unse=FOO git push",
            "env --ign git push",
            "env -vS'git push'",
            "env -ivS'git push'",
            "env -S 'git push\\c'",
            "env -S'git push\\c'",
            "env --split-string='git push\\c'",
            "env -vS'git push\\c'",
            "env -S 'git push\\c\\'",
            "env -S 'X=\\\\c git push'",
            "env -S \"'X=\\c' git push\"",
            "env -S '\"X=\\c\" git push'",
            "echo ok; env -S 'x\\'; git push",
            "echo ok; env -S'x\\'; git push",
            "echo ok; env --split-string='x\\'; git push",
            "echo ok; env -vS 'x\\'; git push",
            "env -i git push",
            "env -v git push",
            "env -iv git push",
            "env -0 git push",
            "env -u FOO git push",
            "env -uFOO git push",
            "env -uS git push",
            "env -iu FOO git push",
            "env -iuFOO git push",
            f"env -C {self.repo} git push",
            f"env -C{self.repo} git push",
            "env -P /usr/bin git push",
            "env -P/usr/bin git push",
            "env --unset FOO git push",
            "env --unset=FOO git push",
            "env --argv0=git git push",
            "env --env0-from=envfile git push",
            "env --arg=git git push",
            "env --env=envfile git push",
            "env -a git git push",
            f"env --chdir {self.repo} git push",
            f"env --chdir={self.repo} git push",
            f"env --chd {self.repo} git push",
            f"env --chd={self.repo} git push",
            "env --block-signal=TERM git push",
            "env --list-signal-handling git push",
            "env -- git push",
            "env FOO=bar git push",
            "2>/tmp/push.log git push",
            "1>push.log git push",
            "if true; then git push; fi",
            "for item in one; do git push; done",
            "bash -lc 'git push'",
        ):
            with self.subTest(command=command):
                output = self.output(
                    self.invoke(
                        command,
                        cwd=other
                        if "-C ." in command or '(cd "$RUN_GUARD_REPO";' in command or "cd -LP" in command
                        else self.repo,
                    )
                )
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_wrapper_directory_push_checks_the_destination_record(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        recorded = self.record_check("true")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        other = self.root / "other"
        other.mkdir()
        subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local git fixture
            ["git", "init", "-q", str(other)],
            check=True,
        )
        subprocess.run(["git", "-C", str(other), "config", "user.name", "Run guard test"], check=True)  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local git fixture
        subprocess.run(["git", "-C", str(other), "config", "user.email", "run-guard@example.invalid"], check=True)  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local git fixture
        (other / "README").write_text("fixture\n")
        subprocess.run(["git", "-C", str(other), "add", "README"], check=True)  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local git fixture
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "baseline"], check=True)  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local git fixture
        (other / "mise.toml").write_text('[tasks.check]\nrun = "~/.agents/tools/run check_result -- true"\n')
        for command in (
            f"env -C {other} git push",
            f"env --chdir={other} git push",
            f"sudo -D {other} git push",
            f"sudo --chdir={other} git push",
            f"pushd {other}; git push",
        ):
            with self.subTest(command=command):
                output = self.output(self.invoke(command))
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("no passing mise run check record", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_push_rejects_unresolved_repository_and_ref_forms(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        other = self.root / "other"
        other.mkdir()
        for command in (
            f"git --git-dir {self.repo}/.git push",
            f"git --git-dir={self.repo}/.git push",
            f"git --work-tree {self.repo} push",
            f"GIT_DIR={self.repo}/.git git push",
            f"GIT_DIR={self.repo}/.git+=x git push",
            f"export GIT_DIR={self.repo}/.git; git push",
            f'DEST={self.repo}; cd "$DEST"; git push',
            "git push --all",
            "git push origin --tags",
            "git push origin main :refs/heads/other",
            "git push origin :refs/heads/other main",
            "cd; git push",
            "cd -; git push",
        ):
            with self.subTest(command=command):
                output = self.output(self.invoke(command, cwd=other))
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_deletion_push_requires_a_pass_record(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        for command in ("git push --delete origin x", "git push origin :refs/heads/other"):
            with self.subTest(command=command):
                output = self.output(self.invoke(command))
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("no passing mise run check record", output["hookSpecificOutput"]["permissionDecisionReason"])
        recorded = self.record_check("true")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.assertEqual(self.output(self.invoke("git push --delete origin x")), {})

    def test_push_refspec_requires_a_record_for_the_pushed_commit(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        recorded = self.record_check("true")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.git("checkout", "-qb", "unchecked")
        self.git("commit", "--allow-empty", "-m", "unchecked")
        self.git("checkout", "-")
        output = self.output(self.invoke("git push origin unchecked"))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("unchecked", output["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(self.output(self.invoke("git push origin HEAD")), {})
        self.assertEqual(self.output(self.invoke("git push origin x --delete")), {})
        self.assertEqual(self.output(self.invoke("git push --delete origin x")), {})

    def test_head_changing_command_before_push_requires_a_fresh_check(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        recorded = self.record_check("true")
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        for command in (
            "git commit --allow-empty -m next && git push",
            "nohup git commit --allow-empty -m next && git push",
            "git --$OPT commit && git push",
        ):
            with self.subTest(command=command):
                output = self.output(self.invoke(command))
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("HEAD-changing", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_recorded_red_check_blocks_push(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        recorded = self.record_check("sh", "-c", "exit 9")
        self.assertEqual(recorded.returncode, 9)
        output = self.output(self.invoke("git push"))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

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
        for command in ("gh pr merge --admin", "gh pr -R o/one merge 42 --admin"):
            with self.subTest(command=command):
                output = self.output(self.invoke(command))
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
            ("cd; gh pr merge 42 --admin", "bare"),
            ("cd -; gh pr merge 42 --admin", "previous-directory"),
        ):
            with self.subTest(command=command):
                output = self.output(self.invoke(command))
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn(message, output["hookSpecificOutput"]["permissionDecisionReason"])

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

    def test_plain_merge_does_not_query_github(self):
        self.gh_stub(head="feature/example", topics="")
        for command in (
            "gh pr merge 42 --merge",
            'gh pr merge 42 --merge --body "$BODY"',
            'gh pr merge 42 --merge --body="$BODY"',
            'gh pr merge "$PR" --merge',
            "gh pr merge $PR --merge",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.output(self.invoke(command)), {})
        self.assertFalse((self.root / "gh.log").exists())


if __name__ == "__main__":
    import unittest

    unittest.main()
