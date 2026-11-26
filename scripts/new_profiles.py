"""Profile selection and pinned includes for the new-repository command."""

import json
import re
import tomllib
from pathlib import Path

from adopt_common import git, problem
from new_common import OWNER
from profiles_reader import (
    AMENDS,
    FACTORY_INCLUDE,
    PROFILES_LINE,
    PROFILE_PATH,
    TAG,
    factory_ref,
)

SIMPLE_ENTRY = re.compile(
    r'^(?P<indent>\s*)(?P<quote>["\'])(?P<value>[^"\']*)(?P=quote)'
    r"\s*,?\s*(?P<comment>#.*)?$"
)


def config_array(text, key, section=None):
    """Return the (start, end) offsets of key's array in a JSON-like TOML file."""
    offset = 0
    current = None
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        heading = re.match(r"^\[([^\]]+)\]\s*(?:#.*)?$", stripped)
        if heading:
            current = heading.group(1).strip()
        in_scope = (section is None and current is None) or current == section
        if in_scope and re.match(rf"^{re.escape(key)}\s*=\s*\[", stripped):
            start = offset + line.index("[")
            depth = 0
            index = start
            while index < len(text):
                character = text[index]
                if character == '"':
                    index += 1
                    while index < len(text) and text[index] != '"':
                        index += 2 if text[index] == "\\" else 1
                elif character == "[":
                    depth += 1
                elif character == "]":
                    depth -= 1
                    if depth == 0:
                        return start, index
                index += 1
            problem(f"{key} has no closing bracket")
        offset += len(line)
    return None


def array_values(text, key, section=None):
    data = tomllib.loads(text)
    if section is None:
        return data.get(key, [])
    return (data.get(section) or {}).get(key, [])


def with_trailing_comma(inner):
    lines = inner.splitlines(keepends=True)
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        body = line[: -len(ending)] if ending else line
        match = re.match(r'^(\s*"[^"]*")\s*(,?)\s*(#.*)?$', body)
        if match:
            comma = match.group(2) or ","
            comment = f" {match.group(3)}" if match.group(3) else ""
            lines[index] = match.group(1) + comma + comment + ending
        break
    return "".join(lines)


def same_profile(entry, profile, kind):
    if not FACTORY_INCLUDE.match(entry):
        return False
    match = PROFILE_PATH.search(entry)
    return bool(match) and match.group(1) == profile and f"/{kind}.toml" in entry


def expected_values(values, url, same):
    expected = []
    placed = False
    for entry in values:
        if same(entry):
            if not placed:
                expected.append(url)
                placed = True
        else:
            expected.append(entry)
    if not placed:
        expected.append(url)
    return expected


def rewrite_pin_lines(inner, url, same):
    output = []
    handled = 0
    replaced = False
    for line in inner.splitlines(keepends=True):
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        body = line[: -len(ending)] if ending else line
        match = SIMPLE_ENTRY.match(body)
        if not match or not same(match.group("value")):
            output.append(line)
            continue
        handled += 1
        if not replaced:
            comment = f' {match.group("comment")}' if match.group("comment") else ""
            output.append(f'{match.group("indent")}{json.dumps(url)},{comment}{ending}')
            replaced = True
        elif match.group("comment"):
            output.append(f'{match.group("indent")}{match.group("comment")}{ending}')
    return "".join(output), handled, replaced


def append_pin(inner, url, newline):
    indent = "  "
    for line in inner.splitlines(keepends=True):
        match = re.match(r'^(\s*)"', line)
        if match:
            indent = match.group(1)
            break
    if not inner.endswith(("\n", "\r")):
        inner += newline
    return with_trailing_comma(inner) + f'{indent}{json.dumps(url)},{newline}'


