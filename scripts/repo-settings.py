#!/usr/bin/env python3
import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path


CONFIG = json.loads((Path(__file__).resolve().parent.parent / "repo-settings.json").read_text())


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate_branch_rules():
    rules = CONFIG.get("branch_rules")
    required = {"deletion", "non_fast_forward", "required_signatures", "pull_request"}
    if not isinstance(rules, list) or any(not isinstance(rule, dict) or not isinstance(rule.get("type"), str) or not rule["type"] for rule in rules):
        raise RuntimeError("repo-settings.json: branch_rules must contain objects with a type")
    if len(rules) != len(required) or {rule["type"] for rule in rules} != required:
        raise RuntimeError("repo-settings.json: branch_rules must contain each required rule exactly once")
    for rule in rules:
        if rule["type"] != "pull_request" and set(rule) != {"type"}:
            raise RuntimeError("repo-settings.json: branch_rules non-PR rules must not have parameters")
    pr = next(rule for rule in rules if rule["type"] == "pull_request")
    flags = {"dismiss_stale_reviews_on_push", "require_code_owner_review", "require_last_push_approval", "required_review_thread_resolution", "require_extra_approval_for_unattributed_changes"}
    parameters = pr.get("parameters")
    count = "required_approving_review_count"
    if set(pr) != {"type", "parameters"} or not isinstance(parameters, dict) or set(parameters) != flags | {count} or not integer(parameters[count]) or parameters[count] < 0 or any(not isinstance(parameters[flag], bool) for flag in flags):
        raise RuntimeError("repo-settings.json: branch_rules pull_request parameters are invalid")


def valid_check(check, require_integration):
    if not isinstance(check, dict) or not {"context"}.issubset(check) or set(check) - {"context", "integration_id"}:
        return False
    if not isinstance(check["context"], str) or not check["context"].strip():
        return False
    if require_integration and "integration_id" not in check:
        return False
    return "integration_id" not in check or (integer(check["integration_id"]) and check["integration_id"] > 0)


def validate_checks():
    checks = CONFIG.get("required_checks")
    if not isinstance(checks, list) or not checks or not all(valid_check(check, True) for check in checks):
        raise RuntimeError("repo-settings.json: required_checks must contain valid checks")
    contexts = [check["context"] for check in checks]
    if len(set(contexts)) != len(contexts) or not {"Gates / Gates", "review / review"}.issubset(contexts):
        raise RuntimeError("repo-settings.json: required_checks must uniquely include Gates / Gates and review / review")
    if not valid_check(CONFIG.get("public_check"), False):
        raise RuntimeError("repo-settings.json: public_check is invalid")
    if CONFIG["public_check"]["context"] in contexts:
        raise RuntimeError("repo-settings.json: public_check context must differ from required_checks")


