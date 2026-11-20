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
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_SKILLS_FILE = ROOT / "scripts" / "tool-skills.txt"
REQUIRED_TOOL_GROUPS = ("ardia", "cua", "ctx7", "rea", "gh-stack")
GH_STACK_PIN = "d4ab7ab47e5b3e3708a27c8c42abcdf4bc321419"
CODEX_MANAGED_SKILLS = {".system"}
# Claude Code's claude.ai sync cache: skills/synced/<bucket>/<skill>/SKILL.md.
SYNCED_SKILLS = "synced"
SECRET_LABEL = "dev.factory.secrets"
SECRET_RETRY_SECONDS = 60
SECRET_LOG = Path("Library") / "Logs" / "factory-secrets.log"
AGENT_SOCKET = Path("Library") / "Group Containers" / "2BUA8C4S2C.com.1password" / "t" / "agent.sock"
SSH_MARKER = "# Factory: github.com through the 1Password SSH agent"
GITHUB_HTTPS = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$")
GITHUB_SSH = re.compile(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$")


@dataclass(frozen=True)
class Link:
    path: Path
    target: Path


@dataclass(frozen=True)
class Planned:
    path: Path
    target: Path
    approval: bool


def display(path: Path, home: Path) -> str:
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return str(path)


def codex_home(home: Path) -> Path:
    return Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser()


def config_home(home: Path) -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config").expanduser()


def desired_links(home: Path) -> list[Link]:
    agents = home / ".agents"
    opencode = config_home(home) / "opencode"
    return [
        Link(agents, ROOT),
        Link(home / ".claude" / "skills", ROOT / "skills"),
        Link(opencode / "skills", ROOT / "skills"),
        Link(home / ".claude" / "CLAUDE.md", agents / "AGENTS.md"),
        Link(codex_home(home) / "AGENTS.md", agents / "AGENTS.md"),
        Link(opencode / "AGENTS.md", agents / "AGENTS.md"),
    ]


def resolved_link(path: Path) -> Path | None:
    if not path.is_symlink():
        return None
    stored = Path(os.readlink(path))
    if not stored.is_absolute():
        stored = path.parent / stored
    return stored.resolve(strict=False)


def plan_links(home: Path) -> list[Planned]:
    changes = []
    for link in desired_links(home):
        current = resolved_link(link.path)
        if link.path.is_symlink():
            if current == link.target.resolve(strict=False):
                continue
            approval = bool(current and current.exists() and not inside_factory(current))
        elif os.path.lexists(link.path):
            approval = True
        else:
            approval = False
        changes.append(Planned(link.path, link.target, approval))
    return changes


def inside_factory(path: Path) -> bool:
    return path == ROOT or ROOT in path.parents


def main_checkout() -> Path:
    result = git(ROOT, "worktree", "list", "--porcelain")
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                return Path(line[len("worktree ") :]).resolve()
    return ROOT


def worktree_guard(home: Path) -> list[str]:
    if home != Path.home().resolve():
        return []
    git_dir = git(ROOT, "rev-parse", "--git-dir")
    common_dir = git(ROOT, "rev-parse", "--git-common-dir")
    if git_dir.returncode != 0 or common_dir.returncode != 0:
        return []
    git_path = Path(git_dir.stdout.strip())
    common_path = Path(common_dir.stdout.strip())
    if not git_path.is_absolute():
        git_path = ROOT / git_path
    if not common_path.is_absolute():
        common_path = ROOT / common_path
    if git_path.resolve() == common_path.resolve():
        return []
    return [
        f"refusing to run from linked git worktree {ROOT}",
        f"run it from the main checkout {main_checkout()}",
    ]


def backup_path(path: Path, stamp: str) -> Path:
    candidate = path.with_name(f"{path.name}.backup-{stamp}")
    suffix = 2
    while os.path.lexists(candidate):
        candidate = path.with_name(f"{path.name}.backup-{stamp}-{suffix}")
        suffix += 1
    return candidate


def apply_links(changes: list[Planned], home: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for change in changes:
        change.path.parent.mkdir(parents=True, exist_ok=True)
        if change.approval:
            action = "replace"
            backup = backup_path(change.path, stamp)
            change.path.rename(backup)
            print(f"bootstrap: backed up {display(change.path, home)} -> {display(backup, home)}")
        elif change.path.is_symlink():
            action = "repoint"
            change.path.unlink()
        else:
            action = "link"
        os.symlink(change.target, change.path)
        print(f"bootstrap: {action} {display(change.path, home)} -> {display(change.target, home)}")


def mcp_sync(home: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "mcp-sync.py"), "--home", str(home), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def combined(result: subprocess.CompletedProcess) -> str:
    return "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)


def launchctl_binary() -> str | None:
    for candidate in (shutil.which("launchctl"), "/bin/launchctl"):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def render_mcp(home: Path) -> bool:
    render = mcp_sync(home)
    if render.returncode != 0:
        print(f"bootstrap: MCP render failed: {combined(render)}", file=sys.stderr)
        return False
    check = mcp_sync(home, "--check")
    if check.returncode != 0:
        print(f"bootstrap: MCP configs are not in sync after the render: {combined(check)}", file=sys.stderr)
        return False
    print("bootstrap: MCP configs are in sync")
    return True


def launch_interpreter() -> str:
    executable = Path(sys.executable)
    if executable.is_absolute() and executable.parent != Path("/usr/bin") and executable.is_file():
        return str(executable)
    try:
        result = subprocess.run(["mise", "which", "python"], capture_output=True, text=True, check=False)
    except OSError:
        raise SystemExit("bootstrap: cannot resolve an absolute Python for the secrets agent") from None
    resolved = result.stdout.strip()
    if result.returncode == 0 and resolved and Path(resolved).is_file():
        return resolved
    raise SystemExit("bootstrap: cannot resolve an absolute Python for the secrets agent")


def secret_agent_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{SECRET_LABEL}.plist"


def secret_agent_plist(home: Path, interpreter: str | None = None) -> bytes:
    program = home / ".agents" / "scripts" / "mcp-sync.py"
    log = home / SECRET_LOG
    data = {
        "Label": SECRET_LABEL,
        "ProgramArguments": [interpreter or launch_interpreter(), str(program), "--env"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": SECRET_RETRY_SECONDS,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    return plistlib.dumps(data, fmt=plistlib.FMT_XML, sort_keys=False)


def install_secret_agent(home: Path) -> bool:
    path = secret_agent_path(home)
    text = secret_agent_plist(home)
    if not path.is_file() or path.read_bytes() != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text)
        os.chmod(path, 0o600)
        print(f"bootstrap: wrote {display(path, home)}")
    else:
        print(f"bootstrap: {display(path, home)} is current")
    if launchctl_binary() is None:
        print("bootstrap: launchctl is not available; the secrets agent is written but not loaded")
        return True
    if home != Path.home().resolve():
        print("bootstrap: not the real home; the secrets agent is not loaded")
        return True
    applied = mcp_sync(home, "--env")
    if applied.stdout.strip():
        print(applied.stdout.strip())
    loaded = True
    if applied.returncode != 0:
        print(f"bootstrap: {applied.stderr.strip()}", file=sys.stderr)
        print(
            f"bootstrap: the secrets agent retries; see {display(home / SECRET_LOG, home)}",
            file=sys.stderr,
        )
        loaded = False
    else:
        verified = mcp_sync(home, "--env", "--check")
        if verified.returncode != 0:
            print(f"bootstrap: {(verified.stderr or verified.stdout).strip()}", file=sys.stderr)
            loaded = False
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{SECRET_LABEL}"],
        capture_output=True,
        text=True,
        check=False,
    )
    bootstrap = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if bootstrap.returncode != 0:
        print(f"bootstrap: launchctl bootstrap failed: {bootstrap.stderr.strip()}", file=sys.stderr)
        return False
    return loaded


def secret_agent_errors(home: Path) -> list[str]:
    path = secret_agent_path(home)
    shown = display(path, home)
    if not path.is_file():
        return [f"the secrets agent is missing: {shown}; run mise run bootstrap"]
    try:
        data = plistlib.loads(path.read_bytes())
    except (ValueError, OSError) as error:
        return [f"{shown} is not a readable plist: {error}; run mise run bootstrap"]
    program = data.get("ProgramArguments") or []
    if len(program) < 3:
        return [f"{shown} has no program arguments; run mise run bootstrap"]
    interpreter = Path(str(program[0]))
    if not interpreter.is_file():
        return [f"{shown} names a missing interpreter: {interpreter}; run mise run bootstrap"]
    script = Path(str(program[1]))
    if script.name != "mcp-sync.py" or program[2] != "--env":
        return [f"{shown} does not run mcp-sync.py --env; run mise run bootstrap"]
    try:
        interpreter = launch_interpreter()
    except SystemExit as error:
        reason = str(error).removeprefix("bootstrap: ")
        return [f"{shown} cannot resolve the secrets agent interpreter ({reason}); run mise run bootstrap"]
    if path.read_bytes() != secret_agent_plist(home, interpreter):
        return [f"{shown} differs from what bootstrap writes now; run mise run bootstrap"]
    if home == Path.home().resolve() and launchctl_binary() is not None:
        applied = mcp_sync(home, "--env", "--check")
        if applied.returncode != 0:
            return [f"the launchd secret check failed: {(applied.stderr or applied.stdout).strip()}"]
    return []


def github_slug(url: str) -> tuple[str, str] | None:
    for pattern in (GITHUB_HTTPS, GITHUB_SSH):
        match = pattern.match(url)
        if match:
            return match.group(1), match.group(2)
    return None


def github_host_block(text: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"(?i)^\s*Host\s+([^#]+)", line)
        if not match:
            continue
        hosts = match.group(1).split()
        if not any(host == "github.com" or host.endswith(".github.com") for host in hosts):
            continue
        block = []
        for following in lines[index + 1 :]:
            if re.match(r"(?i)^\s*(Host|Match)\s+", following):
                break
            block.append(following)
        return "\n".join(block)
    return None


def setup_github_ssh(home: Path) -> bool:
    origin = None
    result = git(ROOT, "remote", "get-url", "origin")
    if result.returncode == 0:
        origin = result.stdout.strip() or None
    if origin is None:
        print("bootstrap: no origin remote; GitHub SSH setup skipped")
        return True
    slug = github_slug(origin)
    if slug is None:
        print(f"bootstrap: origin {origin} is not GitHub; SSH setup skipped")
        return True
    socket = home / AGENT_SOCKET
    if not socket.exists():
        print(
            f"bootstrap: {display(socket, home)} is missing; start 1Password so its SSH agent "
            "can serve the GitHub key",
            file=sys.stderr,
        )
        return False
    config = home / ".ssh" / "config"
    config.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(config.parent, 0o700)
    text = config.read_text() if config.is_file() else ""
    block = github_host_block(text)
    if SSH_MARKER in text:
        print(f"bootstrap: {display(config, home)} already routes github.com through 1Password")
    elif block is not None:
        managed = re.search(r'(?im)^\s*IdentityAgent(?:\s*=\s*|\s+)"?[^\n]*2BUA8C4S2C\.com\.1password', block)
        if managed:
            print(f"bootstrap: {display(config, home)} already routes github.com through 1Password")
        else:
            print(
                f"bootstrap: {display(config, home)} already has a github.com block the Factory "
                "does not manage; SSH auth through it is unverified",
                file=sys.stderr,
            )
    else:
        if config.is_file():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            shutil.copy2(config, backup_path(config, stamp))
        block = (
            f"\n{SSH_MARKER}\n"
            "Host github.com\n"
            "    HostName github.com\n"
            "    User git\n"
            f'    IdentityAgent "{socket}"\n'
            "    IdentitiesOnly yes\n"
        )
        config.write_text(text + block)
        os.chmod(config, 0o600)
        print(f"bootstrap: added github.com to {display(config, home)}")
    if GITHUB_HTTPS.match(origin):
        rewritten = f"git@github.com:{slug[0]}/{slug[1]}.git"
        changed = git(ROOT, "remote", "set-url", "origin", rewritten)
        if changed.returncode != 0:
            print(f"bootstrap: cannot rewrite origin: {changed.stderr.strip()}", file=sys.stderr)
            return False
        print(f"bootstrap: origin is now {rewritten}")
    return True


def read_tool_skill_groups() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    current = None
    for line in TOOL_SKILLS_FILE.read_text().splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith("[") and text.endswith("]"):
            current = text[1:-1]
            groups[current] = []
        elif current is None:
            raise SystemExit("bootstrap: tool-skills.txt has a name outside a group")
        else:
            groups[current].append(text)
    missing = [group for group in REQUIRED_TOOL_GROUPS if group not in groups]
    if missing:
        raise SystemExit(f"bootstrap: tool-skills.txt is missing groups: {', '.join(missing)}")
    empty = [group for group in REQUIRED_TOOL_GROUPS if not groups[group]]
    if empty:
        raise SystemExit(f"bootstrap: tool-skills.txt groups are empty: {', '.join(empty)}")
    return groups


def read_tool_skills() -> list[str]:
    return [name for names in read_tool_skill_groups().values() for name in names]


def skill_present(name: str) -> bool:
    return (ROOT / "skills" / name / "SKILL.md").is_file()


def tool_version(name: str) -> str:
    tools = tomllib.loads((ROOT / "mise.toml").read_text()).get("tools", {})
    version = tools.get(name)
    if not isinstance(version, str):
        raise SystemExit(f"bootstrap: mise.toml has no {name} pin")
    return version


def run_installer(label: str, argv: list[str], home: Path) -> None:
    executable = shutil.which(argv[0])
    if executable is None:
        print(f"bootstrap: warning: {label} is not installed; skipping its skills")
        return
    argv = [executable, *argv[1:]]
    print(f"bootstrap: install {label}: {' '.join(argv)}")
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    result = subprocess.run(argv, env=environment, check=False)
    if result.returncode != 0:
        print(f"bootstrap: {label} installer failed with exit {result.returncode}", file=sys.stderr)


def install_gh_stack(home: Path) -> None:
    executable = shutil.which("gh")
    if executable is None:
        print("bootstrap: warning: gh is not installed; skipping gh-stack")
        return
    with tempfile.TemporaryDirectory(prefix="factory-gh-stack-") as state:
        environment = os.environ.copy()
        environment["HOME"] = state
        environment["GH_CONFIG_DIR"] = os.environ.get("GH_CONFIG_DIR") or str(Path.home() / ".config" / "gh")
        argv = [
            executable,
            "skill",
            "install",
            "github/gh-stack",
            "gh-stack",
            "--dir",
            str(home / ".claude" / "skills"),
            "--pin",
            GH_STACK_PIN,
            "--force",
        ]
        print(f"bootstrap: install gh-stack: {' '.join(argv)}")
        result = subprocess.run(argv, env=environment, check=False)
        if result.returncode != 0:
            print(f"bootstrap: gh-stack installer failed with exit {result.returncode}", file=sys.stderr)


def install_tool_skills(home: Path) -> list[str]:
    errors = []
    groups = read_tool_skill_groups()
    installers = {
        "ardia": ["ardia", "skills", "install", "--target", "general", "--no-cua"],
        "cua": ["cua", "connect", "setup", "--skills-only", "--agents", "claude", "-y"],
        "rea": ["rea", "setup", "--skill-client", "shared", "--yes"],
    }
    for group, argv in installers.items():
        names = groups[group]
        if not all(skill_present(name) for name in names):
            if group == "rea" and shutil.which("rea") is None:
                argv = [
                    "npx",
                    "--yes",
                    f"rea-agents@{tool_version('npm:rea-agents')}",
                    "setup",
                    "--skill-client",
                    "shared",
                    "--yes",
                ]
            run_installer(group, argv, home)
        for name in names:
            if not skill_present(name):
                errors.append(f"{group}: {display(ROOT / 'skills' / name / 'SKILL.md', home)} is missing")
    gh_names = groups["gh-stack"]
    if not all(skill_present(name) for name in gh_names):
        install_gh_stack(home)
    for name in gh_names:
        if not skill_present(name):
            errors.append(f"gh-stack: {display(ROOT / 'skills' / name / 'SKILL.md', home)} is missing")
    ctx7_names = groups["ctx7"]
    if not all(skill_present(name) for name in ctx7_names):
        if shutil.which("ctx7"):
            run_installer("ctx7", ["ctx7", "setup", "--claude", "--cli", "-y"], home)
        else:
            run_installer(
                "ctx7",
                ["npx", "--yes", f"ctx7@{tool_version('npm:ctx7')}", "setup", "--claude", "--cli", "-y"],
                home,
            )
    for name in ctx7_names:
        if not skill_present(name):
            errors.append(f"ctx7: {display(ROOT / 'skills' / name / 'SKILL.md', home)} is missing")
    if not errors:
        print("bootstrap: tool-owned skills are installed")
    return errors


def factory_skill_names() -> set[str]:
    return {
        path.name
        for path in (ROOT / "skills").iterdir()
        if path.is_dir() and path.name != SYNCED_SKILLS and (path / "SKILL.md").is_file()
    }


def codex_root_errors(home: Path) -> list[str]:
    root = codex_home(home) / "skills"
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
    errors.extend(secret_agent_errors(home))
    mcp_check = mcp_sync(home, "--check")
    if mcp_check.returncode != 0:
        errors.append(f"MCP configs are not in sync: {combined(mcp_check)}")
    expected_names = factory_skill_names()
    opencode = config_home(home) / "opencode"
    harnesses = {
        "claude-code": {
            "instructions": [home / ".claude" / "CLAUDE.md"],
            "skills": [home / ".claude" / "skills"],
            "shadows": [],
        },
        "opencode": {
            "instructions": [opencode / "AGENTS.md"],
            "skills": [opencode / "skills", home / ".claude" / "skills", home / ".agents" / "skills"],
            "shadows": [],
        },
        "codex": {
            "instructions": [codex_home(home) / "AGENTS.md"],
            "skills": [home / ".agents" / "skills"],
            "shadows": [codex_home(home) / "AGENTS.override.md"],
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
            if not path.is_dir():
                errors.append(f"{harness}: {display(path, home)} is not a directory")
                continue
            if not path.samefile(factory_skills):
                errors.append(
                    f"{harness}: {display(path, home)} does not resolve to "
                    f"{display(factory_skills, home)}"
                )
            names |= {
                entry.name
                for entry in path.iterdir()
                if entry.is_dir()
                and entry.name != SYNCED_SKILLS
                and (entry / "SKILL.md").is_file()
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


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def git_paths(root: Path, label: str, *args: str) -> list[str] | None:
    result = git(root, *args)
    if result.returncode != 0:
        print(f"bootstrap: cannot list {label} in {root}: {result.stderr.strip()}", file=sys.stderr)
        return None
    return [line for line in result.stdout.splitlines() if line]


def synced_cache_errors(root: Path) -> list[str] | None:
    tracked = git_paths(root, "tracked synced cache files", "ls-files", "--", f"skills/{SYNCED_SKILLS}")
    if tracked is None:
        return None
    untracked = git_paths(
        root,
        "untracked synced cache files",
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        f"skills/{SYNCED_SKILLS}",
    )
    if untracked is None:
        return None
    return [f"synced skill cache is tracked: {path}" for path in tracked] + [
        f"synced skill cache is not ignored: {path}" for path in untracked
    ]


def check_tool_skills(root: Path) -> int:
    names = read_tool_skills()
    tracked = git_paths(
        root, "tracked tool-owned skill files", "ls-files", "--", *[f"skills/{name}" for name in names]
    )
    if tracked is None:
        return 1
    untracked = git_paths(
        root,
        "untracked skill files",
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        ":(glob)skills/*/SKILL.md",
    )
    if untracked is None:
        return 1
    synced_errors = synced_cache_errors(root)
    if synced_errors is None:
        return 1
    problems = False
    for path in tracked:
        print(f"bootstrap: tool-owned skill is tracked: {path}", file=sys.stderr)
        problems = True
    for path in untracked:
        print(f"bootstrap: untracked skill is not declared as tool-owned: {path}", file=sys.stderr)
        problems = True
    for error in synced_errors:
        print(f"bootstrap: {error}", file=sys.stderr)
        problems = True
    for name in names:
        ignored = git(root, "check-ignore", "-q", f"skills/{name}/SKILL.md")
        if ignored.returncode != 0:
            print(f"bootstrap: declared tool-owned skill is not ignored: skills/{name}", file=sys.stderr)
            problems = True
    ignored_files = git_paths(
        root,
        "ignored skill files",
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--",
        ":(glob)skills/*/SKILL.md",
    )
    if ignored_files is None:
        return 1
    for path in ignored_files:
        name = Path(path).parts[1] if len(Path(path).parts) > 1 else path
        if name != SYNCED_SKILLS and name not in names:
            print(f"bootstrap: ignored skill is not declared tool-owned: skills/{name}", file=sys.stderr)
            problems = True
    if problems:
        return 1
    print("bootstrap: tool-owned skill checks passed")
    return 0


def run_skills_sync(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["mise", "run", "skills:sync", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def check_vendored_skills() -> list[str]:
    try:
        check = run_skills_sync("--check")
    except OSError as error:
        return [f"skills:sync unavailable: {error}"]
    if check.returncode == 0:
        print("bootstrap: vendored skills check passed")
        return []
    for line in (check.stderr or check.stdout).strip().splitlines():
        print(f"bootstrap: {line}", file=sys.stderr)
    return ["vendored skills drifted; run `mise run skills:sync` to repair them, then rerun bootstrap"]


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
    guard = worktree_guard(home)
    if guard:
        for line in guard:
            print(f"bootstrap: {line}", file=sys.stderr)
        return 2
    print(f"bootstrap: Factory checkout {ROOT}")
    changes = plan_links(home)
    needs_approval = [change for change in changes if change.approval]
    if needs_approval and not args.adopt_existing:
        print("bootstrap: refusing to replace these managed paths:", file=sys.stderr)
        for change in needs_approval:
            print(f"  {display(change.path, home)}", file=sys.stderr)
        print("Approve with: mise run bootstrap -- --adopt-existing", file=sys.stderr)
        return 2
    vendored_errors = check_vendored_skills()
    if vendored_errors:
        for error in vendored_errors:
            print(f"bootstrap: {error}", file=sys.stderr)
        return 1
    apply_links(changes, home)
    if not render_mcp(home):
        return 1
    if not install_secret_agent(home):
        return 1
    if not setup_github_ssh(home):
        return 1
    tools_present_before = all(skill_present(name) for name in read_tool_skills())
    errors = install_tool_skills(home)
    discovery_code = discovery(home)
    for error in errors:
        print(f"bootstrap: {error}", file=sys.stderr)
    if errors or discovery_code != 0:
        return 1
    if not changes and tools_present_before:
        print("Already bootstrapped: no changes.")
    else:
        work = []
        if changes:
            work.append(f"{len(changes)} link change(s)")
        if not tools_present_before:
            work.append("tool skills installed")
        print("bootstrap: complete with " + ", ".join(work))
    return 0


if __name__ == "__main__":
    sys.exit(main())
