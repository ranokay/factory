import json
import os
import shutil
import subprocess  # nosec B404 -- the tests drive the CLI under test
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUSHBACK = ROOT / "scripts/pushback.py"
FIXTURE = ROOT / "scripts/fixtures/pushback/project"
GH = (ROOT / "scripts/fixtures/pushback/gh").read_text()
TRIAGE_MARKER = "<!-- factory:triage -->"


class PushbackCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        shutil.copytree(FIXTURE, self.project)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(GH)
        gh.chmod(0o755)
        self.log = self.root / "gh.log"
        self.state_path = self.root / "state.json"
        self.env = dict(
            os.environ,
            PATH=f"{self.bin}:{os.environ['PATH']}",
            FAKE_GH_STATE=str(self.state_path),
            FAKE_GH_LOG=str(self.log),
        )
        self.reset()

    def reset(self):
        def issue(number, title, body, labels):
            return {
                "number": number,
                "title": title,
                "body": body,
                "labels": [{"name": name} for name in labels],
                "author": {"login": "ranokay"},
                "comments": [],
            }

        self.write_state(
            {
                "issues": {
                    "1": issue(
                        1,
                        "Add a web dashboard",
                        "Show reading stats in a web dashboard.",
                        ["enhancement", "needs-triage"],
                    ),
                    "2": issue(
                        2,
                        "Add JSON output to stats",
                        "Make `reading stats` print JSON for scripts.",
                        ["enhancement", "needs-triage"],
                    ),
                    "3": issue(
                        3,
                        "Count pages read this week",
                        "`reading report` prints a total; add a weekly count.",
                        ["enhancement", "needs-triage"],
                    ),
                },
                "search": [],
            }
        )
        self.log.write_text("")

    def write_state(self, state):
        self.state_path.write_text(json.dumps(state))

    def state(self):
        return json.loads(self.state_path.read_text())

    def issue(self, number):
        return self.state()["issues"][str(number)]

    def labels(self, number):
        return [label["name"] for label in self.issue(number)["labels"]]

    def comments(self, number):
        return self.issue(number).get("comments", [])

    def calls(self):
        return [
            json.loads(line)
            for line in self.log.read_text().splitlines()
            if line.strip()
        ]

    def cli(self, *arguments, env=None):
        return subprocess.run(  # nosec B603 -- this checkout's pushback CLI  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- this checkout's pushback CLI
            [sys.executable, str(PUSHBACK), *arguments],
            cwd=str(ROOT),
            env=env or self.env,
            capture_output=True,
            text=True,
        )

    def context(self, number):
        result = self.cli(
            "context", "o/one", str(number), "--project", str(self.project)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def verdict_file(self, data):
        path = self.root / "verdict.json"
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return str(path)

    def record(self, number, data, *extra):
        return self.cli(
            "record",
            "o/one",
            str(number),
            "--from",
            self.verdict_file(data),
            "--project",
            str(self.project),
            *extra,
        )

    def gate(self, number):
        return self.cli("gate", "o/one", str(number))

    def env_without_gh(self):
        empty = self.root / "empty-bin"
        empty.mkdir(exist_ok=True)
        return dict(self.env, PATH=f"{empty}:/usr/bin:/bin")

    def add_comment(self, number, body):
        state = self.state()
        state["issues"][str(number)].setdefault("comments", []).append(
            {
                "body": body,
                "url": f"https://example.test/issue/{number}#issuecomment-seed",
                "author": {"login": "ranokay"},
                "createdAt": "2026-11-25T00:00:00Z",
            }
        )
        self.write_state(state)