def validate_config():
    names = (CONFIG.get("ruleset_name"), CONFIG.get("review_ruleset_name"))
    if any(not isinstance(name, str) or not name.strip() for name in names) or names[0].casefold() == names[1].casefold():
        raise RuntimeError("repo-settings.json: ruleset names must be nonempty and distinct")
    validate_branch_rules()
    validate_checks()
    bypass = CONFIG.get("fork_bypass_actor")
    if bypass != {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"} or not integer(bypass["actor_id"]):
        raise RuntimeError("repo-settings.json: fork_bypass_actor must be the admin role in pull_request mode")


def command(args, data=None, env=None):
    result = subprocess.run(args, input=data, capture_output=True, env=env, check=False)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed (exit {result.returncode})")
    return result.stdout


def gh(endpoint, method="GET", body=None, paginate=False):
    args = ["gh", "api", "--method", method]
    if paginate:
        args += ["--paginate", "--slurp"]
    args.append(endpoint)
    if body is not None:
        args += ["--input", "-"]
    result = command(args, json.dumps(body).encode() if body is not None else None)
    if not result:
        return None
    value = json.loads(result)
    return [item for page in value for item in page] if paginate else value


def op_read(reference):
    env = os.environ.copy()
    if not env.get("OP_SERVICE_ACCOUNT_TOKEN"):
        for args in (["launchctl", "getenv", "OP_SERVICE_ACCOUNT_TOKEN"], ["security", "find-generic-password", "-a", "Factory", "-s", "dev.factory.op-service-account", "-w"]):
            try:
                token = command(args).decode().strip()
            except (RuntimeError, FileNotFoundError):
                continue
            if token:
                env["OP_SERVICE_ACCOUNT_TOKEN"] = token
                break
    if not env.get("OP_SERVICE_ACCOUNT_TOKEN"):
        raise RuntimeError("1Password service account token unavailable in environment, launchd, and keychain")
    return command(["op", "read", "--no-newline", reference], env=env)


def repo_file(base, path, branch):
    endpoint = base + "/contents/" + urllib.parse.quote(path) + "?" + urllib.parse.urlencode({"ref": branch})
    result = subprocess.run(["gh", "api", endpoint], capture_output=True, check=False)
    if result.returncode:
        if b"HTTP 404" in result.stderr:
            return None
        raise RuntimeError(f"could not read {path} from the default branch")
    return base64.b64decode(json.loads(result.stdout)["content"])


def optional_gh(endpoint):
    result = subprocess.run(["gh", "api", endpoint], capture_output=True, check=False)
    if result.returncode:
        if b"HTTP 404" in result.stderr:
            return None
        raise RuntimeError(f"could not read {endpoint}")
    return json.loads(result.stdout)


def is_advisory(value):
    return any(name in str(value).lower() for name in ("coderabbit", "copilot", "greptile", "devin"))


def reviewer_name(reviewer):
    if isinstance(reviewer, str):
        return reviewer
    if not isinstance(reviewer, dict):
        raise RuntimeError("cannot identify a required reviewer in existing protection")
    for key in ("login", "slug", "name"):
        if reviewer.get(key):
            return reviewer[key]
    kind, identifier = reviewer.get("type"), reviewer.get("id")
    if kind == "User" and identifier:
        return gh(f"user/{identifier}")["login"]
    if kind == "Team" and identifier:
        return gh(f"teams/{identifier}")["slug"]
    raise RuntimeError("cannot identify a required reviewer in existing protection")


def advisory_drift(policy, rule):
    found = []
    kind = rule.get("type", "legacy branch protection")
    params = rule.get("parameters", rule)
    if kind == "copilot_code_review":
        found.append(f"{policy}: copilot_code_review rule")
    checks = params.get("required_status_checks") or {}
    if isinstance(checks, dict):
        checks = checks.get("checks", []) + [{"context": c} for c in checks.get("contexts", [])]
    for check in checks:
        if is_advisory(check.get("context", "")):
            found.append(f"{policy}: required check {check['context']}")
    for item in params.get("required_reviewers", []):
        if item.get("minimum_approvals", 1) == 0:
            continue
        name = reviewer_name(item.get("reviewer", item))
        if is_advisory(name):
            found.append(f"{policy}: required reviewer {name}")
    return found


def codacy(path, token, body=None):
    request = urllib.request.Request("https://app.codacy.com/api/v3/" + path, data=json.dumps(body).encode() if body is not None else None, headers={"api-token": token.decode(), "Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def ruleset(codacy_check, personal_fork=False):
    checks = CONFIG["required_checks"] + ([codacy_check] if codacy_check else [])
    review_types = {"required_signatures", "pull_request"}
    branch_rules = [rule for rule in CONFIG["branch_rules"] if not personal_fork or rule["type"] not in review_types]
    return {"name": CONFIG["ruleset_name"], "target": "branch", "enforcement": "active", "bypass_actors": [], "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}, "rules": [*branch_rules, {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": False, "required_status_checks": checks}}]}


