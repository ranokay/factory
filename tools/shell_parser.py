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
    dynamic: bool = False
    environment: tuple[tuple[str, str], ...] = ()
    directory: str | None = None


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
        is_fd_prefix = (
            word.text.isdigit()
            and bool(word.unquoted)
            and all(word.unquoted)
            and command[position : position + 1] in {"<", ">"}
        )
        if not is_fd_prefix:
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
        while words and words[0].text in {"!", "{", "}", "then", "do", "else", "elif", "fi", "done", "in", "case", "esac", "time"}:
            words.pop(0)
        if not words:
            continue
        invocation = invocation_from_words(words)
        if invocation is not None:
            result.append(invocation)
    return result


def has_env_expansion(value: str) -> bool:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote == "'":
            if char == "'":
                quote = None
            continue
        if char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "$" and value[index + 1 : index + 2] == "{":
            return True
    return False


def truncate_env_string(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote == "'":
            if char == "'":
                quote = None
            continue
        if char == "\\":
            following = value[index + 1 : index + 2]
            if quote is None and following == "c":
                return value[:index]
            if quote is None or (quote == '"' and following in {'$', '`', '"', "\\", "\n"}):
                escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
    return value


def split_env_string(value: str) -> list[str] | None:
    try:
        return shlex.split(truncate_env_string(value))
    except ValueError:
        return None


def invocation_from_words(words: list[Word]) -> Invocation | None:
    remaining = list(words)
    dynamic = False
    environment: list[tuple[str, str]] = []
    directory: str | None = None
    while remaining:
        first = remaining[0].text
        assignment = re.match(r"([A-Za-z_][A-Za-z_0-9]*)(\+=|=)", first)
        if assignment is not None:
            environment.append((assignment.group(1), first[assignment.end() :]))
            remaining.pop(0)
            continue
        if first in {"command", "builtin"}:
            remaining.pop(0)
            while remaining and remaining[0].text.startswith("-") and remaining[0].text != "-":
                option = remaining.pop(0).text
                if option == "--":
                    break
                if option == "--help":
                    return None
                if "v" in option[1:] or "V" in option[1:]:
                    return None
            continue
        if first in {"sh", "bash", "zsh", "dash", "ksh"}:
            remaining.pop(0)
            command_index = next(
                (
                    index + 1
                    for index, word in enumerate(remaining)
                    if word.text.startswith("-") and not word.text.startswith("--") and "c" in word.text[1:]
                ),
                None,
            )
            if command_index is None:
                return None
            if command_index >= len(remaining):
                return None
            command_word = remaining.pop(command_index)
            command_words = shell_words(command_word.text)
            if "$" in command_word.text or "`" in command_word.text or any(word.operator for word in command_words):
                dynamic = True
            remaining = command_words
            continue
        if first == "sudo":
            remaining.pop(0)
            value_options = {
                "-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
                "-C", "--close-from", "-T", "--command-timeout", "-D", "--chdir",
                "-R", "--chroot", "-r", "--role", "-t", "--type", "-U", "--other-user",
            }
            long_value_options = {
                option for option in value_options if option.startswith("--")
            }
            short_value_options = {
                option[1] for option in value_options if option.startswith("-") and not option.startswith("--")
            }
            shell_mode = False
            while remaining:
                option = remaining[0].text
                if option == "--":
                    remaining.pop(0)
                    break
                if option in {"-l", "--list", "-v", "--validate", "-V", "--version", "--help"}:
                    return None
                if option in {"-s", "--shell", "-i", "--login"}:
                    shell_mode = True
                    remaining.pop(0)
                    continue
                if option in value_options:
                    remaining.pop(0)
                    if remaining:
                        value = remaining.pop(0).text
                        if option in {"-D", "--chdir"}:
                            directory = value
                    continue
                if option.startswith("--"):
                    name = option.split("=", 1)[0]
                    if "=" not in option:
                        shell_mode = shell_mode or "--shell".startswith(name) or "--login".startswith(name)
                    takes_value = "=" not in option and any(
                        candidate.startswith(name) for candidate in long_value_options
                    )
                    remaining.pop(0)
                    if takes_value and remaining:
                        value = remaining.pop(0).text
                        if "--chdir".startswith(name) and not "--chroot".startswith(name):
                            directory = value
                    elif "=" in option and "--chdir".startswith(name) and not "--chroot".startswith(name):
                        directory = option.split("=", 1)[1]
                    continue
                if option.startswith("-") and len(option) > 1:
                    grouped = option[1:]
                    for index, character in enumerate(grouped):
                        if character in "lvV":
                            return None
                        if character in "si":
                            shell_mode = True
                        if character in short_value_options:
                            remaining.pop(0)
                            if index == len(grouped) - 1:
                                if remaining:
                                    value = remaining.pop(0).text
                                else:
                                    value = ""
                            else:
                                value = grouped[index + 1 :]
                            if character == "D":
                                directory = value
                            break
                    else:
                        remaining.pop(0)
                    continue
                break
            if shell_mode and remaining:
                command_word = remaining.pop(0)
                command_words = shell_words(command_word.text)
                if "$" in command_word.text or "`" in command_word.text or any(
                    word.operator for word in command_words
                ):
                    dynamic = True
                remaining[0:0] = command_words
            continue
        if first == "env":
            remaining.pop(0)
            while remaining:
                candidate = remaining[0].text
                assignment = re.match(r"([A-Za-z_][A-Za-z_0-9]*)(\+=|=)", candidate)
                if assignment is not None:
                    environment.append((assignment.group(1), candidate[assignment.end() :]))
                    remaining.pop(0)
                    continue
                if candidate == "--":
                    remaining.pop(0)
                    break
                if candidate in {"--help", "--version"}:
                    return None
                if candidate in {"-S", "--split-string"}:
                    remaining.pop(0)
                    if not remaining:
                        return None
                    value_word = remaining.pop(0)
                    dynamic = dynamic or has_expansion(value_word) or has_env_expansion(value_word.text)
                    split = split_env_string(value_word.text)
                    if split is None:
                        return None
                    remaining[0:0] = [Word(text, [False] * len(text), [False] * len(text)) for text in split]
                    continue
                if candidate.startswith("--split-string="):
                    dynamic = dynamic or has_expansion(remaining[0]) or has_env_expansion(candidate)
                    split = split_env_string(candidate.split("=", 1)[1])
                    if split is None:
                        return None
                    remaining.pop(0)
                    remaining[0:0] = [Word(text, [False] * len(text), [False] * len(text)) for text in split]
                    continue
                if candidate in {"-u", "--unset", "-C", "--chdir"}:
                    option = remaining.pop(0).text
                    if remaining:
                        value = remaining.pop(0).text
                        if option in {"-C", "--chdir"}:
                            directory = value
                    continue
                if candidate.startswith(("--unset=", "--chdir=")):
                    if candidate.startswith("--chdir="):
                        directory = candidate.split("=", 1)[1]
                    remaining.pop(0)
                    continue
                if candidate in {
                    "-i", "-0", "-v", "-", "--ignore-environment", "--null", "--debug",
                    "--list-signal-handling", "--block-signal", "--default-signal", "--ignore-signal",
                } or candidate.startswith(("--block-signal=", "--default-signal=", "--ignore-signal=")):
                    remaining.pop(0)
                    continue
                if candidate.startswith("-") and len(candidate) > 1:
                    dynamic = dynamic or has_expansion(remaining[0]) or has_env_expansion(candidate)
                    grouped = candidate[1:]
                    for index, character in enumerate(grouped):
                        if character == "S":
                            remaining.pop(0)
                            if index == len(grouped) - 1:
                                if not remaining:
                                    return None
                                value_word = remaining.pop(0)
                                dynamic = dynamic or has_expansion(value_word) or has_env_expansion(value_word.text)
                                value = value_word.text
                            else:
                                value = grouped[index + 1 :]
                            split = split_env_string(value)
                            if split is None:
                                return None
                            remaining[0:0] = [Word(text, [False] * len(text), [False] * len(text)) for text in split]
                            break
                        if character in "uCP":
                            remaining.pop(0)
                            if index == len(grouped) - 1:
                                if remaining:
                                    value = remaining.pop(0).text
                                else:
                                    value = ""
                            else:
                                value = grouped[index + 1 :]
                            if character == "C":
                                directory = value
                            break
                    else:
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
        if environment:
            return Invocation("", (), False, tuple(environment), directory)
        return None
    return Invocation(
        Path(remaining[0].text).name,
        tuple(remaining[1:]),
        dynamic or has_expansion(remaining[0]),
        tuple(environment),
        directory,
    )


def has_expansion(word: Word) -> bool:
    return any(
        active and char in "$`"
        for char, active in zip(word.text, word.expansions, strict=True)
    )


def has_unquoted_glob(word: Word, start: int = 0) -> bool:
    return any(active and char in "*?[" for char, active in zip(word.text[start:], word.unquoted[start:], strict=True))


def has_unquoted_query(word: Word) -> bool:
    for match in re.finditer(r"\?[A-Za-z_][\w.-]*=", word.text):
        if word.unquoted[match.start()]:
            return True
    return False
