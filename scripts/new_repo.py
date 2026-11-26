"""Repository, clone and settings helpers for the new-repository command."""

import re
import sys

from adopt_common import gh, git, git_out, problem, run, show
from new_common import REPO_SETTINGS, SYNC_UPSTREAM


def repo_exists(slug):
    result = run(["gh", "repo", "view", slug, "--json", "nameWithOwner"])
    if result.returncode == 0:
        return True
    detail = (result.stderr or result.stdout).strip()
    if "HTTP 404" in detail or "Not Found" in detail or "Could not resolve to a Repository" in detail:
        return False
    problem(f"cannot read {slug}: {detail}")


def create_repo(slug, private, description):
    visibility = "--private" if private else "--public"
    print(f"new: creating {slug}")
    gh("repo", "create", slug, visibility, "--description", description)


def parse_remote(url):
    url = url.strip().rstrip("/")
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return match.group(1).lower() if match else None


def ensure_clone(clone, slug):
    if clone.exists():
        if not (clone / ".git").exists():
            problem(f"{clone} exists and is not a git checkout")
        origin = git(clone, "remote", "get-url", "origin")
        if origin.returncode != 0 or parse_remote(origin.stdout) != slug.lower():
            problem(f"{clone} does not have {slug} as origin")
        status = git(clone, "status", "--porcelain", "--untracked-files=all")
        if status.returncode != 0:
            problem(f"cannot read the status of {clone}")
        if status.stdout.strip():
            problem(f"{clone} has uncommitted changes; commit or stash them first")
        return False
    clone.parent.mkdir(parents=True, exist_ok=True)
    print(f"new: cloning into {clone}")
    result = run(["git", "clone", f"https://github.com/{slug}.git", str(clone)])
    if result.returncode != 0:
        problem(f"cannot clone {slug}: {(result.stderr or result.stdout).strip()}")
    return True


def has_head(clone):
    return git(clone, "rev-parse", "--verify", "HEAD").returncode == 0


def commit_and_push(clone, name):
    if not has_head(clone):
        git_out(clone, "checkout", "-b", "main")
    status = git(clone, "status", "--porcelain", "--untracked-files=all")
    if status.returncode != 0:
        problem("cannot read the new clone's status")
    committed = False
    signed = None
    if status.stdout.strip():
        git_out(clone, "add", "-A")
        message = f"chore: seed {name} from the Project Template"
        commit = run(["git", "-C", str(clone), "commit", "-qm", message])
        if commit.returncode == 0:
            signed = True
        else:
            print("new: signing the seed commit failed; committing unsigned", file=sys.stderr)
            commit = run(
                ["git", "-C", str(clone), "-c", "commit.gpgsign=false", "commit", "-qm", message]
            )
            if commit.returncode != 0:
                problem(f"cannot commit the seed: {(commit.stderr or commit.stdout).strip()}")
            signed = False
        committed = True
    pushed = False
    if committed or git(clone, "rev-parse", "--verify", "origin/main").returncode != 0:
        git_out(clone, "push", "-u", "origin", "main")
        pushed = True
    if committed:
        state = "signed" if signed else "unsigned"
        print(f"new: committed the seed ({state}){' and pushed' if pushed else ''}")
    return committed, pushed


def install_template(clone):
    result = run(
        [sys.executable, str(SYNC_UPSTREAM), "install-files", "--target", str(clone)]
    )
    show(result.stdout, sys.stdout)
    show(result.stderr, sys.stderr)
    if result.returncode != 0:
        problem("installing the Project Template failed")


def restore_clone(clone):
    if has_head(clone):
        result = run(["git", "-C", str(clone), "checkout", "--", "."])
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            print(f"new: warning: could not restore {clone}: {detail}", file=sys.stderr)
    result = run(["git", "-C", str(clone), "clean", "-fdq"])
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        print(f"new: warning: could not clean {clone}: {detail}", file=sys.stderr)


def apply_repo_settings(slug):
    result = run([sys.executable, str(REPO_SETTINGS), slug, "--apply"])
    show(result.stdout, sys.stdout)
    show(result.stderr, sys.stderr)
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout).strip()
    problem(f"repository settings failed: {detail}")
