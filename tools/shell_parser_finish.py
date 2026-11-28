#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path

from shell_parser_types import Invocation, ParseState, has_expansion


def finish_invocation(state: ParseState) -> Invocation | None:
    if state.invalid:
        return None
    if not state.remaining:
        if state.environment:
            return Invocation("", (), False, tuple(state.environment), state.directory)
        return None
    return Invocation(
        Path(state.remaining[0].text).name,
        tuple(state.remaining[1:]),
        state.dynamic or any(has_expansion(word) for word in state.remaining),
        tuple(state.environment),
        state.directory,
    )
