import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404 -- the fixture drives git, mise and the CLIs under test
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
    "-h": "",
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


class AdoptCase(unittest.TestCase):
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

    def tool_paths(self):
        mise = shutil.which("mise")
        if not mise:
            raise RuntimeError("mise is required for the adopt fixture test")
        result = subprocess.run(  # nosec B603 B607  # noqa: S603 S607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit, python.lang.security.dangerous-subprocess-use.dangerous-subprocess-use -- mise from PATH
            [mise, "which", "yq"], capture_output=True, text=True  # noqa: S607
        )
        yq = result.stdout.strip()
        if result.returncode != 0 or not yq:
            raise RuntimeError(f"yq is required for the adopt fixture test: {result.stderr.strip()}")
        return os.pathsep.join(
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

    def environment(self):
        env = dict(os.environ)
        for name in (
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "GITHUB_ENTERPRISE_TOKEN",
            "OP_SERVICE_ACCOUNT_TOKEN",
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_INDEX_FILE",
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_SYSTEM",
        ):
            env.pop(name, None)
        home = self.root / "home"
        home.mkdir()
        env.update(
            {
                "PATH": self.tool_paths(),
                "PYTHONPATH": str(self.fakebin),
                "FAKE_GH_STATE": str(self.state_path),
                "FAKE_GH_LOG": str(self.log),
                "FAKE_OP_LOG": str(self.op_log),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "HOME": str(home),
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
        return subprocess.run(  # nosec B603 B607  # noqa: S603 S607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit, python.lang.security.dangerous-subprocess-use.dangerous-subprocess-use -- git from the controlled PATH
            ["git", *arguments],  # noqa: S607
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

    def write_tooling(self, includes, tasks, hk):
        (self.project / "mise.toml").write_text(
            f"include = {json.dumps(includes)}\n\n"
            f"[task_config]\nincludes = {json.dumps(tasks)}\n"
        )
        (self.project / "hk.pkl").write_text(f'amends "{hk}"\n')
        (self.project / "renovate.json").write_text(
            '{"extends": ["github>ranokay/factory"]}\n'
        )
        self.write_blocked_caller()
        self.commit("add tooling")

    def write_blocked_caller(self, uses=None, quote="", newline="\n", comment=""):
        path = self.project / ".github/workflows/blocked.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = (ROOT / "project/.github/workflows/blocked.yml").read_text()
        if uses is not None:
            replacement = f"{quote}{uses}{quote}{comment}"
            text = re.sub(r"(?m)^(    uses:).*$", rf"\1 {replacement}", text)
        path.write_bytes(text.replace("\n", newline).encode())

    def use_factory_state(self, repo="ranokay/factory"):
        self.reset_state()
        state = self.state()
        state["name"] = repo
        state["repo"]["name"] = repo.split("/")[1]
        state["repo"]["full_name"] = repo
        self.write_state(state)
        origin = self.git("remote", "set-url", "origin", f"https://github.com/{repo}.git")
        self.assertEqual(origin.returncode, 0, origin.stderr)
        self.reset_log()

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

    def add_verification_skill(self, name="verify-counter"):
        skill = self.project / ".agents/skills" / name
        (skill / "references/features").mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: fixture\n---\n"
        )
        (skill / "references/features/feature.md").write_text("feature\n")
        link = self.project / ".claude/skills" / name
        link.parent.mkdir(parents=True)
        os.symlink(f"../../.agents/skills/{name}", link)

    def run_adopt(self, *arguments, repo="o/one"):
        return subprocess.run(  # nosec B603  # noqa: S603 S607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit, python.lang.security.dangerous-subprocess-use.dangerous-subprocess-use -- this checkout's adopt CLI
            [sys.executable, str(ADOPT), repo, "--project", str(self.project), *arguments],  # noqa: S607
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
