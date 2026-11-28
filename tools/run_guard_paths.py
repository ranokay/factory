#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path

from shell_parser import Word


def resolve_directory(base: Path, target: str) -> Path:
    path = Path(os.path.expandvars(target)).expanduser()
    return path if path.is_absolute() else base / path


def directory_target(arguments: tuple[Word, ...]) -> tuple[str | None, str | None]:
    if not arguments:
        return None, "bare"
    index = 0
    while index < len(arguments):
        option = arguments[index].text
        if option == "--":
            index += 1
            break
        if option.startswith("-") and len(option) > 1:
            index += 1
            continue
        break
    if index >= len(arguments):
        return None, "bare"
    if arguments[index].text == "-":
        return None, "previous"
    return arguments[index].text, None
