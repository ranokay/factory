#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import binascii
import functools
import json
import re
import shutil
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
    r"|(?P<unicode>U\+[a-fA-F0-9]{4}(?:\s+U\+[a-fA-F0-9]{4})*(?=\s|$)|(?i:(?:\\{1,2}u[a-fA-F0-9]{4})+))"
    r"|(?P<hex>[0-9A-Fa-f]{32,})"
    r"|(?P<base64>[\w/+-]{16,}={0,2})",
    re.ASCII,
)
ENCODING_RE_JOINED = re.compile(
    r"(?P<percent>%[0-9A-Fa-f]{2}(?:.*%[0-9A-Fa-f]{2})?)"
    r"|(?P<unicode>(?:(?:U\+[a-fA-F0-9]{4}(?:\s|$))+|(?i:(?:\\{1,2}u[a-fA-F0-9]{4})+)))"
    r"|(?P<hex>[0-9A-Fa-f]{32,})"
    r"|(?P<base64>[\w/+-]{16,}={0,2})",
    re.ASCII,
)
UNSAFE_REGEX = re.compile(
    r"\[\[:|\\[zCQEpP]|\(\?U(?::|\))|\(\?<[A-Za-z]|\\[1-9]|\(\?(?:=|!|<=|<!)"
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
    return sorted(
        {
            rule_id
            for decoded in decoded_texts(text)
            for rule_id, pattern in FALLBACK_RULES
            if pattern.search(decoded)
        }
    )


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


def decode_pass(text: str, encoding_re: re.Pattern[str] = ENCODING_RE) -> str:
    parts: list[str] = []
    cursor = 0
    for match in encoding_re.finditer(text):
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


@functools.lru_cache(maxsize=1)
def prefilter_catalog() -> list[dict] | None:
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
        rules = catalog["rules"]
        if not isinstance(rules, list) or not rules:
            return None
        ids: set[str] = set()
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) != {
                "id",
                "keywords",
                "regex",
                "python_safe",
                "python_unsafe_reason",
            }:
                return None
            rule_id = rule["id"]
            keywords = rule["keywords"]
            regex = rule["regex"]
            if (
                not isinstance(rule_id, str)
                or not rule_id
                or rule_id in ids
                or not isinstance(keywords, list)
                or not all(isinstance(keyword, str) and keyword and keyword == keyword.lower() for keyword in keywords)
                or (regex is not None and not isinstance(regex, str))
                or not isinstance(rule["python_safe"], bool)
                or (rule["python_unsafe_reason"] is not None and not isinstance(rule["python_unsafe_reason"], str))
                or (regex is None and keywords)
            ):
                return None
            ids.add(rule_id)
        unkeyworded_ids: set[str] = set()
        for rule in catalog["unkeyworded_rules"]:
            if not isinstance(rule, dict) or set(rule) != {"id", "path"} or not isinstance(rule["path"], str):
                return None
            if re.search(rule["path"], "") is not None:
                return None
            unkeyworded_ids.add(rule["id"])
        if unkeyworded_ids != {rule["id"] for rule in rules if not rule["keywords"]}:
            return None
        return rules
    except (OSError, ValueError, KeyError, TypeError, re.error):
        return None


@functools.lru_cache(maxsize=None)
def compile_rule_regex(rule_id: str, regex: str) -> re.Pattern[str] | None:
    if UNSAFE_REGEX.search(regex):
        return None
    try:
        return re.compile(regex, re.ASCII)
    except re.error:
        return None


def decoded_texts(text: str) -> list[str]:
    texts = [text]
    for encoding_re in (ENCODING_RE, ENCODING_RE_JOINED):
        current = text
        for _ in range(MAX_DECODE_DEPTH):
            decoded = decode_pass(current, encoding_re)
            if decoded == current:
                break
            texts.append(decoded)
            current = decoded
    return list(dict.fromkeys(texts))


def exceeds_prefilter_limit(text: str) -> bool:
    try:
        return len(text.encode("utf-8")) > MAX_PREFILTER_BYTES
    except UnicodeEncodeError:
        return True


def needs_gitleaks(text: str) -> bool:
    if exceeds_prefilter_limit(text):
        return True
    rules = prefilter_catalog()
    if rules is None:
        return True
    texts = decoded_texts(text)
    lowered = [item.lower() for item in texts]
    for rule in rules:
        if not any(keyword in item for keyword in rule["keywords"] for item in lowered):
            continue
        if not rule["python_safe"] or rule["regex"] is None:
            return True
        pattern = compile_rule_regex(rule["id"], rule["regex"])
        if pattern is None or any(pattern.search(item) for item in texts):
            return True
    return False


def rule_ids(text: str) -> tuple[list[str], str | None]:
    if not needs_gitleaks(text):
        return [], None
    executable = shutil.which("gitleaks")
    if executable is None:
        return fallback_rule_ids(text), "gitleaks not found"
    gitleaks = str(Path(executable).absolute())
    try:
        result = subprocess.run(
            [
                gitleaks,
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
        return (fallback_rule_ids(text) if exceeds_prefilter_limit(text) else []), None
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
