#!/usr/bin/env python3
"""Inventory a repository and file one issue per unseen Factory adoption finding.

adopt reads a local checkout, applies repository settings through
`scripts/repo-settings.py --apply`, and files one issue per category containing
only findings that no earlier adopt issue (open or closed) already reports.
The target tree is never modified.
"""

import argparse
import re
import sys
from pathlib import Path

from adopt_common import (
    CHUNK_SIZE,
    CONFIG,
    ROOT,
    Problem,
    digest,
    gh,
    git,
    git_out,
    parse_json,
    problem,
    run,
    show,
    valid_repo,
)
from adopt_categories import CATEGORIES
from adopt_report import body_for, existing_markers


def validate_config():
    for label in ("ready-for-agent", "ready-for-human", "enhancement"):
        if label not in CONFIG["labels"]:
            problem(f"repo-settings.json has no {label} label")


def apply_settings(repo, apply):
    command = [sys.executable, str(ROOT / "repo-settings.py"), repo]
    if apply:
        command.append("--apply")
    result = run(command)
    show(result.stdout, sys.stdout)
    show(result.stderr, sys.stderr)
    if result.returncode == 0:
        return False
    detail = (result.stderr or result.stdout).strip()
    if apply and "unresolved PR or manual drift" in detail:
        return True
    problem(f"repo settings failed: {detail}")


def resolve_checkout(project, repo):
    top = git(project, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        problem(f"{project} is not a git checkout")
    project = Path(top.stdout.strip()).resolve()
    origin = git(project, "remote", "get-url", "origin")
    if origin.returncode != 0:
        problem(f"{project} has no origin remote")
    url = origin.stdout.strip().rstrip("/")
    if url.endswith(".git"):
        url = url.removesuffix(".git")
    match = re.search(r"[:/]([^/:]+/[^/:]+)$", url)
    if not match or match.group(1).lower() != repo.lower():
        problem(
            f"{project} origin is {url!r}, not {repo!r}; run adopt in the matching clone"
        )
    status = git(project, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        problem(f"cannot read the status of {project}")
    if status.stdout.strip():
        problem(f"{project} has uncommitted changes; commit or stash them first")
    return project


def tracked_files(project):
    result = git(project, "ls-files", "-z")
    if result.returncode != 0:
        problem(f"cannot list tracked files: {(result.stderr or result.stdout).strip()}")
    return [path for path in result.stdout.split("\0") if path]


def file_category(category, unseen, total, repo, head, dry_run):
    if dry_run:
        print(f"adopt: would file {category['key']}: {len(unseen)} finding(s)")
        for item in unseen:
            print(f"  {item.key}")
        return 0
    chunks = [
        unseen[start : start + CHUNK_SIZE]
        for start in range(0, len(unseen), CHUNK_SIZE)
    ]
    filed = 0
    for number, chunk in enumerate(chunks, start=1):
        title = category["title"]
        if len(chunks) > 1:
            title = f"{title} ({number}/{len(chunks)})"
        body = body_for(category, chunk, total, repo, head)
        created = gh(
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            title,
            "--body",
            body,
            "--label",
            category["state"],
            "--label",
            "enhancement",
        )
        print(f"adopt: filed {created.strip()}")
        filed += 1
    return filed


def adopt(args):
    repo = args.repo
    project = resolve_checkout(Path(args.project).expanduser().resolve(), repo)
    tracked = tracked_files(project)
    topics_payload = parse_json(
        gh("api", f"repos/{repo}/topics"), f"topics for {repo}"
    )
    topics = topics_payload.get("names") or []
    upstream = "factory-upstream-fork" in topics
    drift = apply_settings(repo, apply=not args.dry_run)
    if upstream:
        return upstream_result(drift)
    seen = existing_markers(repo)
    head = git_out(project, "rev-parse", "--short", "HEAD").strip()
    filed = 0
    for category in CATEGORIES:
        findings = category["findings"](project, tracked, repo)
        unseen = [
            item
            for item in findings
            if (category["key"], digest(item.key)) not in seen
        ]
        if not unseen:
            if findings:
                print(
                    f"adopt: skip {category['key']}: every finding was already filed"
                )
            continue
        filed += file_category(
            category, unseen, len(findings), repo, head, args.dry_run
        )
    if not args.dry_run and filed == 0:
        print("adopt: no new findings")
    return drift_result(drift)


def upstream_result(drift):
    print(
        "adopt: Upstream Fork (topic factory-upstream-fork): upstream "
        "conventions win, so filing nothing."
    )
    return drift_result(drift)


def drift_result(drift):
    if drift:
        print(
            "adopt: repository settings still have unresolved drift; see the "
            "repo-settings output above.",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", help="repository as owner/name")
    parser.add_argument(
        "--project",
        default=".",
        help="local checkout of the repository (default: the current directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report findings and settings drift without filing issues or applying settings",
    )
    args = parser.parse_args(argv)
    try:
        if not valid_repo(args.repo):
            problem(f"repository must be <owner>/<repo>, not {args.repo!r}")
        validate_config()
        return adopt(args)
    except Problem as error:
        print(f"adopt: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
