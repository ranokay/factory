#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from check_result import cache_root

REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")


def targets_path(repository: str, issue: str) -> Path:
    if not REPOSITORY.fullmatch(repository) or not issue.isdigit():
        raise ValueError("invalid repository or issue")
    owner, name = repository.split("/")
    return cache_root() / "factory" / "runs" / owner / name / issue / "targets.json"


def normalize_target(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("target must be a JSON object")
    provider = value.get("providerInstanceId")
    model = value.get("model")
    options = value.get("options", {})
    if not isinstance(provider, str) or not provider or not isinstance(model, str) or not model:
        raise ValueError("target requires providerInstanceId and model")
    if not isinstance(options, dict):
        raise ValueError("target options must be a JSON object")
    target = {key: item for key, item in value.items() if key != "tried"}
    target["options"] = options
    target["tried"] = value.get("tried") is True
    return target


def load_targets(repository: str, issue: str) -> list[dict] | None:
    path = targets_path(repository, issue)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f"cannot read targets file: {error}") from error
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"targets file is not JSON: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("targets"), list):
        raise ValueError("targets file must contain a targets list")
    try:
        return [normalize_target(target) for target in data["targets"]]
    except ValueError as error:
        raise ValueError(f"targets file is invalid: {error}") from error


def write_targets(repository: str, issue: str, targets: list[dict]) -> None:
    path = targets_path(repository, issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".targets.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"targets": targets}, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def target_identity(target: dict) -> tuple[str, str, str]:
    return target["providerInstanceId"], target["model"], json.dumps(target.get("options", {}), sort_keys=True)


def next_target(repository: str, issue: str) -> dict | None:
    targets = load_targets(repository, issue)
    if targets is None:
        return None
    return next((target for target in targets if not target["tried"]), None)


def parse_target(text: str) -> dict:
    try:
        return normalize_target(json.loads(text))
    except json.JSONDecodeError as error:
        raise ValueError(f"target is not JSON: {error}") from error


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if len(arguments) < 3 or arguments[0] not in {"set", "mark", "next"}:
            raise ValueError("usage: run_targets {set <repo> <issue> <target-json>...|mark <repo> <issue> <target-json>|next <repo> <issue>}")
        command, repository, issue = arguments[:3]
        if command == "next":
            if len(arguments) != 3:
                raise ValueError("next takes only a repository and issue")
            target = next_target(repository, issue)
            if target is not None:
                print(json.dumps(target, sort_keys=True))
            return 0
        if command == "set":
            if len(arguments) == 3:
                raise ValueError("set requires at least one target")
            targets = [parse_target(text) for text in arguments[3:]]
            for target in targets:
                target["tried"] = False
            write_targets(repository, issue, targets)
            return 0
        if len(arguments) != 4:
            raise ValueError("mark requires one target")
        targets = load_targets(repository, issue)
        if targets is None:
            raise ValueError("targets file is missing or invalid")
        identity = target_identity(parse_target(arguments[3]))
        for target in targets:
            if target_identity(target) == identity:
                target["tried"] = True
                write_targets(repository, issue, targets)
                return 0
        raise ValueError("target is not in the ordered list")
    except (OSError, ValueError) as error:
        print(f"run-targets: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
