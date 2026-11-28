#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 -- the fixture drives the local guard and git
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
GUARD = TOOLS / "run_guard.py"


class RunGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = dict(os.environ, XDG_CACHE_HOME=str(self.root / "cache"))
        self.git("init", "-q")
        self.git("config", "user.name", "Run guard test")
        self.git("config", "user.email", "run-guard@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "README").write_text("fixture\n")
        self.git("add", "README")
        self.git("commit", "-qm", "baseline")

    def git(self, *args: str) -> str:
        result = subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local git with an argument list
            ["git", "-C", str(self.repo), *args],
            text=True,
            capture_output=True,
            check=True,
            env=self.env,
        )
        return result.stdout.strip()

    def invoke(self, command: str, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- this checkout's guard
            [sys.executable, str(GUARD), "tool"],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd or self.repo)}),
            text=True,
            capture_output=True,
            check=False,
            cwd=cwd or self.repo,
            env=self.env,
        )

    def invoke_mode(self, mode: str, payload: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- this checkout's guard
            [sys.executable, str(GUARD), mode],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            cwd=self.repo,
            env=self.env,
        )

    def output(self, result: subprocess.CompletedProcess[str]) -> dict:
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else {}

    def write_mise(self, command: str):
        (self.repo / "mise.toml").write_text(f'[tasks.check]\nrun = {json.dumps(command)}\n')
        self.git("add", "mise.toml")
        self.git("commit", "-qm", "check task")

    def test_push_outside_a_recorder_repo_is_allowed(self):
        self.write_mise("echo no recorder")
        for command in ("git push", f"git -C {self.repo} push", "echo ready && git push"):
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
            "gh pr \"$SUB\" --admin",
            "gh pr merge sync/x \"--admin=$T\"",
            "A=--admin; gh pr merge \"$A\"",
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

    def record_check(self, *command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- this checkout's recorder
            [sys.executable, str(TOOLS / "check_result.py"), "--", *command],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
            env=self.env,
        )

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

    def gh_stub(self, *, head: str = "sync/upstream-2026-11-28", topics: str = "factory-personal-fork"):
        binary = self.root / "bin"
        binary.mkdir(exist_ok=True)
        script = binary / "gh"
        script.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >>\"$GH_LOG\"\n"
            "case \"$1 $2\" in\n"
            "  'pr view') [ -z \"${GH_PR_FAIL:-}\" ] || { printf '%s\\n' \"${GH_PR_ERROR:-no pull requests found for branch}\" >&2; exit 1; }; printf '{\"number\":42,\"headRefName\":\"%s\",\"url\":\"https://github.com/o/one/pull/42\"}\\n' \"$GH_HEAD\" ;;\n"
            "  'pr checks') printf '%s\\n' \"$GH_CHECKS\"; exit \"$GH_CHECKS_CODE\" ;;\n"
            "  'repo view') printf '{\"repositoryTopics\":[{\"name\":\"%s\"}]}\\n' \"$GH_TOPICS\" ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n"
        )
        script.chmod(0o755)
        self.env["PATH"] = str(binary) + os.pathsep + self.env["PATH"]
        self.env["GH_HEAD"] = head
        self.env["GH_TOPICS"] = topics
        self.env["GH_LOG"] = str(self.root / "gh.log")
        self.env["GH_PR_FAIL"] = ""
        self.env["GH_PR_ERROR"] = "no pull requests found for branch"
        self.env["GH_CHECKS"] = "[]"
        self.env["GH_CHECKS_CODE"] = "0"

    def test_admin_merge_all_selector_forms_pass_for_a_sync_pr_on_a_personal_fork(self):
        self.gh_stub()
        for command in (
            "gh pr merge --admin",
            "gh pr merge 42 --admin",
            "gh pr merge 42 --admin=1",
            "gh pr merge https://github.com/o/one/pull/42 --admin",
            "gh pr merge sync/upstream-2026-11-28 --admin",
            "gh -R o/one pr merge 42 --admin",
            "gh pr merge 42 -R o/one --admin",
            "gh pr merge -R o/one 42 --admin",
            "gh pr merge -t subject 42 --admin",
            "gh pr merge -b body 42 --admin",
            "gh pr merge -F body.md 42 --admin",
            "gh pr merge -A author@example.invalid 42 --admin",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.output(self.invoke(command)), {})

    def test_admin_merge_is_denied_for_a_feature_branch(self):
        self.gh_stub(head="feature/example")
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
            ("cd /tmp && gh pr merge 42 --admin", "directory change"),
            ("cd; gh pr merge 42 --admin", "bare"),
            ("cd -; gh pr merge 42 --admin", "previous-directory"),
        ):
            with self.subTest(command=command):
                output = self.output(self.invoke(command))
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn(message, output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_plain_merge_does_not_query_github(self):
        self.gh_stub(head="feature/example", topics="")
        self.assertEqual(self.output(self.invoke("gh pr merge 42 --merge")), {})
        self.assertFalse((self.root / "gh.log").exists())

    def test_stop_blocks_each_pending_required_run(self):
        self.gh_stub()
        self.env["GH_CHECKS"] = json.dumps(
            [
                {"name": "Lint", "state": "PENDING", "bucket": "pending", "link": "https://github.com/o/one/actions/runs/11"},
                {"name": "Tests", "state": "PENDING", "bucket": "pending", "link": "https://github.com/o/one/actions/runs/12"},
            ]
        )
        self.env["GH_CHECKS_CODE"] = "8"
        output = self.output(self.invoke_mode("stop", {"cwd": str(self.repo), "stop_hook_active": False}))
        self.assertEqual(output["decision"], "block")
        self.assertIn("gh run watch 11 --exit-status", output["reason"])
        self.assertIn("gh run watch 12 --exit-status", output["reason"])

    def test_stop_allows_a_branch_without_a_pr(self):
        self.gh_stub()
        self.env["GH_PR_FAIL"] = "1"
        self.assertEqual(self.output(self.invoke_mode("stop", {"cwd": str(self.repo)})), {})

    def test_unavailable_stop_facts_block_once_then_warn_and_allow(self):
        self.gh_stub()
        self.env["GH_PR_FAIL"] = "1"
        self.env["GH_PR_ERROR"] = "network unavailable"
        self.env["GH_CHECKS_CODE"] = "1"
        first = self.output(self.invoke_mode("stop", {"cwd": str(self.repo), "stop_hook_active": False}))
        self.assertEqual(first["decision"], "block")
        repeat = self.output(self.invoke_mode("stop", {"cwd": str(self.repo), "stop_hook_active": True}))
        self.assertNotIn("decision", repeat)
        self.assertIn("warning", repeat["systemMessage"].lower())

    def test_pending_checks_keep_blocking_on_a_repeat_stop(self):
        self.gh_stub()
        self.env["GH_CHECKS"] = json.dumps(
            [{"name": "Lint", "state": "PENDING", "bucket": "pending", "link": "https://github.com/o/one/actions/runs/11"}]
        )
        self.env["GH_CHECKS_CODE"] = "8"
        output = self.output(self.invoke_mode("stop", {"cwd": str(self.repo), "stop_hook_active": True}))
        self.assertEqual(output["decision"], "block")

    def target_command(self, command: str, *targets: str) -> None:
        result = subprocess.run(  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- this checkout's target CLI
            [sys.executable, str(TOOLS / "run_targets.py"), command, "o/one", "6", *targets],
            text=True,
            capture_output=True,
            check=False,
            env=self.env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def fallback_command(self, reason: str, unblock: str = "Launch the next target") -> str:
        return f"skills/supervise/scripts/block-issue o/one 6 --reason '{reason}' --unblock '{unblock}'"

    def test_model_only_blocker_with_untried_fallback_is_refused(self):
        primary = json.dumps({"providerInstanceId": "opencode", "model": "opencode-go/hungerg-free", "options": {}})
        fallback = json.dumps(
            {"providerInstanceId": "opencode", "model": "opencode-go/hy4-flash-free", "options": {"variant": "high"}}
        )
        self.target_command("set", primary, fallback)
        self.target_command("mark", primary)
        output = self.output(self.invoke(self.fallback_command("opencode-go/hungerg-free unavailable quota")))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("opencode-go/hy4-flash-free", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_unblock_remediation_wording_does_not_create_a_model_only_cause(self):
        primary = json.dumps({"providerInstanceId": "opencode", "model": "opencode-go/hungerg-free", "options": {}})
        fallback = json.dumps(
            {"providerInstanceId": "opencode", "model": "opencode-go/hy4-flash-free", "options": {"variant": "high"}}
        )
        self.target_command("set", primary, fallback)
        self.target_command("mark", primary)
        output = self.output(
            self.invoke(self.fallback_command("opencode-go/hungerg-free unavailable quota", "Check the provider quota"))
        )
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("opencode-go/hy4-flash-free", output["hookSpecificOutput"]["permissionDecisionReason"])
        for reason in ("blocked", "tests fail on main"):
            with self.subTest(reason=reason):
                command = self.fallback_command(reason, "Restore the hungerg-free provider quota")
                self.assertEqual(self.output(self.invoke(command)), {})

    def test_model_only_blocker_with_every_fallback_tried_is_allowed(self):
        primary = json.dumps({"providerInstanceId": "opencode", "model": "opencode-go/hungerg-free", "options": {}})
        fallback = json.dumps(
            {"providerInstanceId": "opencode", "model": "opencode-go/hy4-flash-free", "options": {"variant": "high"}}
        )
        self.target_command("set", primary, fallback)
        self.target_command("mark", primary)
        self.target_command("mark", fallback)
        self.assertEqual(self.output(self.invoke(self.fallback_command("opencode-go/hungerg-free unavailable quota"))), {})

    def test_later_block_issue_violation_in_a_compound_command_is_checked(self):
        primary = json.dumps({"providerInstanceId": "opencode", "model": "opencode-go/hungerg-free", "options": {}})
        fallback = json.dumps(
            {"providerInstanceId": "opencode", "model": "opencode-go/hy4-flash-free", "options": {"variant": "high"}}
        )
        self.target_command("set", primary, fallback)
        self.target_command("mark", primary)
        allowed = "skills/supervise/scripts/block-issue o/one 65 --reason 'ordinary failure' --unblock 'Fix it'"
        command = allowed + "; " + self.fallback_command("opencode-go/hungerg-free unavailable quota")
        output = self.output(self.invoke(command))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_mixed_cause_blocker_is_allowed(self):
        primary = json.dumps({"providerInstanceId": "opencode", "model": "opencode-go/hungerg-free", "options": {}})
        fallback = json.dumps(
            {"providerInstanceId": "opencode", "model": "opencode-go/hy4-flash-free", "options": {"variant": "high"}}
        )
        self.target_command("set", primary, fallback)
        self.target_command("mark", primary)
        command = self.fallback_command("opencode-go/hungerg-free unavailable quota; mise run check failed")
        self.assertEqual(self.output(self.invoke(command)), {})

    def test_blocker_without_targets_file_is_allowed(self):
        command = self.fallback_command("opencode-go/hungerg-free unavailable quota")
        self.assertEqual(self.output(self.invoke(command)), {})

    def test_malformed_targets_file_warns_and_fails_open(self):
        path = self.root / "cache" / "factory" / "runs" / "o" / "one" / "6" / "targets.json"
        path.parent.mkdir(parents=True)
        path.write_text("not json")
        result = self.invoke(self.fallback_command("opencode-go/hungerg-free unavailable quota"))
        self.assertEqual(self.output(result), {})
        self.assertIn("Factory Run guard warning", result.stderr)

    def test_admin_merge_with_unavailable_github_facts_blocks(self):
        self.gh_stub()
        self.env["GH_PR_FAIL"] = "1"
        self.env["GH_PR_ERROR"] = "network unavailable"
        output = self.output(self.invoke("gh pr merge 42 --admin"))
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("network unavailable", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_opencode_tool_payload_returns_a_rule_block(self):
        self.write_mise("~/.agents/tools/run check_result -- true")
        result = self.invoke_mode("opencode", {"tool": "bash", "input": {"command": "git push"}, "cwd": str(self.repo)})
        output = self.output(result)
        self.assertTrue(output["block"])
        self.assertIn("mise run check", output["message"])

    def test_opencode_idle_pending_and_unavailable_results(self):
        self.gh_stub()
        self.env["GH_CHECKS"] = json.dumps(
            [{"name": "Lint", "state": "PENDING", "bucket": "pending", "link": "https://github.com/o/one/actions/runs/11"}]
        )
        self.env["GH_CHECKS_CODE"] = "8"
        pending = self.output(self.invoke_mode("opencode-idle", {"cwd": str(self.repo), "repeat": False}))
        self.assertTrue(pending["block"])
        self.assertIn("gh run watch 11 --exit-status", pending["message"])
        self.env["GH_PR_FAIL"] = "1"
        self.env["GH_PR_ERROR"] = "network unavailable"
        first = self.output(self.invoke_mode("opencode-idle", {"cwd": str(self.repo), "repeat": False}))
        self.assertTrue(first["block"])
        repeat = self.output(self.invoke_mode("opencode-idle", {"cwd": str(self.repo), "repeat": True}))
        self.assertFalse(repeat["block"])
        self.assertIn("warning", repeat["warning"].lower())

    def test_internal_crash_warns_and_fails_open(self):
        result = subprocess.run(  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- this checkout's guard
            [sys.executable, str(GUARD), "tool"],
            input="not json",
            text=True,
            capture_output=True,
            check=False,
            cwd=self.repo,
            env=self.env,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Factory Run guard warning", result.stderr)


if __name__ == "__main__":
    unittest.main()
