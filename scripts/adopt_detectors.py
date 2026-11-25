#!/usr/bin/env python3
"""Detectors and category definitions for the adopt inventory."""

import importlib.util
import json
import os
import re
import sys
import tomllib
from pathlib import Path, PurePosixPath

from adopt_common import (
    COMMENT_BAN,
    Finding,
    Problem,
    finding,
    inside_checkout,
    problem,
    read_file,
    read_reference,
    run,
)
from profiles_reader import profiles_entries

COMMENT_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+): (?P<message>.+)$")
TEST_NAME_RE = re.compile(
    r"(?:\.(?:test|spec)\.[^./]+|_test\.[^./]+|Tests?\.swift|\.bats)$"
)
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
BATCH_SIZE = 100
AGENTS_LINE_LIMIT = 120
SOURCE_ROOTS = {"src", "lib", "Sources", "cmd", "internal", "pkg"}
SOURCE_SUFFIXES = {
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".py",
    ".go",
    ".swift",
    ".rs",
    ".rb",
    ".lua",
    ".kt",
    ".kts",
    ".java",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cs",
}
ENTRY_STEMS = {"index", "main", "mod", "__init__", "cli", "setup"}
TEST_DIRS = {"tests", "test", "__tests__", "Tests"}
FIXTURE_DIRS = {"fixture", "fixtures"}
STALE_DIRS = {"node_modules", "__pycache__", ".venv", "coverage", "dist", ".next"}
STALE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".eslintcache"}
STALE_SUFFIXES = (
    ".orig",
    ".rej",
    ".bak",
    ".tmp",
    ".temp",
    ".log",
    ".swp",
    ".swo",
    ".pyc",
    ".tsbuildinfo",
)


def is_test_fixture_path(path):
    parts = PurePosixPath(path).parts[:-1]
    return any(
        part in FIXTURE_DIRS and index > 0 and parts[index - 1] in TEST_DIRS
        for index, part in enumerate(parts)
    )


def is_test_path(path):
    item = PurePosixPath(path)
    if any(part in TEST_DIRS for part in item.parts[:-1]):
        return True
    if item.name.startswith("test_"):
        return True
    return bool(TEST_NAME_RE.search(item.name))


def skills_lock_names(project):
    if not (project / "skills.lock").is_file():
        return set()
    spec = importlib.util.spec_from_file_location(
        "factory_skills_sync", Path(__file__).resolve().parent / "skills-sync.py"
    )
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    try:
        lock = sync.load_lock(project)
    except (sync.SyncError, OSError, json.JSONDecodeError) as error:
        raise Problem(f"cannot read skills.lock: {error}") from error
    return {entry["name"] for entry in lock["skills"]}


def is_vendored_skill(path, names):
    item = PurePosixPath(path)
    return len(item.parts) > 1 and item.parts[0] == "skills" and item.parts[1] in names


def inventory_files(project, tracked):
    vendored = skills_lock_names(project)
    inventoried = []
    fixtures = 0
    vendored_files = 0
    for path in tracked:
        if is_test_fixture_path(path):
            fixtures += 1
        elif is_vendored_skill(path, vendored):
            vendored_files += 1
        else:
            inventoried.append(path)
    if vendored_files:
        print(f"adopt: skip {vendored_files} vendored skill file(s)")
    if fixtures:
        print(f"adopt: skip {fixtures} test fixture file(s)")
    return inventoried


def comment_rule(message):
    if message.startswith("commented-out code"):
        return "commented-out-code"
    match = re.search(r"\b(TODO|FIXME|HACK|XXX)\b", message)
    if match:
        return match.group(1)
    return "comment"


def comment_sources(project, tracked):
    files = []
    for path in tracked:
        if path.lower().endswith((".md", ".markdown")):
            continue
        if not inside_checkout(project, path):
            print(
                f"adopt: warning: {path} resolves outside the checkout; "
                "skipping it as a comment source",
                file=sys.stderr,
            )
            continue
        files.append(path)
    return files


def add_comment_lines(result, occurrences):
    parsed = 0
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        match = COMMENT_RE.match(line.strip())
        if not match:
            continue
        try:
            number = int(match.group("line"))
        except ValueError:
            print(f"adopt: warning: cannot parse comment-ban line: {line}", file=sys.stderr)
            continue
        path = match.group("path").removeprefix("./")
        rule = comment_rule(match.group("message"))
        key = f"comments:{path}:{rule}"
        entry = occurrences.setdefault(key, {"path": path, "rule": rule, "items": []})
        entry["items"].append((number, match.group("message")))
        parsed += 1
    return parsed


