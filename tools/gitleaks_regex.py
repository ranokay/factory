from __future__ import annotations

import re

UNSAFE_REGEX = re.compile(
    r"\[\[:|\\[zCQEpP]|\(\?U(?::|\))|\(\?<[A-Za-z]|\\[1-9]|\(\?(?:=|!|<=|<!)"
)


def translate_fallback_regex(regex: str) -> str:
    translated = regex.replace("[[:alnum:]]", "[A-Za-z0-9]").replace(r"\z", r"\Z")
    output: list[str] = []
    groups = [0]
    in_class = False
    escaped = False
    index = 0
    while index < len(translated):
        char = translated[index]
        if escaped:
            output.append(char)
            escaped = False
        elif char == "\\":
            output.append(char)
            escaped = True
        elif in_class:
            output.append(char)
            if char == "]":
                in_class = False
        elif char == "[":
            output.append(char)
            in_class = True
        elif translated.startswith("(?i)", index):
            output.append("(?i:")
            groups[-1] += 1
            index += 4
            continue
        elif char == "(":
            output.append(char)
            groups.append(0)
        elif char == ")" and len(groups) > 1:
            output.append(")" * (groups.pop() + 1))
        elif char == "|" and groups[-1]:
            output.append(")" * groups[-1] + "|" + "(?i:" * groups[-1])
        else:
            output.append(char)
        index += 1
    output.append(")" * groups[0])
    return "".join(output)
