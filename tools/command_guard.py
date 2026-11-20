#!/usr/bin/env python3
"""Deny a few known shell mistakes in a PreToolUse hook payload."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

FILTER_GLOB_REASON = "Quote grep/rg filter globs, for example --include='*.py' or -g '*.py'."
MAX_CORRECTIONS = 8


@dataclass
class Word:
    text: str
    unquoted: list[bool]
    expansions: list[bool]
    operator: bool = False
    data: bool = False
    start: int = -1
    end: int = -1


def heredoc_word(body: str) -> Word:
    text: list[str] = []
    expansions: list[bool] = []
    position = 0
    while position < len(body):
        char = body[position]
        if char == "\\" and position + 1 < len(body) and body[position + 1] in "$`\\\n":
            following = body[position + 1]
            position += 2
            if following != "\n":
                text.append(following)
                expansions.append(False)
            continue
        text.append(char)
        expansions.append(True)
        position += 1
    return Word("".join(text), [False] * len(text), expansions, data=True)


def shell_words(command: str) -> list[Word]:
    """Keep quote provenance; shlex discards the information this guard needs."""
    words: list[Word] = []
    heredocs: list[tuple[str, bool, bool]] = []
    heredoc_operator: str | None = None
    position = 0
    while position < len(command):
        char = command[position]
        if char.isspace() and char != "\n":
            position += 1
            continue
        if char == "#":
            end = command.find("\n", position)
            position = len(command) if end == -1 else end
            continue
        if char in "\n;&|()<>":
            operator = char
            position += 1
            if char in ";&|<>" and command[position : position + 1] == char:
                operator += char
                position += 1
            if operator == "<<" and command[position : position + 1] == "-":
                operator += "-"
                position += 1
            elif operator == "<<" and command[position : position + 1] == "<":
                operator += "<"
                position += 1
            words.append(Word(operator, [], [], operator=True))
            if operator in {"<<", "<<-"}:
                heredoc_operator = operator
            if operator == "\n" and heredocs:
                for delimiter, strip_tabs, expand in heredocs:
                    body: list[str] = []
                    while position < len(command):
                        end = command.find("\n", position)
                        end = len(command) if end == -1 else end
                        line = command[position:end]
                        position = min(end + 1, len(command))
                        if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                            break
                        body.append(line + "\n")
                    if expand:
                        words.append(heredoc_word("".join(body)))
                heredocs.clear()
            continue

        text: list[str] = []
        unquoted: list[bool] = []
        expansions: list[bool] = []
        quote: str | None = None
        quoted = False
        start = position
        while position < len(command):
            char = command[position]
            if quote is None and (char.isspace() or char in ";&|()<>"):
                break
            if quote is None and char in "'\"":
                quote = char
                quoted = True
                position += 1
                continue
            if char == quote:
                quote = None
                position += 1
                continue
            if char == "\\" and quote != "'" and position + 1 < len(command):
                following = command[position + 1]
                if quote is None or following in '$`"\\\n':
                    position += 2
                    if following != "\n":
                        quoted = True
                        text.append(following)
                        unquoted.append(False)
                        expansions.append(False)
                    continue
            text.append(char)
            unquoted.append(quote is None)
            expansions.append(quote != "'")
            position += 1
        word = Word("".join(text), unquoted, expansions, start=start, end=position)
        words.append(word)
        if heredoc_operator is not None:
            heredocs.append((word.text, heredoc_operator == "<<-", not quoted))
            heredoc_operator = None
    return words


def has_unquoted_glob(word: Word, start: int = 0) -> bool:
    return any(active and char in "*?[" for char, active in zip(word.text[start:], word.unquoted[start:], strict=True))


def has_unquoted_query(word: Word) -> bool:
    """A `?name=` query string, as in `gh api repos/o/r/contents/x?ref=main`."""
    for match in re.finditer(r"\?[A-Za-z_][\w.-]*=", word.text):
        if word.unquoted[match.start()]:
            return True
    return False


def swiftc_without_sdk() -> str | None:
    """Name the PATH swiftc when it can't find the macOS SDK by itself."""
    if sys.platform != "darwin" or os.environ.get("SDKROOT"):
        return None
    found = shutil.which("swiftc")
    if found is None or found.startswith(("/usr/bin/", "/Applications/Xcode", "/Library/Developer/CommandLineTools/")):
        return None
    return found


def quoted_from(command: str, word: Word, offset: int = 0) -> str | None:
    """Single-quote a word from `offset`, when its source holds nothing quoting would change."""
    source = command[word.start : word.end]
    if word.start < 0 or set(source) & set("'\"\\$`"):
        return None
    return f"{command[: word.start]}{source[:offset]}'{source[offset:]}'{command[word.end :]}"


