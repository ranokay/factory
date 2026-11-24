#!/usr/bin/env python3
"""File one maintenance issue for each repo whose app changed since its verify skill was maintained.

Last maintenance is the newer of the last commit touching a `.agents/skills/verify-*`
path and the head recorded in the most recently closed maintenance issue. A clean pass
closes its issue without committing, so its recorded head keeps the next week quiet
until the app changes again.
"""

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARKER = "factory:verify-maintenance"
HEAD_MARKER = re.compile(r"<!--\s*factory:verify-maintenance\s+head=([0-9a-f]{40})\s*-->")
SKILL_ROOT = ".agents/skills"
CLAUDE_ROOT = ".claude/skills"
EXCLUDES = [f":(exclude){SKILL_ROOT}", f":(exclude){CLAUDE_ROOT}"]


def load_canonical_clones():
    spec = importlib.util.spec_from_file_location("factory_canonical_clones", ROOT / "canonical-clones.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo, *args, timeout=60):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def git_out(repo, *args, timeout=60):
    result = git(repo, *args, timeout=timeout)
    return result.stdout.strip() if result.returncode == 0 else None


def gh(*args, timeout=60):
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def default_branch(repo):
    result = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    upstream = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream.returncode == 0 and upstream.stdout.strip():
        return upstream.stdout.strip()
    return None


def is_ancestor(repo, older, newer, timeout=60):
    return git(repo, "merge-base", "--is-ancestor", older, newer, timeout=timeout).returncode == 0


def commit_time(repo, commit):
    output = git_out(repo, "show", "-s", "--format=%ct", commit)
    if not output or not output.isdigit():
        raise RuntimeError(f"cannot read the commit time of {commit}")
    return int(output)


def newer(repo, first, second):
    if not first:
        return second
    if not second or first == second:
        return first
    if is_ancestor(repo, first, second):
        return second
    if is_ancestor(repo, second, first):
        return first
    return second if commit_time(repo, second) > commit_time(repo, first) else first


def fetch(repo):
    result = git(repo, "fetch", "--quiet", "--prune", "origin", timeout=120)
    if result.returncode != 0:
        print(f"verify-maintenance: warning: cannot fetch {repo}: {result.stderr.strip()}", file=sys.stderr)


def verify_skills(repo):
    root = repo / SKILL_ROOT
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.glob("verify-*") if (path / "SKILL.md").is_file())


def last_skill_commit(repo, branch, skills):
    paths = [f"{SKILL_ROOT}/{name}" for name in skills]
    result = git(repo, "log", "-1", "--format=%H", branch, "--", *paths)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def issues(repo_slug, state):
    result = gh(
        "issue",
        "list",
        "--repo",
        repo_slug,
        "--state",
        state,
        "--limit",
        "200",
        "--json",
        "number,title,body,closedAt",
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh issue list --state {state} failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"gh issue list --state {state} returned invalid JSON: {error}") from None
    if not isinstance(data, list):
        raise RuntimeError(f"gh issue list --state {state} did not return a list")
    return [issue for issue in data if isinstance(issue, dict) and MARKER in str(issue.get("body") or "")]


def closed_head(repo, repo_slug, branch):
    maintenance = issues(repo_slug, "closed")
    if not maintenance:
        return None
    latest = max(maintenance, key=lambda issue: (str(issue.get("closedAt") or ""), int(issue.get("number") or 0)))
    match = HEAD_MARKER.search(str(latest.get("body") or ""))
    if match is None:
        print(
            f"verify-maintenance: warning: closed maintenance issue #{latest.get('number')} has no head marker",
            file=sys.stderr,
        )
        return None
    head = match.group(1)
    if git(repo, "cat-file", "-e", f"{head}^{{commit}}").returncode != 0:
        print(f"verify-maintenance: warning: closed maintenance issue head {head} is not in {repo}", file=sys.stderr)
        return None
    if not is_ancestor(repo, head, branch):
        print(f"verify-maintenance: warning: closed maintenance head {head} is not on {branch}", file=sys.stderr)
        return None
    return head


