#!/usr/bin/env python3
"""Tooling-baseline detectors for the adopt inventory."""

import json
import re
import tomllib

from profiles_reader import (
    BLOCKER_CALLER,
    BLOCKER_LOCAL,
    BLOCKER_WORKFLOW,
    PROFILE_PATH,
    TAG,
    factory_ref,
)

from adopt_common import PROFILE_NAMES, Problem, finding, read_file


def valid_factory_include(value, allow_local=False, expected=None):
    if not isinstance(value, str):
        return False
    match = PROFILE_PATH.search(value)
    if not match or match.group(1) not in PROFILE_NAMES:
        return False
    if expected and not value.split("?", 1)[0].endswith("/" + expected):
        return False
    if re.search(r"github\.com/ranokay/factory/(?:blob|tree)/", value, re.IGNORECASE):
        return False
    if value.startswith(("./profiles/", "profiles/")):
        return allow_local
    if "ranokay/factory" not in value.lower():
        return False
    ref = factory_ref(value)
    return bool(ref and TAG.match(ref))


def factory_references(values):
    return [
        value
        for value in values
        if isinstance(value, str)
        and "profiles/" in value
        and (
            "ranokay/factory" in value.lower()
            or value.startswith(("./profiles/", "profiles/"))
        )
    ]


def includes_are_factory(values, allow_local=False, expected=None):
    references = factory_references(values)
    return bool(references) and all(
        valid_factory_include(value, allow_local, expected) for value in references
    )


def mise_config(mise):
    includes = mise.get("include")
    if isinstance(includes, str):
        includes = [includes]
    elif not isinstance(includes, list):
        includes = []
    task_config = mise.get("task_config")
    tasks = task_config.get("includes") if isinstance(task_config, dict) else []
    if isinstance(tasks, str):
        tasks = [tasks]
    elif not isinstance(tasks, list):
        tasks = []
    return includes, tasks


def mise_findings(project, present, allow_local):
    if "mise.toml" not in present:
        return [
            finding(
                "tooling",
                "mise.toml",
                "factory-include",
                "mise.toml is missing a Factory Profile include pinned to a v* tag.",
            )
        ]
    try:
        mise = tomllib.loads(read_file(project, "mise.toml"))
    except Problem as error:
        return [
            finding(
                "tooling",
                "mise.toml",
                "factory-include",
                f"mise.toml could not be read: {error}",
            )
        ]
    except tomllib.TOMLDecodeError as error:
        return [
            finding(
                "tooling",
                "mise.toml",
                "factory-include",
                f"mise.toml is not valid TOML: {error}",
            )
        ]
    includes, tasks = mise_config(mise)
    if includes_are_factory(includes, allow_local, "mise.toml") and includes_are_factory(
        tasks, allow_local, "tasks.toml"
    ):
        return []
    return [
        finding(
            "tooling",
            "mise.toml",
            "factory-include",
            "mise.toml is missing a Factory Profile include pinned to a v* tag.",
        )
    ]


def hk_findings(project, present, allow_local):
    if "hk.pkl" not in present:
        return [
            finding(
                "tooling",
                "hk.pkl",
                "factory-amend",
                "hk.pkl is not amending a Factory Profile pinned to a v* tag.",
            )
        ]
    try:
        text = read_file(project, "hk.pkl")
    except Problem as error:
        return [
            finding(
                "tooling",
                "hk.pkl",
                "factory-amend",
                f"hk.pkl could not be read: {error}",
            )
        ]
    amended = re.search(r'(?m)^\s*amends\s+"([^"]+)"', text)
    if amended and valid_factory_include(amended.group(1), allow_local, "hk.pkl"):
        return []
    return [
        finding(
            "tooling",
            "hk.pkl",
            "factory-amend",
            "hk.pkl is not amending a Factory Profile pinned to a v* tag.",
        )
    ]


def renovate_extends(renovate):
    extends = renovate.get("extends")
    if isinstance(extends, str):
        return [extends]
    if isinstance(extends, list):
        return extends
    return []


def renovate_findings(project, present):
    if "renovate.json" not in present:
        return [
            finding(
                "tooling",
                "renovate.json",
                "factory-preset",
                "renovate.json is not extending github>ranokay/factory.",
            )
        ]
    try:
        renovate = json.loads(read_file(project, "renovate.json"))
    except Problem as error:
        return [
            finding(
                "tooling",
                "renovate.json",
                "factory-preset",
                f"renovate.json could not be read: {error}",
            )
        ]
    except json.JSONDecodeError as error:
        return [
            finding(
                "tooling",
                "renovate.json",
                "factory-preset",
                f"renovate.json is not valid JSON: {error}",
            )
        ]
    if not isinstance(renovate, dict):
        return [
            finding(
                "tooling",
                "renovate.json",
                "factory-preset",
                "renovate.json must contain a JSON object with the Factory preset.",
            )
        ]
    if "github>ranokay/factory" in renovate_extends(renovate):
        return []
    return [
        finding(
            "tooling",
            "renovate.json",
            "factory-preset",
            "renovate.json is not extending github>ranokay/factory.",
        )
    ]


def advisory_config_findings(present):
    return [
        finding(
            "tooling",
            path,
            "missing",
            f"{path} is missing from the repository.",
        )
        for path in (".coderabbit.yaml", ".greptile/config.json")
        if path not in present
    ]


BLOCKER_USES = re.compile(
    rf"""^[ \t]*uses:[ \t]*["']?{re.escape(BLOCKER_WORKFLOW)}@(\S+?)["']?(?:[ \t]+#[^\n]*)?[ \t]*$""",
    re.MULTILINE,
)
LOCAL_BLOCKER_USES = re.compile(
    rf"""^[ \t]*uses:[ \t]*["']?{re.escape(BLOCKER_LOCAL)}["']?(?:[ \t]+#[^\n]*)?[ \t]*$""",
    re.MULTILINE,
)


def pinned_blocker_uses(text):
    return any(
        TAG.match(match.group(1))
        for match in BLOCKER_USES.finditer(text.replace("\r\n", "\n"))
    )


def blocker_caller_findings(project, present, allow_local):
    if BLOCKER_CALLER not in present:
        return [
            finding(
                "tooling",
                BLOCKER_CALLER,
                "blocker-caller",
                f"{BLOCKER_CALLER} is missing; add the Blocker caller from the Project Template.",
            )
        ]
    try:
        text = read_file(project, BLOCKER_CALLER)
    except Problem as error:
        return [
            finding(
                "tooling",
                BLOCKER_CALLER,
                "blocker-caller",
                f"{BLOCKER_CALLER} could not be read: {error}",
            )
        ]
    if allow_local:
        accepted = LOCAL_BLOCKER_USES.search(text.replace("\r\n", "\n"))
    else:
        accepted = pinned_blocker_uses(text)
    if accepted:
        return []
    required = (
        BLOCKER_LOCAL if allow_local else f"{BLOCKER_WORKFLOW}@<vX.Y.Z>"
    )
    return [
        finding(
            "tooling",
            BLOCKER_CALLER,
            "blocker-caller",
            f"{BLOCKER_CALLER} does not call {required}.",
        )
    ]


def tooling_findings(project, tracked, repo):
    present = set(tracked)
    allow_local = repo.lower() == "ranokay/factory"
    return [
        *mise_findings(project, present, allow_local),
        *hk_findings(project, present, allow_local),
        *renovate_findings(project, present),
        *advisory_config_findings(present),
        *blocker_caller_findings(project, present, allow_local),
    ]
