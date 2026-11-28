from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from shell_parser_types import Invocation, ParseState


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


def scan_word(command: str, position: int) -> tuple[Word, int, bool]:
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
    return Word("".join(text), unquoted, expansions, start=start, end=position), position, quoted


def consume_heredocs(command: str, position: int, heredocs: list[tuple[str, bool, bool]]) -> tuple[int, list[Word]]:
    words: list[Word] = []
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
    return position, words


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
                position, bodies = consume_heredocs(command, position, heredocs)
                words.extend(bodies)
                heredocs.clear()
            continue
        word, position, quoted = scan_word(command, position)
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


def consume_assignment(state: ParseState) -> bool:
    first = state.remaining[0].text
    assignment = re.match(r"([A-Za-z_][A-Za-z_0-9]*)(\+=|=)", first)
    if assignment is None:
        return False
    state.dynamic = state.dynamic or has_expansion(state.remaining[0])
    state.environment.append((assignment.group(1), first[assignment.end() :]))
    state.remaining.pop(0)
    return True


def consume_command(state: ParseState) -> bool:
    if state.remaining[0].text not in {"command", "builtin"}:
        return False
    state.remaining.pop(0)
    while state.remaining and state.remaining[0].text.startswith("-") and state.remaining[0].text != "-":
        option = state.remaining.pop(0).text
        if option == "--":
            break
        if option == "--help" or "v" in option[1:] or "V" in option[1:]:
            state.invalid = True
            return True
    return True


def consume_shell(state: ParseState) -> bool:
    if state.remaining[0].text not in {"sh", "bash", "zsh", "dash", "ksh"}:
        return False
    state.remaining.pop(0)
    command_index = next(
        (
            index + 1
            for index, word in enumerate(state.remaining)
            if word.text.startswith("-") and not word.text.startswith("--") and "c" in word.text[1:]
        ),
        None,
    )
    if command_index is None:
        state.invalid = True
        return True
    command_word = state.remaining.pop(command_index)
    command_words = shell_words(command_word.text)
    if "$" in command_word.text or "`" in command_word.text or any(word.operator for word in command_words):
        state.dynamic = True
    state.remaining = command_words
    return True


def consume_nohup(state: ParseState) -> bool:
    if state.remaining[0].text != "nohup":
        return False
    state.remaining.pop(0)
    if state.remaining and state.remaining[0].text.startswith("-") and state.remaining[0].text != "--":
        state.dynamic = True
    while state.remaining and state.remaining[0].text.startswith("-"):
        if state.remaining[0].text == "--":
            state.remaining.pop(0)
            break
        state.remaining.pop(0)
    return True


def consume_sudo_long(state: ParseState, value_options: set[str]) -> tuple[bool, bool]:
    option = state.remaining[0].text
    name = option.split("=", 1)[0]
    shell_mode = "=" not in option and ("--shell".startswith(name) or "--login".startswith(name))
    takes_value = "=" not in option and any(candidate.startswith(name) for candidate in value_options)
    state.remaining.pop(0)
    if takes_value and state.remaining:
        value = state.remaining.pop(0).text
        if "--chdir".startswith(name) and not "--chroot".startswith(name):
            state.directory = value
    elif "=" in option and "--chdir".startswith(name) and not "--chroot".startswith(name):
        state.directory = option.split("=", 1)[1]
    return True, shell_mode


def consume_sudo_short(state: ParseState, value_options: set[str]) -> tuple[bool, bool]:
    grouped = state.remaining[0].text[1:]
    shell_mode = False
    for index, character in enumerate(grouped):
        if character in "lvV":
            state.invalid = True
            return True, shell_mode
        if character in "si":
            shell_mode = True
        if character in value_options:
            state.remaining.pop(0)
            if index == len(grouped) - 1:
                value = state.remaining.pop(0).text if state.remaining else ""
            else:
                value = grouped[index + 1 :]
            if character == "D":
                state.directory = value
            break
    else:
        state.remaining.pop(0)
    return True, shell_mode


def consume_sudo(state: ParseState) -> bool:
    if state.remaining[0].text != "sudo":
        return False
    state.remaining.pop(0)
    value_options = {
        "-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
        "-C", "--close-from", "-T", "--command-timeout", "-D", "--chdir",
        "-R", "--chroot", "-r", "--role", "-t", "--type", "-U", "--other-user",
    }
    long_values = {option for option in value_options if option.startswith("--")}
    short_values = {option[1] for option in value_options if option.startswith("-") and not option.startswith("--")}
    shell_mode = False
    while state.remaining and not state.invalid:
        option = state.remaining[0].text
        if option == "--":
            state.remaining.pop(0)
            break
        if option in {"-l", "--list", "-v", "--validate", "-V", "--version", "--help"}:
            state.invalid = True
            return True
        if option in {"-s", "--shell", "-i", "--login"}:
            shell_mode = True
            state.remaining.pop(0)
            continue
        if option in value_options:
            state.remaining.pop(0)
            if state.remaining:
                value = state.remaining.pop(0).text
                if option in {"-D", "--chdir"}:
                    state.directory = value
            continue
        if option.startswith("--"):
            _, option_shell = consume_sudo_long(state, long_values)
            shell_mode = shell_mode or option_shell
            continue
        if option.startswith("-") and len(option) > 1:
            _, option_shell = consume_sudo_short(state, short_values)
            shell_mode = shell_mode or option_shell
            continue
        break
    if shell_mode and state.remaining:
        command_word = state.remaining.pop(0)
        command_words = shell_words(command_word.text)
        if "$" in command_word.text or "`" in command_word.text or any(word.operator for word in command_words):
            state.dynamic = True
        state.remaining[0:0] = command_words
    return True


