#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import subprocess  # nosec B404 -- Run tools invoke local programs with argument lists
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- local git with an argument list
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def repository_key(root: Path) -> str:
    common = git(root, "rev-parse", "--git-common-dir")
    identity = Path(common.stdout.strip()) if common.returncode == 0 else root
    if not identity.is_absolute():
        identity = root / identity
    return hashlib.sha256(str(identity.resolve()).encode()).hexdigest()[:20]


def cache_root() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")


def result_path(root: Path, head: str) -> Path:
    return cache_root() / "factory" / "check-results" / repository_key(root) / f"{head}.json"


def load_result(root: Path, head: str) -> dict | None:
    path = result_path(root, head)
    try:
        result = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return result if isinstance(result, dict) else None


def snapshot(root: Path) -> tuple[str | None, bool]:
    head = git(root, "rev-parse", "HEAD")
    status = git(root, "status", "--porcelain")
    if head.returncode or status.returncode:
        return None, False
    return head.stdout.strip(), not status.stdout.strip()


def write_result(root: Path, head: str, result: dict) -> None:
    path = result_path(root, head)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{head}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments or arguments[0] != "--" or len(arguments) == 1:
        print("usage: check_result -- <command> [arguments...]", file=sys.stderr)
        return 2
    command = arguments[1:]
    root_result = git(Path.cwd(), "rev-parse", "--show-toplevel")
    if root_result.returncode:
        print(f"check_result: not a Git checkout: {root_result.stderr.strip()}", file=sys.stderr)
        return 2
    root = Path(root_result.stdout.strip()).resolve()
    head, clean_before = snapshot(root)
    if head is None:
        print("check_result: cannot resolve HEAD and worktree state", file=sys.stderr)
        return 2
    result_path(root, head).unlink(missing_ok=True)
    try:
        completed = subprocess.run(command, cwd=root, check=False)  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- runs the caller's requested command without a shell
        exit_code = completed.returncode
    except OSError as error:
        print(f"check_result: cannot run {command[0]}: {error}", file=sys.stderr)
        return 2
    head_after, clean_after = snapshot(root)
    passed = exit_code == 0 and clean_before and clean_after and head_after == head
    result = {
        "repository": str(root),
        "head": head,
        "head_after": head_after,
        "clean_before": clean_before,
        "clean_after": clean_after,
        "exit_code": exit_code,
        "passed": passed,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    write_result(root, head, result)
    if exit_code:
        return exit_code
    if not passed:
        print("check_result: the check did not run on a clean, unchanged HEAD", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
