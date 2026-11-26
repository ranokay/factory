#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNSAFE_REGEX = re.compile(
    r"\[\[:|\\[zCQEpP]|\(\?U(?::|\))|\(\?<[A-Za-z]|\\[1-9]|\(\?(?:=|!|<=|<!)"
)


def digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_samples(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def classify_rule(rule: dict, samples: list[dict[str, str]], *, factory_rule: bool) -> tuple[bool, str | None]:
    regex = rule.get("regex")
    if not isinstance(regex, str):
        return False, "path-only rule"
    if UNSAFE_REGEX.search(regex):
        return False, "RE2 syntax differs from Python"
    try:
        pattern = re.compile(regex, re.ASCII)
    except re.error:
        return False, "Python regex compilation failed"
    if not factory_rule and not samples:
        return False, "no upstream true-positive samples"
    for sample in samples:
        if pattern.search(sample["text"]) is None:
            return False, "Python regex misses an upstream true positive"
    return True, None


def generate(source_root: Path, samples_path: Path, version: str) -> tuple[dict, dict]:
    default = tomllib.loads((source_root / "config" / "gitleaks.toml").read_text())
    factory = tomllib.loads((ROOT / ".gitleaks.toml").read_text())
    default_rules = default.get("rules", [])
    factory_rules = factory.get("rules", [])
    samples = load_samples(samples_path)
    samples_by_id: dict[str, list[dict[str, str]]] = {}
    for sample in samples:
        samples_by_id.setdefault(sample["id"], []).append(sample)
    factory_ids = {rule["id"] for rule in factory_rules}
    merged = [*default_rules, *factory_rules]
    rules = []
    for rule in sorted(merged, key=lambda item: item["id"]):
        safe, reason = classify_rule(rule, samples_by_id.get(rule["id"], []), factory_rule=rule["id"] in factory_ids)
        regex = rule.get("regex")
        if isinstance(regex, str):
            regex = regex.replace("bedrock-api-key-", r"bedrock\x2dapi\x2dkey\x2d")
        rules.append(
            {
                "id": rule["id"],
                "keywords": sorted({keyword.lower() for keyword in rule.get("keywords", [])}),
                "regex": regex,
                "python_safe": safe,
                "python_unsafe_reason": reason,
            }
        )
    keywords = sorted({keyword for rule in rules for keyword in rule["keywords"]})
    unkeyworded = [
        {"id": rule["id"], "path": rule["path"]}
        for rule in merged
        if not rule.get("keywords") and "path" in rule
    ]
    default_identity = [
        {"id": rule["id"], "regex": rule.get("regex"), "keywords": rule.get("keywords", [])}
        for rule in sorted(default_rules, key=lambda item: item["id"])
    ]
    source = f"https://raw.githubusercontent.com/gitleaks/gitleaks/v{version}/config/gitleaks.toml"
    catalog = {
        "gitleaks_version": version,
        "source": source,
        "default_rule_count": len(default_rules),
        "default_rules_sha256": digest(default_identity),
        "rules_sha256": digest(rules),
        "keywords": keywords,
        "unkeyworded_rules": unkeyworded,
        "rules": rules,
    }
    encoded_samples = []
    for sample in sorted(samples, key=lambda item: (item["id"], item["text"], item.get("path", ""))):
        encoded = {"id": sample["id"], "text": base64.b64encode(sample["text"].encode()).decode()}
        if "path" in sample:
            encoded["path"] = sample["path"]
        encoded_samples.append(encoded)
    fixture = {
        "gitleaks_version": version,
        "source": f"https://github.com/gitleaks/gitleaks/tree/v{version}/cmd/generate/config/rules",
        "encoding": "base64",
        "samples_sha256": digest(
            [
                {"id": sample["id"], "text": base64.b64decode(sample["text"]).decode(), **({"path": sample["path"]} if "path" in sample else {})}
                for sample in encoded_samples
            ]
        ),
        "samples": encoded_samples,
    }
    return catalog, fixture


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--catalog", type=Path, default=ROOT / "tools" / "gitleaks-keywords.json")
    parser.add_argument("--fixture", type=Path, default=ROOT / "tools" / "gitleaks-rule-samples.json.gz")
    args = parser.parse_args()
    catalog, fixture = generate(args.source_root.resolve(), args.samples.resolve(), args.version)
    args.catalog.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    args.fixture.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(filename=args.fixture, mode="wb", mtime=0) as output:
        output.write((json.dumps(fixture, separators=(",", ":")) + "\n").encode())
    safe = sum(rule["python_safe"] for rule in catalog["rules"])
    print(f"generated {len(catalog['rules'])} rules, {len(fixture['samples'])} samples, {safe} Python-safe rules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
