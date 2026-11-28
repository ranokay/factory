#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess  # nosec B404  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local git with argument lists
import tomllib
from pathlib import Path

from check_result import load_result
from run_guard_paths import directory_target, resolve_directory
from shell_parser import Invocation, Word, has_expansion, invocations

HEAD_CHANGING_COMMANDS = {"am", "checkout", "cherry-pick", "commit", "merge", "pull", "rebase", "reset", "revert", "switch", "update-ref"}


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local git with an argument list
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def git_subcommand_word(invocation: Invocation) -> Word | None:
    if invocation.executable != "git":
        return None
    index = 0
    while index < len(invocation.arguments):
        text = invocation.arguments[index].text
        if text in {"-C", "-c", "--namespace", "--config-env", "--git-dir", "--work-tree"}:
            index += 2
            continue
        if text.startswith("-"):
            index += 1
            continue
        return invocation.arguments[index]
    return None


def git_push_options(invocation: Invocation, cwd: Path) -> tuple[Path, int] | None:
    if invocation.executable != "git":
        return None
    if any(name == "GIT_DIR" for name, _ in invocation.environment):
        raise ValueError("GIT_DIR pushes are unsupported; use git -C <repository> push")
    directory = cwd
    arguments = invocation.arguments
    index = 0
    while index < len(arguments):
        text = arguments[index].text
        if text == "-C":
            index += 1
            if index >= len(arguments):
                return None
            directory = resolve_directory(directory, arguments[index].text)
            index += 1
            continue
        if text.startswith("-C") and len(text) > 2:
            directory = resolve_directory(directory, text[2:])
            index += 1
            continue
        if text in {"--git-dir", "--work-tree"} or text.startswith(("--git-dir=", "--work-tree=")):
            raise ValueError("--git-dir and --work-tree pushes are unsupported; use git -C <repository> push")
        if text in {"-c", "--namespace", "--config-env"}:
            index += 2
            continue
        if text.startswith("-"):
            index += 1
            continue
        if text != "push":
            return None
        return directory, index + 1
    return None


def push_sources(arguments: tuple[Word, ...], index: int) -> list[str] | None:
    sources: list[str] = []
    deleted_refs = 0
    repository_seen = False
    delete_option = False
    while index < len(arguments):
        text = arguments[index].text
        if text in {"--all", "--mirror", "--tags"}:
            raise ValueError("multi-ref pushes are unsupported; push an explicit ref with a passing check record")
        if text == "--delete":
            delete_option = True
            index += 1
            continue
        if text in {"--repo", "--receive-pack", "--exec", "--push-option", "-o"}:
            index += 2
            continue
        if text.startswith("-"):
            index += 1
            continue
        if not repository_seen:
            repository_seen = True
            index += 1
            continue
        refspec = text[1:] if text.startswith("+") else text
        source = refspec.split(":", 1)[0]
        if source:
            sources.append(source)
        else:
            deleted_refs += 1
        index += 1
    if delete_option and not sources and not deleted_refs:
        raise ValueError("--delete requires an explicit ref")
    if sources and deleted_refs:
        raise ValueError("mixed deletion and update pushes are unsupported")
    if delete_option or deleted_refs:
        return None
    return sources


def git_push(invocation: Invocation, cwd: Path) -> tuple[Path, list[str]] | None:
    options = git_push_options(invocation, cwd)
    if options is None:
        return None
    directory, index = options
    sources = push_sources(invocation.arguments, index)
    return directory, [] if sources is None else sources or ["HEAD"]


def repository_root(directory: Path) -> Path | None:
    result = git(directory, "rev-parse", "--show-toplevel")
    if result.returncode:
        return None
    return Path(result.stdout.strip()).resolve()


def records_checks(root: Path) -> bool:
    path = root / "mise.toml"
    try:
        text = path.read_text()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"cannot parse {path}: {error}") from error
    tasks = data.get("tasks", {})
    if not isinstance(tasks, dict):
        raise ValueError(f"{path} tasks must be a table")
    task = tasks.get("check")
    if task is None:
        return False
    if isinstance(task, str):
        run = task
    elif isinstance(task, dict):
        run = task.get("run")
        if run is None:
            return False
    else:
        raise ValueError(f"{path} check task must be a string or table")
    if not isinstance(run, (str, list)):
        raise ValueError(f"{path} check task run must be a string or array")
    if isinstance(run, list) and not all(isinstance(item, str) for item in run):
        raise ValueError(f"{path} check task run array must contain only strings")
    return "check_result" in json.dumps(run)


def push_record_reason(directory: Path, source: str = "HEAD") -> str | None:
    root = repository_root(directory)
    if root is None:
        return None
    try:
        recorder_repo = records_checks(root)
    except ValueError as error:
        return f"Factory Run guard: cannot determine push-check scope: {error}"
    if not recorder_repo:
        return None
    revision = "HEAD" if source == "HEAD" else f"{source}^{{commit}}"
    head = git(root, "rev-parse", "--verify", revision)
    if head.returncode:
        return f"Factory Run guard: cannot resolve pushed ref {source}. Run `mise run check` on the commit you intend to push."
    sha = head.stdout.strip()
    record = load_result(root, sha)
    if record and record.get("passed") is True and record.get("head") == sha:
        return None
    return (
        f"Factory Run guard: no passing mise run check record for {source} ({sha}). "
        f"Run `mise run check` on a clean tree at {sha}, then push."
    )


def directory_change(invocation: Invocation, possible_cwds: set[Path]) -> tuple[set[Path], str | None]:
    if invocation.executable not in {"cd", "pushd"}:
        return possible_cwds, None
    target, error = directory_target(invocation.arguments)
    if error == "bare":
        return possible_cwds, "Factory Run guard: cannot verify a push after a bare directory change. Use an explicit repository directory."
    if error == "previous":
        return possible_cwds, "Factory Run guard: cannot verify a push after a bare or previous-directory change. Use an explicit repository directory."
    return possible_cwds | {resolve_directory(directory, target) for directory in possible_cwds}, None


def verify_push(invocation: Invocation, directory: Path, head_may_change: bool) -> str | None:
    try:
        push = git_push(invocation, directory)
    except ValueError as error:
        return f"Factory Run guard: {error}."
    if push is None:
        return None
    if head_may_change:
        return "Factory Run guard: a HEAD-changing command precedes this push. Run `mise run check` after it, then push in a separate call."
    root, sources = push
    for source in sources:
        reason = push_record_reason(root, source)
        if reason:
            return reason
    return None


def push_rule(command: str, cwd: Path) -> str | None:
    possible_cwds = {cwd}
    head_may_change = False
    for invocation in invocations(command):
        if invocation.executable == "popd":
            return "Factory Run guard: cannot verify a push after popd. Run the push from an explicit repository directory."
        possible_cwds, error = directory_change(invocation, possible_cwds)
        if error:
            return error
        if invocation.executable in {"cd", "pushd"}:
            continue
        subcommand_word = git_subcommand_word(invocation)
        if subcommand_word is not None and (
            subcommand_word.text in HEAD_CHANGING_COMMANDS or has_expansion(subcommand_word)
        ):
            head_may_change = True
        if subcommand_word is None or subcommand_word.text != "push":
            continue
        base_cwds = (
            {resolve_directory(directory, invocation.directory) for directory in possible_cwds}
            if invocation.directory
            else possible_cwds
        )
        for directory in sorted(base_cwds):
            reason = verify_push(invocation, directory, head_may_change)
            if reason:
                return reason
    return None
