#!/usr/bin/env python3
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

CLI = Path(__file__).resolve().parent / "skills-sync.py"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_upstream(base):
    repo = base / "upstream"
    repo.mkdir()
    git(repo, "init", "--quiet")
    write(repo / "skills/alpha/SKILL.md", "alpha\n")
    write(repo / "skills/alpha/reference.md", "reference\n")
    write(repo / "skills/beta/SKILL.md", "beta\n\nRead ../alpha/SKILL.md.\n")
    write(
        repo / "skills/untangle-codebase/SKILL.md",
        "---\nname: untangle-codebase\ndisable-model-invocation: true\n---\n\nuntangle\n",
    )
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "--message", "first")
    return repo, git(repo, "rev-parse", "HEAD").stdout.strip()


def write_lock(base, source, commit):
    payload = {
        "version": 1,
        "skills": [
            {
                "name": name,
                "source": str(source),
                "path": f"skills/{name}",
                "commit": commit,
                "patches": [],
                "files": [],
            }
            for name in ("alpha", "beta", "untangle-codebase")
        ],
    }
    write(base / "skills.lock", json.dumps(payload, indent=2) + "\n")


def run_cli(base, *args):
    subprocess.run([sys.executable, str(CLI), "--root", str(base), *args], check=True, capture_output=True, text=True)


def load_lock(base):
    return json.loads((base / "skills.lock").read_text())


def save_lock(base, lock):
    write(base / "skills.lock", json.dumps(lock, indent=2) + "\n")


def add_patch(base, name, body):
    path = base / "patches" / name / "0001-alpha.patch"
    write(path, body)
    return path


def clean(base, scenario):
    repo, commit = make_upstream(base)
    write_lock(base, repo, commit)
    if scenario in ("patch", "patch-tamper"):
        add_patch(
            base,
            "alpha",
            "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-alpha\n+patched alpha\n",
        )
    if scenario == "patch-conflict":
        add_patch(
            base,
            "alpha",
            "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -1 +1 @@\n-never present\n+patched alpha\n",
        )
        return repo
    run_cli(base)
    return repo


def main():
    if len(sys.argv) < 2:
        print("usage: skills-sync-fixture.py SCENARIO [DIR]", file=sys.stderr)
        return 2
    scenario = sys.argv[1]
    base = Path(sys.argv[2] if len(sys.argv) > 2 else ".").resolve()
    if base.exists():
        for child in base.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    base.mkdir(parents=True, exist_ok=True)

    if scenario == "patch-conflict":
        clean(base, scenario)
        return 0

    repo = clean(base, scenario)

    if scenario == "edit":
        with (base / "skills/alpha/SKILL.md").open("a") as handle:
            handle.write("hand edit\n")
    elif scenario == "add":
        write(base / "skills/alpha/extra.md", "extra\n")
    elif scenario == "delete":
        (base / "skills/alpha/reference.md").unlink()
    elif scenario == "patch":
        write(base / "skills/alpha/SKILL.md", "alpha\n")
    elif scenario == "update":
        write(repo / "skills/alpha/SKILL.md", "alpha v2\n")
        git(repo, "add", "--all")
        git(repo, "commit", "--quiet", "--message", "second")
    elif scenario == "tamper":
        write(base / "skills/alpha/SKILL.md", "tampered\n")
        lock = load_lock(base)
        alpha = next(entry for entry in lock["skills"] if entry["name"] == "alpha")
        for item in alpha["files"]:
            if item["path"] == "SKILL.md":
                item["sha256"] = sha256(base / "skills/alpha/SKILL.md")
        save_lock(base, lock)
    elif scenario == "dangling-reference":
        path = base / "skills/alpha/SKILL.md"
        write(path, "alpha\n\nRead ../missing/SKILL.md.\n")
        lock = load_lock(base)
        alpha = next(entry for entry in lock["skills"] if entry["name"] == "alpha")
        for item in alpha["files"]:
            if item["path"] == "SKILL.md":
                item["sha256"] = sha256(path)
        save_lock(base, lock)
    elif scenario == "human-only":
        path = base / "skills/untangle-codebase/SKILL.md"
        write(path, "---\nname: untangle-codebase\n---\n\nuntangle\n")
        lock = load_lock(base)
        entry = next(item for item in lock["skills"] if item["name"] == "untangle-codebase")
        for item in entry["files"]:
            if item["path"] == "SKILL.md":
                item["sha256"] = sha256(path)
        save_lock(base, lock)
    elif scenario == "orphan-patch":
        write(base / "patches/orphan/0001-orphan.patch", "--- a/x\n+++ b/x\n")
    elif scenario == "patch-tamper":
        with (base / "patches/alpha/0001-alpha.patch").open("a") as handle:
            handle.write("\n")
    elif scenario == "own-skill":
        write(base / "skills/own-skill/SKILL.md", "own\n")
    elif scenario != "clean":
        print(f"unknown scenario {scenario}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
