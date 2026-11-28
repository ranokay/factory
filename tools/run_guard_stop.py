#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import subprocess  # nosec B404  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local gh with an argument list
from pathlib import Path


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


def unavailable(message: str, repeat: bool) -> tuple[str, str]:
    if repeat:
        return "block", f"Factory Run guard warning: {message}"
    return "block", message


def current_pr(cwd: Path, repeat: bool) -> tuple[int | None, tuple[str, str] | None]:
    pull = gh(["pr", "view", "--json", "number,headRefName,url"], cwd)
    if pull.returncode:
        diagnostic = pull.stderr.strip() or pull.stdout.strip()
        if "no pull requests found" in diagnostic.lower():
            return None, ("allow", "")
        return None, unavailable(
            f"Factory Run guard: required-check facts remain unavailable: {diagnostic}"
            if repeat
            else f"Factory Run guard: cannot verify required checks: {diagnostic}. Restore GitHub access and retry the stop.",
            repeat,
        )
    try:
        number = json.loads(pull.stdout)["number"]
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise ValueError("PR number must be a positive integer")
        return number, None
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        message = f"Factory Run guard: cannot read the current PR: {error}"
        return None, unavailable(message, repeat)


def required_checks(cwd: Path, number: int, repeat: bool) -> tuple[list[dict] | None, tuple[str, str] | None]:
    checks = gh(["pr", "checks", str(number), "--required", "--json", "name,state,link,bucket"], cwd)
    if checks.returncode not in {0, 8}:
        diagnostic = checks.stderr.strip() or checks.stdout.strip()
        return None, unavailable(f"Factory Run guard: cannot read required checks: {diagnostic}", repeat)
    try:
        rollup = json.loads(checks.stdout)
    except json.JSONDecodeError as error:
        return None, unavailable(f"Factory Run guard: cannot read required checks: {error}", repeat)
    if not isinstance(rollup, list):
        return None, unavailable("Factory Run guard: required-check response is not a list", repeat)
    if any(
        not isinstance(check, dict)
        or not isinstance(check.get("name"), str)
        or not isinstance(check.get("bucket"), str)
        or (check.get("link") is not None and not isinstance(check.get("link"), str))
        for check in rollup
    ):
        return None, unavailable("Factory Run guard: required-check response contains malformed entries", repeat)
    return [check for check in rollup if check["bucket"] == "pending"], None


def pending_reason(number: int, pending: list[dict]) -> str:
    names = ", ".join(str(check.get("name", "unnamed check")) for check in pending)
    commands: list[str] = []
    unknown: list[str] = []
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
    return reason


def stop_rule(cwd: Path, repeat: bool) -> tuple[str, str]:
    number, outcome = current_pr(cwd, repeat)
    if outcome is not None:
        return outcome
    pending, outcome = required_checks(cwd, number, repeat)
    if outcome is not None:
        return outcome
    if not pending:
        return "allow", ""
    return "block", pending_reason(number, pending)
