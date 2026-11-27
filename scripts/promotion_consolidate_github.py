import json
import subprocess  # nosec B404 -- gh and git run with an argument list, never a shell
from pathlib import Path
from pushback_common import issue_comments
from promotion_consolidate_text import Refusal, TRACKING_MARKER, author_login


PROMOTION = "promotion"


TRACKING_LIST_LIMIT = 1000


PROMOTION_LIST_LIMIT = 1000


def gh(*args, timeout=120):
    result = subprocess.run(  # nosec B603 B607 -- gh with an argument list
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {detail}")
    return result.stdout


def fetch_login(repo):
    login = gh("api", "user", "--jq", ".login").strip()
    if not login:
        raise Refusal(f"gh returned no login for the account that reads {repo}")
    return login


def load_promotions(repo):
    raw = json.loads(
        gh(
            "issue", "list", "-R", repo, "--label", PROMOTION, "--state", "all",
            "--limit", str(PROMOTION_LIST_LIMIT), "--json", "number,title,body,state,labels,createdAt",
        )
    )
    if len(raw) >= PROMOTION_LIST_LIMIT:
        raise Refusal(f"the promotion listing reached its {PROMOTION_LIST_LIMIT}-issue cap; it cannot see the whole backlog")
    return {
        item["number"]: {
            "number": item["number"],
            "title": item["title"],
            "body": item["body"],
            "state": item["state"].upper(),
            "labels": [label["name"] for label in item["labels"]],
            "createdAt": item["createdAt"],
        }
        for item in raw
    }


class Trust:
    def __init__(self, repo, login):
        self.repo = repo
        self.login = login
        self.cache = {}

    def comments(self, number):
        if number not in self.cache:
            self.cache[number] = issue_comments(self.repo, number)
        return self.cache[number]

    def by_login(self, number):
        return [comment for comment in self.comments(number) if author_login(comment) == self.login]

    def has(self, number, marker):
        return any(marker in comment["body"] for comment in self.by_login(number))


def find_tracking(repo, login):
    found = json.loads(
        gh("issue", "list", "-R", repo, "--state", "all", "--limit", str(TRACKING_LIST_LIMIT), "--json", "number,body,author")
    )
    if len(found) >= TRACKING_LIST_LIMIT:
        raise Refusal(
            f"the tracking issue lookup reached its {TRACKING_LIST_LIMIT}-issue cap; "
            "it cannot prove there is one tracking issue"
        )
    matches = [
        item["number"]
        for item in found
        if TRACKING_MARKER in item["body"] and author_login(item) == login
    ]
    if len(matches) > 1:
        raise Refusal(f"more than one tracking issue carries the marker: {matches}")
    return matches[0] if matches else None


def fetch_integration(root):
    result = subprocess.run(  # nosec B603 B607 -- git with an argument list
        ["git", "-C", str(root), "fetch", "--quiet", "origin", "main"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "git fetch failed"
        raise Refusal(f"cannot fetch origin main, so checks cannot be verified against it: {detail}")


class Runner:
    def __init__(self, repo, dry_run, scratch):
        self.repo = repo
        self.dry_run = dry_run
        self.scratch = Path(scratch)
        self.actions = []
        self.counter = 0

    def file(self, text):
        self.counter += 1
        path = self.scratch / f"body-{self.counter}.md"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def write(self, description, *args):
        self.actions.append(description)
        if self.dry_run:
            return ""
        return gh(*args)
