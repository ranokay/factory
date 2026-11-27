#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import binascii
import functools
import gzip
import json
import re
import shutil
import string
import subprocess
import sys
import tomllib
import zlib
from pathlib import Path

try:
    from tools.gitleaks_regex import UNSAFE_REGEX, translate_fallback_regex
except ModuleNotFoundError:
    from gitleaks_regex import UNSAFE_REGEX, translate_fallback_regex

ROOT = Path(__file__).resolve().parents[1]
KEYWORD_CATALOG = ROOT / "tools" / "gitleaks-keywords.json"
# Leave room for a loaded machine: gitleaks alone takes several seconds at a high load average.
GITLEAKS_TIMEOUT = 10
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
FALLBACK_RULES = (
    ("curl-auth-user", re.compile(r"\bcurl\b[\s\S]*?[ \t](?:-u|--user)(?:=|[ \t]+)(?:'[^']*:[^']+'|\"[^\"]*:[^\"]+\"|[^\s:]*:[^\s]+)", re.IGNORECASE)),
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
    texts = decoded_texts(text)
    matches = {
        rule_id
        for decoded in texts
        for rule_id, pattern in FALLBACK_RULES
        if pattern.search(decoded)
    }
    rules = prefilter_catalog()
    if rules is not None:
        lowered = [item.lower() for item in texts]
        for rule in rules:
            if not any(keyword in item for keyword in rule["keywords"] for item in lowered):
                continue
            if rule["regex"] is None:
                continue
            pattern = (
                compile_rule_regex(rule["id"], rule["regex"])
                if rule["python_safe"]
                else compile_fallback_regex(rule["id"], rule["regex"])
            )
            if pattern is None:
                if rule["python_safe"] or fallback_samples() is None:
                    matches.add(rule["id"])
                continue
            if any(pattern.search(item) for item in texts):
                matches.add(rule["id"])
    return sorted(matches)


def fallback_scan(text: str, reason: str) -> tuple[list[str], str]:
    coverage = (
        "fallback scan passed"
        if prefilter_catalog() is not None
        else "fallback scan used built-in rules only; catalog rules not evaluated"
    )
    return fallback_rule_ids(text), f"{reason}; {coverage}"


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
        return re.compile(translate_fallback_regex(regex), re.ASCII)
    except re.error:
        return None


@functools.lru_cache(maxsize=1)
def fallback_samples() -> dict[str, list[str]] | None:
    try:
        with gzip.open(ROOT / "tools" / "gitleaks-rule-samples.json.gz", "rt") as source:
            fixture = json.load(source)
        if not isinstance(fixture, dict) or not isinstance(fixture.get("samples"), list):
            return None
        samples: dict[str, list[str]] = {}
        for sample in fixture["samples"]:
            if not isinstance(sample, dict) or not isinstance(sample.get("id"), str) or not isinstance(sample.get("text"), str):
                return None
            if "path" not in sample:
                samples.setdefault(sample["id"], []).append(base64.b64decode(sample["text"], validate=True).decode())
        return samples
    except (OSError, EOFError, ValueError, KeyError, TypeError, zlib.error):
        return None


@functools.lru_cache(maxsize=None)
def compile_fallback_regex(rule_id: str, regex: str) -> re.Pattern[str] | None:
    translated = translate_fallback_regex(regex)
    try:
        pattern = re.compile(translated, re.ASCII)
    except re.error:
        return None
    samples = fallback_samples()
    if samples is None or any(pattern.search(sample) is None for sample in samples.get(rule_id, [])):
        return None
    return pattern


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
    executable = shutil.which("gitleaks")
    if not needs_gitleaks(text):
        if executable is None:
            findings, warning = fallback_scan(text, "gitleaks not found")
            if findings:
                return findings, warning
        return [], None
    if executable is None:
        return fallback_scan(text, "gitleaks not found")
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
                "--ignore-gitleaks-allow",
                "--max-decode-depth",
                str(MAX_DECODE_DEPTH),
            ],
            input=text,
            text=True,
            capture_output=True,
            # A mise shim resolves its version from the working directory; use the Factory's pin in every project.
            cwd=ROOT,
            timeout=GITLEAKS_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return fallback_scan(text, "gitleaks not found")
    except OSError:
        return fallback_scan(text, "gitleaks could not start")
    except subprocess.TimeoutExpired:
        return fallback_scan(text, f"gitleaks timed out after {GITLEAKS_TIMEOUT} seconds")
    if result.returncode not in {0, 1}:
        return fallback_scan(text, f"gitleaks exited with code {result.returncode}")
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
        return fallback_scan(text, "gitleaks returned invalid output")
    return ids, None


def escaped_string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if json.dumps(value, ensure_ascii=False)[1:-1] != value else []
    if isinstance(value, dict):
        return [part for key, item in value.items() for part in [str(key), *escaped_string_values(item)]]
    if isinstance(value, list):
        return [part for item in value for part in escaped_string_values(item)]
    return []


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
    if not isinstance(value, str):
        raw_values = escaped_string_values(value)
        if raw_values:
            text += "\n" + "\n".join(raw_values)
    findings, warning = rule_ids(text)
    if not findings:
        if args.mode == "opencode":
            output: dict[str, object] = {"block": False}
            if warning:
                output["warning"] = f"Factory secret guard warning: {warning}"
            print(json.dumps(output))
        elif warning:
            print(json.dumps({"systemMessage": f"Factory secret guard warning: {warning}"}))
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
