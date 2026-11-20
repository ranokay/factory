#!/usr/bin/env python3
"""Render the Factory MCP registry into each harness's native config.

Registry entries never hold secret values: a secret header names the
environment variable that carries the value and the ``op://`` reference the
LaunchAgent reads it from. Each harness config gets that harness's own
env-var syntax, so a rendered config is safe to commit or share.

Usage: mcp-sync [--check] [--env [--check]] [--home PATH] [--registry PATH]
  --check          report drift without writing
  --env            read each secret from 1Password and set it in launchd
  --env --check    report which secret env vars launchd is missing
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "mcp.json"
TARGETS = ("claude-code", "opencode", "codex")
HEADER = re.compile(r"^\[([^\]]+)\]\s*$")
OP_TIMEOUT = 60


class SyncError(Exception):
    pass


def toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def expand_home(value: str, home: Path) -> str:
    if value == "~":
        return str(home)
    if value.startswith("~/"):
        return str(home / value[2:])
    return value


def backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.backup-{stamp}")
    suffix = 2
    while os.path.lexists(candidate):
        candidate = path.with_name(f"{path.name}.backup-{stamp}-{suffix}")
        suffix += 1
    return candidate


def load_registry(path: Path) -> tuple[dict, list[str]]:
    data = json.loads(path.read_text())
    servers = data.get("servers")
    if not isinstance(servers, dict) or not servers:
        raise SyncError(f"{path}: servers must be a non-empty object")
    retired = data.get("retired", [])
    if not isinstance(retired, list) or any(not isinstance(name, str) for name in retired):
        raise SyncError(f"{path}: retired must be a list of server names")
    overlap = sorted(set(servers) & set(retired))
    if overlap:
        raise SyncError(f"{path}: retired servers are also managed: {', '.join(overlap)}")
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            raise SyncError(f"{path}: server {name} must be an object")
        transport = spec.get("transport")
        if transport not in ("stdio", "http"):
            raise SyncError(f"{path}: server {name} has unsupported transport {transport!r}")
        if transport == "stdio" and not isinstance(spec.get("command"), str):
            raise SyncError(f"{path}: server {name} is missing command")
        if transport == "http" and not isinstance(spec.get("url"), str):
            raise SyncError(f"{path}: server {name} is missing url")
        targets = spec.get("targets")
        if not isinstance(targets, list) or not targets:
            raise SyncError(f"{path}: server {name} is missing targets")
        unknown = sorted(set(targets) - set(TARGETS))
        if unknown:
            raise SyncError(f"{path}: server {name} has unknown targets: {', '.join(unknown)}")
        headers = spec.get("headers", {})
        if not isinstance(headers, dict):
            raise SyncError(f"{path}: server {name} headers must be an object")
        for header, value in headers.items():
            if not isinstance(value, dict):
                raise SyncError(
                    f"{path}: server {name} header {header} must be an object with env and reference"
                )
            if value.get("scheme") not in (None, "Bearer"):
                raise SyncError(f"{path}: server {name} header {header} has unsupported scheme")
            if not isinstance(value.get("env"), str) or not value["env"]:
                raise SyncError(f"{path}: server {name} header {header} is missing env")
            if not str(value.get("reference", "")).startswith("op://"):
                raise SyncError(f"{path}: server {name} header {header} needs an op:// reference")
    return servers, retired


def secret_references(servers: dict) -> dict[str, str]:
    references: dict[str, str] = {}
    for name, spec in servers.items():
        for header, value in (spec.get("headers") or {}).items():
            env = value["env"]
            if env in references and references[env] != value["reference"]:
                raise SyncError(f"{name} header {header}: {env} has two 1Password references")
            references[env] = value["reference"]
    return references


def header_reference(value: dict, target: str) -> str:
    prefix = f"{value['scheme']} " if value.get("scheme") else ""
    if target == "claude-code":
        return f"{prefix}${{{value['env']}}}"
    return f"{prefix}{{env:{value['env']}}}"


def headers_for(spec: dict, target: str) -> dict[str, str]:
    return {
        name: header_reference(value, target) for name, value in (spec.get("headers") or {}).items()
    }


def claude_entry(spec: dict, home: Path) -> dict:
    if spec["transport"] == "stdio":
        return {
            "type": "stdio",
            "command": expand_home(spec["command"], home),
            "args": [expand_home(arg, home) for arg in spec.get("args", [])],
            "env": spec.get("env", {}),
        }
    entry = {"type": "http", "url": spec["url"]}
    headers = headers_for(spec, "claude-code")
    if headers:
        entry["headers"] = headers
    return entry


def opencode_entry(spec: dict, home: Path) -> dict:
    if spec["transport"] == "stdio":
        entry = {
            "type": "local",
            "command": [expand_home(spec["command"], home)]
            + [expand_home(arg, home) for arg in spec.get("args", [])],
            "enabled": True,
        }
        if spec.get("env"):
            entry["environment"] = spec["env"]
        return entry
    entry = {"type": "remote", "url": spec["url"], "enabled": True}
    headers = headers_for(spec, "opencode")
    if headers:
        entry["headers"] = headers
    return entry


def codex_blocks(servers: dict, home: Path) -> str:
    lines: list[str] = []
    for name, spec in servers.items():
        if "codex" not in spec["targets"]:
            continue
        lines.append(f"[mcp_servers.{name}]")
        if spec["transport"] == "stdio":
            lines.append(f"command = {toml_str(expand_home(spec['command'], home))}")
            args = ", ".join(toml_str(expand_home(arg, home)) for arg in spec.get("args", []))
            lines.append(f"args = [{args}]")
            env = spec.get("env") or {}
            if env:
                lines.append("")
                lines.append(f"[mcp_servers.{name}.env]")
                lines.extend(f"{key} = {toml_str(value)}" for key, value in env.items())
        else:
            lines.append(f"url = {toml_str(spec['url'])}")
            from_env = {}
            for header, value in (spec.get("headers") or {}).items():
                if value.get("scheme") == "Bearer" and header.lower() == "authorization":
                    lines.append(f"bearer_token_env_var = {toml_str(value['env'])}")
                else:
                    from_env[header] = value["env"]
            if from_env:
                lines.append("")
                lines.append(f"[mcp_servers.{name}.env_http_headers]")
                lines.extend(f"{key} = {toml_str(value)}" for key, value in from_env.items())
        lines.append("")
    return "\n".join(lines)


def strip_codex(text: str, names: set[str]) -> str:
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        match = HEADER.match(line.strip())
        if match:
            parts = match.group(1).split(".")
            skipping = len(parts) > 1 and parts[0] == "mcp_servers" and parts[1] in names
        if not skipping:
            kept.append(line)
    return "\n".join(kept).rstrip() + "\n" if kept else ""


def desired_json(document: dict, key: str, servers: dict, retired: list[str], target: str, home: Path) -> dict:
    document = copy.deepcopy(document)
    managed = document.setdefault(key, {})
    for name in retired:
        managed.pop(name, None)
    for name, spec in servers.items():
        if target in spec["targets"]:
            managed[name] = claude_entry(spec, home) if target == "claude-code" else opencode_entry(spec, home)
    return document


def desired_codex(text: str, servers: dict, retired: list[str], home: Path) -> str:
    names = {name for name, spec in servers.items() if "codex" in spec["targets"]} | set(retired)
    body = strip_codex(text, names)
    blocks = codex_blocks(servers, home)
    return (body.rstrip() + "\n\n" if body.strip() else "") + blocks


def config_paths(home: Path) -> dict[str, Path]:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config").expanduser()
    codex = Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser()
    return {
        "claude-code": home / ".claude.json",
        "opencode": config / "opencode" / "opencode.json",
        "codex": codex / "config.toml",
    }


def write(path: Path, text: str, check: bool, changed: list[str]) -> None:
    if path.exists() and path.read_text() == text:
        return
    changed.append(str(path))
    if check:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = None
    if path.exists():
        mode = path.stat().st_mode & 0o777
        shutil.copy2(path, backup_path(path))
    temporary = path.with_name(path.name + ".mcp-sync.tmp")
    temporary.write_text(text)
    if mode is not None:
        os.chmod(temporary, mode)
    os.replace(temporary, path)


def render(home: Path, servers: dict, retired: list[str], check: bool) -> int:
    changed: list[str] = []
    for target, path in config_paths(home).items():
        if not path.exists():
            continue
        try:
            text = path.read_text()
        except OSError as error:
            raise SyncError(f"{path}: {error}") from None
        if target == "codex":
            write(path, desired_codex(text, servers, retired, home), check, changed)
            continue
        try:
            document = json.loads(text)
        except ValueError as error:
            raise SyncError(f"{path}: {error}") from None
        key = "mcpServers" if target == "claude-code" else "mcp"
        rendered = desired_json(document, key, servers, retired, target, home)
        write(path, json.dumps(rendered, indent=2, ensure_ascii=False) + "\n", check, changed)
    if check:
        if changed:
            print("drift:")
            for path in changed:
                print(f"  {path}")
            return 1
        print("in sync")
        return 0
    for path in changed:
        print(f"wrote {path}")
    if not changed:
        print("in sync")
    return 0


def op_binary() -> str:
    found = shutil.which("op")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/op", "/usr/local/bin/op", "/home/linuxbrew/.linuxbrew/bin/op"):
        if Path(candidate).is_file():
            return candidate
    raise SyncError("the 1Password CLI (op) is not installed")


def launchctl_binary() -> str:
    for candidate in (shutil.which("launchctl"), "/bin/launchctl"):
        if candidate and Path(candidate).is_file():
            return candidate
    raise SyncError("launchctl is not available; launchd env vars need macOS")


def set_env(references: dict[str, str]) -> None:
    op = op_binary()
    launchctl = launchctl_binary()
    for env, reference in references.items():
        try:
            result = subprocess.run(
                [op, "read", "--no-newline", reference],
                capture_output=True,
                text=True,
                timeout=OP_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise SyncError(
                f"op read for {env} timed out after {OP_TIMEOUT}s; unlock 1Password and rerun bootstrap"
            ) from None
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise SyncError(f"cannot read {env} from 1Password: {detail[-1] if detail else 'op failed'}")
        if not result.stdout:
            raise SyncError(f"{env} is empty in 1Password")
        applied = subprocess.run(
            [launchctl, "setenv", env, result.stdout], capture_output=True, text=True, check=False
        )
        if applied.returncode != 0:
            raise SyncError(f"launchctl setenv {env} failed: {applied.stderr.strip()}")
        print(f"mcp-sync: set {env}")


def check_env(references: dict[str, str]) -> None:
    launchctl = launchctl_binary()
    missing = []
    for env in references:
        result = subprocess.run([launchctl, "getenv", env], capture_output=True, text=True, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            missing.append(env)
    if missing:
        raise SyncError(f"launchd is missing {', '.join(missing)}; run mise run bootstrap")
    print("mcp-sync: launchd has every secret")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    parser.add_argument("--env", action="store_true", help="set the secret env vars in launchd")
    parser.add_argument("--home", type=Path, default=Path.home(), help="home directory to render into")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY, help="registry to read")
    args = parser.parse_args(argv)
    home = args.home.expanduser().resolve()
    try:
        servers, retired = load_registry(args.registry)
        if args.env:
            references = secret_references(servers)
            if not references:
                print("mcp-sync: the registry has no secret references; nothing to set")
                return 0
            if args.check:
                check_env(references)
            else:
                set_env(references)
            return 0
        return render(home, servers, retired, args.check)
    except SyncError as error:
        print(f"mcp-sync: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
