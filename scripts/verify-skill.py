#!/usr/bin/env python3
"""Check a project's verify-<app> skill and CLI against the Factory shape.

The checker starts nothing. It reads the skill and feature map, then invokes the
mise task for `commands`, `doctor`, every destructive command with `--dry-run`,
and an unknown command. Doctor may fail when the app is stopped; that failure must
carry an actionable alternative.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

SKILL_ROOT = Path(".agents/skills")
CLAUDE_ROOT = Path(".claude/skills")
REQUIRED_SECTIONS = ("Launch", "Doctor", "Drive", "Evidence", "Cleanup")
FEATURE_SECTIONS = ("Sub-features", "How to get to it (user POV)", "Gotchas")
DRIVING_PREFIX = "Driving it with "
SKIP_PARTS = {".git", ".mise", ".venv", "__pycache__", "node_modules"}
TIMEOUT = 90


class Problem(Exception):
    pass


def problem(message):
    raise Problem(message)


def h2_sections(text):
    return [match.group(1).strip() for match in re.finditer(r"^##\s+(.+?)\s*$", text, re.MULTILINE)]


def frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        problem("SKILL.md must start with YAML frontmatter")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index])
    problem("SKILL.md frontmatter is not closed with ---")


def frontmatter_value(front, key):
    match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", front, re.MULTILINE)
    return match.group(1).strip().strip("\"'") if match else None


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(root):
    entries = {}
    for base, directories, files in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in SKIP_PARTS)
        for name in sorted(files):
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            entries[relative] = "link:" + os.readlink(path) if path.is_symlink() else sha256(path)
    return entries


def differing(before, after):
    changed = set(before) ^ set(after)
    changed |= {path for path in set(before) & set(after) if before[path] != after[path]}
    return sorted(changed)


def run_task(root, task, *args, environment=None):
    mise = shutil.which("mise")
    if mise is None:
        problem("mise is not on PATH; install it before running the shape check")
    env = dict(os.environ)
    if environment:
        env.update(environment)
    try:
        return subprocess.run(
            [mise, "--cd", str(root), "run", task, "--", *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        problem(f"`mise run {task} -- {' '.join(args)}` did not finish within {TIMEOUT}s")
    except OSError as error:
        problem(f"cannot invoke `mise run {task}`: {error}")


def parse_json(result, label):
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        problem(f"{label}: stdout is not one JSON object (exit {result.returncode}): {result.stdout.strip()[:200]!r}")
    if not isinstance(data, dict):
        problem(f"{label}: stdout JSON must be an object")
    return data


def check_document(root, skill, task):
    entry = skill / "SKILL.md"
    if not entry.is_file():
        problem(f"{skill.relative_to(root)}/SKILL.md is missing")
    text = entry.read_text()
    front = frontmatter(text)
    name = frontmatter_value(front, "name")
    if name != skill.name:
        problem(f"SKILL.md frontmatter name must be {skill.name}, found {name!r}")
    if not frontmatter_value(front, "description"):
        problem("SKILL.md frontmatter has no description")
    sections = h2_sections(text)
    for heading in REQUIRED_SECTIONS:
        if heading not in sections:
            problem(f"SKILL.md is missing the `## {heading}` section")
    if f"mise run {task}" not in text:
        problem(f"SKILL.md does not show `mise run {task}`")
    return text


def check_features(root, skill):
    features = skill / "references/features"
    readme = features / "README.md"
    if not readme.is_file():
        problem(f"{features.relative_to(root)}/README.md is missing")
    links = re.findall(r"\]\(([^)]+?\.md)\)", readme.read_text())
    indexed = set()
    for link in links:
        target = (readme.parent / link).resolve()
        if not target.is_file():
            problem(f"features/README.md links a missing file: {link}")
        indexed.add(target)
    files = sorted(path for path in features.glob("*.md") if path.name != "README.md")
    if not files:
        problem("references/features/ has no feature files")
    for path in files:
        if path.resolve() not in indexed:
            problem(f"{path.relative_to(root)} is not indexed by features/README.md")
        feature_sections = h2_sections(path.read_text())
        for heading in FEATURE_SECTIONS:
            if heading not in feature_sections:
                problem(f"{path.relative_to(root)} is missing the `## {heading}` section")
        if not any(heading.startswith(DRIVING_PREFIX) for heading in feature_sections):
            problem(f"{path.relative_to(root)} is missing a `## {DRIVING_PREFIX}<harness>` section")
    return len(files)


def check_claude_link(root, skill):
    link = root / CLAUDE_ROOT / skill.name
    if not link.is_symlink():
        problem(f"{link.relative_to(root)} must be a relative symlink to the skill for Claude Code")
    stored = os.readlink(link)
    if os.path.isabs(stored):
        problem(f"{link.relative_to(root)} must be a relative symlink")
    if (link.parent / stored).resolve() != skill.resolve():
        problem(f"{link.relative_to(root)} does not resolve to {skill.relative_to(root)}")


def check_task(root, task):
    manifest = root / "mise.toml"
    if not manifest.is_file():
        problem("mise.toml is missing")
    try:
        tasks = tomllib.loads(manifest.read_text()).get("tasks") or {}
    except tomllib.TOMLDecodeError as error:
        problem(f"mise.toml is not valid TOML: {error}")
    if task not in tasks:
        problem(f"""mise.toml has no [tasks."{task}"] entry""")
    entry = tasks[task]
    command = entry.get("run") if isinstance(entry, dict) else entry
    if not command:
        problem(f"""[tasks."{task}"] has no run command""")


def process_snapshot():
    result = subprocess.run(["ps", "-axo", "pid=,uid=,command="], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        problem("cannot inspect processes with ps")
    processes = {}
    for line in result.stdout.splitlines():
        pid, _, rest = line.strip().partition(" ")
        uid, _, command = rest.strip().partition(" ")
        if pid.isdigit() and uid.isdigit():
            processes[int(pid)] = (int(uid), command.strip())
    return processes


def terminate_process(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError as error:
        problem(f"cannot stop leaked process {pid}: {error}")
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError as error:
        problem(f"cannot stop leaked process {pid}: {error}")
    try:
        os.kill(pid, 0)
    except OSError:
        return
    problem(f"leaked process {pid} survived SIGKILL")


def leaked_processes(before, root):
    after = process_snapshot()
    marker = str(root) + os.sep
    return [
        (pid, command)
        for pid, (uid, command) in after.items()
        if pid not in before
        and uid == os.getuid()
        and (str(root) in command and (marker in command or command.endswith(str(root))))
    ]


def check_cli(root, task, text):
    sandbox = tempfile.mkdtemp(prefix="verify-skill-")
    environment = {
        "TMPDIR": sandbox,
        "TMP": sandbox,
        "TEMP": sandbox,
    }
    try:
        return check_cli_in(root, task, text, sandbox, environment)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def check_cli_in(root, task, text, sandbox, environment):
    inventory = parse_json(run_task(root, task, "commands", environment=environment), "commands")
    if inventory.get("ok") is not True:
        problem("commands must report ok: true")
    commands = inventory.get("commands")
    if not isinstance(commands, list) or not commands:
        problem("commands must return a non-empty commands list")
    names = {}
    for item in commands:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("destructive"), bool):
            problem("every commands entry needs a string name and a boolean destructive")
        names[item["name"]] = item["destructive"]
        if not re.search(rf"\b{re.escape(item['name'])}\b", text):
            problem(f"SKILL.md does not document the `{item['name']}` subcommand")
    if "doctor" not in names or names["doctor"]:
        problem("commands must list doctor as a non-destructive subcommand")
    destructive = sorted(name for name, is_destructive in names.items() if is_destructive)

    doctor = run_task(root, task, "doctor", environment=environment)
    payload = parse_json(doctor, "doctor")
    if doctor.returncode != 0:
        if payload.get("ok") is not False:
            problem("a failing doctor must report ok: false")
        if not payload.get("error"):
            problem("a failing doctor must report an error")
        if not payload.get("alternative"):
            problem("a failing doctor must report an alternative command")
    elif payload.get("ok") is not True:
        problem("doctor must report ok: true when it exits zero")

    before = snapshot(root)
    before_sandbox = snapshot(sandbox)
    before_processes = process_snapshot()
    for name in destructive:
        result = run_task(root, task, name, "--dry-run", environment=environment)
        label = f"{name} --dry-run"
        dry = parse_json(result, label)
        sandbox_changes = differing(before_sandbox, snapshot(sandbox))
        leaked = leaked_processes(before_processes, root)
        for pid, _ in leaked:
            terminate_process(pid)
        if leaked:
            problem(f"{label} left a process running: {leaked[0][0]} {leaked[0][1]}")
        if sandbox_changes:
            problem(f"{label} changed its temp state: {', '.join(sandbox_changes[:5])}")
        if result.returncode != 0:
            problem(f"{label} exited {result.returncode}; a dry-run must succeed")
        if dry.get("dry_run") is not True:
            problem(f"{label} did not report dry_run: true")
        changed = differing(before, snapshot(root))
        if changed:
            problem(f"{label} changed the tree: {', '.join(changed[:5])}")

    invalid = run_task(root, task, "doctor", "--not-an-argument", environment=environment)
    invalid_error = parse_json(invalid, "invalid argument")
    if invalid.returncode == 0:
        problem("an invalid argument must exit non-zero")
    if invalid_error.get("ok") is not False:
        problem("an invalid argument must report ok: false")
    if not invalid_error.get("error") or not invalid_error.get("alternative"):
        problem("an invalid argument must report an error and an alternative")

    unknown = run_task(root, task, "__not_a_command__", environment=environment)
    error = parse_json(unknown, "unknown command")
    if unknown.returncode == 0:
        problem("an unknown command must exit non-zero")
    if error.get("ok") is not False:
        problem("an unknown command must report ok: false")
    if not error.get("error") or not error.get("alternative"):
        problem("an unknown command must report an error and an alternative")
    alternative = str(error["alternative"])
    if "mise run" not in alternative and not any(name in alternative for name in names):
        problem("an unknown command's alternative must name a command to run instead")
    return len(names)


def check_skill(root, skill):
    task = f"verify:{skill.name.removeprefix('verify-')}"
    text = check_document(root, skill, task)
    features = check_features(root, skill)
    check_claude_link(root, skill)
    check_task(root, task)
    commands = check_cli(root, task, text)
    return skill.name, commands, features


def check(root):
    root = root.resolve()
    if (root / ".cursor/skills").is_dir() and any((root / ".cursor/skills").glob("verify-*")):
        problem("verification skills belong in .agents/skills, not .cursor/skills")
    skills = sorted(path for path in (root / SKILL_ROOT).glob("verify-*") if path.is_dir())
    if not skills:
        problem(f"no {SKILL_ROOT}/verify-* directory found")
    if len(skills) > 1:
        names = ", ".join(path.name for path in skills)
        problem(f"several verification skills found ({names}); keep one per project")
    return check_skill(root, skills[0])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="project root to check")
    args = parser.parse_args(argv)
    try:
        name, commands, features = check(args.root)
    except Problem as error:
        print(f"verify-skill: {args.root}: {error}", file=sys.stderr)
        return 1
    print(f"verify-skill: {args.root}: ok ({name}, {commands} commands, {features} features)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
