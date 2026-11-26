#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pinned_gitleaks(root: Path) -> str:
    pending = [root / "mise.toml"]
    seen: set[Path] = set()
    tools: dict[str, object] = {}
    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        data = tomllib.loads(path.read_text())
        for include in data.get("include", []):
            included = (path.parent / include).resolve()
            if not included.is_file():
                raise ValueError(f"included mise file is missing: {included}")
            pending.append(included)
        declared = data.get("tools", {})
        if not isinstance(declared, dict):
            raise ValueError(f"mise tools must be a table: {path}")
        tools.update(declared)
    version = tools.get("gitleaks")
    if isinstance(version, dict):
        version = version.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("mise.toml has no gitleaks pin")
    return version


def check(root: Path) -> tuple[int, int]:
    version = pinned_gitleaks(root)
    catalog = json.loads((root / "tools" / "gitleaks-keywords.json").read_text())
    if catalog.get("gitleaks_version") != version:
        raise ValueError(
            f"gitleaks version pinned in mise.toml is {version}, "
            f"but the keyword catalog targets {catalog.get('gitleaks_version')}"
        )
    if catalog.get("source") != f"https://raw.githubusercontent.com/gitleaks/gitleaks/v{version}/config/gitleaks.toml":
        raise ValueError("the keyword catalog source does not match the gitleaks version pin")
    keywords = catalog.get("keywords")
    if (
        not isinstance(keywords, list)
        or not keywords
        or not all(isinstance(keyword, str) and keyword and keyword == keyword.lower() for keyword in keywords)
        or keywords != sorted(set(keywords))
    ):
        raise ValueError("the keyword catalog must contain sorted, unique lowercase keywords")
    config = tomllib.loads((root / ".gitleaks.toml").read_text())
    if config.get("extend", {}).get("useDefault") is not True:
        raise ValueError("the Factory gitleaks config must extend the default rules")
    rules = config.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("Factory gitleaks rules must be an array")
    own_keywords = {keyword.lower() for rule in rules for keyword in rule.get("keywords", [])}
    missing = own_keywords - set(keywords)
    if missing:
        raise ValueError(f"the keyword catalog omits Factory keywords: {', '.join(sorted(missing))}")
    unkeyworded = catalog.get("unkeyworded_rules")
    if not isinstance(unkeyworded, list):
        raise ValueError("the keyword catalog must list unkeyworded rules")
    ids: set[str] = set()
    for rule in unkeyworded:
        if not isinstance(rule, dict) or set(rule) != {"id", "path"}:
            raise ValueError("unkeyworded stdin rules must be path-only")
        rule_id = rule.get("id")
        pattern = rule.get("path")
        if not isinstance(rule_id, str) or not rule_id or rule_id in ids or not isinstance(pattern, str):
            raise ValueError("the keyword catalog has an invalid unkeyworded rule")
        ids.add(rule_id)
        try:
            matched = re.search(pattern, "") is not None
        except re.error as error:
            raise ValueError(f"the keyword catalog has an invalid path regex: {error}") from error
        if matched:
            raise ValueError(f"the unkeyworded rule {rule_id} can match stdin's empty path")
    own_unkeyworded = {rule.get("id") for rule in rules if not rule.get("keywords")}
    if not own_unkeyworded <= ids:
        raise ValueError("the keyword catalog omits Factory rules without keywords")
    return len(keywords), len(unkeyworded)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        keywords, unkeyworded = check(args.root.resolve())
    except (OSError, ValueError) as error:
        print(f"gitleaks keyword catalog: {error}", file=sys.stderr)
        return 1
    print(f"gitleaks keyword catalog: {keywords} keywords and {unkeyworded} path-only rule(s) match the pin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
