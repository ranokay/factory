import re
import subprocess  # nosec B404 -- the test runs the local check script
import sys
import tempfile
import unittest
from pathlib import Path

from profiles_reader import BLOCKER_CALLER, BLOCKER_WORKFLOW

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/blocked-caller-check.py"
FACTORY = ROOT / BLOCKER_CALLER
TEMPLATE = ROOT / "project" / BLOCKER_CALLER
PINNED_USES = f"{BLOCKER_WORKFLOW}@v0.4.0"


def with_uses(path, uses):
    return re.sub(r"(?m)^(    uses:).*$", rf"\1 {uses}", path.read_text())


def run(factory, template):
    return subprocess.run(  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- the test runs the local check script
        [sys.executable, str(SCRIPT), "--factory", str(factory), "--template", str(template)],
        capture_output=True,
        text=True,
        check=False,
    )


class BlockedCallerCheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.template = self.write("template.yml", with_uses(TEMPLATE, PINNED_USES))

    def write(self, name, text):
        path = self.root / name
        path.write_text(text)
        return path

    def assert_rejected(self, text, message):
        result = run(FACTORY, self.write("changed.yml", text))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)

    def test_the_repository_template_matches_the_factory(self):
        result = run(FACTORY, TEMPLATE)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_drift_in_the_contract_fails(self):
        cases = (
            ("trigger", "  issues:\n    types: [labeled]\n", "  push:\n"),
            (
                "label condition",
                "github.event.label.name == 'blocked'",
                "github.event.label.name == 'urgent'",
            ),
            ("job permission", "      issues: write\n", "      issues: read\n"),
            ("top-level permission", "permissions: {}\n", "permissions:\n  contents: read\n"),
        )
        for name, old, new in cases:
            with self.subTest(name):
                changed = with_uses(TEMPLATE, PINNED_USES).replace(old, new)
                self.assert_rejected(changed, "does not match")

    def test_a_branch_ref_fails(self):
        self.assert_rejected(
            with_uses(TEMPLATE, f"{BLOCKER_WORKFLOW}@main"),
            "vX.Y.Z",
        )

    def test_a_commit_sha_fails(self):
        self.assert_rejected(
            with_uses(TEMPLATE, f"{BLOCKER_WORKFLOW}@" + "a" * 40),
            "vX.Y.Z",
        )

    def test_a_prerelease_tag_is_accepted(self):
        result = run(
            FACTORY,
            self.write(
                "prerelease.yml",
                with_uses(TEMPLATE, f"{BLOCKER_WORKFLOW}@v0.5.0-rc.1"),
            ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_factory_caller_must_use_its_local_workflow(self):
        remote = self.write("remote-factory.yml", with_uses(FACTORY, PINNED_USES))
        result = run(remote, self.template)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local", result.stderr)

    def test_the_command_names_the_offending_file(self):
        result = run(FACTORY, self.template)
        self.assertEqual(result.returncode, 0, result.stderr)
        changed = self.write(
            "changed.yml",
            with_uses(TEMPLATE, PINNED_USES).replace(
                "github.event.label.name == 'blocked'",
                "github.event.label.name == 'urgent'",
            ),
        )
        result = run(FACTORY, changed)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("blocked-caller:", result.stderr)
        self.assertIn(str(changed), result.stderr)


if __name__ == "__main__":
    unittest.main()