def ensure_include(text, key, section, url, profile):
    values = array_values(text, key, section)
    kind = "tasks" if "/tasks.toml?" in url else "mise"

    def same(entry):
        return same_profile(entry, profile, kind)

    if [entry for entry in values if same(entry)] == [url]:
        return text, False
    expected = expected_values(values, url, same)
    span = config_array(text, key, section)
    if span is None:
        problem(f"the Project Template's {key} array is missing")
    start, end = span
    inner = text[start + 1 : end]
    newline = "\r\n" if "\r\n" in text else "\n"
    total = sum(1 for entry in values if same(entry))
    rewritten, handled, replaced = rewrite_pin_lines(inner, url, same)
    if "\n" in inner and handled == total and (replaced or not total):
        output = rewritten if replaced else append_pin(inner, url, newline)
        updated = text[: start + 1] + output + text[end:]
    else:
        if re.search(r"(?m)(^|\s)#", inner):
            problem(f"cannot update the {key} pin without deleting a comment; add it by hand")
        body = "".join(f"  {json.dumps(entry)},\n" for entry in expected)
        updated = text[: start + 1] + "\n" + body + text[end:]
    try:
        if array_values(updated, key, section) != expected:
            problem(f"the {key} pin did not survive the merge")
    except tomllib.TOMLDecodeError as error:
        problem(f"mise.toml could not be merged safely: {error}")
    return updated, True


def include_urls(profile, tag):
    base = f"git::https://github.com/{OWNER}/factory.git//profiles/{profile}"
    return (
        f"{base}/mise.toml?ref={tag}",
        f"{base}/tasks.toml?ref={tag}",
    )


def hk_url(profile, tag):
    return (
        f"https://raw.githubusercontent.com/{OWNER}/factory/{tag}"
        f"/profiles/{profile}/hk.pkl"
    )


