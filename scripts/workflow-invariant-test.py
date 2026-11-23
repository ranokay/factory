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

    def test_required_contexts_must_be_produced_by_pull_request_jobs(self):
        script = ROOT / "scripts/workflow-invariant.py"
        config = json.loads((ROOT / "repo-settings.json").read_text())
        config["required_checks"].append({"context": "Fabricated", "integration_id": 15368})
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "settings.json"
            config_path.write_text(json.dumps(config))
            result = subprocess.run(
                ["python3", str(script), str(ROOT / ".github/workflows"), str(config_path)],
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("required check", result.stderr)
            self.assertIn("Fabricated", result.stderr)

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

    def test_ai_review_skips_drafts_on_the_job(self):
        script = ROOT / "scripts/workflow-invariant.py"
        ai_review = (ROOT / ".github/workflows/ai-review.yml").read_text()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ai-review.yml").write_text(ai_review)
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            (root / "ai-review.yml").write_text(ai_review.replace(" && github.event.pull_request.draft == false", ""))
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("AI Review job must skip draft pull requests", result.stderr)
            (root / "ai-review.yml").write_text(
                ai_review.replace("github.event.pull_request.draft == false", "github.event.pull_request.draft == true")
            )
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("AI Review job must skip draft pull requests", result.stderr)
            guard = "github.event.pull_request.head.repo.full_name == github.repository && github.event.pull_request.user.login == 'ranokay'"
            (root / "ai-review.yml").write_text(
                "name: AI Review\non: pull_request\njobs:\n  review:\n    runs-on: ubuntu-latest\n    steps:\n"
                f"      - if: {guard}\n        run: echo ok\n"
            )
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("AI Review job has no author guard", result.stderr)

    def test_ai_review_guard_must_match_factory_authors(self):
        script = ROOT / "scripts/workflow-invariant.py"
        ai_review = (ROOT / ".github/workflows/ai-review.yml").read_text()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ai-review.yml").write_text(ai_review.replace("renovate[bot]", "other[bot]"))
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("AI Review author guard does not match factory_authors", result.stderr)
            self.assertIn("has secrets without a same-repository guard", result.stderr)
            (root / "ai-review.yml").write_text(ai_review)
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = json.loads((ROOT / "repo-settings.json").read_text())
            config["factory_authors"].append("factory-helper[bot]")
            config_path = root / "settings.json"
            config_path.write_text(json.dumps(config))
            result = subprocess.run(["python3", str(script), temp, str(config_path)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("AI Review author guard does not match factory_authors", result.stderr)

    def test_caller_without_guard_needs_a_guarded_called_job(self):
        script = ROOT / "scripts/workflow-invariant.py"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "caller.yml").write_text("name: caller\non: pull_request\njobs:\n  review:\n    uses: $/.github/workflows/callee.yml\n    secrets:\n      TOKEN: ${{ secrets.TOKEN }}\n")
            (root / "callee.yml").write_text("name: callee\non: workflow_call\njobs:\n  unit:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n")
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("caller.yml: review has secrets without a same-repository guard", result.stderr)

    def test_dangling_subdirectory_and_non_reusable_callees_fail(self):
        script = ROOT / "scripts/workflow-invariant.py"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            caller = root / "caller.yml"
            caller.write_text("name: caller\non: pull_request\njobs:\n  review:\n    uses: $/.github/workflows/missing.yml\n")
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing.yml does not exist", result.stderr)
            caller.write_text("name: caller\non: pull_request\njobs:\n  review:\n    uses: $/.github/workflows/sub/missing.yml\n")
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("is not a workflow directly in .github/workflows", result.stderr)
            caller.write_text("name: caller\non: pull_request\njobs:\n  review:\n    uses: $/.github/workflows/missing.yml\n")
            (root / "missing.yml").write_text("name: other\non: push\njobs:\n  review:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n")
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing.yml is not a reusable workflow", result.stderr)

    def test_external_caller_cannot_hide_a_required_context(self):
        script = ROOT / "scripts/workflow-invariant.py"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = json.loads((ROOT / "repo-settings.json").read_text())
            config["required_checks"] = [{"context": "review / unit", "integration_id": 15368}]
            config_path = root / "settings.json"
            config_path.write_text(json.dumps(config))
            path = root / "caller.yml"
            path.write_text("name: caller\non: pull_request\njobs:\n  review:\n    if: always()\n    uses: other/repo/.github/workflows/review.yml@v1\n")
            result = subprocess.run(["python3", str(script), temp, str(config_path)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("guards an external reusable workflow at the caller", result.stderr)
            path.write_text("name: caller\non: pull_request\njobs:\n  other:\n    if: always()\n    uses: other/repo/.github/workflows/review.yml@v1\n")
            result = subprocess.run(["python3", str(script), temp, str(config_path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            path.write_text("name: caller\non: push\njobs:\n  review:\n    if: always()\n    uses: other/repo/.github/workflows/review.yml@v1\n")
            result = subprocess.run(["python3", str(script), temp, str(config_path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_required_check_cannot_be_guarded_at_the_caller(self):
        script = ROOT / "scripts/workflow-invariant.py"
        guard = "github.event.pull_request.head.repo.full_name == github.repository"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = json.loads((ROOT / "repo-settings.json").read_text())
            config["required_checks"] = [{"context": "review / unit", "integration_id": 15368}]
            config_path = root / "settings.json"
            config_path.write_text(json.dumps(config))
            (root / "callee.yml").write_text("name: callee\non: workflow_call\njobs:\n  unit:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n")
            (root / "caller.yml").write_text(f"name: caller\non: pull_request\njobs:\n  review:\n    if: {guard}\n    uses: $/.github/workflows/callee.yml\n")
            result = subprocess.run(["python3", str(script), temp, str(config_path)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("guards required check review / unit at the caller", result.stderr)
            (root / "caller.yml").write_text("name: caller\non: pull_request\njobs:\n  review:\n    uses: $/.github/workflows/callee.yml\n")
            result = subprocess.run(["python3", str(script), temp, str(config_path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_ai_review_guard_without_secrets_still_must_match_factory_authors(self):
        script = ROOT / "scripts/workflow-invariant.py"
        guard = "github.event.pull_request.head.repo.full_name == github.repository && (github.event.pull_request.user.login == 'other')"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ai-review.yml"
            path.write_text(f"name: AI Review\non: pull_request\njobs:\n  review:\n    if: {guard}\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n")
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("AI Review author guard does not match factory_authors", result.stderr)
            path.write_text("name: AI Review\non: pull_request\njobs:\n  review:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n")
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("AI Review job has no author guard", result.stderr)
            path.write_text((ROOT / ".github/workflows/ai-review.yml").read_text().replace("renovate[bot]", "other[bot]"))
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("AI Review author guard does not match factory_authors", result.stderr)

    def test_secret_workflows_need_a_guard_only_for_fork_reachable_triggers(self):
        script = ROOT / "scripts/workflow-invariant.py"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "release.yml"
            path.write_text('name: release\non:\n  push:\n    tags: ["v*"]\njobs:\n  publish:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n        env:\n          TOKEN: ${{ secrets.TOKEN }}\n')
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            path.write_text('name: release\non: [push, pull_request]\njobs:\n  publish:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n        env:\n          TOKEN: ${{ secrets.TOKEN }}\n')
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("has secrets without a same-repository guard", result.stderr)

    def test_folded_guard_condition_matches(self):
        script = ROOT / "scripts/workflow-invariant.py"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "guarded.yml"
            path.write_text("name: guarded\non: pull_request\njobs:\n  review:\n    if: >-\n      github.event.pull_request.head.repo.full_name == github.repository &&\n      (github.event.pull_request.user.login == 'ranokay' || github.event.pull_request.user.login == 'renovate[bot]')\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n        env:\n          TOKEN: ${{ secrets.TOKEN }}\n")
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_workflow_without_a_trigger_fails(self):
        script = ROOT / "scripts/workflow-invariant.py"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "release.yml"
            path.write_text("name: release\njobs:\n  publish:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n")
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no workflow trigger", result.stderr)

    def test_workflow_run_secrets_need_the_run_guard(self):
        script = ROOT / "scripts/workflow-invariant.py"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "after.yml"
            body = 'name: after\non:\n  workflow_run:\n    workflows: [Gates]\n    types: [completed]\njobs:\n  publish:\n    if: {guard}\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n        env:\n          TOKEN: ${{ secrets.TOKEN }}\n'
            path.write_text(body.replace("{guard}", "always()"))
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("has secrets without a same-repository guard", result.stderr)
            path.write_text(body.replace("{guard}", "github.event.workflow_run.head_repository.full_name == github.repository"))
            result = subprocess.run(["python3", str(script), temp], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