def collect_comments(project, files):
    occurrences = {}
    for start in range(0, len(files), BATCH_SIZE):
        batch = [f"./{path}" for path in files[start : start + BATCH_SIZE]]
        result = run(["sh", str(COMMENT_BAN), *batch], cwd=project)
        if result.returncode not in (0, 1):
            detail = (result.stderr or result.stdout).strip()
            problem(f"comment-ban exited {result.returncode}: {detail}")
        parsed = add_comment_lines(result, occurrences)
        if result.returncode == 1 and parsed == 0:
            print(
                "adopt: warning: comment-ban exited 1 with no parsed findings for a batch",
                file=sys.stderr,
            )
    return occurrences


def comment_findings(project, tracked, _repo):
    occurrences = collect_comments(project, comment_sources(project, tracked))
    found = []
    for key in sorted(occurrences):
        entry = occurrences[key]
        lines = sorted({line for line, _ in entry["items"]})
        if len(entry["items"]) == 1:
            display = f"{entry['path']}:{lines[0]}: {entry['items'][0][1]}"
        else:
            shown = ", ".join(str(line) for line in lines)
            display = f"{entry['path']}: {entry['rule']} at lines {shown}"
        found.append(Finding(key, display))
    return found


def reference_tokens(project, tracked):
    tokens = {}
    counts = {}
    incomplete = False
    for path in tracked:
        if path.endswith(".d.ts"):
            continue
        text = read_reference(project, path)
        if text is None:
            incomplete = True
            continue
        if not text:
            continue
        names = set(TOKEN_RE.findall(text))
        tokens[path] = names
        for name in names:
            counts[name] = counts.get(name, 0) + 1
    return tokens, counts, incomplete


def dead_code_candidates(tracked, tokens, counts):
    found = []
    for path in tracked:
        item = PurePosixPath(path)
        if not item.parts or item.parts[0] not in SOURCE_ROOTS:
            continue
        if item.suffix not in SOURCE_SUFFIXES or is_test_path(path):
            continue
        if item.stem in ENTRY_STEMS or item.stem.endswith(".config"):
            continue
        own = 1 if item.stem in tokens.get(path, set()) else 0
        if counts.get(item.stem, 0) <= own:
            found.append(path)
    return found


def dead_code_findings(project, tracked, _repo):
    tokens, counts, incomplete = reference_tokens(project, tracked)
    if incomplete:
        print(
            "adopt: warning: dead-code detection skipped because a reference "
            "file could not be read",
            file=sys.stderr,
        )
        return []
    return [
        finding(
            "dead-code",
            path,
            "unreferenced",
            f"{path} is not referenced outside itself.",
        )
        for path in dead_code_candidates(tracked, tokens, counts)
    ]


def agents_profiles_findings(text):
    raw = profiles_entries(text)
    entries = [entry.strip() for entry in raw]
    if not raw:
        return [
            finding("agents", "AGENTS.md", "profiles", "AGENTS.md has no Profiles: line.")
        ]
    if not any(entries):
        return [
            finding(
                "agents",
                "AGENTS.md",
                "profiles-empty",
                "AGENTS.md has an empty Profiles: line.",
            )
        ]
    if len(raw) > 1:
        return [
            finding(
                "agents",
                "AGENTS.md",
                "profiles-duplicate",
                "AGENTS.md has more than one Profiles: line.",
            )
        ]
    return []


def agents_file_findings(project, tracked):
    if "AGENTS.md" not in tracked:
        return [
            finding(
                "agents",
                "AGENTS.md",
                "missing",
                "AGENTS.md is missing; start from the Factory's project/AGENTS.md.",
            )
        ]
    try:
        text = read_file(project, "AGENTS.md")
    except Problem as error:
        return [
            finding(
                "agents",
                "AGENTS.md",
                "read",
                f"AGENTS.md could not be read: {error}",
            )
        ]
    found = agents_profiles_findings(text)
    if "mise run check" not in text:
        found.append(
            finding(
                "agents",
                "AGENTS.md",
                "check",
                "AGENTS.md does not name mise run check.",
            )
        )
    lines = len(text.splitlines())
    if lines > AGENTS_LINE_LIMIT:
        found.append(
            finding(
                "agents",
                "AGENTS.md",
                "length",
                f"AGENTS.md is {lines} lines; rewrite it to the minimum.",
            )
        )
    return found


