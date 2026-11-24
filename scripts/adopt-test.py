import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADOPT = ROOT / "scripts/adopt.py"
GH = (ROOT / "scripts/fixtures/adopt/gh").read_text()
MARKER_RE = re.compile(
    r"<!--\s*factory:adopt\s+category=([a-z-]+)\s+finding=([0-9a-f]{12})\s*-->"
)
CODERABBIT = "reviews:\n  auto_review:\n    enabled: false\n"
GREPTILE = '{"autoReview": [], "statusCheck": false}'
OP = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

with Path(os.environ["FAKE_OP_LOG"]).open("a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
print("op stub: no secrets in adopt tests", file=sys.stderr)
sys.exit(1)
"""
SITE = """import socket
import urllib.request


def blocked(*args, **kwargs):
    raise RuntimeError("network blocked in adopt test")


socket.create_connection = blocked
urllib.request.urlopen = blocked
"""
FILES = {
    "main.ts": 'import { used } from "./src/used";\nconsole.log(used);\n',
    "src/used.ts": "export const used = 1; // TODO: replace\n",
    "src/unused.ts": "export const unused = 2;\n",
    "tests/example.test.ts": 'import { used } from "../src/used";\nexport const covered = used;\n',
    "AGENTS.md": "# Fixture\n\nOld instructions.\n",
    "CLAUDE.md": "Old instructions\n",
    "README.md": "# Fixture\n",
    "renovate.json": '{"$schema": "https://docs.renovatebot.com/renovate-schema.json", "extends": []}\n',
    ".coderabbit.yaml": CODERABBIT,
    ".greptile/config.json": GREPTILE,
    ".DS_Store": "",
    "notes.txt.orig": "old\n",
}
LABELS = [
    {"name": "bug", "color": "d73a4a", "description": "Something isn't working", "default": True},
    {"name": "documentation", "color": "0075ca", "description": "Improvements or additions to documentation", "default": True},
    {"name": "duplicate", "color": "cfd3d7", "description": "This issue or pull request already exists", "default": True},
    {"name": "enhancement", "color": "a2eeef", "description": "New feature or request", "default": True},
    {"name": "good first issue", "color": "7057ff", "description": "Good for newcomers", "default": True},
    {"name": "help wanted", "color": "008672", "description": "Extra attention is needed", "default": True},
    {"name": "invalid", "color": "e4e669", "description": "This doesn't seem right", "default": True},
    {"name": "question", "color": "d876e3", "description": "Further information is requested", "default": True},
    {"name": "wontfix", "color": "ffffff", "description": "This will not be worked on", "default": True},
]


def base_state():
    return {
        "name": "o/one",
        "repo": {
            "name": "one",
            "full_name": "o/one",
            "private": True,
            "fork": False,
            "default_branch": "main",
            "allow_squash_merge": False,
            "allow_merge_commit": False,
            "allow_rebase_merge": True,
            "allow_auto_merge": True,
        },
        "topics": {"names": []},
        "labels": LABELS,
        "rulesets": [],
        "approval": {"approval_policy": "first_time_contributors"},
        "private_settings": {
            "run_workflows_from_fork_pull_requests": False,
            "send_write_tokens_to_workflows": True,
            "send_secrets_and_variables": True,
            "require_approval_for_fork_pr_workflows": False,
        },
        "secrets": [{"name": "OPENCODE_ZEN_API_KEY"}],
        "files": {".coderabbit.yaml": CODERABBIT, ".greptile/config.json": GREPTILE},
        "issues": [],
    }


class Adopt(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "repo"
        self.fakebin = self.root / "bin"
        self.fakebin.mkdir()
        self.log = self.root / "gh.log"
        self.op_log = self.root / "op.log"
        self.state_path = self.root / "state.json"
        self.log.write_text("")
        self.op_log.write_text("")
        self.write_fakes()
        self.env = self.environment()
        self.reset_state()
        self.write_repo()

    def write_fakes(self):
        for name, content in (("gh", GH), ("op", OP), ("sitecustomize.py", SITE)):
            path = self.fakebin / name
            path.write_text(content)
            if name != "sitecustomize.py":
                path.chmod(0o755)

    def environment(self):
        yq = shutil.which("yq")
        if not yq:
            raise RuntimeError("yq is required for the adopt fixture test; run it through mise")
        path = os.pathsep.join(
            [
                str(self.fakebin),
                str(Path(sys.executable).parent),
                str(Path(yq).parent),
                "/usr/bin",
                "/bin",
                "/usr/sbin",
                "/sbin",
            ]
        )
        env = dict(os.environ)
        for name in (
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "GITHUB_ENTERPRISE_TOKEN",
            "OP_SERVICE_ACCOUNT_TOKEN",
        ):
            env.pop(name, None)
        env.update(
            {
                "PATH": path,
                "PYTHONPATH": str(self.fakebin),
                "FAKE_GH_STATE": str(self.state_path),
                "FAKE_GH_LOG": str(self.log),
                "FAKE_OP_LOG": str(self.op_log),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        return env

    def reset_state(self, **overrides):
        state = base_state()
        state.update(overrides)
        self.write_state(state)

    def state(self):
        return json.loads(self.state_path.read_text())

    def write_state(self, state):
        self.state_path.write_text(json.dumps(state))

    def git(self, *arguments):
        return subprocess.run(  # nosec B603 B607 -- git from the controlled PATH
            ["git", *arguments],
            cwd=str(self.project),
            env=self.env,
            capture_output=True,
            text=True,
        )

    def commit(self, message):
        added = self.git("add", "-A")
        self.assertEqual(added.returncode, 0, added.stderr)
        committed = self.git("commit", "-qm", message)
        self.assertEqual(committed.returncode, 0, committed.stderr)

    def write_repo(self):
        self.project.mkdir()
        for path, content in FILES.items():
            target = self.project / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        for arguments in (
            ("init", "-q", "--initial-branch=main"),
            ("config", "user.name", "adopt test"),
            ("config", "user.email", "adopt@example.invalid"),
            ("config", "commit.gpgsign", "false"),
        ):
            result = self.git(*arguments)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.commit("fixture")
        remote = self.git("remote", "add", "origin", "https://github.com/o/one.git")
        self.assertEqual(remote.returncode, 0, remote.stderr)

    def run_adopt(self, *arguments):
        return subprocess.run(  # nosec B603 -- this checkout's adopt CLI
            [sys.executable, str(ADOPT), "o/one", "--project", str(self.project), *arguments],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def calls(self):
        return [
            json.loads(line)
            for line in self.log.read_text().splitlines()
            if line.strip()
        ]

    def reset_log(self):
        self.log.write_text("")
        self.op_log.write_text("")

    def issues(self):
        return self.state()["issues"]

    def markers(self, issue):
        return [(match.group(1), match.group(2)) for match in MARKER_RE.finditer(issue["body"])]

    def categories(self, issue):
        return {match.group(1) for match in MARKER_RE.finditer(issue["body"])}

    def issue_for(self, issues, category):
        matches = [issue for issue in issues if category in self.categories(issue)]
        self.assertEqual(len(matches), 1, [(issue["number"], self.categories(issue)) for issue in issues])
        return matches[0]

    def snapshot(self):
        found = {}
        for path in sorted(self.project.rglob("*")):
            relative = path.relative_to(self.project).as_posix()
            if relative == ".git" or relative.startswith(".git/"):
                continue
            if path.is_symlink():
                found[relative] = ["symlink", os.readlink(path)]
            elif path.is_file():
                found[relative] = ["file", hashlib.sha256(path.read_bytes()).hexdigest()]
            else:
                found[relative] = ["dir"]
        return found

    def assert_unchanged(self, before):
        self.assertEqual(self.snapshot(), before)
        status = self.git("status", "--porcelain")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(status.stdout, "")

    def no_creates(self):
        self.assertFalse(
            [call for call in self.calls() if call[:2] == ["issue", "create"]],
            self.calls(),
        )

    def settings_writes(self):
        return [
            call
            for call in self.calls()
            if call[:1] == ["api"]
            and "--method" in call
            and call[call.index("--method") + 1] in {"POST", "PUT", "PATCH", "DELETE"}
        ]

    def test_first_run_files_one_issue_per_category_with_canonical_labels(self):
        before = self.snapshot()
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        issues = self.issues()
        self.assertEqual(len(issues), 8)
        by_category = {}
        for issue in issues:
            categories = self.categories(issue)
            self.assertEqual(len(categories), 1, issue)
            by_category[categories.pop()] = issue
        self.assertEqual(
            set(by_category),
            {
                "tooling",
                "comments",
                "dead-code",
                "agents",
                "stale-files",
                "verification",
                "test-pruning",
                "brand",
            },
        )
        for category in by_category:
            labels = set(by_category[category]["labels"])
            if category == "brand":
                self.assertEqual(labels, {"ready-for-human", "enhancement"})
            else:
                self.assertEqual(labels, {"ready-for-agent", "enhancement"})
        self.assertEqual(
            {category: len(self.markers(issue)) for category, issue in by_category.items()},
            {
                "tooling": 3,
                "comments": 1,
                "dead-code": 1,
                "agents": 3,
                "stale-files": 2,
                "verification": 1,
                "test-pruning": 1,
                "brand": 1,
            },
        )
        self.assertIn("mise.toml", by_category["tooling"]["body"])
        self.assertIn("src/used.ts", by_category["comments"]["body"])
        self.assertIn("src/unused.ts", by_category["dead-code"]["body"])
        self.assertNotIn("src/used.ts", by_category["dead-code"]["body"])
        self.assertIn("Profiles", by_category["agents"]["body"])
        self.assertIn(".DS_Store", by_category["stale-files"]["body"])
        self.assertIn("verify-", by_category["verification"]["body"])
        self.assertIn("tests/example.test.ts", by_category["test-pruning"]["body"])
        self.assertIn("prune-tests", by_category["test-pruning"]["body"])
        self.assertIn("brand-assets", by_category["brand"]["body"])

        calls = self.calls()
        first_issue = next(
            index for index, call in enumerate(calls) if call[:2] == ["issue", "create"]
        )
        before_issues = calls[:first_issue]
        self.assertTrue(
            any(
                call[:3] == ["api", "--method", "DELETE"]
                and any("labels/documentation" in argument for argument in call)
                for call in before_issues
            ),
            before_issues,
        )
        self.assertTrue(
            any(
                call[:3] == ["api", "--method", "POST"]
                and any("rulesets" in argument for argument in call)
                for call in before_issues
            ),
            before_issues,
        )
        names = {label["name"] for label in self.state()["labels"]}
        for label in ("ready-for-agent", "ready-for-human", "enhancement", "blocked"):
            self.assertIn(label, names)
        self.assertNotIn("documentation", names)
        self.assert_unchanged(before)

    def test_second_run_files_nothing_and_applies_no_settings(self):
        first = self.run_adopt()
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        filed = len(self.issues())
        before = self.snapshot()
        self.reset_log()
        second = self.run_adopt()
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(len(self.issues()), filed)
        self.assertIn("no new findings", second.stdout)
        self.no_creates()
        self.assertEqual(self.settings_writes(), [], self.calls())
        self.assert_unchanged(before)
        self.assertEqual(self.op_log.read_text(), "")

    def test_closed_issues_suppress_repeats(self):
        first = self.run_adopt()
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        state = self.state()
        for issue in state["issues"]:
            issue["state"] = "closed"
        self.write_state(state)
        self.reset_log()
        second = self.run_adopt()
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertIn("no new findings", second.stdout)
        self.no_creates()
        self.assertEqual(len(self.issues()), 8)

    def test_new_finding_files_only_the_new_finding(self):
        first = self.run_adopt()
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        old_issues = self.issues()
        old_comments = self.issue_for(old_issues, "comments")
        old_body = old_comments["body"]
        used = self.project / "src/used.ts"
        used.write_text(used.read_text() + "// FIXME: replace this too\n")
        self.commit("add a finding")
        self.reset_log()
        second = self.run_adopt()
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        issues = self.issues()
        self.assertEqual(len(issues), len(old_issues) + 1)
        comments = [issue for issue in issues if "comments" in self.categories(issue)]
        self.assertEqual(len(comments), 2)
        latest = max(comments, key=lambda issue: issue["number"])
        self.assertEqual(len(self.markers(latest)), 1)
        self.assertIn("FIXME", latest["body"])
        self.assertIn("src/used.ts", latest["body"])
        self.assertNotIn("TODO", latest["body"])
        old_numbers = {issue["number"] for issue in old_issues}
        new_numbers = {issue["number"] for issue in issues} - old_numbers
        self.assertEqual(len(new_numbers), 1)
        new_issue = next(issue for issue in issues if issue["number"] in new_numbers)
        self.assertEqual(self.categories(new_issue), {"comments"})
        self.assertEqual(
            next(issue for issue in issues if issue["number"] == old_comments["number"])["body"],
            old_body,
        )
        self.assertEqual(self.settings_writes(), [], self.calls())

    def test_unresolved_settings_drift_files_issues_then_fails(self):
        state = self.state()
        state["files"] = {}
        self.write_state(state)
        before = self.snapshot()
        result = self.run_adopt()
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertEqual(len(self.issues()), 8)
        self.assertIn("needs PR", result.stdout)
        self.assertIn("unresolved drift", result.stderr)
        self.assert_unchanged(before)

    def test_upstream_fork_files_nothing_and_says_why(self):
        state = self.state()
        state["repo"].update({"fork": True, "private": True})
        state["topics"]["names"] = ["factory-upstream-fork"]
        self.write_state(state)
        self.reset_log()
        before = self.snapshot()
        result = self.run_adopt()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.issues(), [])
        self.assertIn("Upstream Fork", result.stdout)
        self.assertIn("filing nothing", result.stdout)
        self.no_creates()
        self.assert_unchanged(before)

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

    def test_dry_run_files_nothing_and_writes_no_settings(self):
        before = self.snapshot()
        result = self.run_adopt("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.issues(), [])
        self.assertIn("would file", result.stdout)
        self.no_creates()
        self.assertEqual(self.settings_writes(), [], self.calls())
        self.assert_unchanged(before)

    def test_environment_shadows_gh_and_op(self):
        for name in ("gh", "op"):
            resolved = shutil.which(name, path=self.env["PATH"])
            self.assertEqual(Path(resolved).parent, self.fakebin, name)
        result = self.run_adopt("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.op_log.read_text(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
