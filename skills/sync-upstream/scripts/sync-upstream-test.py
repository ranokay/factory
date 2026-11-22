import json
import os
import shutil
import subprocess  # nosec B404 -- the fixture runs local executables
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from sync_upstream_fixtures import GH, ForkPair

SCRIPT = Path(__file__).resolve().with_name("sync-upstream")
GIT = shutil.which("git") or "git"


def run_process(args, cwd, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)  # nosec B603  # nosemgrep -- the fixture runs local executables


class SyncUpstream(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(GH)
        gh.chmod(0o755)
        self.repo_file = self.root / "repo.json"
        self.env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], FAKE_REPO=str(self.repo_file))
        self.write_repo("o/r", ["factory-personal-fork"], "main")

    def write_repo(self, name, topics, default, fork=True, parent="up/stream", parent_default="main"):
        self.repo_file.write_text(json.dumps({"name": name, "info": {"fork": fork, "default_branch": default, "parent": {"full_name": parent, "default_branch": parent_default}, "topics": topics}}))

    def cli(self, *args, cwd=None, check=True):
        result = run_process([sys.executable, str(SCRIPT), *args], cwd or self.root, self.env)
        if check and result.returncode:
            self.fail(result.stderr)
        return result

    def git(self, cwd, *args, check=True):
        result = run_process([GIT, "-C", str(cwd), *args], None)
        if check and result.returncode:
            self.fail(result.stderr)
        return result

    def init(self, name):
        path = self.root / name
        path.mkdir()
        self.git(path, "init", "-q", "-b", "main")
        self.git(path, "config", "user.name", "Fixture")
        self.git(path, "config", "user.email", "fixture@example.com")
        return path

    def commit(self, path, name, content, message):
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.git(path, "add", "-A")
        self.git(path, "commit", "-qm", message)
        return self.git(path, "rev-parse", "HEAD").stdout.strip()

    def bare(self, name, seed, branch="main"):
        path = self.root / f"{name}.git"
        self.git(self.root, "init", "--bare", "-q", "-b", "main", str(path))
        self.git(seed, "push", "-q", str(path), f"HEAD:refs/heads/{branch}")
        return path

    def seed(self, name, files):
        path = self.init(name)
        for filename, content in files.items():
            (path / filename).parent.mkdir(parents=True, exist_ok=True)
            (path / filename).write_text(content)
        self.git(path, "add", "-A")
        self.git(path, "commit", "-qm", "base")
        return path

    def clone(self, source, target):
        self.git(self.root, "clone", "-q", str(source), str(target))
        self.git(target, "config", "user.name", "Fixture")
        self.git(target, "config", "user.email", "fixture@example.com")
        return target

    def fork_pair(self):
        origin_seed = self.seed("origin-seed", {"file.txt": "base\n"})
        base = self.git(origin_seed, "rev-parse", "HEAD").stdout.strip()
        origin = self.bare("origin", origin_seed)
        upstream_seed = self.root / "upstream-seed"
        self.clone(origin, upstream_seed)
        ahead = self.commit(upstream_seed, "upstream.txt", "upstream\n", "upstream change")
        upstream = self.bare("upstream", upstream_seed)
        self.mirror(upstream)
        return ForkPair(origin_seed, base, origin, upstream, upstream_seed, ahead)

    def mirror(self, path):
        self.git(self.root, "--git-dir", str(path), "config", "remote.origin.url", "https://github.com/up/stream.git")

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

    def test_merge_command_prints_only_when_green(self):
        repo, head = self.command_fixture(check_runs=[{"name": "Gates / Gates", "status": "completed", "conclusion": "success"}, {"name": "CodeRabbit", "status": "completed", "conclusion": "skipped"}], statuses=[{"context": "Codacy Static Code Analysis", "state": "success"}])
        result = self.merge_command(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"gh pr merge 7 -R o/r --merge --match-head-commit {head} --admin\n")

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

    def factory_repo(self):
        repo = self.init("factory")
        self.commit(repo, "mise.toml", 'go = "1.24"\n', "mise")
        self.commit(repo, "AGENTS.md", "# upstream\n", "agents")
        self.commit(repo, "GLOSSARY.md", "# glossary\n", "glossary")
        self.commit(repo, ".github/workflows/ci.yml", "on: push\n", "ci")
        base = self.git(repo, "rev-parse", "HEAD").stdout.strip()
        self.git(repo, "update-ref", "refs/remotes/upstream/main", base)
        return repo

    def branch(self, repo, name, filename, content, delete=False):
        self.git(repo, "switch", "-q", "main")
        self.git(repo, "switch", "-q", "-c", name)
        if delete:
            self.git(repo, "rm", "-q", filename)
        else:
            (repo / filename).parent.mkdir(parents=True, exist_ok=True)
            (repo / filename).write_text(content)
            self.git(repo, "add", filename)
        self.git(repo, "commit", "-qm", name)

    def factory_files(self, repo, branch):
        return self.cli("factory-files", "--from", "upstream/main", "--head", branch, cwd=repo, check=False)

    def test_factory_files_allow_upstream_edits_and_reject_factory_ones(self):
        repo = self.factory_repo()
        self.branch(repo, "add-hk", "hk.pkl", 'amends "upstream.pkl"\n')
        result = self.factory_files(repo, "add-hk")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hk.pkl: added Factory file", result.stderr)
        self.branch(repo, "profiles", "AGENTS.md", "# upstream\n\nProfiles: universal\n")
        self.assertIn("AGENTS.md: adds a Profiles: line", self.factory_files(repo, "profiles").stderr)
        self.branch(repo, "factory-include", "mise.toml", 'include = ["git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=v0.2.0"]\n')
        self.assertIn("mise.toml: adds ranokay/factory", self.factory_files(repo, "factory-include").stderr)
        self.branch(repo, "plain-mise", "mise.toml", 'go = "1.25"\n')
        self.assertEqual(self.factory_files(repo, "plain-mise").returncode, 0)
        self.branch(repo, "workflow", ".github/workflows/gates.yml", "name: Gates\njobs:\n  gates:\n    uses: ranokay/factory/.github/workflows/gates.yml@main\n")
        self.assertIn(".github/workflows/gates.yml: adds ranokay/factory", self.factory_files(repo, "workflow").stderr)
        self.branch(repo, "workflow-edit", ".github/workflows/ci.yml", "on: push\njobs:\n  test:\n    uses: ranokay/factory/.github/workflows/gates.yml@main\n")
        self.assertIn(".github/workflows/ci.yml: adds ranokay/factory", self.factory_files(repo, "workflow-edit").stderr)
        self.branch(repo, "workflow-plain", ".github/workflows/ci.yml", "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
        self.assertEqual(self.factory_files(repo, "workflow-plain").returncode, 0)
        self.branch(repo, "delete", "GLOSSARY.md", "", delete=True)
        self.assertEqual(self.factory_files(repo, "delete").returncode, 0)

    def target(self, existing=None):
        path = self.root / "target"
        path.mkdir()
        for filename, content in (existing or {}).items():
            target = path / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        return path

    def test_install_files_copies_the_template_and_checks_clean(self):
        target = self.target()
        check = self.cli("install-files", "--target", str(target), "--check", check=False)
        self.assertEqual(check.returncode, 1)
        self.assertEqual(list(target.iterdir()), [])
        self.cli("install-files", "--target", str(target))
        for name in ("AGENTS.md", "mise.toml", "hk.pkl", "renovate.json", ".coderabbit.yaml", ".greptile/config.json"):
            self.assertTrue((target / name).is_file(), name)
        again = self.cli("install-files", "--target", str(target), "--check")
        self.assertIn("hk.pkl: upstream has its own; Factory Gates not amended", again.stdout)

    def test_install_files_fails_on_an_existing_file_with_no_merge_rule(self):
        template = self.root / "template"
        template.mkdir()
        (template / "extra.txt").write_text("factory\n")
        target = self.target({"extra.txt": "upstream\n"})
        result = self.cli("install-files", "--target", str(target), "--template", str(template), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no merge rule", result.stderr)
        self.assertEqual((target / "extra.txt").read_text(), "upstream\n")

    def test_install_files_requires_an_existing_template(self):
        target = self.target()
        result = self.cli("install-files", "--target", str(target), "--template", str(self.root / "missing"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stderr)

    def test_install_files_scopes_coderabbit_enabled_to_auto_review(self):
        template = self.root / "template"
        template.mkdir()
        (template / ".coderabbit.yaml").write_text("reviews:\n  tools:\n    enabled: true\n  auto_review:\n    enabled: false\n")
        target = self.target({".coderabbit.yaml": "reviews:\n  auto_review:\n    other: true\n  tools:\n    enabled: true\n"})
        self.cli("install-files", "--target", str(target), "--template", str(template))
        text = (target / ".coderabbit.yaml").read_text()
        self.assertIn("auto_review:\n    enabled: false\n    other: true", text)
        self.assertIn("tools:\n    enabled: true", text)

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

    def test_install_files_preserves_inline_array_and_comment(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('include = ["factory"]\n[settings]\nexperimental = true\n')
        original = 'include = ["café.toml"] # preserve ] comment\n[settings]\nexperimental = true\n'
        target = self.target({"mise.toml": original})
        self.cli("install-files", "--target", str(target), "--template", str(template))
        self.assertEqual((target / "mise.toml").read_text(), original.replace('"café.toml"]', '"café.toml", "factory"]'))

    def test_install_files_preserves_block_arrays_and_adds_missing_tables(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('include = ["factory"]\n[task_config]\nincludes = ["factory-tasks"]\n[settings]\nexperimental = true\n')
        original = 'include = [ # existing ] comment\n  "other", # keep\n]\n[task_config]\nincludes = [\n  "tasks", # keep\n]\n'
        target = self.target({"mise.toml": original})
        self.cli("install-files", "--target", str(target), "--template", str(template))
        expected = original.replace('include = [ # existing ] comment\n', 'include = [ # existing ] comment\n  "factory",\n').replace('includes = [\n', 'includes = [\n  "factory-tasks",\n')
        self.assertEqual((target / "mise.toml").read_text(), expected + '\n[settings]\nexperimental = true\n')

    def test_install_files_adds_missing_task_and_settings_tables(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('include = ["factory"]\n[task_config]\nincludes = ["factory-tasks"]\n[settings]\nexperimental = true\n')
        target = self.target({"mise.toml": 'include = ["factory"]\n[tools]\npython = "3"\n'})
        self.cli("install-files", "--target", str(target), "--template", str(template))
        self.assertEqual((target / "mise.toml").read_text(), 'include = ["factory"]\n[tools]\npython = "3"\n\n[task_config]\nincludes = ["factory-tasks"]\n\n[settings]\nexperimental = true\n')

    def test_install_files_rejects_invalid_mise_shapes(self):
        template = self.root / "template"
        template.mkdir()
        (template / "mise.toml").write_text('include = ["factory"]\n[task_config]\nincludes = ["factory-tasks"]\n')
        target = self.target({"mise.toml": 'task_config = "invalid"\n'})
        for invalid in ('task_config = "invalid"\n', 'include = "invalid"\n', '[task_config]\nincludes = "invalid"\n'):
            with self.subTest(invalid=invalid):
                (target / "mise.toml").write_text(invalid)
                result = self.cli("install-files", "--target", str(target), "--template", str(template), check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("install-files: mise.toml", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_install_files_extends_existing_files(self):
        target = self.target({
            "AGENTS.md": "# upstream\n\nOwn instructions.\n",
            "mise.toml": 'include = ["other.toml"]\n\n[tools]\ngo = "1.24"\n\n[settings]\nexperimental = false\n',
            "renovate.json": '{"extends":["config:base"],"rangeStrategy":"pin"}\n',
            ".greptile/config.json": '{"custom":true}\n',
            ".coderabbit.yaml": "reviews:\n  auto_review:\n    enabled: true\n  request_changes_workflow: false\n",
            "hk.pkl": 'amends "upstream.pkl"\n',
        })
        result = self.cli("install-files", "--target", str(target))
        self.assertIn("hk.pkl: upstream has its own; Factory Gates not amended", result.stdout)
        agents = (target / "AGENTS.md").read_text()
        self.assertTrue(agents.startswith("# upstream"))
        self.assertIn("Own instructions.", agents)
        self.assertIn("Profiles: universal", agents)
        mise = tomllib.loads((target / "mise.toml").read_text())
        self.assertEqual(mise["include"][0], "other.toml")
        self.assertTrue(any("ranokay/factory" in item for item in mise["include"]))
        self.assertIs(mise["settings"]["experimental"], True)
        self.assertTrue(any("ranokay/factory" in item for item in mise["task_config"]["includes"]))
        self.assertEqual(mise["tools"]["go"], "1.24")
        renovate = json.loads((target / "renovate.json").read_text())
        self.assertEqual(set(renovate["extends"]), {"config:base", "github>ranokay/factory"})
        self.assertEqual(renovate["rangeStrategy"], "pin")
        greptile = json.loads((target / ".greptile/config.json").read_text())
        self.assertIs(greptile["custom"], True)
        self.assertEqual(greptile["autoReview"], [])
        self.assertIs(greptile["statusCheck"], False)
        coderabbit = (target / ".coderabbit.yaml").read_text()
        self.assertIn("enabled: false", coderabbit)
        self.assertIn("request_changes_workflow: false", coderabbit)
        self.assertEqual((target / "hk.pkl").read_text(), 'amends "upstream.pkl"\n')
        self.cli("install-files", "--target", str(target), "--check")


if __name__ == "__main__":
    unittest.main()
