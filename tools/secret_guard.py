#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import string
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYWORD_CATALOG = ROOT / "tools" / "gitleaks-keywords.json"
GITLEAKS_TIMEOUT = 2
MAX_DECODE_DEPTH = 5
# Stdin pipe reads can split greedy decoding; use a conservative 4 KiB ceiling for whole-string prefiltering.
# https://github.com/gitleaks/gitleaks/blob/v8.30.1/sources/file.go#L166-L212
# https://github.com/gitleaks/gitleaks/blob/v8.30.1/sources/common.go#L56-L124
MAX_PREFILTER_BYTES = 4096
# Mirror gitleaks v8.30.1 candidate precedence and thresholds.
# https://github.com/gitleaks/gitleaks/blob/v8.30.1/detect/codec/encodings.go#L22-L43
ENCODING_RE = re.compile(
    r"(?P<percent>%[0-9A-Fa-f]{2}(?:.*%[0-9A-Fa-f]{2})?)"
    r"|(?P<unicode>(?:(?:U\+[a-fA-F0-9]{4}(?:\s|$))+|(?i:(?:\\{1,2}u[a-fA-F0-9]{4})+)))"
    r"|(?P<hex>[0-9A-Fa-f]{32,})"
    r"|(?P<base64>[\w/+-]{16,}={0,2})",
    re.ASCII,
)
FALLBACK_RULES = (
    ("private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("anthropic-api-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openai-api-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("google-api-key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    (
        "literal-credential",
        re.compile(
            r"(?i)\b(?:authorization|context7[_-]?api[_-]?key|x-api-key|api[_-]?token|access[_-]?token|"
            r"token|password|passwd|secret)[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?[A-Za-z0-9._~+/=-]{20,}"
        ),
    ),
    ("context7-key-token", re.compile(r"(?i)\bctx7[a-z]{1,6}-[a-z0-9._~+/=-]{16,}\b")),
)


def fallback_rule_ids(text: str) -> list[str]:
    return sorted(rule_id for rule_id, pattern in FALLBACK_RULES if pattern.search(text))


# Match gitleaks v8.30.1's kept-segment and printable-ASCII acceptance rules.
# https://github.com/gitleaks/gitleaks/blob/v8.30.1/detect/codec/decoder.go#L51-L62
# https://github.com/gitleaks/gitleaks/blob/v8.30.1/detect/codec/ascii.go#L5-L10
def printable_ascii(value: bytes) -> bool:
    return all(0x08 < byte < 0x7F for byte in value)


def decode_base64(value: str) -> str | None:
    if not any(char in "0123456789+/-_" for char in value):
        return None
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        decoded = None
    if decoded is not None and printable_ascii(decoded):
        return decoded.decode("ascii")
    if "=" not in value and "+" not in value and "/" not in value:
        padded = value + "=" * (-len(value) % 4)
        try:
            decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError):
            return None
        if printable_ascii(decoded):
            return decoded.decode("ascii")
    return None


def decode_hex(value: str) -> str | None:
    if len(value) % 2 or not any(char in string.digits for char in value):
        return None
    decoded = bytes.fromhex(value)
    return decoded.decode("ascii") if printable_ascii(decoded) else None


def decode_percent(value: str) -> str | None:
    decoded: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "%" and index + 2 < len(value):
            pair = value[index + 1 : index + 3]
            if all(char in string.hexdigits for char in pair):
                byte = int(pair, 16)
                if not 0x08 < byte < 0x7F:
                    return None
                decoded.append(chr(byte))
                index += 3
                continue
        decoded.append(value[index])
        index += 1
    return "".join(decoded)


def decode_unicode(value: str) -> str | None:
    if "U+" in value:
        points = re.findall(r"U\+([a-fA-F0-9]{4})", value)
    else:
        points = re.findall(r"\\{1,2}u([a-fA-F0-9]{4})", value, re.IGNORECASE)
    if not points:
        return None
    return "".join("\ufffd" if 0xD800 <= int(point, 16) <= 0xDFFF else chr(int(point, 16)) for point in points)


def decode_pass(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in ENCODING_RE.finditer(text):
        parts.append(text[cursor : match.start()])
        decoder = {"percent": decode_percent, "unicode": decode_unicode, "hex": decode_hex, "base64": decode_base64}[
            match.lastgroup
        ]
        decoded = decoder(match.group())
        parts.append(decoded if decoded else text[match.start() : match.end()])
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts)


