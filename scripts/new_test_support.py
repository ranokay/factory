import json
import os
import shutil
import subprocess  # nosec B404 -- the fixture drives the local CLI and git
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
print("fixture-value", end="")
"""
SITE = """import socket
import urllib.request


def blocked(*args, **kwargs):
    raise RuntimeError("network blocked in new tests")


socket.create_connection = blocked
urllib.request.urlopen = blocked
"""
OP_VALUE = "fixture-value"


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
        result = subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- mise from PATH
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
                "OP_SERVICE_ACCOUNT_TOKEN": "fixture-" + "token",
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
        return subprocess.run(  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- the fixture runs a local script
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
        return subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- real git in the fixture clone
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
