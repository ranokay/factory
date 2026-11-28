#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from run_guard_admin import GH_MERGE_VALUE_OPTIONS, admin_rule
from run_guard_fallback import fallback_reason, fallback_rule
from run_guard_push import git_subcommand_word, push_rule
from run_guard_stop import stop_rule
from shell_parser import Invocation, Word, has_expansion, invocations


def gh_pr_index(arguments: list[str]) -> int | None:
    index = 0
    while index < len(arguments):
        text = arguments[index]
        if text in {"-R", "--repo", "--hostname"}:
            index += 2
            continue
        if text.startswith(("--repo=", "--hostname=")) or (text.startswith("-R") and len(text) > 2):
            index += 1
            continue
        if text.startswith("-"):
            index += 1
            continue
        return index if text == "pr" else None
    return None


def command_substitutions(value: str) -> list[str]:
    substitutions: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(value):
        char = value[index]
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if char == "`":
            end = index + 1
            while end < len(value) and value[end] != "`":
                end += 2 if value[end] == "\\" else 1
            if end < len(value):
                substitutions.append(value[index + 1 : end])
                index = end + 1
                continue
        if char == "$" and value[index + 1 : index + 2] == "(":
            depth = 1
            end = index + 2
            while end < len(value) and depth:
                if value[end] == "(":
                    depth += 1
                elif value[end] == ")":
                    depth -= 1
                end += 1
            if depth == 0:
                substitutions.append(value[index + 2 : end - 1])
                index = end
                continue
        index += 1
    return substitutions


def command_value_guarded(value: str, depth: int = 0) -> bool:
    if depth > 3:
        return True
    if any(command_value_guarded(item, depth + 1) for item in command_substitutions(value)):
        return True
    for invocation in invocations(value):
        if invocation.executable == "block-issue":
            return True
        if invocation.executable == "git":
            word = git_subcommand_word(invocation)
            if word is not None and word.text == "push":
                return True
        arguments = [word.text for word in invocation.arguments]
        if (
            invocation.executable == "gh"
            and "pr" in arguments
            and "merge" in arguments
            and any(text == "--admin" or text.startswith("--admin=") for text in arguments)
        ):
            return True
    return False


def has_unquoted_expansion(word: Word) -> bool:
    return any(
        active and unquoted
        for char, active, unquoted in zip(word.text, word.expansions, word.unquoted, strict=True)
        if char in "$`"
    )


def gh_admin_risk(invocation: Invocation, pr_index: int | None) -> bool:
    arguments = [word.text for word in invocation.arguments]
    if pr_index is None:
        return False
    index = pr_index + 1
    if index >= len(arguments):
        return False
    if has_expansion(invocation.arguments[index]):
        return True
    if arguments[index] != "merge":
        return any(text == "--admin" or text.startswith("--admin=") for text in arguments)
    explicit_admin = any(text == "--admin" or text.startswith("--admin=") for text in arguments)
    selector_seen = False
    selector_dynamic = False
    index += 1
    while index < len(arguments):
        text = arguments[index]
        if text in GH_MERGE_VALUE_OPTIONS:
            index += 2
            continue
        if text.startswith("-"):
            if has_expansion(invocation.arguments[index]) and not any(
                text.startswith(option + "=") for option in GH_MERGE_VALUE_OPTIONS
            ):
                return True
            index += 1
            continue
        if not selector_seen:
            selector_seen = True
            if has_expansion(invocation.arguments[index]):
                selector_dynamic = True
                if has_unquoted_expansion(invocation.arguments[index]):
                    return True
            index += 1
            continue
        if has_expansion(invocation.arguments[index]):
            return True
        index += 1
    return explicit_admin and selector_dynamic


def dynamic_rule(command: str) -> str | None:
    parsed = invocations(command)
    if any(
        invocation.executable == "eval"
        and any(command_value_guarded(word.text) for word in invocation.arguments)
        for invocation in parsed
    ):
        return "Factory Run guard: cannot verify a dynamic command run through eval. Use a literal command."
    command_expansion_guarded = any(
        command_value_guarded(item) for item in command_substitutions(command)
    )
    for position, invocation in enumerate(parsed):
        if not invocation.dynamic:
            continue
        arguments = [word.text for word in invocation.arguments]
        executable = Path(invocation.executable).name
        nested_words = [word.text for word in invocation.arguments if not word.data]
        nested_guarded = any(word.operator for word in invocation.arguments) and (
            ("git" in nested_words and "push" in nested_words)
            or ("gh" in nested_words and "pr" in nested_words and "merge" in nested_words and "--admin" in nested_words)
            or "block-issue" in nested_words
        )
        substitution_guarded = invocation.executable == "$" and any(
            following.executable == "push"
            or (
                following.executable == "pr"
                and "merge" in [word.text for word in following.arguments]
                and (
                    "--admin" in [word.text for word in following.arguments]
                    or any(has_expansion(word) for word in following.arguments)
                )
            )
            or (
                following.executable == "which"
                and any(word.text == "block-issue" for word in following.arguments)
            )
            for following in parsed[position + 1 :]
        )
        block_issue_shape = (
            len(arguments) >= 2
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", arguments[0]) is not None
            and arguments[1].isdigit()
            and {"--reason", "--unblock"}.issubset(arguments)
        )
        dynamic_executable = "$" in invocation.executable or "`" in invocation.executable
        environment_values = [
            value
            for previous in parsed[: position + 1]
            for _, value in previous.environment
        ]
        pr_index = gh_pr_index(arguments) if invocation.executable == "gh" else None
        explicit_admin = any(text == "--admin" or text.startswith("--admin=") for text in arguments)
        assigned_admin = invocation.executable == "gh" and any(
            has_expansion(word) for word in invocation.arguments
        ) and any(value == "--admin" or value.startswith("--admin=") for value in environment_values)
        admin_expansion = assigned_admin or (
            gh_admin_risk(invocation, pr_index) if invocation.executable == "gh" else (
                explicit_admin and dynamic_executable and "pr" in arguments and "merge" in arguments
            )
        )
        assigned_guarded = dynamic_executable and any(
            command_value_guarded(value) for value in environment_values
        )
        subcommand_word = git_subcommand_word(invocation)
        git_guarded = invocation.executable == "git" and subcommand_word is not None and (
            subcommand_word.text == "push" or has_expansion(subcommand_word)
        )
        gh_guarded = False
        if pr_index is not None:
            index = pr_index + 1
            if index < len(arguments):
                gh_guarded = has_expansion(invocation.arguments[index]) or admin_expansion
        if (
            executable == "block-issue"
            or block_issue_shape
            or admin_expansion
            or git_guarded
            or gh_guarded
            or (dynamic_executable and "push" in arguments)
            or assigned_guarded
            or command_expansion_guarded
            or nested_guarded
            or substitution_guarded
        ):
            return "Factory Run guard: cannot verify a dynamic command that may invoke a guarded action. Use a literal command."
    return None


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
