#!/usr/bin/env python3

from __future__ import annotations

import json
import re
from pathlib import Path

from run_guard_paths import resolve_directory
from run_guard_stop import gh
from shell_parser import Invocation, invocations


def gh_merge(invocation: Invocation) -> tuple[str | None, str | None] | None:
    if invocation.executable != "gh":
        return None
    texts = [word.text for word in invocation.arguments]
    repository = None
    index = 0
    while index < len(texts):
        text = texts[index]
        if text in {"-R", "--repo"}:
            index += 1
            if index < len(texts):
                repository = texts[index]
            index += 1
            continue
        if text.startswith("--repo="):
            repository = text.split("=", 1)[1]
        elif text.startswith("-R") and len(text) > 2:
            repository = text[2:]
        index += 1
    try:
        pr = texts.index("pr")
        merge = texts.index("merge", pr + 1)
    except ValueError:
        return None
    admin = any(
        text == "--admin"
        or (text.startswith("--admin=") and text.split("=", 1)[1].lower() in {"1", "t", "true"})
        for text in texts[merge + 1 :]
    )
    if not admin:
        return None
    selector = None
    index = merge + 1
    options_with_values = {
        "-A", "--author-email", "-b", "--body", "-F", "--body-file",
        "-R", "--repo", "-t", "--subject", "--match-head-commit",
    }
    while index < len(texts):
        text = texts[index]
        if text in options_with_values:
            index += 2
            continue
        if not text.startswith("-"):
            selector = text
            break
        index += 1
    return selector, repository


def repository_arguments(repository: str | None) -> list[str]:
    return ["-R", repository] if repository else []


def topic_names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names = []
    for item in value:
        name = item if isinstance(item, str) else item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str):
            names.append(name)
    return names


def exported_repo(invocation: Invocation) -> bool:
    if any(name == "GH_REPO" for name, _ in invocation.environment):
        return True
    return invocation.executable in {"export", "declare", "typeset", "readonly"} and any(
        argument.text == "GH_REPO" or argument.text.startswith(("GH_REPO=", "GH_REPO+="))
        for argument in invocation.arguments
    )


def directory_changed(invocation: Invocation) -> tuple[bool, str | None]:
    if invocation.executable not in {"cd", "pushd"}:
        return False, None
    if not invocation.arguments:
        return False, "Factory Run guard: cannot verify an admin merge after a bare directory change. Use an explicit repository directory."
    index = 0
    while index < len(invocation.arguments):
        option = invocation.arguments[index].text
        if option == "--":
            index += 1
            break
        if option.startswith("-") and len(option) > 1:
            index += 1
            continue
        break
    if index < len(invocation.arguments) and invocation.arguments[index].text != "-":
        return True, None
    return False, "Factory Run guard: cannot verify an admin merge after a bare or previous-directory change. Use an explicit repository directory."


def admin_context(command: str) -> tuple[Invocation | None, tuple[str | None, str | None], bool, bool, str | None]:
    changed = False
    exported = False
    invocation: Invocation | None = None
    for invocation in invocations(command):
        exported = exported or exported_repo(invocation)
        if invocation.executable in {"export", "declare", "typeset", "readonly"}:
            continue
        if invocation.executable in {"cd", "pushd"}:
            changed, error = directory_changed(invocation)
            if error:
                return invocation, (None, None), changed, exported, error
            continue
        if invocation.executable == "popd":
            return invocation, (None, None), changed, exported, "Factory Run guard: cannot verify an admin merge after popd. Run the merge from an explicit repository directory."
        merge = gh_merge(invocation)
        if merge is not None:
            return invocation, merge, changed, exported, None
    return invocation, (None, None), changed, exported, None


def repository_from_selector(selector: str | None, repository: str | None) -> str | None:
    if repository is not None or not selector:
        return repository
    match = re.fullmatch(r"https?://github\.com/([^/]+/[^/]+)/pull/\d+", selector)
    return match.group(1) if match else repository


def verify_admin(invocation: Invocation, selector: str | None, repository: str | None, cwd: Path) -> str | None:
    repository = repository_from_selector(selector, repository)
    view = ["pr", "view"]
    if selector:
        view.append(selector)
    view.extend(["--json", "number,headRefName,url", *repository_arguments(repository)])
    check_cwd = resolve_directory(cwd, invocation.directory) if invocation.directory else cwd
    pull = gh(view, check_cwd)
    if pull.returncode:
        return (
            "Factory Run guard: cannot verify admin-merge eligibility: "
            f"{pull.stderr.strip() or pull.stdout.strip()}. Restore GitHub access and retry without --admin."
        )
    try:
        head = json.loads(pull.stdout)["headRefName"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        return f"Factory Run guard: cannot verify admin-merge eligibility: {error}. Retry without --admin."
    topics = gh(["repo", "view", *([repository] if repository else []), "--json", "repositoryTopics"], check_cwd)
    if topics.returncode:
        return (
            "Factory Run guard: cannot verify the base repository topics: "
            f"{topics.stderr.strip() or topics.stdout.strip()}. Restore GitHub access and retry without --admin."
        )
    try:
        names = topic_names(json.loads(topics.stdout)["repositoryTopics"])
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        return f"Factory Run guard: cannot verify the base repository topics: {error}. Retry without --admin."
    if not isinstance(head, str) or not head.startswith("sync/upstream-"):
        return (
            "Factory Run guard: --admin is reserved for Sync PRs with a sync/upstream-* head ref. "
            "Run the merge without --admin or use the eligible Sync PR."
        )
    if "factory-personal-fork" not in names:
        return (
            "Factory Run guard: --admin requires the base repository topic factory-personal-fork. "
            "Restore that topic or run the merge without --admin."
        )
    return None


def admin_rule(command: str, cwd: Path) -> str | None:
    invocation, merge, changed, exported, error = admin_context(command)
    if error:
        return error
    if invocation is None:
        return None
    selector, repository = merge
    if selector is None and repository is None:
        return None
    if exported or any(name == "GH_REPO" for name, _ in invocation.environment):
        return "Factory Run guard: GH_REPO admin merges are unsupported; use gh -R <repository> pr merge."
    if changed:
        return "Factory Run guard: cannot verify an admin merge after a directory change. Run the merge from the target checkout."
    return verify_admin(invocation, selector, repository, cwd)
