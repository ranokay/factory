#!/usr/bin/env python3
"""Link this Mac's harness config to the Factory and verify every harness sees it.

Discovery paths come from the harness documentation:
- Claude Code: ~/.claude/CLAUDE.md and ~/.claude/skills (CHANGELOG v2.1.0).
- Codex: $CODEX_HOME/AGENTS.md (AGENTS.override.md wins) and ~/.agents/skills,
  plus the deprecated $CODEX_HOME/skills root; .system is Codex-managed.
- OpenCode: ~/.config/opencode/AGENTS.md and the skills roots
  ~/.config/opencode/skills, ~/.claude/skills and ~/.agents/skills.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_SKILLS_FILE = ROOT / "scripts" / "tool-skills.txt"
CTX7_VERSION = "0.12.2"
CODEX_MANAGED_SKILLS = {".system"}


@dataclass(frozen=True)
class Link:
    path: Path
    target: Path
    action: str = ""


def display(path: Path, home: Path) -> str:
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return str(path)


def desired_links(home: Path) -> list[Link]:
    agents = home / ".agents"
    return [
        Link(agents, ROOT),
        Link(home / ".claude" / "skills", ROOT / "skills"),
        Link(home / ".config" / "opencode" / "skills", ROOT / "skills"),
        Link(home / ".claude" / "CLAUDE.md", agents / "AGENTS.md"),
        Link(home / ".codex" / "AGENTS.md", agents / "AGENTS.md"),
        Link(home / ".config" / "opencode" / "AGENTS.md", agents / "AGENTS.md"),
    ]


def resolved_link(path: Path) -> Path | None:
    if not path.is_symlink():
        return None
    stored = Path(os.readlink(path))
    if not stored.is_absolute():
        stored = path.parent / stored
    return stored.resolve(strict=False)


def plan_links(home: Path) -> tuple[list[Link], list[Link]]:
    changes = []
    blocked = []
    for link in desired_links(home):
        if link.path.is_symlink():
            if resolved_link(link.path) == link.target.resolve(strict=False):
                continue
            changes.append(Link(link.path, link.target, "repoint"))
        elif os.path.lexists(link.path):
            changes.append(Link(link.path, link.target, "replace"))
            blocked.append(link)
        else:
            changes.append(Link(link.path, link.target, "link"))
    return changes, blocked


def backup_path(path: Path, stamp: str) -> Path:
    candidate = path.with_name(f"{path.name}.backup-{stamp}")
    suffix = 2
    while os.path.lexists(candidate):
        candidate = path.with_name(f"{path.name}.backup-{stamp}-{suffix}")
        suffix += 1
    return candidate


def apply_links(changes: list[Link], home: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for change in changes:
        change.path.parent.mkdir(parents=True, exist_ok=True)
        if change.action == "replace":
            backup = backup_path(change.path, stamp)
            change.path.rename(backup)
            print(f"bootstrap: backed up {display(change.path, home)} -> {display(backup, home)}")
        elif change.action == "repoint":
            change.path.unlink()
        os.symlink(change.target, change.path)
        print(f"bootstrap: {change.action} {display(change.path, home)} -> {display(change.target, home)}")


def read_tool_skills() -> list[str]:
    names = []
    for line in TOOL_SKILLS_FILE.read_text().splitlines():
        name = line.strip()
        if name and not name.startswith("#"):
            names.append(name)
    return names


def skill_present(name: str) -> bool:
    return (ROOT / "skills" / name / "SKILL.md").is_file()


def run_installer(label: str, argv: list[str], home: Path) -> bool:
    executable = shutil.which(argv[0])
    if executable is None:
        print(f"bootstrap: warning: {label} is not installed; skipping its skills")
        return False
    argv = [executable, *argv[1:]]
    print(f"bootstrap: install {label}: {' '.join(argv)}")
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    result = subprocess.run(argv, env=environment, check=False)
    if result.returncode != 0:
        print(f"bootstrap: {label} installer failed with exit {result.returncode}", file=sys.stderr)
        return False
    return True


def install_tool_skills(home: Path) -> list[str]:
    errors = []
    if not skill_present("ardia"):
        run_installer("ardia", ["ardia", "skills", "install", "--target", "general", "--no-cua"], home)
    if not skill_present("ardia"):
        errors.append(f"ardia: {display(ROOT / 'skills' / 'ardia' / 'SKILL.md', home)} is missing")
    cua_names = ["cua-driver", "cua-sandboxes", "cua-spaces", "cua-volume", "gui-automation"]
    if not all(skill_present(name) for name in cua_names):
        run_installer("cua", ["cua", "connect", "setup", "--skills-only", "--agents", "claude", "-y"], home)
    for name in cua_names:
        if not skill_present(name):
            errors.append(f"cua: {display(ROOT / 'skills' / name / 'SKILL.md', home)} is missing")
    if not skill_present("find-docs"):
        if shutil.which("ctx7"):
            run_installer("ctx7", ["ctx7", "setup", "--claude", "--cli", "-y"], home)
        else:
            run_installer(
                "ctx7", ["npx", "--yes", f"ctx7@{CTX7_VERSION}", "setup", "--claude", "--cli", "-y"], home
            )
    if not skill_present("find-docs"):
        errors.append(f"ctx7: {display(ROOT / 'skills' / 'find-docs' / 'SKILL.md', home)} is missing")
    if not errors:
        print("bootstrap: tool-owned skills are installed")
    return errors


def factory_skill_names() -> set[str]:
    return {
        path.name
        for path in (ROOT / "skills").iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }


def codex_root_errors(home: Path) -> list[str]:
    root = home / ".codex" / "skills"
    if not root.is_dir():
        return []
    errors = []
    factory_skills = ROOT / "skills"
    for entry in sorted(root.iterdir()):
        if entry.name in CODEX_MANAGED_SKILLS:
            continue
        if entry.is_symlink():
            resolved = entry.resolve(strict=False)
            if resolved == factory_skills or factory_skills in resolved.parents:
                continue
            if not entry.exists():
                errors.append(f"codex: {display(entry, home)} is a broken symlink")
                continue
        if not entry.is_dir():
            continue
        if any(entry.rglob("SKILL.md")):
            errors.append(
                f"codex: {display(entry, home)} holds skills Codex does not manage; "
                "remove it or move it out of ~/.codex/skills"
            )
    return errors


def discovery(home: Path) -> int:
    errors = []
    factory_agents = ROOT / "AGENTS.md"
    factory_skills = ROOT / "skills"
    if not factory_agents.is_file():
        print(f"bootstrap: missing {factory_agents}", file=sys.stderr)
        return 1
    if not factory_skills.is_dir():
        print(f"bootstrap: missing {factory_skills}", file=sys.stderr)
        return 1
    expected_names = factory_skill_names()
    harnesses = {
        "claude-code": {
            "instructions": [home / ".claude" / "CLAUDE.md"],
            "skills": [home / ".claude" / "skills"],
            "shadows": [],
        },
        "opencode": {
            "instructions": [home / ".config" / "opencode" / "AGENTS.md"],
            "skills": [
                home / ".config" / "opencode" / "skills",
                home / ".claude" / "skills",
                home / ".agents" / "skills",
            ],
            "shadows": [],
        },
        "codex": {
            "instructions": [home / ".codex" / "AGENTS.md"],
            "skills": [home / ".agents" / "skills"],
            "shadows": [home / ".codex" / "AGENTS.override.md"],
        },
    }
    discovered = {}
    for harness, paths in harnesses.items():
        names = set()
        for path in paths["instructions"]:
            if not path.exists():
                errors.append(f"{harness}: {display(path, home)} is missing")
                continue
            if not path.samefile(factory_agents):
                errors.append(
                    f"{harness}: {display(path, home)} does not resolve to "
                    f"{display(factory_agents, home)}"
                )
        for path in paths["shadows"]:
            if path.is_file() and path.stat().st_size > 0:
                errors.append(
                    f"{harness}: {display(path, home)} shadows the global AGENTS.md; "
                    "remove it or keep it empty"
                )
        for path in paths["skills"]:
            if not path.exists():
                errors.append(f"{harness}: {display(path, home)} is missing")
                continue
            if not path.samefile(factory_skills):
                errors.append(
                    f"{harness}: {display(path, home)} does not resolve to "
                    f"{display(factory_skills, home)}"
                )
                continue
            names |= {
                entry.name
                for entry in path.iterdir()
                if entry.is_dir() and (entry / "SKILL.md").is_file()
            }
        discovered[harness] = names
        if names != expected_names:
            missing = ", ".join(sorted(expected_names - names)) or "none"
            extra = ", ".join(sorted(names - expected_names)) or "none"
            errors.append(f"{harness}: skill set differs from the Factory (missing: {missing}; extra: {extra})")
    errors.extend(codex_root_errors(home))
    for harness, names in sorted(discovered.items()):
        other = next((key for key, value in sorted(discovered.items()) if key != harness and value != names), None)
        if other:
            errors.append(f"{harness} and {other} do not see the same skill set")
    if errors:
        for error in errors:
            print(f"bootstrap: {error}", file=sys.stderr)
        print(f"bootstrap: discovery check failed with {len(errors)} problem(s)", file=sys.stderr)
        return 1
    print(
        "bootstrap: discovery check passed for claude-code, opencode and codex "
        f"({len(expected_names)} skills)"
    )
    return 0


def check_tool_skills(root: Path) -> int:
    names = read_tool_skills()
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--", *[f"skills/{name}" for name in names]],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"bootstrap: cannot list tracked files in {root}: {result.stderr.strip()}", file=sys.stderr)
        return 1
    tracked = [line for line in result.stdout.splitlines() if line]
    if tracked:
        for path in tracked:
            print(f"bootstrap: tool-owned skill is tracked: {path}", file=sys.stderr)
        return 1
    print("bootstrap: no tool-owned skill is tracked")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home(), help="home directory to bootstrap")
    parser.add_argument("--check", action="store_true", help="run only the discovery check")
    parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="replace real files and directories at managed paths, after a dated backup",
    )
    parser.add_argument("--check-tool-skills", action="store_true", help="fail if a tool-owned skill is tracked")
    parser.add_argument("--root", type=Path, default=ROOT, help="checkout to inspect with --check-tool-skills")
    args = parser.parse_args(argv)
    home = args.home.expanduser().resolve()
    if args.check_tool_skills:
        return check_tool_skills(args.root.resolve())
    if not home.is_dir():
        print(f"bootstrap: home directory does not exist: {home}", file=sys.stderr)
        return 1
    if args.check:
        return discovery(home)
    print(f"bootstrap: Factory checkout {ROOT}")
    changes, blocked = plan_links(home)
    if blocked and not args.adopt_existing:
        print("bootstrap: refusing to replace real paths:", file=sys.stderr)
        for link in blocked:
            print(f"  {display(link.path, home)}", file=sys.stderr)
        print("Approve with: mise run bootstrap -- --adopt-existing", file=sys.stderr)
        return 2
    apply_links(changes, home)
    errors = install_tool_skills(home)
    discovery_code = discovery(home)
    for error in errors:
        print(f"bootstrap: {error}", file=sys.stderr)
    if errors or discovery_code != 0:
        return 1
    if not changes and not errors:
        print("Already bootstrapped: no changes.")
    else:
        print(f"bootstrap: complete with {len(changes)} link change(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
