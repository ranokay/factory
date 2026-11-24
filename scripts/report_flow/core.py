from __future__ import annotations

import json
import subprocess


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def totals(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        severity = row["severity"]
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def send(channel: str, payload: dict) -> None:
    subprocess.run(f"echo {json.dumps(payload)} > {channel}", shell=True, check=True)
