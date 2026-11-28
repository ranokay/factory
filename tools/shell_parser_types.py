#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shell_parser import Word


@dataclass(frozen=True)
class Invocation:
    executable: str
    arguments: tuple[Word, ...]
    dynamic: bool = False
    environment: tuple[tuple[str, str], ...] = ()
    directory: str | None = None


@dataclass
class ParseState:
    remaining: list[Word]
    dynamic: bool = False
    environment: list[tuple[str, str]] = field(default_factory=list)
    directory: str | None = None
    invalid: bool = False


def has_expansion(word: Word) -> bool:
    return any(
        active and char in "$`"
        for char, active in zip(word.text, word.expansions, strict=True)
    )
