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
    if key.startswith("/rules/branches/"):
        print(json.dumps([s.get("applied_rules", [])]))
        sys.exit(0)
    if key.startswith("/commits/") and key.endswith("/check-runs"):
        head = key.removeprefix("/commits/").removesuffix("/check-runs")
        if head in s.get("unavailable_heads", []):
            print("HTTP 404", file=sys.stderr)
            sys.exit(1)
        if head in s.get("denied_heads", []):
            print("HTTP 403", file=sys.stderr)
            sys.exit(1)
        print(json.dumps([{"check_runs": s.get("check_runs", {}).get(head, [])}]))
        sys.exit(0)
    if key == "/pulls":
        print(json.dumps([s.get("pulls", [])] if "--slurp" in a else s.get("pulls", [])))
        sys.exit(0)
    if key.endswith("/protection") and method == "GET":
        if s.get("protection") is None:
            print("HTTP 404", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(s["protection"]))
        sys.exit(0)
    mapping = {"": "repo", "/topics": "topics", "/labels": "labels", "/rulesets": "rulesets", "/actions/permissions/fork-pr-contributor-approval": "approval", "/actions/permissions/fork-pr-workflows-private-repos": "private_settings", "/actions/secrets": "secret_response"}
    if method == "GET":
        if key.startswith("/rulesets/"):
            result = s["rulesets"][0]
        elif key == "/actions/secrets":
            if "--slurp" in a:
                print(json.dumps([{"secrets": s["secrets"][:1]}, {"secrets": s["secrets"][1:]}]))
                sys.exit(0)
            result = {"secrets": s["secrets"][:1]}
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
            if method != "DELETE":
                body["default"] = False
                s["labels"].append(body)
        elif key == "/labels":
            body["default"] = False
            s["labels"].append(body)
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
        self.write({"repo": {"private": False, "fork": False, "default_branch": "main", "allow_squash_merge": False, "allow_merge_commit": False, "allow_rebase_merge": True, "allow_auto_merge": True}, "files": {".coderabbit.yaml": "reviews:\n  auto_review:\n    enabled: false\n", ".greptile/config.json": '{"autoReview": [], "statusCheck": false}'}, "topics": {"names": []}, "labels": [{"name": "documentation", "color": "0075ca", "description": "", "default": True}, {"name": "accessibility", "color": "f143ab", "description": "", "default": True}, {"name": "custom", "color": "123456", "description": "keep", "default": False}], "rulesets": [], "approval": {"approval_policy": "first_time_contributors"}, "private_settings": {"run_workflows_from_fork_pull_requests": False}, "secrets": [], "writes": []})

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
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manual: Codacy check not yet reporting", result.stdout)
        self.assertNotIn("fixture-secret", result.stdout + result.stderr)
        state = json.loads(self.path.read_text())
        self.assertIn("custom", [item["name"] for item in state["labels"]])
        self.assertNotIn("documentation", [item["name"] for item in state["labels"]])
        self.assertNotIn("accessibility", [item["name"] for item in state["labels"]])
        self.assertIn("label accessibility: delete", result.stdout)
        state["check_runs"] = {"main": [{"name": "Codacy Static Code Analysis"}]}
        state["writes"] = []
        self.write(state)
        result = self.run_command("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.path.read_text())["writes"], ["PUT /rulesets/1"])
        state = json.loads(self.path.read_text())
        state["writes"] = []
        self.write(state)
        result = self.run_command("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no changes", result.stdout)
        self.assertEqual(json.loads(self.path.read_text())["writes"], [])

    def test_auto_merge_is_enabled(self):
        state = json.loads(self.path.read_text())
        state["repo"]["allow_auto_merge"] = False
        state["enrolled"] = True
        state["check_runs"] = {"main": [{"name": "Codacy Static Code Analysis"}]}
        self.write(state)
        result = self.run_command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("merge settings:", result.stdout)
        self.assertIn('"allow_auto_merge": false', result.stdout)
        self.assertIn('"allow_auto_merge": true', result.stdout)
        result = self.run_command("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(self.path.read_text())["repo"]["allow_auto_merge"])
        self.assertIn("PATCH ", json.loads(self.path.read_text())["writes"])

    def test_unreadable_vendor_file_is_reported_and_other_drift_applies(self):
        state = json.loads(self.path.read_text())
        state["files"][".coderabbit.yaml"] = "reviews: [\n"
        self.write(state)
        result = self.run_command("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("needs PR: .coderabbit.yaml: unreadable", result.stdout)
        self.assertIn("codacy", json.loads(self.path.read_text())["writes"])

    def test_ruleset_body_matches_the_contract(self):
        state = json.loads(self.path.read_text())
        state["enrolled"] = True
        state["check_runs"] = {"main": [{"name": "Codacy Static Code Analysis"}]}
        self.write(state)
        result = self.run_command("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        ruleset = json.loads(self.path.read_text())["rulesets"][0]
        self.assertEqual(ruleset["name"], "Factory default branch")
        self.assertEqual(ruleset["target"], "branch")
        self.assertEqual(ruleset["enforcement"], "active")
        self.assertEqual(ruleset["bypass_actors"], [])
        self.assertEqual(ruleset["conditions"], {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}})
        rules = {rule["type"]: rule.get("parameters", {}) for rule in ruleset["rules"]}
        self.assertEqual(len(ruleset["rules"]), 5)
        self.assertEqual(set(rules), {"deletion", "non_fast_forward", "required_signatures", "pull_request", "required_status_checks"})
        self.assertEqual(rules["pull_request"]["required_approving_review_count"], 0)
        self.assertTrue(rules["pull_request"]["required_review_thread_resolution"])
        self.assertNotIn("required_reviewers", rules["pull_request"])
        self.assertNotIn("copilot_code_review", rules)
        checks = rules["required_status_checks"]["required_status_checks"]
        self.assertEqual([check["context"] for check in checks], ["Gates / Gates", "review / review", "Codacy Static Code Analysis"])
        self.assertEqual([check["integration_id"] for check in checks[:2]], [15368, 15368])

    def test_secret_lookup_reads_every_page(self):
        state = json.loads(self.path.read_text())
        state["secrets"] = [{"name": "OTHER"}, {"name": "OPENCODE_ZEN_API_KEY"}]
        state["enrolled"] = True
        state["check_runs"] = {"main": [{"name": "Codacy Static Code Analysis"}]}
        self.write(state)
        result = self.run_command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Actions secret OPENCODE_ZEN_API_KEY", result.stdout)

    def test_secret_is_set_only_when_missing_or_rotated(self):
        state = json.loads(self.path.read_text())
        state["secrets"] = [{"name": "OPENCODE_ZEN_API_KEY"}]
        state["enrolled"] = True
        state["check_runs"] = {"main": [{"name": "Codacy Static Code Analysis"}]}
        self.write(state)
        result = self.run_command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("OPENCODE_ZEN_API_KEY", result.stdout)
        result = self.run_command("--rotate-secret")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Actions secret OPENCODE_ZEN_API_KEY: set from 1Password", result.stdout)
        self.assertNotIn("fixture-secret", result.stdout + result.stderr)
        self.assertEqual(json.loads(self.path.read_text())["writes"], [])
        result = self.run_command("--apply", "--rotate-secret")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("secret", json.loads(self.path.read_text())["writes"])
        self.assertNotIn("fixture-secret", result.stdout + result.stderr)

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

    def test_upstream_fork_changes_only_topic_and_fork_safety(self):
        state = json.loads(self.path.read_text())
        state["repo"].update({"fork": True, "private": True})
        state["files"] = {}
        self.write(state)
        result = self.run_command("--fork-kind", "upstream", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Upstream Fork: skipped", result.stdout)
        state = json.loads(self.path.read_text())
        self.assertEqual(state["topics"]["names"], ["factory-upstream-fork"])
        self.assertEqual(set(state["writes"]), {"PUT /topics", "PUT /actions/permissions/fork-pr-contributor-approval", "PUT /actions/permissions/fork-pr-workflows-private-repos"})
        self.assertFalse(state["private_settings"]["run_workflows_from_fork_pull_requests"])
        state["writes"] = []
        self.write(state)
        result = self.run_command("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no changes", result.stdout)
        self.assertEqual(json.loads(self.path.read_text())["writes"], [])

    def test_unmanaged_advisory_requirements_report_manual_drift(self):
        state = json.loads(self.path.read_text())
        state["applied_rules"] = [{"ruleset_source": "o/r", "ruleset_id": 8, "type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "Greptile Review"}]}}, {"ruleset_source": "o", "ruleset_id": 9, "type": "copilot_code_review"}, {"ruleset_source": "o", "ruleset_id": 10, "type": "pull_request", "parameters": {"required_reviewers": [{"reviewer": {"login": "coderabbitai[bot]"}, "minimum_approvals": 1}]}}]
        state["protection"] = {"required_status_checks": {"contexts": ["Devin Review"], "checks": []}}
        self.write(state)
        result = self.run_command("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manual: ruleset o/r (8): required check Greptile Review", result.stdout)
        self.assertIn("manual: ruleset o (9): copilot_code_review rule", result.stdout)
        self.assertIn("manual: ruleset o (10): required reviewer coderabbitai[bot]", result.stdout)
        self.assertIn("manual: legacy branch protection: required check Devin Review", result.stdout)
        self.assertFalse(any("rules/branches" in entry or "/protection" in entry for entry in json.loads(self.path.read_text())["writes"]))

    def test_public_codacy_is_enrolled_before_ruleset_and_required_only_when_observed(self):
        state = json.loads(self.path.read_text())
        state["check_runs"] = {"main": [{"name": "Codacy Static Code Analysis"}]}
        self.write(state)
        result = self.run_command("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manual: Codacy check not yet reporting", result.stdout)
        state = json.loads(self.path.read_text())
        self.assertLess(state["writes"].index("codacy"), state["writes"].index("POST /rulesets"))
        checks = state["rulesets"][0]["rules"][-1]["parameters"]["required_status_checks"]
        self.assertNotIn("Codacy Static Code Analysis", [check["context"] for check in checks])
        state["writes"] = []
        state["pulls"] = [{"head": {"sha": "recent-pr"}}]
        state["check_runs"] = {"recent-pr": [{"name": "Codacy Static Code Analysis"}]}
        self.write(state)
        result = self.run_command("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.path.read_text())
        self.assertEqual(state["writes"], ["PUT /rulesets/1"])
        checks = state["rulesets"][0]["rules"][-1]["parameters"]["required_status_checks"]
        self.assertIn("Codacy Static Code Analysis", [check["context"] for check in checks])

    def test_enrolled_codacy_without_check_stays_advisory(self):
        state = json.loads(self.path.read_text())
        state["enrolled"] = True
        self.write(state)
        result = self.run_command("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manual: Codacy check not yet reporting", result.stdout)
        state = json.loads(self.path.read_text())
        self.assertNotIn("codacy", state["writes"])
        checks = state["rulesets"][0]["rules"][-1]["parameters"]["required_status_checks"]
        self.assertNotIn("Codacy Static Code Analysis", [check["context"] for check in checks])

    def test_existing_codacy_requirement_is_preserved_without_recent_evidence(self):
        state = json.loads(self.path.read_text())
        state["enrolled"] = True
        state["check_runs"] = {"main": [{"name": "Codacy Static Code Analysis"}]}
        self.write(state)
        result = self.run_command("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.path.read_text())
        state["check_runs"] = {}
        state["rulesets"][0]["rules"][-1]["parameters"]["strict_required_status_checks_policy"] = True
        checks = state["rulesets"][0]["rules"][-1]["parameters"]["required_status_checks"]
        next(check for check in checks if check["context"] == "Codacy Static Code Analysis")["integration_id"] = 42
        self.write(state)
        result = self.run_command("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manual: Codacy check not yet reporting", result.stdout)
        state = json.loads(self.path.read_text())
        checks = state["rulesets"][0]["rules"][-1]["parameters"]["required_status_checks"]
        self.assertIn({"context": "Codacy Static Code Analysis", "integration_id": 42}, checks)
        self.assertEqual(state["rulesets"][0]["enforcement"], "active")

    def test_disabled_ruleset_does_not_activate_unobserved_codacy_check(self):
        state = json.loads(self.path.read_text())
        state["enrolled"] = True
        state["check_runs"] = {"main": [{"name": "Codacy Static Code Analysis"}]}
        self.write(state)
        result = self.run_command("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.path.read_text())
        state["check_runs"] = {}
        state["rulesets"][0]["enforcement"] = "disabled"
        self.write(state)
        result = self.run_command("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manual: Codacy check not yet reporting", result.stdout)
        state = json.loads(self.path.read_text())
        self.assertEqual(state["rulesets"][0]["enforcement"], "active")
        checks = state["rulesets"][0]["rules"][-1]["parameters"]["required_status_checks"]
        self.assertNotIn("Codacy Static Code Analysis", [check["context"] for check in checks])

    def test_unavailable_pr_head_does_not_hide_later_check(self):
        state = json.loads(self.path.read_text())
        state["enrolled"] = True
        state["pulls"] = [{"head": {"sha": "missing"}}, {"head": {"sha": "available"}}]
        state["unavailable_heads"] = ["missing"]
        state["check_runs"] = {"available": [{"name": "Codacy Static Code Analysis"}]}
        self.write(state)
        result = self.run_command("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.path.read_text())
        checks = state["rulesets"][0]["rules"][-1]["parameters"]["required_status_checks"]
        self.assertIn("Codacy Static Code Analysis", [check["context"] for check in checks])

    def test_inaccessible_pr_head_without_evidence_still_applies_other_settings(self):
        state = json.loads(self.path.read_text())
        state["enrolled"] = True
        state["pulls"] = [{"head": {"sha": "missing"}}]
        state["unavailable_heads"] = ["missing"]
        self.write(state)
        result = self.run_command("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manual: Codacy check not yet reporting", result.stdout)
        self.assertIn("POST /rulesets", json.loads(self.path.read_text())["writes"])

    def test_check_api_access_errors_do_not_count_as_missing_evidence(self):
        state = json.loads(self.path.read_text())
        state["enrolled"] = True
        state["denied_heads"] = ["main"]
        self.write(state)
        result = self.run_command("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not read check runs", result.stderr)
        self.assertEqual(json.loads(self.path.read_text())["writes"], [])


if __name__ == "__main__":
    unittest.main()
