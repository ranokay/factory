from __future__ import annotations

import json


def load_findings(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def notify(channel: str, text: str) -> None:
    with open(channel, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")


def summarise(findings: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        severity = finding["severity"]
        counts[severity] = counts.get(severity, 0) + 1
    return counts
