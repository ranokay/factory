import copy
import json
import os
import re
import shutil
import subprocess  # nosec B404 -- the tests drive the CLI under test
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


SCRIPTS = ROOT / "scripts"


PASS = SCRIPTS / "promotion-consolidate.py"


PLAN = SCRIPTS / "promotion-consolidation.json"


FIXTURE = SCRIPTS / "fixtures/promotion-consolidate/open-promotions.json"


GH = SCRIPTS / "fixtures/promotion-consolidate/gh"


TRACKING = "<!-- factory:promotion-consolidate-tracking -->"


MUTATING = {("issue", "comment"), ("issue", "edit"), ("issue", "close"), ("issue", "create")}


WORKFLOW = """name: CI
on: push
jobs:
  lint:
    runs-on: ubuntu-latest
    env:
      TOKEN:
        value: abc
    steps:
      - name: Typecheck
        uses: actions/checkout@v4
        with:
          image:
            tag: x
"""


NAMED_CLUSTERS = [[79, 80, 163, 206], [104, 105], [64, 109], [89, 211]]


FIXTURE_COVERED = {"issue": 52, "check": {"file": "hk.pkl", "name": "gitleaks-test"}, "reason": "Test-only mapping that exercises the covered path."}


def fixture_state():
    issues = json.loads(FIXTURE.read_text())["issues"]
    return {"issues": {str(item["number"]): item for item in issues}, "mutations": 0, "next_comment": 1, "next_issue": 900}


def normalized(state):
    clean = copy.deepcopy(state)
    clean.pop("mutations", None)
    return json.dumps(clean, sort_keys=True, indent=1)


def without_summaries(state):
    clean = copy.deepcopy(state)
    for item in clean["issues"].values():
        item["comments"] = [c for c in item.get("comments", []) if "factory:promotion-consolidate-run id=" not in c["body"]]
    return clean


def proposal_members(stdout):
    section = stdout.split("Proposed clusters (not applied)")[1].split("Confirm a proposal")[0]
    groups = []
    for line in section.splitlines():
        match = re.match(r"- (.*?) \(shared terms:|- not proposed, \d+ members.*?: (.*)$", line)
        if match:
            text = match.group(1) or match.group(2)
            groups.append([int(n.strip().lstrip("#")) for n in text.split(",")])
    return groups


class Pass(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        shutil.copy(GH, gh)
        gh.chmod(0o755)
        self.log = self.root / "gh.log"
        self.state_path = self.root / "state.json"
        self.plan_path = self.root / "plan.json"
        self.plan_path.write_text(PLAN.read_text())
        self.work = self.root / "work"
        self.make_integration_clone()
        self.write_state(fixture_state())

    def make_integration_clone(self):
        remote = self.root / "remote.git"
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.test"]
        subprocess.run(["git", "init", "--bare", "--quiet", "-b", "main", str(remote)], check=True)  # nosec B603 B607  # nosemgrep -- the test runs git and the pass with fixed argument lists
        self.work.mkdir()
        for name in (".gitleaks.toml", "hk.pkl"):
            shutil.copy(ROOT / name, self.work / name)
        (self.work / ".github" / "workflows").mkdir(parents=True)
        (self.work / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW)
        git = ["git", "-C", str(self.work)]
        subprocess.run([*git, "init", "--quiet", "-b", "main"], check=True)  # nosec B603 B607  # nosemgrep -- the test runs git and the pass with fixed argument lists
        subprocess.run([*git, "add", "."], check=True)  # nosec B603 B607  # nosemgrep -- the test runs git and the pass with fixed argument lists
        subprocess.run([*git, *identity, "commit", "--quiet", "-m", "fixture"], check=True)  # nosec B603 B607  # nosemgrep -- the test runs git and the pass with fixed argument lists
        subprocess.run([*git, "remote", "add", "origin", str(remote)], check=True)  # nosec B603 B607  # nosemgrep -- the test runs git and the pass with fixed argument lists
        subprocess.run([*git, "push", "--quiet", "origin", "main"], check=True)  # nosec B603 B607  # nosemgrep -- the test runs git and the pass with fixed argument lists

    def write_state(self, state):
        self.state_path.write_text(json.dumps(state, sort_keys=True))

    def read_state(self):
        return json.loads(self.state_path.read_text())

    def write_plan(self, plan):
        self.plan_path.write_text(json.dumps(plan))

    def run_pass(self, *extra, fail_after=None, plan=None, run_id=None):
        env = dict(
            os.environ,
            PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            FAKE_GH_STATE=str(self.state_path),
            FAKE_GH_LOG=str(self.log),
        )
        env.pop("FAKE_GH_FAIL_AFTER", None)
        if fail_after is not None:
            env["FAKE_GH_FAIL_AFTER"] = str(fail_after)
        command = [
            sys.executable, str(PASS), "--plan", str(plan or self.plan_path),
            "--repo", "ranokay/factory", "--date", "2026-11-27", "--root", str(self.work), *extra,
        ]
        if run_id is not None:
            command += ["--run-id", run_id]
        return subprocess.run(  # nosec B603 -- the test runs the pass with an argument list  # nosemgrep -- the test runs git and the pass with fixed argument lists
            command, capture_output=True, text=True, check=False, env=env, timeout=120
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def writes(self):
        return [args for args in self.calls() if tuple(args[:2]) in MUTATING or (args[0] == "api" and "PATCH" in args)]

    def tracking(self, state):
        for item in state["issues"].values():
            if TRACKING in item["body"]:
                return item
        return None

    def summaries(self, state):
        tracking = self.tracking(state)
        if tracking is None:
            return []
        return [c for c in tracking["comments"] if "factory:promotion-consolidate-run id=" in c["body"]]

    def comment_bodies(self, item):
        return "\n".join(c["body"] for c in item.get("comments", []))

    def labels(self, item):
        return [label["name"] for label in item["labels"]]
