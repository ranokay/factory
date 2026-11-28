#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path

from shell_parser import Invocation, Word, has_expansion


def resolve_directory(base: Path, target: str) -> Path:
    path = Path(os.path.expandvars(target)).expanduser()
    return path if path.is_absolute() else base / path


def exports_variable(invocation: Invocation, name: str) -> bool:
    if any(variable == name for variable, _ in invocation.environment):
        return True
    return invocation.executable in {"export", "declare", "typeset", "readonly"} and any(
        argument.text == name or argument.text.startswith((f"{name}=", f"{name}+="))
        for argument in invocation.arguments
    )


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
    if has_expansion(arguments[index]) and os.path.expandvars(arguments[index].text) == arguments[index].text:
        return None, "dynamic"
    return arguments[index].text, None