def problem(command: str, shell: str) -> tuple[str, str | None] | None:
    """Return why a command is unsafe, and the corrected command when one can be built."""
    words = shell_words(command)
    if Path(shell).name == "zsh":
        for word in words:
            if word.data or word.operator:
                continue
            if has_unquoted_query(word) or ("://" in word.text and has_unquoted_glob(word)):
                return (
                    "Quote this URL or API path. In zsh an unquoted ?, * or [ is a glob, "
                    "and a glob that matches nothing fails with 'no matches found'.",
                    quoted_from(command, word),
                )
    if Path(shell).name != "bash":
        for word in words:
            for match in re.finditer(r"\$\{PIPESTATUS\b", word.text):
                if word.expansions[match.start()]:
                    return (
                        "${PIPESTATUS...} requires Bash, but this command does not "
                        "explicitly use Bash as its execution shell. Select Bash "
                        "explicitly or preserve the exit status without a pipe.",
                        f"bash -c {shlex.quote(command)}",
                    )

    executable: str | None = None
    expect_filter = False
    redirect_target = False
    env_argument = False
    env_options = True
    sdkroot_assigned = False
    for word in words:
        if word.data:
            continue
        if word.operator:
            if word.text in {"\n", ";", ";;", "&&", "||", "|", "&", "(", ")"}:
                executable = None
                expect_filter = False
                env_argument = False
                env_options = True
                sdkroot_assigned = False
            elif word.text in {"<", ">", "<<", "<<-", "<<<", ">>"}:
                redirect_target = True
            continue
        if redirect_target:
            redirect_target = False
            continue
        if executable == "env":
            if env_argument:
                env_argument = False
                continue
            if env_options and word.text == "--":
                env_options = False
                continue
            if env_options and word.text in {"-u", "--unset", "-C", "--chdir"}:
                env_argument = True
                continue
            if env_options and (
                word.text in {"-i", "--ignore-environment", "-", "-v", "--debug"}
                or word.text.startswith(("--unset=", "--chdir="))
                or (word.text.startswith(("-u", "-C")) and len(word.text) > 2)
            ):
                continue
            if env_options and word.text.startswith("-"):
                executable = "other-command"
                continue
            if re.match(r"[A-Za-z_][A-Za-z_0-9]*=", word.text):
                continue
            executable = None
        if executable is None:
            if re.match(r"[A-Za-z_][A-Za-z_0-9]*=", word.text):
                sdkroot_assigned = sdkroot_assigned or word.text.startswith("SDKROOT=")
                continue
            if word.text in {"command", "builtin", "sudo"}:
                continue
            executable = Path(word.text).name
            if word.text == "swiftc" and not sdkroot_assigned and (found := swiftc_without_sdk()):
                return (
                    f"The swiftc on PATH ({found}) has no macOS SDK, so builds fail to find "
                    "Apple frameworks or to link. Use `xcrun -sdk macosx swiftc`.",
                    None,
                )
            if executable == "env":
                env_argument = False
                env_options = True
            continue
        if executable == "rtk":
            executable = None if word.text == "proxy" else "other-command"
            continue
        if executable not in {"grep", "egrep", "fgrep", "rg"}:
            continue
        if word.text == "--":
            executable = "finished-options"
            continue
        if expect_filter:
            if has_unquoted_glob(word):
                return FILTER_GLOB_REASON, quoted_from(command, word)
            expect_filter = False
            continue
        filters = ("--include", "--exclude", "--exclude-dir")
        if executable == "rg":
            filters += ("--glob", "--iglob", "-g")
        for option in filters:
            if word.text == option:
                expect_filter = True
                break
            prefix = option + "="
            if word.text.startswith(prefix) and has_unquoted_glob(word, len(prefix)):
                return FILTER_GLOB_REASON, quoted_from(command, word, len(prefix))
            if option == "-g" and word.text.startswith("-g") and has_unquoted_glob(word, 2):
                return FILTER_GLOB_REASON, quoted_from(command, word, 2)

    # Only recognize the common final pipeline. General shell control flow is
    # deliberately outside this hook's contract.
    pipefail_prefix = [word.text for word in words[:4]] == ["set", "-o", "pipefail", ";"]
    if not pipefail_prefix:
        for index, word in enumerate(words):
            if not word.operator or word.text != "|":
                continue
            start = index - 1
            while start >= 0 and not words[start].operator:
                start -= 1
            gate = [item.text for item in words[start + 1 : index]]
            following = words[index + 1 :]
            while following and following[-1].operator and following[-1].text == "\n":
                following = following[:-1]
            if (
                len(gate) == 3
                and Path(gate[0]).name == "mise"
                and gate[1] == "run"
                and gate[2] in {"check", "test-strict"}
                and following
                and Path(following[0].text).name in {"head", "tail", "tee"}
                and all(not item.operator or item.text == "|" for item in following)
            ):
                return (
                    "Piping a mise gate to head/tail/tee can hide its failure. "
                    "Run the gate without the pipe and redirect its full output to a file.",
                    None,
                )
    return None


def unsafe_reason(command: str, shell: str) -> str | None:
    found = problem(command, shell)
    if found is None:
        return None
    reason, corrected = found
    # One command can hold several mistakes; offer a correction only if it passes as a whole.
    for _ in range(MAX_CORRECTIONS):
        if corrected is None:
            return reason
        remaining = problem(corrected, shell)
        if remaining is None:
            return f"{reason} Run this instead:\n{corrected}"
        corrected = remaining[1]
    return reason


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shell", help="Execution shell, if the hook payload omits it")
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    tool_input = payload.get("tool_input")
    if payload.get("tool_name") not in {"Bash", "bash"} or not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0
    shell = tool_input.get("shell") or payload.get("shell") or args.shell or os.environ.get("SHELL", "")
    reason = unsafe_reason(command, shell if isinstance(shell, str) else "")
    if reason:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