def split_env_value(state: ParseState, value: str) -> bool:
    state.dynamic = state.dynamic or has_env_expansion(value)
    split = split_env_string(value)
    if split is None:
        state.invalid = True
        return False
    state.remaining[0:0] = [Word(text, [False] * len(text), [False] * len(text)) for text in split]
    return True


def consume_env_long(state: ParseState) -> bool:
    candidate = state.remaining[0].text
    name, separator, suffix = candidate.partition("=")
    long_options = {
        "--help", "--version", "--split-string", "--unset", "--chdir",
        "--ignore-environment", "--null", "--debug", "--list-signal-handling",
        "--block-signal", "--default-signal", "--ignore-signal",
        "--env0-from", "--argv0",
    }
    matches = [option for option in long_options if option.startswith(name)]
    if len(matches) > 1:
        state.remaining.pop(0)
        return True
    if len(matches) == 1:
        if matches[0] in {"--env0-from", "--argv0"} and not separator:
            state.invalid = True
            return True
        candidate = matches[0] + (f"={suffix}" if separator else "")
    if candidate in {"--help", "--version"}:
        state.invalid = True
        return True
    if candidate in {"-S", "--split-string"}:
        state.remaining.pop(0)
        if not state.remaining:
            state.invalid = True
            return True
        value_word = state.remaining.pop(0)
        state.dynamic = state.dynamic or has_expansion(value_word)
        return split_env_value(state, value_word.text)
    if candidate.startswith("--split-string="):
        option_word = state.remaining.pop(0)
        state.dynamic = state.dynamic or has_expansion(option_word)
        return split_env_value(state, candidate.split("=", 1)[1])
    if candidate in {"-u", "--unset", "-C", "--chdir"}:
        option = state.remaining.pop(0).text
        if state.remaining:
            value = state.remaining.pop(0).text
            if option in {"-C", "--chdir"}:
                state.directory = value
        return True
    if candidate.startswith(("--unset=", "--chdir=", "--env0-from=", "--argv0=")):
        if candidate.startswith("--chdir="):
            state.directory = candidate.split("=", 1)[1]
        state.remaining.pop(0)
        return True
    if candidate in {
        "-i", "-0", "-v", "-", "--ignore-environment", "--null", "--debug",
        "--list-signal-handling", "--block-signal", "--default-signal", "--ignore-signal",
    } or candidate.startswith(("--block-signal=", "--default-signal=", "--ignore-signal=")):
        state.remaining.pop(0)
        return True
    return False


def consume_env_short(state: ParseState) -> bool:
    candidate = state.remaining[0].text
    state.dynamic = state.dynamic or has_expansion(state.remaining[0]) or has_env_expansion(candidate)
    grouped = candidate[1:]
    for index, character in enumerate(grouped):
        if character == "S":
            state.remaining.pop(0)
            if index == len(grouped) - 1:
                if not state.remaining:
                    state.invalid = True
                    return True
                value_word = state.remaining.pop(0)
                state.dynamic = state.dynamic or has_expansion(value_word)
                value = value_word.text
            else:
                value = grouped[index + 1 :]
            return split_env_value(state, value)
        if character in "uaCP":
            state.remaining.pop(0)
            if index == len(grouped) - 1:
                value = state.remaining.pop(0).text if state.remaining else ""
            else:
                value = grouped[index + 1 :]
            if character == "C":
                state.directory = value
            return True
    state.remaining.pop(0)
    return True


def consume_env(state: ParseState) -> bool:
    if state.remaining[0].text != "env":
        return False
    state.remaining.pop(0)
    while state.remaining and not state.invalid:
        candidate = state.remaining[0].text
        if consume_assignment(state):
            continue
        if candidate == "--":
            state.remaining.pop(0)
            break
        if candidate.startswith("--"):
            if consume_env_long(state):
                continue
            break
        if candidate.startswith("-") and len(candidate) > 1:
            consume_env_short(state)
            continue
        break
    return True


def consume_rtk(state: ParseState) -> bool:
    if state.remaining[0].text != "rtk" or len(state.remaining) < 2 or state.remaining[1].text != "proxy":
        return False
    state.remaining.pop(0)
    state.remaining.pop(0)
    return True


def finish_invocation(state: ParseState) -> Invocation | None:
    if state.invalid:
        return None
    if not state.remaining:
        if state.environment:
            return Invocation("", (), False, tuple(state.environment), state.directory)
        return None
    return Invocation(
        Path(state.remaining[0].text).name,
        tuple(state.remaining[1:]),
        state.dynamic or any(has_expansion(word) for word in state.remaining),
        tuple(state.environment),
        state.directory,
    )


def invocation_from_words(words: list[Word]) -> Invocation | None:
    state = ParseState(list(words))
    handlers = (consume_assignment, consume_command, consume_shell, consume_nohup, consume_sudo, consume_env, consume_rtk)
    while state.remaining and not state.invalid:
        for handler in handlers:
            if handler(state):
                break
        else:
            break
    return finish_invocation(state)


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
