#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import gzip
import json
import re
import sys
import tomllib
from pathlib import Path

from gitleaks_catalog import classify_rule, digest

ROOT = Path(__file__).resolve().parents[1]
RULE_KEYS = {"id", "keywords", "regex", "python_safe", "python_unsafe_reason"}
PINNED_DIGESTS = {
    "8.30.1": {
        "default_rules_sha256": "3f143eac780c94c929261e83431997d27329cc53d16545bcd953e2550dcb7509",
        "samples_sha256": "df72c17b185798f098865e1708fdde6c365b4939b102c0f9f601ee1f2e98fa43",
        "unkeyworded_rules_sha256": "9cc0a1ec60fca2d14bf39c0e3ba2f15adb36c5d0ac561322bd20aee02d105b2a",
    }
}


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


def check(root: Path) -> tuple[int, int, int, int, int]:
    version = pinned_gitleaks(root)
    catalog = json.loads((root / "tools" / "gitleaks-keywords.json").read_text())
    source = f"https://raw.githubusercontent.com/gitleaks/gitleaks/v{version}/config/gitleaks.toml"
    if catalog.get("gitleaks_version") != version:
        raise ValueError(
            f"gitleaks version pinned in mise.toml is {version}, "
            f"but the keyword catalog targets {catalog.get('gitleaks_version')}"
        )
    if catalog.get("source") != source:
        raise ValueError("the keyword catalog source does not match the gitleaks version pin")
    pinned = PINNED_DIGESTS.get(version)
    if pinned is None:
        raise ValueError(f"no independently pinned gitleaks digests exist for {version}")
    if not isinstance(catalog.get("default_rules_sha256"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", catalog["default_rules_sha256"]
    ):
        raise ValueError("the keyword catalog has no default-rule digest")
    if catalog["default_rules_sha256"] != pinned["default_rules_sha256"]:
        raise ValueError("the default-rule digest does not match the independently pinned upstream data")
    rules = catalog.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("the keyword catalog must list merged rules")
    if catalog.get("rules_sha256") != digest(rules):
        raise ValueError("the merged rule digest is stale")
    config = tomllib.loads((root / ".gitleaks.toml").read_text())
    if config.get("extend", {}).get("useDefault") is not True:
        raise ValueError("the Factory gitleaks config must extend the default rules")
    own_rules = config.get("rules", [])
    if not isinstance(own_rules, list):
        raise ValueError("Factory gitleaks rules must be an array")
    if catalog.get("default_rule_count") != len(rules) - len(own_rules):
        raise ValueError("the default rule count does not match the Factory rule count")
    by_id: dict[str, dict] = {}
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != RULE_KEYS:
            raise ValueError("the keyword catalog has an invalid rule record")
        rule_id = rule["id"]
        keywords = rule["keywords"]
        regex = rule["regex"]
        if (
            not isinstance(rule_id, str)
            or not rule_id
            or rule_id in by_id
            or not isinstance(keywords, list)
            or not all(isinstance(keyword, str) and keyword and keyword == keyword.lower() for keyword in keywords)
            or keywords != sorted(set(keywords))
            or (regex is not None and not isinstance(regex, str))
            or not isinstance(rule["python_safe"], bool)
            or (rule["python_unsafe_reason"] is not None and not isinstance(rule["python_unsafe_reason"], str))
            or (rule["python_safe"] and rule["python_unsafe_reason"] is not None)
            or (not rule["python_safe"] and not rule["python_unsafe_reason"])
            or (regex is None and keywords)
        ):
            raise ValueError(f"the keyword catalog has an invalid rule: {rule_id}")
        by_id[rule_id] = rule
    own_ids = {rule["id"] for rule in own_rules}
    default_identity = []
    for rule_id in sorted(set(by_id) - own_ids):
        rule = by_id[rule_id]
        regex = rule["regex"]
        if isinstance(regex, str):
            regex = regex.replace(r"bedrock\x2dapi\x2dkey\x2d", "bedrock-api-key-")
        default_identity.append({"id": rule_id, "regex": regex, "keywords": rule["keywords"]})
    if digest(default_identity) != pinned["default_rules_sha256"]:
        raise ValueError("the default-rule digest does not match the independently pinned upstream data")
    for own in own_rules:
        stored = by_id.get(own["id"])
        if stored is None:
            raise ValueError(f"the keyword catalog omits Factory rule {own['id']}")
        if stored["regex"] != own.get("regex") or stored["keywords"] != sorted(
            {keyword.lower() for keyword in own.get("keywords", [])}
        ):
            raise ValueError(f"the keyword catalog has stale Factory rule {own['id']}")
    keywords = sorted({keyword for rule in rules for keyword in rule["keywords"]})
    if catalog.get("keywords") != keywords:
        raise ValueError("the keyword union does not match the merged rules")
    unkeyworded = catalog.get("unkeyworded_rules")
    if not isinstance(unkeyworded, list):
        raise ValueError("the keyword catalog must list unkeyworded rules")
    unkeyworded_ids: set[str] = set()
    for rule in unkeyworded:
        if not isinstance(rule, dict) or set(rule) != {"id", "path"}:
            raise ValueError("unkeyworded stdin rules must be path-only")
        rule_id = rule.get("id")
        pattern = rule.get("path")
        if not isinstance(rule_id, str) or not rule_id or rule_id in unkeyworded_ids or not isinstance(pattern, str):
            raise ValueError("the keyword catalog has an invalid unkeyworded rule")
        unkeyworded_ids.add(rule_id)
        try:
            matched = re.search(pattern, "") is not None
        except re.error as error:
            raise ValueError(f"the keyword catalog has an invalid path regex: {error}") from error
        if matched:
            raise ValueError(f"the unkeyworded rule {rule_id} can match stdin's empty path")
    if catalog.get("unkeyworded_rules_sha256") != digest(unkeyworded):
        raise ValueError("the unkeyworded-rule digest is stale")
    if digest(unkeyworded) != pinned["unkeyworded_rules_sha256"]:
        raise ValueError("the unkeyworded-rule digest does not match the independently pinned upstream data")
    expected_unkeyworded = {rule["id"] for rule in rules if not rule["keywords"]}
    if unkeyworded_ids != expected_unkeyworded:
        raise ValueError("the unkeyworded rule list does not match the merged rules")
    with gzip.open(root / "tools" / "gitleaks-rule-samples.json.gz", "rt") as source_file:
        fixture = json.load(source_file)
    if fixture.get("gitleaks_version") != version:
        raise ValueError("the true-positive fixture targets a different gitleaks version")
    if fixture.get("source") != f"https://github.com/gitleaks/gitleaks/tree/v{version}/cmd/generate/config/rules":
        raise ValueError("the true-positive fixture source does not match the gitleaks version pin")
    if fixture.get("encoding") != "base64" or not isinstance(fixture.get("samples"), list):
        raise ValueError("the true-positive fixture must be a base64 sample list")
    if fixture.get("samples_sha256") != pinned["samples_sha256"]:
        raise ValueError("the true-positive digest does not match the independently pinned upstream data")
    samples = []
    for encoded in fixture["samples"]:
        if not isinstance(encoded, dict) or not {"id", "text"} <= set(encoded) or set(encoded) - {"id", "text", "path"}:
            raise ValueError("the true-positive fixture has an invalid sample")
        if encoded["id"] not in by_id or not isinstance(encoded["text"], str):
            raise ValueError("the true-positive fixture references an unknown rule")
        try:
            text = base64.b64decode(encoded["text"], validate=True).decode()
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError(f"the true-positive fixture has invalid base64: {error}") from error
        sample = {"id": encoded["id"], "text": text}
        if "path" in encoded:
            if not isinstance(encoded["path"], str):
                raise ValueError("the true-positive fixture has an invalid sample path")
            sample["path"] = encoded["path"]
        samples.append(sample)
    if fixture.get("samples_sha256") != digest(samples):
        raise ValueError("the true-positive fixture digest is stale")
    samples_by_id: dict[str, list[dict[str, str]]] = {}
    for sample in samples:
        samples_by_id.setdefault(sample["id"], []).append(sample)
    safe = 0
    for rule in rules:
        expected_safe, expected_reason = classify_rule(
            rule, samples_by_id.get(rule["id"], []), factory_rule=rule["id"] in own_ids
        )
        if rule["python_safe"] != expected_safe or rule["python_unsafe_reason"] != expected_reason:
            raise ValueError(f"the Python safety classification is stale for {rule['id']}")
        safe += expected_safe
    return len(keywords), len(unkeyworded), len(rules), safe, len(samples)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        keywords, unkeyworded, rules, safe, samples = check(args.root.resolve())
    except (OSError, ValueError) as error:
        print(f"gitleaks keyword catalog: {error}", file=sys.stderr)
        return 1
    print(
        f"gitleaks keyword catalog: {keywords} keywords, {rules} rules "
        f"({safe} Python-safe), {samples} true positives and {unkeyworded} path-only rule(s) match the pin"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