def claude_link_findings(project):
    claude = project / "CLAUDE.md"
    if claude.is_symlink() and os.readlink(claude) == "AGENTS.md":
        return []
    return [
        finding(
            "agents",
            "CLAUDE.md",
            "symlink",
            "CLAUDE.md is not a relative symlink to AGENTS.md.",
        )
    ]


def agents_findings(project, tracked, _repo):
    return [*agents_file_findings(project, tracked), *claude_link_findings(project)]


def stale_findings(project, tracked, _repo):
    found = []
    for path in tracked:
        item = PurePosixPath(path)
        if (
            item.name in STALE_NAMES
            or item.name.endswith(STALE_SUFFIXES)
            or item.name.endswith("~")
            or any(part in STALE_DIRS for part in item.parts[:-1])
        ):
            found.append(
                finding("stale-files", path, "artifact", f"{path} is a tracked artifact.")
            )
    return found


def verification_skill_names(tracked):
    names = set()
    for path in tracked:
        item = PurePosixPath(path)
        if (
            len(item.parts) == 4
            and item.parts[:2] == (".agents", "skills")
            and item.parts[2].startswith("verify-")
            and item.name == "SKILL.md"
        ):
            names.add(item.parts[2])
    return sorted(names)


def verification_mise_tasks(project, tracked):
    if "mise.toml" not in tracked:
        return None, None
    try:
        data = tomllib.loads(read_file(project, "mise.toml"))
    except Problem as error:
        return None, f"mise.toml could not be read: {error}"
    except tomllib.TOMLDecodeError as error:
        return None, f"mise.toml could not be parsed: {error}"
    tasks = data.get("tasks")
    return (tasks if isinstance(tasks, dict) else {}), None


def verification_skill_findings(project, tracked, name, tasks):
    app = name.removeprefix("verify-")
    found = []
    link = f".claude/skills/{name}"
    target = project / link
    expected = f"../../.agents/skills/{name}"
    if (
        link not in tracked
        or not target.is_symlink()
        or os.readlink(target) != expected
    ):
        found.append(
            finding(
                "verification",
                link,
                "symlink",
                f"{link} is not a relative symlink to {expected}.",
            )
        )
    features = f".agents/skills/{name}/references/features"
    if not any(path.startswith(features + "/") for path in tracked):
        found.append(
            finding("verification", features, "map", f"{features}/ is missing.")
        )
    if tasks is not None and f"verify:{app}" not in tasks:
        found.append(
            finding(
                "verification",
                "mise.toml",
                f"verify-{app}",
                f"mise.toml has no verify:{app} task.",
            )
        )
    return found


def verification_findings(project, tracked, _repo):
    names = verification_skill_names(tracked)
    if not names:
        return [
            finding(
                "verification",
                ".agents/skills",
                "no-skill",
                "No .agents/skills/verify-<app>/SKILL.md is committed.",
            )
        ]
    tasks, error = verification_mise_tasks(project, tracked)
    found = []
    if error is not None:
        found.append(
            finding(
                "verification",
                "mise.toml",
                "read",
                error,
            )
        )
    for name in names:
        found.extend(verification_skill_findings(project, tracked, name, tasks))
    return found


def test_pruning_findings(project, tracked, _repo):
    return [
        finding("test-pruning", path, "test-file", f"{path} is a test file.")
        for path in tracked
        if is_test_path(path)
    ]


def product_findings(project, tracked, _repo):
    if "PRODUCT.md" in tracked:
        return []
    return [
        finding(
            "product",
            "PRODUCT.md",
            "missing",
            "PRODUCT.md is missing; start from the Factory's project/PRODUCT.md.",
        )
    ]


def brand_findings(project, tracked, _repo):
    masters = [
        path
        for path in tracked
        if path.startswith("assets/brand/") and path.endswith(".svg")
    ]
    icons = [path for path in masters if "icon" in PurePosixPath(path).stem.lower()]
    names = [
        path
        for path in masters
        if "icon" not in PurePosixPath(path).stem.lower()
        and any(
            role in PurePosixPath(path).stem.lower() for role in ("logo", "wordmark")
        )
    ]
    if icons and names:
        return []
    if not icons:
        return [
            finding(
                "brand",
                "assets/brand",
                "no-icon-master",
                "No icon SVG master is committed under assets/brand/.",
            )
        ]
    return [
        finding(
            "brand",
            "assets/brand",
            "no-name-master",
            "No logo or wordmark SVG master is committed under assets/brand/.",
        )
    ]
