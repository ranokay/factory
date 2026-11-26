import json
import subprocess  # nosec B404 -- the test runs local declared tools
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ACTION = ROOT / ".github/actions/gates/action.yml"


def run(*command):
    return subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- the test runs local declared tools
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class TrivyGates(unittest.TestCase):
    def test_root_inherits_the_only_trivy_pin(self):
        root = tomllib.loads((ROOT / "mise.toml").read_text())
        profile = tomllib.loads((ROOT / "profiles/universal/mise.toml").read_text())
        self.assertNotIn("trivy", root["tools"])
        self.assertIn("./profiles/universal/mise.toml", root["include"])
        self.assertEqual(profile["tools"]["trivy"], "0.76.2")

        result = run("mise", "ls", "--current", "--json", "trivy")
        self.assertEqual(result.returncode, 0, result.stderr)
        current = json.loads(result.stdout)
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["version"], profile["tools"]["trivy"])

    def test_trivy_cache_rotates_daily_and_restores_from_prior_days(self):
        result = run("yq", "-p=yaml", "-o=json", ".", str(ACTION))
        self.assertEqual(result.returncode, 0, result.stderr)
        steps = json.loads(result.stdout)["runs"]["steps"]
        date_step = next(step for step in steps if step.get("id") == "trivy-cache-date")
        cache_step = next(step for step in steps if step.get("name") == "Cache Trivy databases")

        self.assertEqual(date_step["if"], "inputs.group == 'security'")
        self.assertIn("date -u +%Y-%m-%d", date_step["run"])
        self.assertIn("$GITHUB_OUTPUT", date_step["run"])
        self.assertEqual(cache_step["if"], "inputs.group == 'security'")
        self.assertEqual(cache_step["with"]["path"], "~/.cache/trivy")
        self.assertEqual(
            cache_step["with"]["key"],
            "${{ runner.os }}-trivy-${{ steps.trivy-cache-date.outputs.date }}",
        )
        self.assertEqual(cache_step["with"]["restore-keys"].strip(), "${{ runner.os }}-trivy-")


if __name__ == "__main__":
    unittest.main()
