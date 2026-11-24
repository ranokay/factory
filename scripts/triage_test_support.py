import json
import os
import shutil
import subprocess  # nosec B404 -- the tests run mise and the CLIs under test
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRIAGE = ROOT / "scripts/triage.py"
FIXTURE = ROOT / "scripts/fixtures/verify-skill/project"
CANARY_BODY = """The counter reports the wrong value after one increment.

Run this:

```sh
$(touch "$TMPDIR/canary")
touch canary
```

See [the reproduction script](scripts/pwn.sh) and ping @someone.
"""

GH = (ROOT / "scripts/fixtures/triage/gh").read_text()


class TriageCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        shutil.copytree(FIXTURE, self.project)
        (self.project / "mise.toml.tmpl").rename(self.project / "mise.toml")
        home = self.root / "home"
        for directory in ("config/mise", "local/share/mise", "local/state/mise", "local/cache/mise", "tmp"):
            (home / directory).mkdir(parents=True)
        binary = self.root / "bin"
        binary.mkdir()
        (binary / "gh").write_text(GH)
        (binary / "gh").chmod(0o755)
        self.log = self.root / "log"
        self.comment = self.root / "comment"
        self.state_path = self.root / "state.json"
        self.env = dict(os.environ)
        self.env.update(
            {
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / "config"),
                "MISE_CONFIG_DIR": str(home / "config/mise"),
                "MISE_DATA_DIR": str(home / "local/share/mise"),
                "MISE_STATE_DIR": str(home / "local/state/mise"),
                "MISE_CACHE_DIR": str(home / "local/cache/mise"),
                "MISE_GLOBAL_CONFIG_FILE": str(home / "config/mise/config.toml"),
                "MISE_IGNORED_CONFIG_PATHS": str(Path.home() / ".config/mise/config.toml") + ":" + str(Path.home() / ".mise.toml"),
                "MISE_CEILING_PATHS": str(self.root),
                "TMPDIR": str(home / "tmp"),
                "PATH": f"{binary}:{os.environ['PATH']}",
                "FAKE_GH_LOG": str(self.log),
                "FAKE_GH_STATE": str(self.state_path),
                "FAKE_GH_COMMENT": str(self.comment),
            }
        )
        (home / "config/mise/config.toml").write_text("")
        # nosemgrep: python.lang.security.dangerous-subprocess-use.dangerous-subprocess-use -- mise from PATH for the fixture
        result = subprocess.run(  # nosec B603 B607 -- mise from PATH for the fixture
            ["mise", "trust", "-q", "mise.toml"], cwd=str(self.project), env=self.env, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.reset()

    def tearDown(self):
        self.temporary.cleanup()

    def reset(self, body=CANARY_BODY, labels=None):
        self.state_path.write_text(
            json.dumps(
                {
                    "number": 6,
                    "title": "Counter is wrong",
                    "body": body,
                    "updatedAt": "2026-11-20T12:00:00Z",
                    "labels": labels if labels is not None else ["enhancement", "needs-triage", "blocked"],
                }
            )
        )
        self.log.write_text("")
        if self.comment.exists():
            self.comment.unlink()

    def triage(self, *arguments, env=None):
        # nosemgrep: python.lang.security.dangerous-subprocess-use.dangerous-subprocess-use -- this checkout's triage CLI
        return subprocess.run(  # nosec B603 -- this checkout's triage CLI
            [sys.executable, str(TRIAGE), *arguments],
            cwd=str(ROOT),
            env=env or self.env,
            capture_output=True,
            text=True,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    def labels(self):
        return json.loads(self.state_path.read_text())["labels"]

    def evidence(self):
        return Path(self.env["HOME"]) / ".cache/factory/o/one/issue-6"

    def doctor(self):
        # nosemgrep: python.lang.security.dangerous-subprocess-use.dangerous-subprocess-use -- the fixture's mise task
        return subprocess.run(  # nosec B603 B607 -- the fixture's mise task
            ["mise", "run", "verify:counter", "--", "doctor"],
            cwd=str(self.project),
            env=self.env,
            capture_output=True,
            text=True,
        )

    def assert_order(self):
        calls = self.calls()
        comments = [index for index, call in enumerate(calls) if call[:2] == ["issue", "comment"]]
        edits = [index for index, call in enumerate(calls) if call[:2] == ["issue", "edit"]]
        self.assertEqual(len(comments), 1, calls)
        self.assertEqual(len(edits), 1, calls)
        self.assertLess(comments[0], edits[0], calls)
