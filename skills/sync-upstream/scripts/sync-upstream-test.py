import json
import unittest

from sync_upstream_fixtures import SyncFixture


class SyncUpstream(SyncFixture):
    def test_kind_comes_from_the_topic(self):
        result = self.cli("kind", "--repo", "o/r")
        self.assertEqual(result.stdout.strip(), "personal")
        self.write_repo("o/r", ["factory-upstream-fork"], "main")
        self.assertEqual(self.cli("kind", "--repo", "o/r").stdout.strip(), "upstream")
        self.write_repo("o/r", [], "main")
        result = self.cli("kind", "--repo", "o/r", check=False)
        self.assertEqual(result.returncode, 3)
        self.assertIn("o/r has no fork topic", result.stderr)
        self.assertIn("uv run scripts/repo-settings.py o/r --fork-kind personal --apply", result.stderr)
        self.assertIn("uv run scripts/repo-settings.py o/r --fork-kind upstream --apply", result.stderr)
        self.write_repo("o/r", ["factory-personal-fork", "factory-upstream-fork"], "main")
        self.assertEqual(self.cli("kind", "--repo", "o/r", check=False).returncode, 2)
        self.write_repo("o/r", [], "main", fork=False)
        self.assertEqual(self.cli("kind", "--repo", "o/r").stdout.strip(), "personal-project")

    def test_repo_name_comes_from_origin_not_the_parent(self):
        work = self.init("remote-work")
        self.git(work, "remote", "add", "origin", "https://github.com/o/r.git")
        state = json.loads(self.repo_file.read_text())
        state["view"] = "up/stream"
        self.repo_file.write_text(json.dumps(state))
        result = self.cli("kind", cwd=work)
        self.assertEqual(result.stdout.strip(), "personal")

    def test_sync_refuses_a_missing_topic(self):
        self.write_repo("o/r", [], "main")
        result = self.cli("sync", "--repo", "o/r", check=False)
        self.assertEqual(result.returncode, 3)
        self.assertIn("--fork-kind personal --apply", result.stderr)

    def test_personal_sync_merges_upstream_with_a_merge_commit(self):
        pair = self.fork_pair()
        work = self.clone(pair.origin, self.root / "work")
        self.git(work, "remote", "add", "upstream", str(pair.upstream))
        result = self.cli("sync", "--repo", "o/r", "--date", "2026-11-22", cwd=work)
        self.assertIn("sync/upstream-2026-11-22", result.stdout)
        self.assertEqual(self.git(work, "branch", "--show-current").stdout.strip(), "sync/upstream-2026-11-22")
        parents = self.git(work, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(len(parents), 3)
        self.assertEqual(self.git(work, "merge-base", "--is-ancestor", "upstream/main", "HEAD", check=False).returncode, 0)
        self.assertEqual(self.git(work, "rev-parse", "origin/main").stdout.strip(), pair.base)
        self.git(work, "push", "-q", "origin", "HEAD:refs/heads/sync/upstream-2026-11-22")
        rerun = self.cli("sync", "--repo", "o/r", "--date", "2026-11-22", cwd=work)
        self.assertIn("already contains", rerun.stdout)
        self.git(work, "switch", "-q", "main")
        self.git(work, "branch", "-q", "-D", "sync/upstream-2026-11-22")
        tracked = self.cli("sync", "--repo", "o/r", "--date", "2026-11-22", cwd=work)
        self.assertIn("already contains", tracked.stdout)
        self.assertEqual(self.git(work, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}").stdout.strip(), "origin/sync/upstream-2026-11-22")
        (work / "dirty.txt").write_text("dirty\n")
        dirty = self.cli("sync", "--repo", "o/r", "--date", "2026-11-22", cwd=work, check=False)
        self.assertNotEqual(dirty.returncode, 0)
        self.assertIn("uncommitted", dirty.stderr)

    def test_upstream_default_branch_differs_from_the_fork(self):
        origin_seed = self.seed("origin-seed", {"file.txt": "base\n"})
        base = self.git(origin_seed, "rev-parse", "HEAD").stdout.strip()
        self.git(origin_seed, "branch", "trunk")
        origin = self.bare("origin", origin_seed)
        self.git(origin_seed, "push", "-q", str(origin), "trunk:refs/heads/trunk")
        upstream_seed = self.clone(origin_seed, self.root / "upstream-seed")
        self.commit(upstream_seed, "upstream.txt", "upstream\n", "upstream change")
        upstream_sha = self.git(upstream_seed, "rev-parse", "HEAD").stdout.strip()
        upstream = self.bare("upstream", upstream_seed)
        self.mirror(upstream)
        work = self.clone(origin, self.root / "work")
        self.git(work, "remote", "add", "upstream", str(upstream))
        self.write_repo("o/r", ["factory-personal-fork"], "trunk", parent_default="main")
        result = self.cli("sync", "--repo", "o/r", "--date", "2026-11-22", cwd=work)
        self.assertIn("merges upstream/main into trunk", result.stdout)
        parents = self.git(work, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(parents[1], base)
        self.assertEqual(parents[2], upstream_sha)
        body = self.root / "body.md"
        body.write_text(f"## Verdicts\n\n- `{upstream_sha[:8]}` adopt — upstream change\n")
        self.cli("verdicts", "--repo", "o/r", "--body-file", str(body), cwd=work)
        self.assertEqual(self.git(work, "rev-parse", "origin/trunk").stdout.strip(), base)

    def test_upstream_sync_fast_forwards_and_refuses_a_divergence(self):
        pair = self.fork_pair()
        work = self.clone(pair.origin, self.root / "work")
        self.git(work, "remote", "add", "upstream", str(pair.upstream))
        self.write_repo("o/r", ["factory-upstream-fork"], "main")
        result = self.cli("sync", "--repo", "o/r", cwd=work)
        self.assertIn("fast-forwarded", result.stdout)
        self.assertEqual(self.git(pair.origin, "rev-parse", "refs/heads/main").stdout.strip(), pair.ahead)
        divergent_seed = self.clone(pair.origin_seed, self.root / "divergent-seed")
        self.commit(divergent_seed, "fork.txt", "fork\n", "fork change")
        divergent = self.bare("divergent", divergent_seed)
        fork_work = self.clone(divergent, self.root / "fork-work")
        self.git(fork_work, "remote", "add", "upstream", str(pair.upstream))
        fork_head = self.git(divergent, "rev-parse", "refs/heads/main").stdout.strip()
        result = self.cli("sync", "--repo", "o/r", cwd=fork_work, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an ancestor", result.stderr)
        self.assertEqual(self.git(divergent, "rev-parse", "refs/heads/main").stdout.strip(), fork_head)

    def verdict_repo(self, first_change=True):
        repo = self.init("verdicts")
        self.commit(repo, "file.txt", "base\n", "base")
        base = self.git(repo, "rev-parse", "HEAD").stdout.strip()
        self.git(repo, "branch", "side")
        first = self.commit(repo, "first.txt", "first\n", "first change") if first_change else base
        self.git(repo, "switch", "-q", "side")
        side_one = self.commit(repo, "side-one.txt", "one\n", "side one")
        self.commit(repo, "side-two.txt", "two\n", "side two")
        self.git(repo, "switch", "-q", "main")
        self.git(repo, "merge", "--no-ff", "-q", "side", "-m", "merge side")
        merge = self.git(repo, "rev-parse", "HEAD").stdout.strip()
        return repo, base, first, side_one, merge

    def verdicts(self, repo, base, merge, body):
        path = self.root / "body.md"
        path.write_text(body)
        return self.cli("verdicts", "--from", base, "--to", merge, "--body-file", str(path), cwd=repo, check=False)

    def test_verdicts_cover_first_parent_commits_only(self):
        repo, base, first, side_one, merge = self.verdict_repo()
        good = f"## Verdicts\n\n- `{first[:8]}` adopt — applies\n- `{merge[:8]}` adopt — merge commit\n"
        result = self.verdicts(repo, base, merge, good)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 first-parent upstream changes", result.stdout)
        with_side = f"## Verdicts\n\n- `{first[:8]}` adopt — applies\n- `{side_one[:8]}` adopt — side change\n- `{merge[:8]}` adopt — merge commit\n"
        self.assertEqual(self.verdicts(repo, base, merge, with_side).returncode, 0)
        missing = f"## Verdicts\n\n- `{first[:8]}` adopt — applies\n"
        result = self.verdicts(repo, base, merge, missing)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(merge, result.stderr)
        self.assertIn("has no verdict", result.stderr)

    def test_verdicts_merge_commit_stands_for_its_side_branch(self):
        repo, base, _, _, merge = self.verdict_repo(first_change=False)
        result = self.verdicts(repo, base, merge, f"## Verdicts\n\n- `{merge[:8]}` adopt — merge commit\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 first-parent upstream changes", result.stdout)
        result = self.verdicts(repo, base, merge, "## Verdicts\n\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(merge, result.stderr)

    def test_verdicts_ignore_fenced_rows_and_side_branch_conflicts(self):
        repo, base, first, side, merge = self.verdict_repo()
        fenced = f"## Verdicts\n```\n- `{first[:8]}` adopt — example\n```\n- `{merge[:8]}` adopt — merge\n"
        result = self.verdicts(repo, base, merge, fenced)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(first, result.stderr)
        body = f"## Verdicts\n- `{first[:8]}` adopt — first\n- `{merge[:8]}` adopt — merge\n- `{side[:8]}` adopt — side\n- `{side[:8]}` keep-ours — side\n~~~\n- `{first[:8]}` keep-ours — example\n~~~\n"
        result = self.verdicts(repo, base, merge, body)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.verdicts(repo, base, merge, body + f"- `{first[:8]}` keep-ours — conflict\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("more than one verdict", result.stderr)

    def test_verdicts_reject_ambiguous_commit_prefix(self):
        first = "abcdef0" + "1" * 33
        side = "abcdef0" + "2" * 33
        fake = self.root / "bin" / "git"
        fake.write_text(f"#!/bin/sh\nif [ \"$2\" = \"--first-parent\" ]; then\n  printf '%s\\tfirst\\n' {first}\nelse\n  printf '%s\\tfirst\\n%s\\tside\\n' {first} {side}\nfi\n")
        fake.chmod(0o755)
        result = self.verdicts(self.root, "base", "tip", "## Verdicts\n- `abcdef0` adopt — ambiguous\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ambiguous commit prefix", result.stderr)

    def test_verdicts_fail_closed_on_mismatched_and_unclosed_fences(self):
        repo, base, first, _, merge = self.verdict_repo()
        row = f"- `{first[:8]}` adopt — example\n"
        merge_row = f"- `{merge[:8]}` adopt — merge\n"
        body = "## Verdicts\n````\n```\n~~~\n" + row + "````\n" + merge_row
        result = self.verdicts(repo, base, merge, body)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(first, result.stderr)
        unclosed = "## Verdicts\n```\n~~~\n" + row + merge_row
        result = self.verdicts(repo, base, merge, unclosed)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verdicts: PR body has an unclosed code fence; verdicts cannot be read safely", result.stderr)
        for indent in ("    ", "\t"):
            body = "## Verdicts\n```\n" + indent + "```\n" + row + merge_row
            result = self.verdicts(repo, base, merge, body)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("verdicts: PR body has an unclosed code fence; verdicts cannot be read safely", result.stderr)
        valid = "## Verdicts\n```\nexample\n```\n" + row + merge_row
        self.assertEqual(self.verdicts(repo, base, merge, valid).returncode, 0)
        generated = self.cli("body", "--from", base, "--to", merge, cwd=repo)
        self.assertIn("<verdict>", generated.stdout)
        self.assertIn("<verdict>", self.verdicts(repo, base, merge, generated.stdout).stderr)

    def test_merge_command_refuses_an_unclosed_verdict_fence(self):
        repo, _ = self.command_fixture(body="## Verdicts\n```\n~~~\n")
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verdicts: PR body has an unclosed code fence; verdicts cannot be read safely", result.stderr)
        self.assertNotIn("gh pr merge", result.stdout)

    def test_verdicts_reads_a_pr_body(self):
        repo, base, first, side_one, merge = self.verdict_repo()
        state = json.loads(self.repo_file.read_text())
        state["body"] = f"## Verdicts\n\n- `{first[:8]}` adopt — applies\n- `{merge[:8]}` adopt — merge\n"
        self.repo_file.write_text(json.dumps(state))
        result = self.cli("verdicts", "--from", base, "--to", merge, "--pr", "7", "--repo", "o/r", cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 first-parent upstream changes", result.stdout)

    def test_verdicts_reject_bad_rows(self):
        repo, base, first, side_one, merge = self.verdict_repo()
        placeholder = f"## Verdicts\n\n- `{first[:8]}` <verdict> — applies\n- `{merge[:8]}` adopt — merge\n"
        result = self.verdicts(repo, base, merge, placeholder)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("<verdict>", result.stderr)
        unknown = f"## Verdicts\n\n- `{first[:8]}` maybe — applies\n- `{merge[:8]}` adopt — merge\n"
        self.assertIn("unknown verdict", self.verdicts(repo, base, merge, unknown).stderr)
        outside = "## Verdicts\n\n- `deadbee` adopt — no such commit\n" + f"- `{first[:8]}` adopt — applies\n- `{merge[:8]}` adopt — merge\n"
        self.assertIn("names no commit", self.verdicts(repo, base, merge, outside).stderr)

    def command_fixture(self, check_runs=None, statuses=None, body=None):
        repo = self.init("command")
        base = self.commit(repo, "file.txt", "base\n", "base")
        self.git(repo, "update-ref", "refs/remotes/origin/main", base)
        self.git(repo, "switch", "-q", "-c", "upstream-side")
        upstream_tip = self.commit(repo, "upstream.txt", "upstream\n", "upstream change")
        self.git(repo, "update-ref", "refs/remotes/upstream/main", upstream_tip)
        self.git(repo, "switch", "-q", "main")
        self.git(repo, "merge", "--no-ff", "-q", "upstream-side", "-m", "sync merge")
        head = self.git(repo, "rev-parse", "HEAD").stdout.strip()
        state = json.loads(self.repo_file.read_text())
        state["head"] = head
        state["base"] = "main"
        state["body"] = body if body is not None else f"## Verdicts\n\n- `{upstream_tip[:8]}` adopt — upstream change\n"
        state["check_runs"] = {head: check_runs if check_runs is not None else [{"name": "Gates / Gates", "status": "completed", "conclusion": "success"}]}
        state["statuses"] = statuses if statuses is not None else []
        state["applied_rules"] = [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "Gates / Gates"}]}}]
        self.repo_file.write_text(json.dumps(state))
        return repo, head

    def merge_command(self, repo, pr=7):
        return self.cli("merge-command", "--repo", "o/r", "--pr", str(pr), cwd=repo, check=False)

    def test_merge_command_requires_verified_commit_and_resolved_threads(self):
        repo, _ = self.command_fixture()
        original = json.loads(self.repo_file.read_text())
        self.assertEqual(self.merge_command(repo).returncode, 0)
        for key, value, message in (("verified", False, "signature is not verified"), ("review_threads", [{"isResolved": False}], "unresolved review threads"), ("threads_unavailable", True, "gh"), ("thread_pages", [{"nodes": []}], "could not verify PR review threads"), ("thread_pages", [{"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}], "could not verify PR review threads")):
            with self.subTest(key=key):
                state = dict(original, **{key: value})
                self.repo_file.write_text(json.dumps(state))
                result = self.merge_command(repo)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertNotIn("gh pr merge", result.stdout)
        state = dict(original, thread_pages=[{"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "next"}}, {"nodes": [{"isResolved": False}], "pageInfo": {"hasNextPage": False, "endCursor": None}}])
        self.repo_file.write_text(json.dumps(state))
        self.assertIn("unresolved review threads", self.merge_command(repo).stderr)

    def test_merge_command_fails_closed_on_malformed_api_pages(self):
        repo, _ = self.command_fixture()
        original = json.loads(self.repo_file.read_text())
        for key, value, message in (
            ("rules_pages", {"message": "Not Found"}, "could not read the default branch rules"),
            ("rules_pages", [["not-a-rule"]], "could not read the default branch rules"),
            ("check_pages", {"message": "Not Found"}, "could not read the PR head check runs"),
            ("check_pages", [{"check_runs": [None]}], "could not read the PR head check runs"),
            ("status_pages", {"message": "Not Found"}, "could not read the PR head statuses"),
            ("status_pages", [[None]], "could not read the PR head statuses"),
        ):
            with self.subTest(key=key, value=value):
                state = dict(original, **{key: value})
                self.repo_file.write_text(json.dumps(state))
                result = self.merge_command(repo)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertNotIn("gh pr merge", result.stdout)

    def test_merge_command_prints_only_when_green(self):
        repo, head = self.command_fixture(check_runs=[{"name": "Gates / Gates", "status": "completed", "conclusion": "success"}, {"name": "CodeRabbit", "status": "completed", "conclusion": "skipped"}], statuses=[{"context": "Codacy Static Code Analysis", "state": "success"}])
        result = self.merge_command(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"gh pr merge 7 -R o/r --merge --match-head-commit {head} --admin\n")

    def test_merge_command_rejects_incomplete_ruleset_parameters(self):
        repo, _ = self.command_fixture()
        state = json.loads(self.repo_file.read_text())
        for parameters in (None, {}, {"required_status_checks": None}, {"required_status_checks": "invalid"}, {"required_status_checks": [{}]}):
            with self.subTest(parameters=parameters):
                state["applied_rules"] = [{"type": "required_status_checks", "parameters": parameters}]
                self.repo_file.write_text(json.dumps(state))
                result = self.merge_command(repo)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("required_status_checks rule", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_merge_command_refuses_empty_check_rule_but_allows_no_rule(self):
        repo, _ = self.command_fixture()
        state = json.loads(self.repo_file.read_text())
        state["applied_rules"] = [{"type": "required_status_checks", "parameters": {"required_status_checks": []}}]
        self.repo_file.write_text(json.dumps(state))
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required_status_checks rule lists no checks", result.stderr)
        self.assertEqual(result.stdout, "")
        state["applied_rules"] = []
        self.repo_file.write_text(json.dumps(state))
        result = self.merge_command(repo)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_merge_command_blocks_a_failing_check(self):
        repo, _ = self.command_fixture(check_runs=[{"name": "Gates / Gates", "status": "completed", "conclusion": "failure"}])
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Gates / Gates concluded failure", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_merge_command_blocks_a_pending_check(self):
        repo, _ = self.command_fixture(check_runs=[{"name": "Gates / Gates", "status": "in_progress", "conclusion": None}])
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Gates / Gates is pending", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_merge_command_blocks_a_missing_verdict(self):
        repo, _ = self.command_fixture(body="## Verdicts\n\n")
        upstream_tip = self.git(repo, "rev-parse", "refs/remotes/upstream/main").stdout.strip()
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(upstream_tip, result.stderr)
        self.assertIn("has no verdict", result.stderr)

    def test_merge_command_blocks_a_required_neutral_check(self):
        repo, _ = self.command_fixture(check_runs=[{"name": "Gates / Gates", "status": "completed", "conclusion": "neutral"}])
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Gates / Gates concluded neutral", result.stderr)

    def test_merge_command_blocks_a_missing_required_check(self):
        repo, _ = self.command_fixture(check_runs=[])
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required check Gates / Gates has no result", result.stderr)

    def test_merge_command_blocks_a_pending_status(self):
        repo, _ = self.command_fixture(statuses=[{"context": "Codacy Static Code Analysis", "state": "pending"}])
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Codacy Static Code Analysis is pending", result.stderr)

    def test_merge_command_blocks_a_stale_checkout(self):
        repo, _ = self.command_fixture()
        base = self.git(repo, "rev-parse", "refs/remotes/origin/main").stdout.strip()
        self.git(repo, "switch", "-q", "--detach", base)
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HEAD is not the PR head", result.stderr)

    def test_merge_command_blocks_stale_upstream_refs(self):
        repo, _ = self.command_fixture()
        base = self.git(repo, "rev-parse", "refs/remotes/origin/main").stdout.strip()
        self.git(repo, "update-ref", "refs/remotes/upstream/main", base)
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not upstream/main", result.stderr)

    def test_merge_command_uses_the_latest_status_per_context(self):
        repo, _ = self.command_fixture(statuses=[{"context": "Codacy Static Code Analysis", "state": "success"}, {"context": "Codacy Static Code Analysis", "state": "failure"}])
        result = self.merge_command(repo)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_merge_command_accepts_a_required_check_from_a_status(self):
        repo, _ = self.command_fixture(statuses=[{"context": "Codacy Static Code Analysis", "state": "success"}])
        state = json.loads(self.repo_file.read_text())
        state["applied_rules"] = [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "Gates / Gates"}, {"context": "Codacy Static Code Analysis"}]}}]
        self.repo_file.write_text(json.dumps(state))
        result = self.merge_command(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("gh pr merge 7 -R o/r --merge --match-head-commit "))

    def test_merge_command_blocks_a_missing_origin_ref(self):
        repo, _ = self.command_fixture()
        self.git(repo, "update-ref", "-d", "refs/remotes/origin/main")
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("origin/main is missing", result.stderr)

    def test_merge_command_blocks_a_non_default_base(self):
        repo, _ = self.command_fixture()
        state = json.loads(self.repo_file.read_text())
        state["base"] = "release"
        self.repo_file.write_text(json.dumps(state))
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PR base release is not main", result.stderr)

    def test_merge_command_blocks_a_failed_status(self):
        repo, _ = self.command_fixture(statuses=[{"context": "Codacy Static Code Analysis", "state": "failure"}])
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Codacy Static Code Analysis is failure", result.stderr)

    def test_merge_command_reads_statuses_past_the_first_page(self):
        repo, _ = self.command_fixture()
        state = json.loads(self.repo_file.read_text())
        state["status_pages"] = [[{"context": "early", "state": "success"}], [{"context": "late", "state": "failure"}]]
        self.repo_file.write_text(json.dumps(state))
        result = self.merge_command(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("late is failure", result.stderr)

    def test_sync_rejects_a_local_upstream_that_is_not_the_parent(self):
        pair = self.fork_pair()
        self.git(self.root, "--git-dir", str(pair.upstream), "config", "remote.origin.url", "https://github.com/other/repo.git")
        work = self.clone(pair.origin, self.root / "work")
        self.git(work, "remote", "add", "upstream", str(pair.upstream))
        result = self.cli("sync", "--repo", "o/r", cwd=work, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a mirror", result.stderr)

    def test_verdicts_requires_a_parent_when_refs_are_omitted(self):
        state = json.loads(self.repo_file.read_text())
        state["info"]["parent"] = None
        self.repo_file.write_text(json.dumps(state))
        body = self.root / "body.md"
        body.write_text("## Verdicts\n\n")
        result = self.cli("verdicts", "--repo", "o/r", "--body-file", str(body), cwd=self.root, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no upstream parent", result.stderr)


if __name__ == "__main__":
    unittest.main()
