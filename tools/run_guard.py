#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 -- Run tools invoke local programs with argument lists
import sys
import tomllib
from pathlib import Path

from check_result import load_result
from run_targets import load_targets, next_target
from shell_parser import Invocation, invocations

UNAVAILABLE_TERMS = (
    "quota",
    "rate limit",
    "rate_limit",
    "overload",
    "overloaded",
    "unavailable",
    "availability",
    "refused",
    "refusal",
    "capacity",
)
OTHER_CAUSE_TERMS = (
    "gate",
    "ci",
    "check",
    "test",
    "permission",
    "credential",
    "authentication",
    "network",
    "timeout",
    "crash",
    "bug",
    "regression",
    "conflict",
    "merge",
    "secret",
)


def dynamic_rule(command: str) -> str | None:
    if any(invocation.dynamic for invocation in invocations(command)):
        return "Factory Run guard: cannot verify a dynamic command executable. Use a literal command."
    return None


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local git with an argument list
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def git_push_root(invocation: Invocation, cwd: Path) -> Path | None:
    if invocation.executable != "git":
        return None
    directory = cwd
    index = 0
    arguments = invocation.arguments
    while index < len(arguments):
        text = arguments[index].text
        if text == "-C":
            index += 1
            if index >= len(arguments):
                return None
            target = Path(os.path.expandvars(arguments[index].text)).expanduser()
            directory = target if target.is_absolute() else directory / target
            index += 1
            continue
        if text.startswith("-C") and len(text) > 2:
            target = Path(os.path.expandvars(text[2:])).expanduser()
            directory = target if target.is_absolute() else directory / target
            index += 1
            continue
        if text in {"-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}:
            index += 2
            continue
        if text.startswith("-"):
            index += 1
            continue
        return directory if text == "push" else None
    return None


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


def push_record_reason(directory: Path) -> str | None:
    root = repository_root(directory)
    if root is None:
        return None
    try:
        recorder_repo = records_checks(root)
    except ValueError as error:
        return f"Factory Run guard: cannot determine push-check scope: {error}"
    if not recorder_repo:
        return None
    head = git(root, "rev-parse", "HEAD")
    if head.returncode:
        return "Factory Run guard: cannot resolve HEAD. Commit your work and run `mise run check` before push."
    sha = head.stdout.strip()
    record = load_result(root, sha)
    if record and record.get("passed") is True and record.get("head") == sha:
        return None
    return (
        f"Factory Run guard: no passing mise run check record for {sha}. "
        f"Run `mise run check` on a clean tree at {sha}, then push."
    )


def push_rule(command: str, cwd: Path) -> str | None:
    possible_cwds = {cwd}
    for invocation in invocations(command):
        if invocation.executable == "cd" and invocation.arguments:
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
            if index >= len(invocation.arguments):
                continue
            target = Path(os.path.expandvars(invocation.arguments[index].text)).expanduser()
            possible_cwds |= {
                target if target.is_absolute() else directory / target
                for directory in possible_cwds
            }
            continue
        directories = {
            directory
            for directory in (git_push_root(invocation, possible_cwd) for possible_cwd in possible_cwds)
            if directory is not None
        }
        for directory in sorted(directories):
            reason = push_record_reason(directory)
            if reason:
                return reason
    return None


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
    options_with_values = {"--body", "--subject", "--match-head-commit"}
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


def gh(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local gh with an argument list
            ["gh", *arguments],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(["gh", *arguments], 124, "", str(error))


def repository_arguments(repository: str | None) -> list[str]:
    return ["-R", repository] if repository else []


def admin_rule(command: str, cwd: Path) -> str | None:
    for invocation in invocations(command):
        merge = gh_merge(invocation)
        if merge is None:
            continue
        selector, repository = merge
        if repository is None and selector:
            match = re.fullmatch(r"https?://github\.com/([^/]+/[^/]+)/pull/\d+", selector)
            if match:
                repository = match.group(1)
        view = ["pr", "view"]
        if selector:
            view.append(selector)
        view.extend(["--json", "number,headRefName,url", *repository_arguments(repository)])
        pull = gh(view, cwd)
        if pull.returncode:
            return (
                "Factory Run guard: cannot verify admin-merge eligibility: "
                f"{pull.stderr.strip() or pull.stdout.strip()}. Restore GitHub access and retry without --admin."
            )
        try:
            head = json.loads(pull.stdout)["headRefName"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            return f"Factory Run guard: cannot verify admin-merge eligibility: {error}. Retry without --admin."
        topics = gh(["repo", "view", *([repository] if repository else []), "--json", "repositoryTopics"], cwd)
        if topics.returncode:
            return (
                "Factory Run guard: cannot verify the base repository topics: "
                f"{topics.stderr.strip() or topics.stdout.strip()}. Restore GitHub access and retry without --admin."
            )
        try:
            names = json.loads(topics.stdout)["repositoryTopics"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            return f"Factory Run guard: cannot verify the base repository topics: {error}. Retry without --admin."
        if not isinstance(head, str) or not head.startswith("sync/upstream-"):
            return (
                "Factory Run guard: --admin is reserved for Sync PRs with a sync/upstream-* head ref. "
                "Run the merge without --admin or use the eligible Sync PR."
            )
        if not isinstance(names, list) or "factory-personal-fork" not in names:
            return (
                "Factory Run guard: --admin requires the base repository topic factory-personal-fork. "
                "Restore that topic or run the merge without --admin."
            )
    return None


def block_issue_arguments(invocation: Invocation) -> tuple[str, str, str, str, str] | None:
    if invocation.executable != "block-issue" or len(invocation.arguments) < 2:
        return None
    texts = [word.text for word in invocation.arguments]
    repository, issue = texts[:2]
    reason = ""
    unblock = ""
    findings: list[str] = []
    index = 2
    while index < len(texts):
        option = texts[index]
        if option in {"--reason", "--unblock", "--finding"}:
            if index + 1 >= len(texts):
                return None
            value = texts[index + 1]
            if option == "--reason":
                reason = value
            elif option == "--unblock":
                unblock = value
            else:
                findings.append(value)
            index += 2
            continue
        index += 2 if option in {"--evidence", "--detail"} else 1
    return repository, issue, reason, unblock, "\n".join(findings)


def contains_term(text: str, terms: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE) for term in terms)


def fallback_reason(repository: str, issue: str, reason: str, unblock: str, findings: str) -> str | None:
    targets = load_targets(repository, issue)
    if not targets:
        return None
    text = "\n".join((reason, unblock, findings))
    named_target = any(target["model"].lower() in text.lower() for target in targets)
    if not named_target or not contains_term(text, UNAVAILABLE_TERMS) or contains_term(text, OTHER_CAUSE_TERMS):
        return None
    fallback = next_target(repository, issue)
    if fallback is None:
        return None
    variant = fallback.get("options", {}).get("variant")
    name = fallback["model"] + (f" at variant {variant}" if variant else "")
    return f"Factory Run guard: an untried model fallback remains: {name}. Launch it instead of posting a Blocker."


def fallback_rule(command: str) -> str | None:
    for invocation in invocations(command):
        arguments = block_issue_arguments(invocation)
        if arguments is not None:
            reason = fallback_reason(*arguments)
            if reason:
                return reason
    return None


def stop_rule(cwd: Path, repeat: bool) -> tuple[str, str]:
    pull = gh(["pr", "view", "--json", "number,headRefName,url"], cwd)
    if pull.returncode:
        diagnostic = pull.stderr.strip() or pull.stdout.strip()
        if "no pull requests found" in diagnostic.lower():
            return "allow", ""
        if repeat:
            return "warning", f"Factory Run guard warning: required-check facts remain unavailable: {diagnostic}"
        return "block", f"Factory Run guard: cannot verify required checks: {diagnostic}. Restore GitHub access and retry the stop."
    try:
        number = json.loads(pull.stdout)["number"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        message = f"Factory Run guard: cannot read the current PR: {error}"
        return ("warning", f"Factory Run guard warning: {message}") if repeat else ("block", message)
    checks = gh(["pr", "checks", str(number), "--required", "--json", "name,state,link,bucket"], cwd)
    if checks.returncode not in {0, 8}:
        diagnostic = checks.stderr.strip() or checks.stdout.strip()
        message = f"Factory Run guard: cannot read required checks: {diagnostic}"
        return ("warning", f"Factory Run guard warning: {message}") if repeat else ("block", message)
    try:
        rollup = json.loads(checks.stdout)
    except json.JSONDecodeError as error:
        message = f"Factory Run guard: cannot read required checks: {error}"
        return ("warning", f"Factory Run guard warning: {message}") if repeat else ("block", message)
    if not isinstance(rollup, list):
        message = "Factory Run guard: required-check response is not a list"
        return ("warning", f"Factory Run guard warning: {message}") if repeat else ("block", message)
    pending = [check for check in rollup if isinstance(check, dict) and check.get("bucket") == "pending"]
    if not pending:
        return "allow", ""
    names = ", ".join(str(check.get("name", "unnamed check")) for check in pending)
    commands = []
    unknown = []
    for check in pending:
        link = check.get("link")
        match = re.search(r"/actions/runs/(\d+)", link) if isinstance(link, str) else None
        if match:
            command = f"gh run watch {match.group(1)} --exit-status"
            if command not in commands:
                commands.append(command)
        else:
            unknown.append(str(check.get("name", "unnamed check")))
    reason = f"Factory Run guard: required checks are pending on PR #{number}: {names}. Wait in this turn:"
    if commands:
        reason += "\n" + "\n".join(commands)
    if unknown:
        reason += f"\nFor checks without an Actions run ({', '.join(unknown)}), inspect `gh pr checks {number} --watch`."
    return "block", reason


def stop(payload: dict) -> None:
    cwd = Path(payload.get("cwd") or os.getcwd())
    decision, message = stop_rule(cwd, payload.get("stop_hook_active") is True)
    if decision == "block":
        print(json.dumps({"decision": "block", "reason": message}))
    elif decision == "warning":
        print(json.dumps({"systemMessage": message}))


def guard_result(decision: str, message: str = "") -> None:
    if decision == "block":
        print(json.dumps({"block": True, "message": message}))
    elif decision == "warning":
        print(json.dumps({"block": False, "warning": message}))
    else:
        print(json.dumps({"block": False}))


def opencode(payload: dict) -> None:
    tool_name = payload.get("tool")
    tool_input = payload.get("input")
    if tool_name not in {"bash", "shell"} or not isinstance(tool_input, dict):
        guard_result("allow")
        return
    command = tool_input.get("command")
    if not isinstance(command, str):
        guard_result("allow")
        return
    cwd = Path(payload.get("cwd") or os.getcwd())
    reason = dynamic_rule(command) or push_rule(command, cwd) or admin_rule(command, cwd) or fallback_rule(command)
    guard_result("block" if reason else "allow", reason or "")


def opencode_idle(payload: dict) -> None:
    cwd = Path(payload.get("cwd") or os.getcwd())
    decision, message = stop_rule(cwd, payload.get("repeat") is True)
    guard_result(decision, message)


def deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


def tool(payload: dict) -> None:
    if payload.get("tool_name") not in {"Bash", "bash"}:
        return
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    command = tool_input.get("command")
    if not isinstance(command, str):
        return
    cwd = Path(payload.get("cwd") or os.getcwd())
    reason = dynamic_rule(command) or push_rule(command, cwd) or admin_rule(command, cwd) or fallback_rule(command)
    if reason:
        deny(reason)


def block_issue(arguments: list[str]) -> int:
    if len(arguments) < 2:
        print("block-issue: repository and issue are required", file=sys.stderr)
        return 2
    repository, issue = arguments[:2]
    reason = ""
    unblock = ""
    findings: list[str] = []
    index = 2
    while index < len(arguments):
        option = arguments[index]
        if option in {"--reason", "--unblock", "--finding", "--evidence", "--detail"}:
            if index + 1 >= len(arguments):
                print(f"block-issue: missing value for {option}", file=sys.stderr)
                return 2
            if option == "--reason":
                reason = arguments[index + 1]
            elif option == "--unblock":
                unblock = arguments[index + 1]
            elif option == "--finding":
                findings.append(arguments[index + 1])
            index += 2
            continue
        index += 1
    message = fallback_reason(repository, issue, reason, unblock, "\n".join(findings))
    if message:
        print(message, file=sys.stderr)
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if arguments and arguments[0] == "block-issue":
            return block_issue(arguments[1:])
        if arguments not in (["tool"], ["stop"], ["opencode"], ["opencode-idle"]):
            return 0
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            if arguments == ["stop"]:
                stop(payload)
            elif arguments == ["opencode"]:
                opencode(payload)
            elif arguments == ["opencode-idle"]:
                opencode_idle(payload)
            else:
                tool(payload)
    except Exception as error:
        print(f"Factory Run guard warning: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
