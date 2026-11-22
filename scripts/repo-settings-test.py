import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
GH = '''#!/usr/bin/env python3
import base64, json, os, sys
a = sys.argv[1:]
path = os.environ["FAKE_STATE"]
s = json.load(open(path))
if a[0] == "secret":
    secret = sys.stdin.read()
    assert secret == "fixture-secret"
    s["secrets"] = [{"name": "OPENCODE_ZEN_API_KEY"}]
    s["writes"].append("secret")
else:
    method = a[a.index("--method") + 1] if "--method" in a else "GET"
    endpoint = next(x for x in a if x.startswith("repos/"))
    body = json.load(sys.stdin) if "--input" in a else None
    key = endpoint.split("?", 1)[0].removeprefix("repos/o/r")
    if key.startswith("/contents/"):
        value = s["files"].get(key.removeprefix("/contents/"))
        if value is None:
            print("HTTP 404", file=sys.stderr)
            sys.exit(1)
        print(json.dumps({"content": base64.b64encode(value.encode()).decode()}))
        sys.exit(0)
    mapping = {"": "repo", "/topics": "topics", "/labels": "labels", "/rulesets": "rulesets", "/actions/permissions/fork-pr-contributor-approval": "approval", "/actions/permissions/fork-pr-workflows-private-repos": "private_settings", "/actions/secrets": "secret_response"}
    if method == "GET":
        if key.startswith("/rulesets/"):
            result = s["rulesets"][0]
        elif key == "/actions/secrets":
            result = {"secrets": s["secrets"]}
        else:
            result = s[mapping[key]]
        print(json.dumps([result] if "--slurp" in a else result))
    else:
        s["writes"].append(method + " " + key)
        if key == "": s["repo"].update(body)
        elif key == "/topics": s["topics"] = body
        elif key.startswith("/labels/"):
            import urllib.parse
            name = urllib.parse.unquote(key.removeprefix("/labels/"))
            s["labels"] = [x for x in s["labels"] if x["name"] != name]
            if method != "DELETE": s["labels"].append(body)
        elif key == "/labels": s["labels"].append(body)
        elif key.startswith("/rulesets"):
            body["id"] = 1
            body["source"] = "o/r"
            for rule in body["rules"]:
                if rule["type"] == "required_status_checks":
                    rule["parameters"]["do_not_enforce_on_create"] = False
                    for check in rule["parameters"]["required_status_checks"]:
                        if "integration_id" not in check: check["integration_id"] = None
            s["rulesets"] = [body]
        elif key == "/actions/permissions/fork-pr-contributor-approval": s["approval"] = body
        elif key == "/actions/permissions/fork-pr-workflows-private-repos": s["private_settings"] = body
json.dump(s, open(path, "w"))
'''
OP = '''#!/usr/bin/env python3
print("fixture-secret", end="")
'''
SITE = '''import json, os, urllib.request
class Response:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, *args): return json.dumps(self.data).encode()
def fake(request, timeout):
    state = json.load(open(os.environ["FAKE_STATE"]))
    r = Response()
    if request.method == "POST":
        state["enrolled"] = True
        state["writes"].append("codacy")
        json.dump(state, open(os.environ["FAKE_STATE"], "w"))
        r.data = {}
    else:
        r.data = {"data": [{"name": "r"}] if state.get("enrolled") else [], "pagination": {}}
    return r
urllib.request.urlopen = fake
'''


class RepoSettings(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        for name, content in (("gh", GH), ("op", OP)):
            (self.root / name).write_text(content)
            (self.root / name).chmod(0o755)
        (self.root / "sitecustomize.py").write_text(SITE)
        self.path = self.root / "state"
        self.env = dict(os.environ, PATH=f"{self.root}:{os.environ['PATH']}", PYTHONPATH=str(self.root), FAKE_STATE=str(self.path), OP_SERVICE_ACCOUNT_TOKEN="fixture-token")
        self.write({"repo": {"private": False, "fork": False, "default_branch": "main", "allow_squash_merge": False, "allow_merge_commit": False, "allow_rebase_merge": True}, "files": {".coderabbit.yaml": "reviews:\n  auto_review:\n    enabled: false\n", ".greptile/config.json": '{"autoReview": [], "statusCheck": false}'}, "topics": {"names": []}, "labels": [{"name": "documentation", "color": "0075ca", "description": ""}, {"name": "custom", "color": "123456", "description": "keep"}], "rulesets": [], "approval": {"approval_policy": "first_time_contributors"}, "private_settings": {"run_workflows_from_fork_pull_requests": False}, "secrets": [], "writes": []})

    def write(self, state):
        self.path.write_text(json.dumps(state))

    def run_command(self, *args):
        return subprocess.run(["python3", str(ROOT / "scripts/repo-settings.py"), "o/r", *args], env=self.env, capture_output=True, text=True)

    def test_dry_run_apply_and_second_run(self):
        result = self.run_command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Codacy: enroll", result.stdout)
        self.assertNotIn("fixture-secret", result.stdout + result.stderr)
        self.assertEqual(json.loads(self.path.read_text())["writes"], [])
        result = self.run_command("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("fixture-secret", result.stdout + result.stderr)
        state = json.loads(self.path.read_text())
        self.assertIn("custom", [item["name"] for item in state["labels"]])
        self.assertNotIn("documentation", [item["name"] for item in state["labels"]])
        state["writes"] = []
        self.write(state)
        result = self.run_command("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no changes", result.stdout)
        self.assertEqual(json.loads(self.path.read_text())["writes"], [])

    def test_fork_refusal_and_private_split(self):
        state = json.loads(self.path.read_text())
        state["repo"].update({"fork": True, "private": True})
        self.write(state)
        result = self.run_command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--fork-kind", result.stderr)
        result = self.run_command("--fork-kind", "personal", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.path.read_text())
        self.assertNotIn("codacy", state["writes"])
        self.assertIn("factory-personal-fork", state["topics"]["names"])
        self.assertTrue(state["private_settings"]["require_approval_for_fork_pr_workflows"])

    def test_missing_vendor_files_need_a_pr_but_other_drift_is_applied(self):
        state = json.loads(self.path.read_text())
        state["files"] = {}
        self.write(state)
        result = self.run_command("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("needs PR: .greptile/config.json", result.stdout)
        self.assertIn("needs PR: .coderabbit.yaml", result.stdout)
        self.assertIn("codacy", json.loads(self.path.read_text())["writes"])


if __name__ == "__main__":
    unittest.main()