def pinned_gitleaks_version() -> str | None:
    try:
        config = tomllib.loads((ROOT / "profiles" / "universal" / "mise.toml").read_text())
        version = config["tools"]["gitleaks"]
        return version if isinstance(version, str) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def prefilter_keywords() -> set[str] | None:
    try:
        catalog = json.loads(KEYWORD_CATALOG.read_text())
        version = catalog["gitleaks_version"]
        source = catalog["source"]
        if (
            not isinstance(version, str)
            or version != pinned_gitleaks_version()
            or source != f"https://raw.githubusercontent.com/gitleaks/gitleaks/v{version}/config/gitleaks.toml"
        ):
            return None
        keywords = catalog["keywords"]
        if (
            not isinstance(keywords, list)
            or not keywords
            or not all(isinstance(keyword, str) and keyword and keyword == keyword.lower() for keyword in keywords)
        ):
            return None
        for rule in catalog["unkeyworded_rules"]:
            if not isinstance(rule, dict) or set(rule) != {"id", "path"} or not isinstance(rule["path"], str):
                return None
            if re.search(rule["path"], "") is not None:
                return None
        return set(keywords)
    except (OSError, ValueError, KeyError, TypeError, re.error):
        return None


def needs_gitleaks(text: str) -> bool:
    try:
        if len(text.encode("utf-8")) > MAX_PREFILTER_BYTES:
            return True
    except UnicodeEncodeError:
        return True
    keywords = prefilter_keywords()
    if keywords is None:
        return True
    current = text
    for depth in range(MAX_DECODE_DEPTH + 1):
        lowered = current.lower()
        if any(keyword in lowered for keyword in keywords):
            return True
        if depth == MAX_DECODE_DEPTH:
            break
        decoded = decode_pass(current)
        if decoded == current:
            break
        current = decoded
    return False


def rule_ids(text: str) -> tuple[list[str], str | None]:
    if not needs_gitleaks(text):
        return [], None
    try:
        result = subprocess.run(
            [
                "gitleaks",
                "stdin",
                "--config",
                str(ROOT / ".gitleaks.toml"),
                "--redact",
                "--report-format",
                "json",
                "--report-path",
                "-",
                "--no-banner",
            ],
            input=text,
            text=True,
            capture_output=True,
            timeout=GITLEAKS_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return fallback_rule_ids(text), "gitleaks not found"
    except subprocess.TimeoutExpired:
        return fallback_rule_ids(text), f"gitleaks timed out after {GITLEAKS_TIMEOUT} seconds"
    if result.returncode not in {0, 1}:
        return fallback_rule_ids(text), f"gitleaks exited with code {result.returncode}"
    if result.returncode == 0:
        return [], None
    try:
        findings = json.loads(result.stdout)
        if not isinstance(findings, list) or not findings:
            raise ValueError("gitleaks returned no findings")
        ids = sorted({finding["RuleID"] for finding in findings})
        if not all(isinstance(rule_id, str) and rule_id for rule_id in ids):
            raise ValueError("gitleaks returned an invalid rule ID")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return fallback_rule_ids(text), "gitleaks returned invalid output"
    return ids, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prompt", "tool", "opencode"])
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print("Factory secret guard: invalid JSON input", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("Factory secret guard: hook input must be a JSON object", file=sys.stderr)
        return 2
    key = {"prompt": "prompt", "tool": "tool_input", "opencode": "input"}[args.mode]
    value = payload.get(key)
    if value is None or (args.mode == "prompt" and not isinstance(value, str)):
        print(f"Factory secret guard: hook input has no valid {key}", file=sys.stderr)
        return 2
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    findings, warning = rule_ids(text)
    if not findings:
        if args.mode == "opencode":
            output: dict[str, object] = {"block": False}
            if warning:
                output["warning"] = f"Factory secret guard warning: {warning}; fallback scan passed"
            print(json.dumps(output))
        elif warning:
            print(json.dumps({"systemMessage": f"Factory secret guard warning: {warning}; fallback scan passed"}))
        return 0
    reason = "Factory secret guard: " + ", ".join(findings)
    if args.mode == "prompt":
        output = {
            "decision": "block",
            "reason": reason,
            "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "suppressOriginalPrompt": True},
        }
    elif args.mode == "tool":
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    else:
        output = {"block": True, "message": reason}
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