def release_tag(factory):
    tags = git(factory, "tag", "--list", "v*", "--sort=-v:refname", "--merged", "HEAD")
    if tags.returncode == 0:
        for line in tags.stdout.splitlines():
            if TAG.match(line.strip()):
                return line.strip()
    try:
        data = tomllib.loads((factory / "project/mise.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        problem(f"cannot read the Project Template's mise.toml: {error}")
    for entry in data.get("include", []):
        tag = factory_ref(entry)
        if tag is None:
            continue
        if not TAG.match(tag):
            problem(f"the Project Template pins {tag!r}, not a Factory release tag")
        return tag
    problem("neither the Factory tags nor the Project Template name a release")


def profile_parents(factory):
    parents = {}
    for profile in sorted(path for path in (factory / "profiles").iterdir() if path.is_dir()):
        try:
            text = (profile / "hk.pkl").read_text()
        except OSError as error:
            problem(f"cannot read {profile}/hk.pkl: {error}")
        match = AMENDS.search(text)
        if match and match.group(1).startswith("../"):
            parents[profile.name] = Path(match.group(1)).parts[1]
    return parents


def profile_chain(names, factory):
    available = {path.name for path in (factory / "profiles").iterdir() if path.is_dir()}
    parents = profile_parents(factory)
    selected = set()

    def add(name):
        if name in selected:
            return
        selected.add(name)
        parent = parents.get(name)
        if parent:
            add(parent)

    for name in names:
        if name not in available:
            problem(f"unknown Profile {name!r}; available: {', '.join(sorted(available))}")
        add(name)
    is_parent = {parents[name] for name in selected if name in parents}
    leaves = sorted(selected - is_parent)
    if len(leaves) != 1:
        problem(
            "Profiles "
            + ", ".join(leaves)
            + " need different hk.pkl amends; choose one Profile chain"
        )
    chain = []
    current = leaves[0]
    while current:
        chain.append(current)
        current = parents.get(current)
    chain.reverse()
    return chain


def profiles_line(text):
    fenced = False
    for index, line in enumerate(text.splitlines(keepends=True)):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced and PROFILES_LINE.match(line):
            return index
    return None


def plan_profiles_line(path, chain, may_rewrite):
    text = path.read_text()
    names = ", ".join(chain)
    index = profiles_line(text)
    if index is None:
        if not may_rewrite:
            problem("AGENTS.md has no Profiles line; add it by hand")
        lines = text.splitlines(keepends=True)
        first = next((i for i, line in enumerate(lines) if line.strip()), None)
        if first is None or not lines[first].startswith("# "):
            problem("AGENTS.md does not start with a heading; add the Profiles line by hand")
        newline = "\r\n" if "\r\n" in text else "\n"
        lines[first + 1 : first + 1] = [newline, f"Profiles: {names}{newline}"]
        return "".join(lines)
    lines = text.splitlines(keepends=True)
    existing = [
        entry.strip()
        for entry in PROFILES_LINE.match(lines[index]).group(1).split(",")
        if entry.strip()
    ]
    if existing == chain:
        return None
    if not may_rewrite:
        problem(
            f"AGENTS.md declares Profiles: {', '.join(existing)}; "
            "new leaves an existing project's Profiles line alone"
        )
    newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
    indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
    lines[index] = f"{indent}Profiles: {names}{newline}"
    return "".join(lines)


def plan_hk_amends(path, profile, tag, may_rewrite):
    target = hk_url(profile, tag)
    if not path.exists():
        return f'amends "{target}"\n'
    text = path.read_text()
    match = AMENDS.search(text)
    if match is None:
        problem("hk.pkl has no amends line; add the Profile amends by hand")
    existing = re.search(r"/profiles/([^/]+)/hk\.pkl", match.group(1))
    existing_ref = factory_ref(match.group(1))
    if existing and existing.group(1) == profile and existing_ref == tag:
        return None
    if not may_rewrite:
        if existing and existing.group(1) == profile:
            problem(
                f"hk.pkl pins {match.group(1)!r} but the Profiles pin {target!r}; align them"
            )
        problem(
            f"hk.pkl amends {match.group(1)!r}; new leaves an existing project's hk.pkl alone"
        )
    return text[: match.start(1)] + target + text[match.end(1) :]


def apply_profiles(clone, chain, tag, agents_absent, hk_absent):
    agents_path = clone / "AGENTS.md"
    agents_text = plan_profiles_line(agents_path, chain, agents_absent)
    mise_path = clone / "mise.toml"
    if not mise_path.is_file():
        problem("the Project Template did not install mise.toml")
    original = mise_path.read_text()
    text = original
    for profile in chain:
        mise_url, tasks_url = include_urls(profile, tag)
        text, _ = ensure_include(text, "include", None, mise_url, profile)
        text, _ = ensure_include(text, "includes", "task_config", tasks_url, profile)
    hk_path = clone / "hk.pkl"
    hk_text = plan_hk_amends(hk_path, chain[-1], tag, hk_absent)
    changed = []
    if agents_text is not None:
        agents_path.write_text(agents_text)
        changed.append("AGENTS.md")
    if text != original:
        mise_path.write_text(text)
        changed.append("mise.toml")
    if hk_text is not None:
        hk_path.write_text(hk_text)
        changed.append("hk.pkl")
    if changed:
        print(f"new: wrote {', '.join(changed)}")


def existing_tag(clone):
    path = clone / "mise.toml"
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        problem(f"cannot read the existing mise.toml: {error}")
    for entry in data.get("include", []):
        ref = factory_ref(entry)
        if ref and "/profiles/" in entry and TAG.match(ref):
            return ref
    return None


def existing_hk_tag(clone):
    path = clone / "hk.pkl"
    if not path.is_file():
        return None
    match = AMENDS.search(path.read_text())
    if match is None:
        return None
    ref = factory_ref(match.group(1))
    return ref if ref and TAG.match(ref) else None


def check_existing_pins(clone):
    mise_tag = existing_tag(clone)
    hk_tag = existing_hk_tag(clone)
    if mise_tag and hk_tag and mise_tag != hk_tag:
        problem(f"mise.toml pins {mise_tag} but hk.pkl pins {hk_tag}; align them")


__all__ = [
    "apply_profiles",
    "check_existing_pins",
    "existing_hk_tag",
    "existing_tag",
    "release_tag",
    "profile_chain",
]
