#!/usr/bin/env python3
"""Shared constants and process helpers for the adopt inventory."""

import hashlib
import json
import re
import subprocess  # nosec B404 -- git, gh and sh run with argument lists
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT.parent / "repo-settings.json").read_text())
COMMENT_BAN = ROOT.parent / "profiles/universal/scripts/comment-ban.sh"
PROFILE_NAMES = {
    path.name for path in (ROOT.parent / "profiles").iterdir() if path.is_dir()
}
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CHUNK_SIZE = 100
MAX_TEXT = 2_000_000
TIMEOUT = 300


class Problem(Exception):
    pass


def problem(message):
    raise Problem(message)


class Finding(NamedTuple):
    key: str
    display: str


def finding(category, path, rule, display):
    return Finding(f"{category}:{path}:{rule}", display)


def digest(key):
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def run(arguments, cwd=None):
    try:
        return subprocess.run(  # nosec B603 B607  # noqa: S603 S607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit, python.lang.security.dangerous-subprocess-use.dangerous-subprocess-use -- argument lists, never a shell
            arguments,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        problem(f"{arguments[0]} timed out after {TIMEOUT}s")
    except OSError as error:
        problem(f"cannot run {arguments[0]}: {error}")


def gh(*arguments):
    result = run(["gh", *arguments])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        problem(f"gh {' '.join(arguments[:2])} failed: {detail}")
    return result.stdout


def git(project, *arguments):
    return run(["git", "-C", str(project), *arguments])


def git_out(project, *arguments):
    result = git(project, *arguments)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        problem(f"git {arguments[0]} failed: {detail}")
    return result.stdout


def parse_json(text, label):
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        problem(f"{label} did not print JSON: {error}")


def valid_repo(value):
    return bool(REPO_RE.match(value)) and all(
        part not in (".", "..") for part in value.split("/")
    )


def read_file(project, path):
    target = project / path
    if not inside_checkout(project, path):
        problem(f"{path} resolves outside the checkout")
    try:
        with target.open("rb") as handle:
            head = handle.read(1024)
            if b"\0" in head:
                return ""
            data = head + handle.read(MAX_TEXT + 1 - len(head))
    except OSError as error:
        problem(f"cannot read {path}: {error}")
    if len(data) > MAX_TEXT:
        problem(f"{path} is larger than {MAX_TEXT} bytes; adopt cannot inventory it")
    return data.decode("utf-8", "ignore")


def inside_checkout(project, path):
    root = project.resolve()
    target = (project / path).resolve()
    return target == root or target.is_relative_to(root)


def read_reference(project, path):
    target = project / path
    if target.is_dir():
        if not inside_checkout(project, path):
            print(
                f"adopt: warning: {path} resolves outside the checkout; "
                "skipping it as a reference source",
                file=sys.stderr,
            )
            return None
        return ""
    try:
        return read_file(project, path)
    except Problem as error:
        print(
            f"adopt: warning: {error}; skipping it as a reference source",
            file=sys.stderr,
        )
        return None


def show(text, stream):
    if not text:
        return
    stream.write(text)
    if not text.endswith("\n"):
        stream.write("\n")
    stream.flush()
