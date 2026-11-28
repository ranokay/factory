#!/usr/bin/env python3

from __future__ import annotations

import os
import re
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


def declared_assignments(invocation: Invocation) -> list[tuple[str, str]]:
    if invocation.executable not in {"export", "declare", "typeset", "readonly"}:
        return []
    assignments = []
    for argument in invocation.arguments:
        match = re.fullmatch(r"([A-Za-z_][A-Za-z_0-9]*)\+?=(.*)", argument.text)
        if match:
            assignments.append((match.group(1), match.group(2)))
    return assignments


def pushd_no_change(arguments: tuple[Word, ...]) -> bool:
    for argument in arguments:
        if argument.text == "--":
            break
        if (
            argument.text.startswith("-")
            and not argument.text.startswith("--")
            and "n" in argument.text[1:]
        ):
            return True
    return False


def pushd_has_stack(arguments: tuple[Word, ...]) -> bool:
    operands = 0
    for argument in arguments:
        if operands == 0 and argument.text != "--" and argument.text.startswith("-"):
            continue
        operands += 1
        if operands > 1:
            return True
    return False


def cdpath_risk(target: str | None, active: bool) -> bool:
    return active and target is not None and not Path(os.path.expandvars(target)).expanduser().is_absolute()


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
