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


if __name__ == "__main__":
    unittest.main()
