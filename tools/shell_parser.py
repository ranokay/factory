from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Word:
    text: str
    unquoted: list[bool]
    expansions: list[bool]
    operator: bool = False
    data: bool = False
    start: int = -1
    end: int = -1


@dataclass(frozen=True)
class Invocation:
    executable: str
    arguments: tuple[Word, ...]


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
                operator += command[position]
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
            elif operator == "\n" and heredocs:
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


def invocations(command: str) -> list[Invocation]:
    segments: list[list[Word]] = []
    segment: list[Word] = []
    redirect = False
    for word in shell_words(command):
        if word.data:
            continue
        if word.operator:
            if word.text in {"\n", ";", ";;", "&&", "||", "|", "&", "(", ")"}:
                if segment:
                    segments.append(segment)
                segment = []
            elif word.text in {"<", ">", "<<", "<<-", "<<<", ">>"}:
                redirect = True
            continue
        if redirect:
            redirect = False
            continue
        segment.append(word)
    if segment:
        segments.append(segment)
    result = []
    for words in segments:
        invocation = invocation_from_words(words)
        if invocation is not None:
            result.append(invocation)
    return result


def invocation_from_words(words: list[Word]) -> Invocation | None:
    remaining = list(words)
    while remaining:
        first = remaining[0].text
        if re.match(r"[A-Za-z_][A-Za-z_0-9]*=", first):
            remaining.pop(0)
            continue
        if first in {"command", "builtin"}:
            remaining.pop(0)
            while remaining and remaining[0].text in {"-p", "-v", "-V", "--"}:
                remaining.pop(0)
            continue
        if first == "sudo":
            remaining.pop(0)
            value_options = {
                "-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
                "-C", "--close-from", "-T", "--command-timeout", "-D", "--chdir",
                "-R", "--chroot", "-t", "--type", "-U", "--other-user",
            }
            short_value_options = {
                option[1] for option in value_options if option.startswith("-") and not option.startswith("--")
            }
            while remaining:
                option = remaining[0].text
                if option == "--":
                    remaining.pop(0)
                    break
                if option in value_options:
                    remaining.pop(0)
                    if remaining:
                        remaining.pop(0)
                    continue
                if option.startswith("--"):
                    remaining.pop(0)
                    continue
                if option.startswith("-") and len(option) > 1:
                    grouped = option[1:]
                    takes_next = any(
                        character in short_value_options and index == len(grouped) - 1
                        for index, character in enumerate(grouped)
                    )
                    remaining.pop(0)
                    if takes_next and remaining:
                        remaining.pop(0)
                    continue
                break
            continue
        if first == "env":
            remaining.pop(0)
            while remaining:
                candidate = remaining[0].text
                if re.match(r"[A-Za-z_][A-Za-z_0-9]*=", candidate):
                    remaining.pop(0)
                    continue
                if candidate in {"-S", "--split-string"}:
                    remaining.pop(0)
                    if remaining:
                        split = shlex.split(remaining.pop(0).text)
                        remaining = [Word(text, [False] * len(text), [False] * len(text)) for text in split] + remaining
                    continue
                if candidate in {"-u", "--unset", "-C", "--chdir"}:
                    remaining.pop(0)
                    if remaining:
                        remaining.pop(0)
                    continue
                if candidate in {"-i", "--ignore-environment", "-", "-v", "--debug", "--"} or candidate.startswith(
                    ("--unset=", "--chdir=", "-u", "-C")
                ):
                    remaining.pop(0)
                    continue
                break
            continue
        if first == "rtk" and len(remaining) > 1 and remaining[1].text == "proxy":
            remaining.pop(0)
            remaining.pop(0)
            continue
        break
    if not remaining:
        return None
    return Invocation(Path(remaining[0].text).name, tuple(remaining[1:]))


def has_unquoted_glob(word: Word, start: int = 0) -> bool:
    return any(active and char in "*?[" for char, active in zip(word.text[start:], word.unquoted[start:], strict=True))


def has_unquoted_query(word: Word) -> bool:
    for match in re.finditer(r"\?[A-Za-z_][\w.-]*=", word.text):
        if word.unquoted[match.start()]:
            return True
    return False
