#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path


def resolve_directory(base: Path, target: str) -> Path:
    path = Path(os.path.expandvars(target)).expanduser()
    return path if path.is_absolute() else base / path
