#!/usr/bin/env python3
"""Report the active checkout and its integration baseline."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

GIB = 1024**3
LOW_DISK_BYTES = 40 * GIB


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def checkout(cwd: Path) -> dict:
    root = git(cwd, "rev-parse", "--show-toplevel")
    if not root:
        return {"status": "not_git", "cwd": str(cwd)}
    path = Path(root).resolve()
    head = git(path, "rev-parse", "HEAD")
    branch = git(path, "branch", "--show-current") or "detached"
    result = {"status": "ok", "root": str(path), "head": head, "branch": branch}
    result["instructions"] = [name for name in ("AGENTS.md", "CLAUDE.md") if (path / name).is_file()]
    if not git(path, "remote", "get-url", "origin"):
        return result
    integration = git(path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    warnings = []
    if integration:
        baseline = git(path, "rev-parse", "--verify", integration)
        merge_base = git(path, "merge-base", "HEAD", integration) if baseline else ""
        result.update(
            integration_branch=integration,
            baseline_head=baseline,
            contains_baseline=bool(baseline and baseline == merge_base),
        )
        if not result["contains_baseline"]:
            warnings.append(f"This checkout does not contain current {integration}; reconcile its base before implementation.")
    else:
        warnings.append("origin/HEAD is unset, so the integration branch is unknown. Run `git remote set-head origin --auto`.")
    if warnings:
        result["warnings"] = warnings
        result["status"] = "needs_preflight"
    return result


def low_disk_warning(cwd: Path) -> str:
    """Stay silent until free space is low, so the session context pays nothing otherwise."""
    try:
        free = shutil.disk_usage(cwd).free
    except OSError:
        return ""
    if free >= LOW_DISK_BYTES:
        return ""
    return f" Low disk: {free // GIB} GiB free. Report it to the user before starting long builds or new worktrees."


def hook_output(event: str, context: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "startup"))
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    if args.mode == "check":
        result = checkout(args.cwd)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "ok" else 1
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("expected an object")
    except (ValueError, OSError) as exc:
        print(f"environment_check: invalid hook input: {exc}", file=sys.stderr)
        return 1
    cwd = Path(payload.get("cwd") or args.cwd)
    state = checkout(cwd)
    if state["status"] == "not_git":
        text = f"Checkout preflight: {state['cwd']} is not a Git checkout."
    else:
        text = "Checkout preflight: " + json.dumps(state, separators=(",", ":"))
    print(json.dumps(hook_output("SessionStart", context=text + low_disk_warning(cwd))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
