import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
GH = '#!/bin/sh\nprintf "%s\\n" "$FIXTURE_PRIVATE"\n'
CODACY = '''#!/bin/sh
printf '%s\\n' "$1" >> "$FIXTURE_LOG"
if [ "$1" = init ]; then
  printf '{"tools": %s}\\n' "$FIXTURE_TOOLS"
fi
'''


class CodacyGate(unittest.TestCase):
    def run_case(self, private, tools):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mise.toml").write_text('[task_config]\nincludes = ["' + str(ROOT / "profiles/universal/tasks.toml") + '"]\n')
            for name, content in (("gh", GH), ("codacy-analysis", CODACY)):
                (root / name).write_text(content)
                (root / name).chmod(0o755)
            yq = subprocess.check_output(["mise", "which", "yq"], cwd=ROOT, text=True).strip()
            (root / "yq").symlink_to(yq)
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", FIXTURE_PRIVATE=private, FIXTURE_TOOLS=tools, FIXTURE_LOG=str(root / "log"), MISE_TRUSTED_CONFIG_PATHS=str(root))
            result = subprocess.run(["mise", "-C", str(root), "run", "check:codacy"], env=env, capture_output=True, text=True)
            calls = (root / "log").read_text().splitlines() if (root / "log").exists() else []
            self.assertFalse((root / ".codacy").exists())
            return result, calls

    def test_public_skips_codacy(self):
        result, calls = self.run_case("false", "[]")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])

    def test_private_zero_tools_fails(self):
        result, calls = self.run_case("true", "[]")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("zero tools", result.stderr)
        self.assertEqual(calls, ["init"])

    def test_private_runs_analysis(self):
        result, calls = self.run_case("true", '[{"name":"fixture"}]')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["init", "analyze"])


if __name__ == "__main__":
    unittest.main()
