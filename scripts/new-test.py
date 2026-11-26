import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from profiles_reader import factory_ref

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = tomllib.loads((ROOT / "project/mise.toml").read_text())
TEMPLATE_TAG = next(
    ref for ref in (factory_ref(entry) for entry in TEMPLATE["include"]) if ref
)
TEST_TAG = "v9.9.9"
NEW = ROOT / "scripts/new.py"
GH = (ROOT / "scripts/fixtures/new/gh").read_text()
GIT = (ROOT / "scripts/fixtures/new/git").read_text()
OP = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

with Path(os.environ["FAKE_OP_LOG"]).open("a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
print("fixture-secret", end="")
"""
SITE = """import socket
import urllib.request


def blocked(*args, **kwargs):
    raise RuntimeError("network blocked in new tests")


socket.create_connection = blocked
urllib.request.urlopen = blocked
"""
SECRET = "fixture-secret"


def base_state():
    return {"projects": [], "items": [], "repos": {}}


class NewCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fakebin = self.root / "bin"
        self.fakebin.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.developer = self.root / "Developer"
        self.developer.mkdir()
        self.state_path = self.root / "state.json"
        self.gh_log = self.root / "gh.log"
        self.git_log = self.root / "git.log"
        self.op_log = self.root / "op.log"
        for path in (self.gh_log, self.git_log, self.op_log):
            path.write_text("")
        for name, content in (("gh", GH), ("git", GIT), ("op", OP)):
            path = self.fakebin / name
            path.write_text(content)
            path.chmod(0o755)
        (self.fakebin / "sitecustomize.py").write_text(SITE)
        self.real_git = shutil.which("git")
        if not self.real_git:
            raise RuntimeError("git is required for the new fixture test")
        self.write_state(base_state())
        self.env = self.environment()

    def yq_path(self):
        mise = shutil.which("mise")
        if not mise:
            raise RuntimeError("mise is required for the new fixture test")
        result = subprocess.run(  # nosec B603 B607 -- mise from PATH
            [mise, "which", "yq"], capture_output=True, text=True  # noqa: S607
        )
        yq = result.stdout.strip()
        if result.returncode != 0 or not yq:
            raise RuntimeError(f"yq is required for the new fixture test: {result.stderr.strip()}")
        return str(Path(yq).parent)

    def environment(self):
        env = dict(os.environ)
        for name in (
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "GITHUB_ENTERPRISE_TOKEN",
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_INDEX_FILE",
        ):
            env.pop(name, None)
        env.update(
            {
                "PATH": os.pathsep.join(
                    [
                        str(self.fakebin),
                        str(Path(sys.executable).parent),
                        self.yq_path(),
                        "/usr/bin",
                        "/bin",
                        "/usr/sbin",
                        "/sbin",
                    ]
                ),
                "PYTHONPATH": str(self.fakebin),
                "HOME": str(self.home),
                "FAKE_GH_STATE": str(self.state_path),
                "FAKE_GH_LOG": str(self.gh_log),
                "FAKE_GIT_LOG": str(self.git_log),
                "FAKE_OP_LOG": str(self.op_log),
                "FAKE_TEMPLATE": str(ROOT / "project"),
                "FAKE_TAG": TEST_TAG,
                "REAL_GIT": self.real_git,
                "OP_SERVICE_ACCOUNT_TOKEN": "fixture-token",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_AUTHOR_NAME": "new fixture",
                "GIT_AUTHOR_EMAIL": "fixtures@invalid",
                "GIT_COMMITTER_NAME": "new fixture",
                "GIT_COMMITTER_EMAIL": "fixtures@invalid",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        return env

    def state(self):
        return json.loads(self.state_path.read_text())

    def write_state(self, state):
        self.state_path.write_text(json.dumps(state))

    def reset_logs(self):
        self.gh_log.write_text("")
        self.git_log.write_text("")
        self.op_log.write_text("")
        state = self.state()
        for repo in state["repos"].values():
            repo["writes"] = []
        self.write_state(state)

    def gh_calls(self):
        return [json.loads(line) for line in self.gh_log.read_text().splitlines() if line]

    def git_calls(self):
        return [json.loads(line) for line in self.git_log.read_text().splitlines() if line]

    def run_new(self, *arguments, extra=None):
        environment = dict(self.env, **(extra or {}))
        return subprocess.run(  # nosec B603 -- the fixture runs a local script
            [
                sys.executable,
                str(NEW),
                *arguments,
                "--developer-root",
                str(self.developer),
            ],
            env=environment,
            capture_output=True,
            text=True,
        )

    def clone(self, name):
        return self.developer / name

    def git(self, name, *arguments):
        return subprocess.run(  # nosec B603 B607 -- real git in the fixture clone
            [self.real_git, "-C", str(self.clone(name)), *arguments],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def head(self, name):
        return self.git(name, "rev-parse", "HEAD").stdout.strip()

    def edit_clone(self, name, path, old, new):
        target = self.clone(name) / path
        text = target.read_text()
        self.assertIn(old, text)
        target.write_text(text.replace(old, new))
        added = self.git(name, "add", "-A")
        self.assertEqual(added.returncode, 0, added.stderr)
        committed = self.git(name, "commit", "-qm", f"edit {path}")
        self.assertEqual(committed.returncode, 0, committed.stderr)

    def seed_idea(self, title="Rocket", body="Fly to Mars."):
        state = self.state()
        state["projects"] = [{"number": 1, "title": "Ideas", "id": "PVT_1"}]
        state["items"] = [
            {
                "id": "PVTI_1",
                "project": 1,
                "title": title,
                "content": {
                    "type": "DraftIssue",
                    "id": "DI_1",
                    "title": title,
                    "body": body,
                },
            }
        ]
        self.write_state(state)

    def assert_handoff(self, output):
        positions = [
            output.index(step)
            for step in ("grill-with-docs", "brand-assets", "to-spec", "to-tickets")
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("may decline", output)

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
        self.assertTrue(any("push" in call for call in self.git_calls()))
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
            repo["secret_values"]["OPENCODE_ZEN_API_KEY"], SECRET
        )
        self.assertNotIn(SECRET, result.stdout + result.stderr)
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
        self.assertFalse(any("push" in call for call in self.git_calls()), self.git_calls())
        self.assertEqual(self.state()["repos"]["ranokay/one"]["writes"], [])
        self.assertNotIn(SECRET, second.stdout + second.stderr)

    def test_settings_failure_stops_before_the_handoff(self):
        result = self.run_new("drift-one", extra={"FAKE_MISSING_VENDOR": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("repository settings failed", result.stderr)
        self.assertNotIn("is ready", result.stdout)
        self.assertNotIn("hand-off", result.stdout)

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

    def test_an_unreadable_repository_is_not_mistaken_for_absent(self):
        result = self.run_new("one", extra={"FAKE_VIEW_ERROR": "1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot read ranokay/one", result.stderr)
        self.assertFalse(
            any(call[:2] == ["repo", "create"] for call in self.gh_calls()),
            self.gh_calls(),
        )

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

    def test_idea_seeds_an_unseeded_existing_repo(self):
        first = self.run_new("one")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.seed_idea()
        result = self.run_new("one", "--idea", "PVTI_1")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("promoted", result.stdout)
        product = (self.clone("one") / "PRODUCT.md").read_text()
        self.assertIn("# Rocket", product)
        self.assertIn("Fly to Mars.", product)
        self.assertNotIn("# <repo>", product)
        self.assertEqual(len(self.state()["repos"]["ranokay/one"]["issues"]), 1)

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
