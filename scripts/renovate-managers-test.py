#!/usr/bin/env python3

import json
import os
import shutil
import subprocess  # nosec B404 -- the Gate runs the pinned local Renovate binary
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PRESET = ROOT / "default.json"
TAG = "v0.2.0"
FIXTURE_SHA = "91cc9539fd82101c0cc9062810d4cdb9cdaef55a"
BLOCKED_PROXY = "http://127.0.0.1:9"

MISE = f"""\
include = [
  "git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref={TAG}",
  "git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=main",
  "git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref={FIXTURE_SHA}",
]

[task_config]
includes = [
  "git::https://github.com/ranokay/factory.git//profiles/universal/tasks.toml?ref={TAG}",
]
"""

HK = f"""\
amends "https://raw.githubusercontent.com/ranokay/factory/{TAG}/profiles/universal/hk.pkl"
amends "https://raw.githubusercontent.com/ranokay/factory/main/profiles/universal/hk.pkl"
amends "https://raw.githubusercontent.com/ranokay/factory/{FIXTURE_SHA}/profiles/universal/hk.pkl"
local const fixture = \"\"\"
  amends "https://raw.githubusercontent.com/ranokay/factory/{TAG}/profiles/universal/hk.pkl"
  \"\"\"
"""

WORKFLOW = f"""\
name: Check
on: pull_request
jobs:
  pinned:
    uses: ranokay/factory/.github/workflows/gates.yml@{TAG}
  branch:
    uses: ranokay/factory/.github/workflows/gates.yml@main
  sha:
    uses: ranokay/factory/.github/workflows/gates.yml@{FIXTURE_SHA}
"""

EXPECTED_PINS = {
    "mise.toml": MISE.count(f"?ref={TAG}"),
    "hk.pkl": len(
        [
            line
            for line in HK.splitlines()
            if line.startswith(
                f'amends "https://raw.githubusercontent.com/ranokay/factory/{TAG}/'
            )
        ]
    ),
    ".github/workflows/check.yml": WORKFLOW.count(f"@{TAG}"),
}


