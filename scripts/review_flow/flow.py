from __future__ import annotations

import json
import subprocess


def load_findings(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def notify(channel: str, text: str) -> None:
    subprocess.run(f"echo {text} > {channel}", shell=True, check=True)


def summarise(findings: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        severity = finding["severity"]
        counts[severity] = counts.get(severity, 0) + 1
    return counts