def review_ruleset():
    return {"name": CONFIG["review_ruleset_name"], "target": "branch", "enforcement": "active", "bypass_actors": [CONFIG["fork_bypass_actor"]], "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}, "rules": [rule for rule in CONFIG["branch_rules"] if rule["type"] in {"required_signatures", "pull_request"}]}


def codacy_check_seen(base, branch):
    pulls = gh(base + "/pulls?state=all&per_page=10")
    heads = [branch, *[pull["head"]["sha"] for pull in pulls if pull.get("head", {}).get("sha")]]
    for head in dict.fromkeys(heads):
        endpoint = base + "/commits/" + urllib.parse.quote(head, safe="") + "/check-runs?per_page=100"
        result = subprocess.run(["gh", "api", "--paginate", "--slurp", endpoint], capture_output=True, check=False)
        if result.returncode:
            if head != branch and b"HTTP 404" in result.stderr:
                continue
            raise RuntimeError(f"could not read check runs for {head}")
        pages = json.loads(result.stdout)
        if any(check["name"] == CONFIG["public_check"]["context"] for page in pages for check in page["check_runs"]):
            return True
    return False


def matches(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and matches(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        unmatched = list(actual)
        for value in expected:
            for index, item in enumerate(unmatched):
                if matches(item, value):
                    unmatched.pop(index)
                    break
            else:
                return False
        return True
    return actual == expected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rotate-secret", action="store_true")
    parser.add_argument("--fork-kind", choices=["personal", "upstream"])
    args = parser.parse_args()
    if len(args.repo.split("/")) != 2 or not all(args.repo.split("/")):
        parser.error("repo must be OWNER/REPO")
    validate_config()
    owner, name = args.repo.split("/")
    base = "repos/" + args.repo

    def owned_rulesets():
        listed = gh(base + "/rulesets?per_page=100&includes_parents=false", paginate=True)
        owned = []
        for item in listed:
            source = item.get("source")
            source_type = item.get("source_type")
            if not isinstance(source, str) or not source or not isinstance(source_type, str) or not source_type:
                raise RuntimeError(f"ruleset {item['name']} ({item['id']}) has missing ownership information (source_type: {source_type!r})")
            if source_type == "Repository":
                if source.lower() != repo["full_name"].lower():
                    raise RuntimeError(f"ruleset {item['name']} belongs to another repository: {source}")
                owned.append(item)
            elif source_type not in ("Organization", "Enterprise"):
                raise RuntimeError(f"ruleset {item['name']} ({item['id']}) has unknown source_type: {source_type!r}")
        return owned
    repo = gh(base)
    topics = set(gh(base + "/topics")["names"])
    kinds = {"factory-personal-fork", "factory-upstream-fork"}
    current_kinds = topics & kinds
    if repo["fork"] and not args.fork_kind and len(current_kinds) != 1:
        raise RuntimeError("fork requires --fork-kind personal or upstream")
    desired_topics = topics - kinds
    if repo["fork"]:
        desired_topics.add("factory-" + args.fork_kind + "-fork" if args.fork_kind else next(iter(current_kinds)))
    changes = []
    needs_pr = []

    def change(description, method, endpoint, body=None):
        changes.append((description, method, endpoint, body))

    upstream = "factory-upstream-fork" in desired_topics
    personal_fork = repo["fork"] and "factory-personal-fork" in desired_topics
    if desired_topics != topics:
        change(f"topics: {sorted(topics)} -> {sorted(desired_topics)}", "PUT", base + "/topics", {"names": sorted(desired_topics)})
    approval_path = base + "/actions/permissions/fork-pr-contributor-approval"
    approval = gh(approval_path)
    if approval["approval_policy"] != CONFIG["fork_approval"]:
        change(f"fork workflow approval: {approval['approval_policy']} -> {CONFIG['fork_approval']}", "PUT", approval_path, {"approval_policy": CONFIG["fork_approval"]})
    if repo["private"]:
        private_path = base + "/actions/permissions/fork-pr-workflows-private-repos"
        private_settings = gh(private_path)
        private_desired = dict(CONFIG["private_fork_settings"])
        if upstream:
            private_desired["run_workflows_from_fork_pull_requests"] = private_settings["run_workflows_from_fork_pull_requests"]
        if any(private_settings.get(k) != v for k, v in private_desired.items()):
            change(f"private fork workflows: {json.dumps(private_settings, sort_keys=True)} -> {json.dumps(private_desired, sort_keys=True)}", "PUT", private_path, private_desired)
    if upstream:
        factory_rules = [item for item in owned_rulesets() if item["name"] in (CONFIG["ruleset_name"], CONFIG["review_ruleset_name"])]
        print("drift:")
        for description, _, _, _ in changes:
            print("  " + description)
        for item in factory_rules:
            print(f"  manual: ruleset {item['name']}: gh api -X DELETE {base}/rulesets/{item['id']}")
        if not changes and not factory_rules:
            print("  no changes")
        print("Upstream Fork: skipped ruleset, merge settings, labels, vendor files, Codacy and CI review key")
        if args.apply:
            for _, method, endpoint, body in changes:
                gh(endpoint, method, body)
        return

    branch = urllib.parse.quote(repo["default_branch"], safe="")
    rules = gh(base + f"/rules/branches/{branch}?per_page=100", paginate=True)
    manual_drift = []
    for rule in rules:
        policy = f"ruleset {rule.get('ruleset_source', 'unknown')} ({rule.get('ruleset_id', 'unknown')})"
        manual_drift.extend(advisory_drift(policy, rule))
    protection = optional_gh(base + f"/branches/{branch}/protection")
    if protection:
        manual_drift.extend(advisory_drift("legacy branch protection", protection))

    for path, expected in CONFIG["vendor_files"].items():
        content = repo_file(base, path, repo["default_branch"])
        if content is None:
            needs_pr.append(f"{path}: missing; expected {json.dumps(expected)}")
            continue
        try:
            parsed = json.loads(command(["yq", "-o=json", ".", "-"], content))
        except (RuntimeError, ValueError) as error:
            needs_pr.append(f"{path}: unreadable ({error}); expected {json.dumps(expected, sort_keys=True)}")
            continue
        if not matches(parsed, expected):
            needs_pr.append(f"{path}: {json.dumps(parsed, sort_keys=True)} -> {json.dumps(expected, sort_keys=True)}")

    merge = CONFIG["merge_settings"]
    if any(repo.get(k) != v for k, v in merge.items()):
        before = {key: repo.get(key) for key in merge}
        change(f"merge settings: {json.dumps(before, sort_keys=True)} -> {json.dumps(merge, sort_keys=True)}", "PATCH", base, merge)
    labels = {label["name"]: label for label in gh(base + "/labels?per_page=100", paginate=True)}
    for label, (colour, description) in CONFIG["labels"].items():
        existing = labels.get(label)
        if not existing or existing["color"].lower() != colour or (existing.get("description") or "") != description:
            before = {"color": existing["color"], "description": existing.get("description")} if existing else None
            after = {"name": label, "color": colour, "description": description}
            change(f"label {label}: {json.dumps(before, sort_keys=True)} -> {json.dumps(after, sort_keys=True)}", "PATCH" if existing else "POST", base + "/labels" + ("/" + urllib.parse.quote(label, safe="") if existing else ""), after)
    for label, details in labels.items():
        if details.get("default") is True and label not in CONFIG["labels"]:
            change(f"label {label}: delete", "DELETE", base + "/labels/" + urllib.parse.quote(label, safe=""))
    token = None
    codacy_ready = False
    if not repo["private"]:
        token = op_read(CONFIG["codacy_token"])
        cursor = None
        enrolled = False
        while True:
            query = {"limit": 100}
            if cursor:
                query["cursor"] = cursor
            page = codacy(f"organizations/gh/{urllib.parse.quote(owner)}/repositories?" + urllib.parse.urlencode(query), token)
            enrolled |= any(item.get("name", "").lower() == name.lower() for item in page["data"])
            cursor = page.get("pagination", {}).get("cursor")
            if not cursor:
                break
        if not enrolled:
            changes.append(("Codacy: enroll public repository", "CODACY", "", None))
        codacy_ready = enrolled and codacy_check_seen(base, repo["default_branch"])
        if not codacy_ready:
            manual_drift.append("Codacy check not yet reporting")
    listed_rulesets = owned_rulesets()

    def find_ruleset(name):
        matching = [item for item in listed_rulesets if item["name"] == name]
        if len(matching) > 1:
            raise RuntimeError(f"multiple rulesets have the name {name}")
        return gh(base + "/rulesets/" + str(matching[0]["id"])) if matching else None

    live = find_ruleset(CONFIG["ruleset_name"])
    existing_codacy = None
    if live and not repo["private"] and live.get("enforcement") == "active":
        for rule in live.get("rules", []):
            if rule.get("type") == "required_status_checks":
                for check in rule.get("parameters", {}).get("required_status_checks", []):
                    if check.get("context") == CONFIG["public_check"]["context"]:
                        existing_codacy = {key: check[key] for key in ("context", "integration_id") if check.get(key) is not None}
    desired = ruleset(existing_codacy or (CONFIG["public_check"] if codacy_ready else None), personal_fork)
    planned = [(live, desired)]
    if personal_fork:
        review = review_ruleset()
        planned.insert(0, (find_ruleset(review["name"]), review))
    for current, target in planned:
        if not current or not matches(current, target):
            before = {key: current.get(key) for key in target} if current else None
            change(f"ruleset {target['name']}: {json.dumps(before, sort_keys=True)} -> {json.dumps(target, sort_keys=True)}", "PUT" if current else "POST", base + "/rulesets" + ("/" + str(current["id"]) if current else ""), target)
    if not personal_fork:
        stale_review = find_ruleset(CONFIG["review_ruleset_name"])
        if stale_review:
            expected_review = review_ruleset()
            if matches(stale_review, expected_review) and all(matches(expected_review[key], stale_review[key]) for key in ("rules", "bypass_actors", "conditions")):
                change(f"ruleset {CONFIG['review_ruleset_name']}: delete", "DELETE", base + "/rulesets/" + str(stale_review["id"]))
            else:
                manual_drift.append(f"ruleset {stale_review['name']} differs from Factory review rules: {json.dumps(stale_review, sort_keys=True)}; gh api -X DELETE {base}/rulesets/{stale_review['id']}")
    secret_pages = json.loads(command(["gh", "api", "--paginate", "--slurp", base + "/actions/secrets?per_page=100"]))
    secrets = [secret for page in secret_pages for secret in page["secrets"]]
    secret_needed = args.rotate_secret or not any(s["name"] == "OPENCODE_ZEN_API_KEY" for s in secrets)
    if secret_needed:
        changes.append(("Actions secret OPENCODE_ZEN_API_KEY: set from 1Password", "SECRET", "", None))
    print("drift:")
    for description, _, _, _ in changes:
        print("  " + description)
    for description in needs_pr:
        print("  needs PR: " + description)
    for description in manual_drift:
        print("  manual: " + description)
    if not changes and not needs_pr and not manual_drift:
        print("  no changes")
    print("manual checks:")
    print("  Copilot personal automatic reviews: https://docs.github.com/en/copilot/how-tos/copilot-on-github/set-up-copilot/configure-code-review")
    print("  Devin pricing: https://docs.devin.ai/admin/billing")
    if args.apply:
        for _, method, endpoint, body in changes:
            if method == "SECRET":
                command(["gh", "secret", "set", "OPENCODE_ZEN_API_KEY", "--repo", args.repo], op_read(CONFIG["review_secret"]))
            elif method == "CODACY":
                codacy("repositories", token, {"provider": "gh", "repositoryFullPath": args.repo})
            else:
                gh(endpoint, method, body)
        if needs_pr or manual_drift:
            raise RuntimeError("repository has unresolved PR or manual drift")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(f"repo-settings: {error}", file=sys.stderr)
        sys.exit(1)
