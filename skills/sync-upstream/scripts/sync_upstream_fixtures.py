import json
import os
import shutil
import subprocess  # nosec B404 -- the fixture runs local executables
import sys
import tempfile
import unittest
from pathlib import Path

from collections import namedtuple

ForkPair = namedtuple("ForkPair", "origin_seed base origin upstream upstream_seed ahead")
GH = '''#!/usr/bin/env python3
import json, os, sys
state = json.load(open(os.environ["FAKE_REPO"]))
args = sys.argv[1:]
if args[:2] == ["api", "graphql"]:
    if state.get("threads_unavailable"):
        print("review threads unavailable", file=sys.stderr)
        sys.exit(1)
    pages = state.get("thread_pages", [{"nodes": state.get("review_threads", []), "pageInfo": {"hasNextPage": False, "endCursor": None}}])
    print(json.dumps([{"data": {"repository": {"pullRequest": {"reviewThreads": page}}}} for page in pages]))
elif args[:2] == ["api", "repos/" + state["name"]]:
    print(json.dumps(state["info"]))
elif args[:2] == ["repo", "view"]:
    print(state.get("view", state["name"]))
elif args[:2] == ["pr", "view"]:
    field = args[args.index("--json") + 1] if "--json" in args else "body"
    if field == "headRefOid":
        print(state["head"])
    elif field == "baseRefName":
        print(state.get("base", "main"))
    else:
        print(state["body"])
elif args[0] == "api":
    endpoint = next(x for x in args if x.startswith("repos/"))
    key = endpoint.split("?", 1)[0]
    if key.startswith("repos/" + state["name"] + "/rules/branches/"):
        print(json.dumps(state.get("rules_pages", [state.get("applied_rules", [])])))
    elif "/commits/" in key and not key.endswith(("/check-runs", "/statuses", "/status")):
        print(json.dumps({"commit": {"verification": {"verified": state.get("verified", True)}}}))
    elif key.endswith("/check-runs"):
        head = key.split("/commits/")[1].removesuffix("/check-runs")
        print(json.dumps(state.get("check_pages", [{"check_runs": state.get("check_runs", {}).get(head, [])}])))
    elif key.endswith("/status"):
        print(json.dumps({"state": "success", "statuses": state.get("statuses", [])}))
    elif key.endswith("/statuses"):
        print(json.dumps(state.get("status_pages", [state.get("statuses", [])])))
    else:
        print("unexpected gh api call: " + " ".join(args), file=sys.stderr)
        sys.exit(1)
else:
    print("unexpected gh call: " + " ".join(args), file=sys.stderr)
    sys.exit(1)
'''


SCRIPT = Path(__file__).resolve().with_name("sync-upstream")
GIT = shutil.which("git") or "git"


def run_process(args, cwd, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)  # nosec B603  # nosemgrep -- the fixture runs local executables


class SyncFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(GH)
        gh.chmod(0o755)
        self.repo_file = self.root / "repo.json"
        self.env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], FAKE_REPO=str(self.repo_file))
        self.write_repo("o/r", ["factory-personal-fork"], "main")

    def write_repo(self, name, topics, default, fork=True, parent="up/stream", parent_default="main"):
        self.repo_file.write_text(json.dumps({"name": name, "info": {"fork": fork, "default_branch": default, "parent": {"full_name": parent, "default_branch": parent_default}, "topics": topics}}))

    def cli(self, *args, cwd=None, check=True):
        result = run_process([sys.executable, str(SCRIPT), *args], cwd or self.root, self.env)
        if check and result.returncode:
            self.fail(result.stderr)
        return result

    def git(self, cwd, *args, check=True):
        result = run_process([GIT, "-C", str(cwd), *args], None)
        if check and result.returncode:
            self.fail(result.stderr)
        return result

    def init(self, name):
        path = self.root / name
        path.mkdir()
        self.git(path, "init", "-q", "-b", "main")
        self.git(path, "config", "user.name", "Fixture")
        self.git(path, "config", "user.email", "fixture@example.com")
        return path

    def commit(self, path, name, content, message):
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.git(path, "add", "-A")
        self.git(path, "commit", "-qm", message)
        return self.git(path, "rev-parse", "HEAD").stdout.strip()

    def bare(self, name, seed, branch="main"):
        path = self.root / f"{name}.git"
        self.git(self.root, "init", "--bare", "-q", "-b", "main", str(path))
        self.git(seed, "push", "-q", str(path), f"HEAD:refs/heads/{branch}")
        return path

    def seed(self, name, files):
        path = self.init(name)
        for filename, content in files.items():
            (path / filename).parent.mkdir(parents=True, exist_ok=True)
            (path / filename).write_text(content)
        self.git(path, "add", "-A")
        self.git(path, "commit", "-qm", "base")
        return path

    def clone(self, source, target):
        self.git(self.root, "clone", "-q", str(source), str(target))
        self.git(target, "config", "user.name", "Fixture")
        self.git(target, "config", "user.email", "fixture@example.com")
        return target

    def fork_pair(self):
        origin_seed = self.seed("origin-seed", {"file.txt": "base\n"})
        base = self.git(origin_seed, "rev-parse", "HEAD").stdout.strip()
        origin = self.bare("origin", origin_seed)
        upstream_seed = self.root / "upstream-seed"
        self.clone(origin, upstream_seed)
        ahead = self.commit(upstream_seed, "upstream.txt", "upstream\n", "upstream change")
        upstream = self.bare("upstream", upstream_seed)
        self.mirror(upstream)
        return ForkPair(origin_seed, base, origin, upstream, upstream_seed, ahead)

    def mirror(self, path):
        self.git(self.root, "--git-dir", str(path), "config", "remote.origin.url", "https://github.com/up/stream.git")
