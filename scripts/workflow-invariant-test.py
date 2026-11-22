import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WorkflowInvariant(unittest.TestCase):
    def test_current_workflows_pass_and_unsafe_fixture_fails(self):
        script = ROOT / "scripts/workflow-invariant.py"
        good = subprocess.run(["python3", str(script), str(ROOT / ".github/workflows")], capture_output=True, text=True)
        bad = subprocess.run(["python3", str(script), str(ROOT / "scripts/fixtures/workflow-invariant")], capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("unsafe.yml", bad.stderr)

    def test_pull_request_target_ref_and_whole_secrets_context_fail(self):
        script = ROOT / "scripts/workflow-invariant.py"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.yml"
            path.write_text('name: bad\non: pull_request_target\njobs:\n  review:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n        with:\n          ref: refs/pull/1/head\n')
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            path.write_text('name: bad\non: pull_request\njobs:\n  review:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo "${{ toJSON(secrets) }}"\n')
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_secret_workflow_guard_must_match_factory_authors(self):
        script = ROOT / "scripts/workflow-invariant.py"
        with tempfile.TemporaryDirectory() as temp:
            for name in ("ai-review-caller.yml", "ai-review.yml"):
                path = Path(temp) / name
                source = (ROOT / ".github/workflows" / name).read_text()
                path.write_text(source.replace("renovate[bot]", "other[bot]"))
                result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, name)
                self.assertIn("guard", result.stderr)
                path.unlink()
            path = Path(temp) / "ai-review-caller.yml"
            path.write_text((ROOT / ".github/workflows/ai-review-caller.yml").read_text())
            config = json.loads((ROOT / "repo-settings.json").read_text())
            config["factory_authors"].append("factory-helper[bot]")
            config_path = Path(temp) / "settings.json"
            config_path.write_text(json.dumps(config))
            result = subprocess.run(["python3", str(script), temp, str(config_path)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("guard", result.stderr)


if __name__ == "__main__":
    unittest.main()