def changed_paths(repo, last, branch):
    result = git(repo, "diff", "--name-only", last, branch, "--", ".", *EXCLUDES)
    if result.returncode != 0:
        raise RuntimeError(f"cannot diff {last}..{branch}: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line]


def count_since(repo, last, branch):
    output = git_out(repo, "rev-list", "--count", f"{last}..{branch}")
    if not output or not output.isdigit():
        raise RuntimeError(f"cannot count commits in {last}..{branch}")
    return int(output)


def subject(repo, commit):
    return git_out(repo, "log", "-1", "--format=%h %s", commit) or commit[:12]


def title_for(skills):
    if len(skills) == 1:
        return f"Maintain {skills[0]} skill"
    return "Maintain verification skills (" + ", ".join(skills) + ")"


def body_for(repo, skills, branch, head, last, paths):
    lines = [
        "Run `/maintain-verification-skill` in this repository.",
        "",
        "The app changed since the verification skill was last maintained.",
        "",
        f"- Verification skill: {', '.join(f'`{SKILL_ROOT}/{name}/`' for name in skills)}",
        f"- Last maintenance: `{subject(repo, last)}`",
        f"- Default branch: `{branch}`",
        f"- Commits since: {count_since(repo, last, branch)}",
        f"- Latest: `{subject(repo, head)}`",
        f"- Changed paths outside the skill: {len(paths)}",
        "",
        "A `clean` pass ships nothing and closes this issue; its recorded head stops a new issue until the app changes again.",
        "",
        f"<!-- {MARKER} head={head} -->",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / "Developer", help="directory of canonical clones")
    parser.add_argument("--dry-run", action="store_true", help="report what would be filed without filing")
    parser.add_argument("--no-fetch", action="store_true", help="skip git fetch")
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"verify-maintenance: no such directory: {root}", file=sys.stderr)
        return 1
    clones = load_canonical_clones()
    failures = 0
    for identity, repo in clones.github_repos(root):
        repo_slug = identity.removeprefix("github.com/")
        skills = verify_skills(repo)
        if not skills:
            continue
        if not args.no_fetch and not args.dry_run:
            fetch(repo)
        try:
            branch = default_branch(repo)
            if not branch:
                raise RuntimeError("cannot resolve origin/HEAD; run `git remote set-head origin --auto`")
            head = git_out(repo, "rev-parse", "--verify", f"{branch}^{{commit}}")
            if not head:
                raise RuntimeError(f"cannot resolve {branch}")
            skill_commit = last_skill_commit(repo, branch, skills)
            recorded = closed_head(repo, repo_slug, branch)
            last = newer(repo, skill_commit, recorded)
            if not last:
                raise RuntimeError("no committed maintenance base")
            paths = changed_paths(repo, last, branch)
            if not paths:
                print(f"unchanged {repo_slug}: no app commits since {subject(repo, last)}", flush=True)
                continue
            open_issues = issues(repo_slug, "open")
            if open_issues:
                numbers = ", ".join(f"#{issue.get('number')}" for issue in open_issues)
                print(f"open {repo_slug}: maintenance already filed ({numbers})", flush=True)
                continue
            title = title_for(skills)
            body = body_for(repo, skills, branch, head, last, paths)
            if args.dry_run:
                print(f"would file {repo_slug}: {title} at {head[:12]}", flush=True)
                continue
            created = gh(
                "issue",
                "create",
                "--repo",
                repo_slug,
                "--title",
                title,
                "--label",
                "ready-for-agent",
                "--body",
                body,
            )
            if created.returncode != 0:
                raise RuntimeError(f"gh issue create failed: {created.stderr.strip() or created.stdout.strip()}")
            print(f"filed {repo_slug}: {created.stdout.strip()}", flush=True)
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            print(f"verify-maintenance: {repo_slug}: {error}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