def renovate_binary():
    override = os.environ.get("RENOVATE_BINARY")
    if override:
        if not Path(override).exists():
            raise AssertionError(f"RENOVATE_BINARY points to a missing path: {override}")
        return override
    mise = shutil.which("mise")
    if mise is None:
        raise AssertionError("mise is not on PATH; run the Gate with mise run check")
    result = subprocess.run(  # nosec B603  # nosemgrep -- fixed mise command
        [mise, "-C", str(ROOT), "which", "renovate"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(
            f"mise could not resolve renovate from mise.toml\n{result.stderr[-2000:]}"
        )
    path = Path(result.stdout.strip())
    if not path.exists():
        raise AssertionError(f"mise resolved renovate to a missing path: {path}")
    return str(path)


def extract(fixture):
    renovate = renovate_binary()
    env = {
        key: os.environ[key]
        for key in ("HOME", "LANG", "PATH", "TMPDIR")
        if key in os.environ
    }
    env.update(
        {
            "RENOVATE_CONFIG_FILE_NAMES": "default.json",
            "LOG_FORMAT": "json",
            "LOG_LEVEL": "debug",
            "HTTP_PROXY": BLOCKED_PROXY,
            "HTTPS_PROXY": BLOCKED_PROXY,
            "ALL_PROXY": BLOCKED_PROXY,
            "NO_PROXY": "",
        }
    )
    try:
        result = subprocess.run(  # nosec B603  # nosemgrep -- pinned local Renovate binary
            [renovate, "--platform=local", "--dry-run=extract"],  # nosemgrep -- pinned local Renovate binary
            cwd=fixture,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as error:
        raise AssertionError(
            "renovate extract timed out\n"
            + (error.stdout or "")[-2000:]
            + (error.stderr or "")[-4000:]
        ) from error
    combined = result.stdout + result.stderr
    if "RE2 not usable" in combined:
        lines = "\n".join(
            line for line in combined.splitlines() if "RE2 not usable" in line
        )
        raise AssertionError(
            "Renovate could not load RE2; the Gate would evaluate regexes with RegExp\n"
            + lines
        )
    if result.returncode:
        raise AssertionError(
            f"renovate extract exited {result.returncode}\n{result.stderr[-4000:]}"
        )
    entries = []
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("msg") == "Extracted dependencies":
            entries.append(entry)
    aggregates = [entry for entry in entries if entry.get("packageFiles")]
    if not aggregates:
        raise AssertionError("renovate logged no packageFiles in extracted dependencies")
    aggregate = max(
        aggregates,
        key=lambda entry: sum(len(files) for files in entry["packageFiles"].values()),
    )
    deps = {}
    for manager, manager_files in aggregate["packageFiles"].items():
        for package_file in manager_files:
            deps.setdefault(package_file["packageFile"], []).extend(
                {**dep, "_manager": manager}
                for dep in package_file.get("deps", [])
            )
    if not deps:
        raise AssertionError("renovate extracted no dependencies")
    return deps


def tag_pins(deps):
    return [
        dep
        for dep in deps
        if dep.get("datasource") == "github-tags"
        and dep.get("currentValue") == TAG
    ]


class FactoryPins(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Path(self.temp.name)
        shutil.copy(PRESET, self.fixture / "default.json")
        (self.fixture / "mise.toml").write_text(MISE)
        (self.fixture / "hk.pkl").write_text(HK)
        workflows = self.fixture / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "check.yml").write_text(WORKFLOW)
        self.deps = extract(self.fixture)

    def test_each_pin_line_yields_one_tag_pin(self):
        for path, expected in EXPECTED_PINS.items():
            with self.subTest(path=path):
                self.assertGreater(expected, 0)
                pins = tag_pins(self.deps.get(path, []))
                self.assertEqual(len(pins), expected, self.deps.get(path))
                replacements = [dep.get("replaceString") for dep in pins]
                self.assertEqual(len(replacements), len(set(replacements)))
                for dep in pins:
                    self.assertEqual(
                        dep.get("packageName") or dep.get("depName"), "ranokay/factory"
                    )
                    self.assertEqual(dep["currentValue"], TAG)

    def test_mise_includes_and_task_includes_both_extract(self):
        pins = tag_pins(self.deps.get("mise.toml", []))
        replacements = " ".join(dep.get("replaceString", "") for dep in pins)
        self.assertIn("profiles/universal/mise.toml?ref=", replacements)
        self.assertIn("profiles/universal/tasks.toml?ref=", replacements)

    def test_workflow_tag_pin_uses_the_builtin_manager(self):
        pins = tag_pins(self.deps.get(".github/workflows/check.yml", []))
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0].get("packageName"), "ranokay/factory")
        self.assertEqual(pins[0].get("versioning"), "github-actions")

    def test_branches_and_shas_are_not_tag_pins(self):
        mise = self.deps.get("mise.toml", [])
        self.assertTrue(
            [dep for dep in mise if "?ref=main" in dep.get("replaceString", "")]
        )
        self.assertTrue([dep for dep in mise if dep.get("currentDigest") == FIXTURE_SHA])
        hk = self.deps.get("hk.pkl", [])
        self.assertEqual({dep.get("currentValue") for dep in hk}, {TAG})
        workflow = self.deps.get(".github/workflows/check.yml", [])
        self.assertTrue([dep for dep in workflow if dep.get("currentDigest") == FIXTURE_SHA])
        self.assertFalse(
            [
                dep
                for dep in workflow
                if dep.get("datasource") == "github-tags"
                and dep.get("currentValue") in ("main", FIXTURE_SHA)
            ]
        )

    def test_custom_managers_only_extract_tag_pins(self):
        for path, deps in self.deps.items():
            for dep in deps:
                if dep.get("_manager") != "regex":
                    continue
                with self.subTest(path=path, replace=dep.get("replaceString")):
                    self.assertEqual(dep.get("datasource"), "github-tags")
                    self.assertEqual(dep.get("currentValue"), TAG)

    def test_preset_keeps_the_update_policy(self):
        config = json.loads(PRESET.read_text())
        self.assertEqual(config["schedule"], ["before 6am on monday"])
        self.assertFalse(config["automerge"])
        self.assertTrue(config["platformAutomerge"])
        self.assertEqual(config["automergeType"], "pr")
        self.assertEqual(config["automergeStrategy"], "squash")
        group = next(
            rule
            for rule in config["packageRules"]
            if rule.get("matchUpdateTypes") == ["minor", "patch"]
        )
        self.assertEqual(group["groupName"], "non-major dependencies")
        self.assertEqual(group["groupSlug"], "non-major-dependencies")
        self.assertTrue(group["automerge"])
        major = next(
            rule
            for rule in config["packageRules"]
            if rule.get("matchUpdateTypes") == ["major"]
        )
        self.assertFalse(major["automerge"])
        self.assertIn("protected", major["addLabels"])

    def test_tool_declaration_builds_re2(self):
        config = tomllib.loads((ROOT / "mise.toml").read_text())
        tool = config["tools"]["npm:renovate"]
        self.assertIsInstance(tool, dict)
        self.assertIn("re2", tool.get("allow_builds", []))

    def test_consumers_extend_the_preset(self):
        for path in (ROOT / "renovate.json", ROOT / "project" / "renovate.json"):
            with self.subTest(path=path):
                config = json.loads(path.read_text())
                self.assertEqual(config["extends"], ["github>ranokay/factory"])


if __name__ == "__main__":
    unittest.main()
