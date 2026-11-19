#!/usr/bin/env python3
import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

LOCK_VERSION = 1
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
HUMAN_ONLY = {"untangle-codebase": "disable-model-invocation: true"}
REFERENCE = re.compile(rb"\.\./([a-z0-9-]+)/SKILL\.md")
ENTRY_FIELDS = ("name", "source", "path", "commit", "patches", "files")


class SyncError(Exception):
    pass


def detail(result):
    return (result.stderr or result.stdout).strip()


def shell(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise SyncError(f"{' '.join(command)}: {detail(result)}")
    return result.stdout


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hashes(directory):
    hashes = {}
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        hashes[path.relative_to(directory).as_posix()] = sha256(path)
    return hashes


def load_lock(root):
    path = root / "skills.lock"
    if not path.is_file():
        raise SyncError("skills.lock is missing")
    data = json.loads(path.read_text())
    if data.get("version") != LOCK_VERSION:
        raise SyncError(f"skills.lock: unsupported version {data.get('version')!r}")
    skills = data.get("skills")
    if not isinstance(skills, list):
        raise SyncError("skills.lock: skills must be a list")
    names = set()
    for entry in skills:
        if not isinstance(entry, dict):
            raise SyncError("skills.lock: every skill must be an object")
        for field in ENTRY_FIELDS:
            if field not in entry:
                raise SyncError(f"skills.lock: entry is missing {field}")
        name = entry["name"]
        if not isinstance(name, str) or not name or "/" in name or name in (".", ".."):
            raise SyncError(f"skills.lock: invalid skill name {name!r}")
        if name in names:
            raise SyncError(f"skills.lock: duplicate skill {name}")
        names.add(name)
        if not isinstance(entry["source"], str) or not entry["source"]:
            raise SyncError(f"skills.lock: {name} has no source")
        path = entry["path"]
        if not isinstance(path, str) or not path or path.startswith("/") or ".." in Path(path).parts:
            raise SyncError(f"skills.lock: {name} has an invalid path {path!r}")
        if not isinstance(entry["commit"], str) or not FULL_SHA.match(entry["commit"]):
            raise SyncError(f"skills.lock: {name} needs a full commit SHA")
        for field in ("patches", "files"):
            values = entry[field]
            if not isinstance(values, list):
                raise SyncError(f"skills.lock: {name} {field} must be a list")
            normalized = {}
            for item in values:
                if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                    raise SyncError(f"skills.lock: {name} {field} entries need path and sha256")
                path_name = item["path"]
                digest = item["sha256"]
                if not isinstance(path_name, str) or not isinstance(digest, str) or not HASH.match(digest):
                    raise SyncError(f"skills.lock: {name} {field} has an invalid hash for {path_name!r}")
                normalized[path_name] = digest
            entry[field] = normalized
    return data


def sorted_entries(lock):
    return sorted(lock["skills"], key=lambda entry: entry["name"])


def write_lock(root, lock):
    skills = []
    for entry in sorted_entries(lock):
        serialized = {field: entry[field] for field in ENTRY_FIELDS}
        for field in ("patches", "files"):
            serialized[field] = [
                {"path": name, "sha256": digest}
                for name, digest in sorted(entry[field].items())
            ]
        skills.append(serialized)
    payload = {"version": LOCK_VERSION, "skills": skills}
    (root / "skills.lock").write_text(json.dumps(payload, indent=2) + "\n")


def patch_directory(root, name):
    return root / "patches" / name


def recorded_patches(root, name):
    directory = patch_directory(root, name)
    if not directory.is_dir():
        return {}
    return tree_hashes(directory)


def apply_patches(root, name, skill_dir):
    directory = patch_directory(root, name)
    if not directory.is_dir():
        return
    for patch in sorted(directory.iterdir()):
        if not patch.is_file():
            continue
        if patch.suffix != ".patch":
            raise SyncError(f"patches/{name}/{patch.name}: patch files must end in .patch")
        result = subprocess.run(
            ["git", "-C", str(skill_dir), "apply", "--whitespace=nowarn", str(patch)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SyncError(f"patches/{name}/{patch.name} failed to apply: {detail(result)}")


def frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    body = []
    for line in lines[1:]:
        if line.strip() == "---":
            return body
        body.append(line)
    return []


def require_human_only(skill_dir, name):
    required = HUMAN_ONLY.get(name)
    if required is None:
        return
    entry = skill_dir / "SKILL.md"
    if not entry.is_file() or required not in frontmatter(entry.read_text()):
        raise SyncError(f"skills/{name}/SKILL.md must keep {required}")


def clone_source(source, workspace, clones):
    if source in clones:
        return clones[source]
    destination = workspace / f"source-{len(clones)}"
    shell(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", source, str(destination)])
    clones[source] = destination
    return destination


def ensure_commit(clone, commit):
    exists = subprocess.run(
        ["git", "-C", str(clone), "cat-file", "-e", f"{commit}^{{commit}}"],
        capture_output=True,
    )
    if exists.returncode != 0:
        shell(["git", "-C", str(clone), "fetch", "--quiet", "origin", commit])


def references(directory):
    found = set()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        for match in REFERENCE.finditer(path.read_bytes()):
            found.add((path.relative_to(directory).as_posix(), match.group(1).decode()))
    return sorted(found)


def staged_reference_problems(root, staged):
    problems = []
    for name, directory in sorted(staged.items()):
        for relative, reference in references(directory):
            if reference in staged or (root / "skills" / reference).is_dir():
                continue
            problems.append(
                f"skills/{name}/{relative}: references ../{reference}/SKILL.md "
                f"but skills/{reference} does not exist"
            )
    return problems


def materialize(root, entry, clone, staging):
    commit = entry["commit"]
    path = entry["path"]
    archive = subprocess.run(
        ["git", "-C", str(clone), "archive", "--format=tar", commit, "--", path],
        capture_output=True,
    )
    if archive.returncode != 0:
        detail = archive.stderr.decode(errors="replace").strip()
        raise SyncError(f"skills/{entry['name']}: {path} is not in {entry['source']} at {commit}: {detail}")
    extracted = staging / ".extract"
    if extracted.exists():
        shutil.rmtree(extracted)
    extracted.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(extracted, filter="data")
    source = extracted / path
    if not source.is_dir():
        raise SyncError(f"skills/{entry['name']}: {path} is not a directory at {commit}")
    destination = staging / entry["name"]
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=True)
    shutil.rmtree(extracted)
    apply_patches(root, entry["name"], destination)
    require_human_only(destination, entry["name"])
    return destination


def stage_all(root, lock, new_commits, workspace, clones=None):
    staging = workspace / "staged"
    if not staging.is_dir():
        staging.mkdir()
    clones = {} if clones is None else clones
    staged = {}
    for entry in sorted_entries(lock):
        commit = new_commits.get(entry["source"], entry["commit"])
        if commit != entry["commit"]:
            entry["commit"] = commit
        clone = clone_source(entry["source"], workspace, clones)
        ensure_commit(clone, commit)
        staged[entry["name"]] = materialize(root, entry, clone, staging)
    problems = staged_reference_problems(root, staged)
    if problems:
        raise SyncError("; ".join(problems))
    return staged


def compare_trees(expected, actual, label, suffix=""):
    problems = []
    for relative in sorted(set(expected) - set(actual)):
        problems.append(f"{label}/{relative}: missing{suffix}")
    for relative in sorted(set(actual) - set(expected)):
        problems.append(f"{label}/{relative}: unexpected file{suffix}")
    for relative in sorted(set(expected) & set(actual)):
        if expected[relative] != actual[relative]:
            problems.append(f"{label}/{relative}: modified{suffix}")
    return problems


def report(problems, mode):
    if not problems:
        return 0
    for problem in problems:
        print(f"skills-sync: {problem}", file=sys.stderr)
    print(f"skills-sync: {mode} failed with {len(problems)} problem(s)", file=sys.stderr)
    return 1


def check_patches(root, entries, problems):
    patch_root = root / "patches"
    known = {entry["name"]: entry["patches"] for entry in entries}
    if patch_root.is_dir():
        for child in sorted(patch_root.iterdir()):
            if not child.is_dir():
                continue
            if child.name not in known:
                problems.append(f"patches/{child.name}: no lock entry")
                continue
            label = f"patches/{child.name}"
            actual = tree_hashes(child)
            problems.extend(compare_trees(known[child.name], actual, label))
    for entry in entries:
        directory = patch_directory(root, entry["name"])
        for name in sorted(entry["patches"]):
            if not (directory / name).is_file():
                problems.append(f"patches/{entry['name']}/{name}: missing")


def check(root, lock):
    problems = []
    entries = sorted_entries(lock)
    skills = root / "skills"
    for entry in entries:
        name = entry["name"]
        directory = skills / name
        label = f"skills/{name}"
        if not directory.is_dir():
            problems.append(f"{label}: missing vendored skill directory")
            continue
        problems.extend(
            compare_trees(entry["files"], tree_hashes(directory), label, " since the pinned commit")
        )
        try:
            require_human_only(directory, name)
        except SyncError as error:
            problems.append(str(error))
        for relative, reference in references(directory):
            if not (skills / reference).is_dir():
                problems.append(
                    f"{label}/{relative}: references ../{reference}/SKILL.md "
                    f"but skills/{reference} does not exist"
                )
    check_patches(root, entries, problems)
    if problems:
        return report(problems, "check")
    print(f"skills-sync: check passed for {len(entries)} vendored skills")
    return 0


def fetch_check(root, lock):
    problems = []
    entries = sorted_entries(lock)
    with tempfile.TemporaryDirectory(prefix="skills-sync-fetch-") as workspace:
        staged = stage_all(root, lock, {}, Path(workspace))
        for entry in entries:
            name = entry["name"]
            label = f"skills/{name}"
            upstream = tree_hashes(staged[name])
            committed_dir = root / "skills" / name
            committed = tree_hashes(committed_dir) if committed_dir.is_dir() else {}
            for relative in sorted(set(upstream) | set(committed)):
                if upstream.get(relative) != committed.get(relative):
                    problems.append(
                        f"{label}/{relative}: differs from upstream at {entry['commit']}"
                    )
            for relative in sorted(set(upstream) | set(entry["files"])):
                if upstream.get(relative) != entry["files"].get(relative):
                    problems.append(
                        f"{label}/{relative}: lock hash does not match upstream at {entry['commit']}"
                    )
    check_patches(root, entries, problems)
    if problems:
        return report(problems, "fetch check")
    print(f"skills-sync: fetch check passed for {len(entries)} vendored skills at their pins")
    return 0


def write_trees(root, entries, staged):
    for entry in entries:
        name = entry["name"]
        destination = root / "skills" / name
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staged[name], destination, symlinks=True)


def record(root, lock, staged):
    for entry in sorted_entries(lock):
        entry["files"] = tree_hashes(staged[entry["name"]])
        entry["patches"] = recorded_patches(root, entry["name"])


def sync(root, lock):
    entries = sorted_entries(lock)
    with tempfile.TemporaryDirectory(prefix="skills-sync-") as workspace:
        staged = stage_all(root, lock, {}, Path(workspace))
        record(root, lock, staged)
        write_trees(root, entries, staged)
        write_lock(root, lock)
    print(f"skills-sync: synced {len(entries)} vendored skills")
    return 0


def update(root, lock):
    entries = sorted_entries(lock)
    sources = sorted({entry["source"] for entry in entries})
    commits = {}
    for source in sources:
        output = shell(["git", "ls-remote", source, "HEAD"])
        lines = [line for line in output.splitlines() if line.strip()]
        if not lines:
            raise SyncError(f"{source}: no HEAD advertised")
        commit = lines[0].split()[0]
        if not FULL_SHA.match(commit):
            raise SyncError(f"{source}: invalid HEAD {commit!r}")
        commits[source] = commit
    bumped = [entry for entry in entries if commits[entry["source"]] != entry["commit"]]
    if not bumped:
        print("skills-sync: every pin is current")
        return 0
    with tempfile.TemporaryDirectory(prefix="skills-sync-update-") as workspace:
        workspace = Path(workspace)
        clones = {}
        for entry in bumped:
            old = entry["commit"]
            new = commits[entry["source"]]
            clone = clone_source(entry["source"], workspace, clones)
            ensure_commit(clone, old)
            ensure_commit(clone, new)
            diff = subprocess.run(
                ["git", "-C", str(clone), "diff", old, new, "--", entry["path"]],
                capture_output=True,
                text=True,
            ).stdout.strip()
            print(f"skills-sync: {entry['name']}: {old} -> {new}")
            print(diff if diff else f"(no upstream changes under {entry['path']})")
        staged = stage_all(root, lock, commits, workspace, clones)
        record(root, lock, staged)
        write_trees(root, entries, staged)
        write_lock(root, lock)
    print(f"skills-sync: updated {len(bumped)} pins")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Sync Vendored Skills at their pinned commits, or check them for drift.",
    )
    parser.add_argument("--root", default=".", help="Factory checkout to operate on")
    parser.add_argument("--check", action="store_true", help="fail on any drift instead of syncing")
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="with --check, re-fetch every pin and compare it with the committed tree",
    )
    parser.add_argument("--update", action="store_true", help="bump every pin and show the upstream diff")
    args = parser.parse_args()
    if args.check and args.update:
        parser.error("--check and --update cannot be combined")
    if args.fetch and not args.check:
        parser.error("--fetch requires --check")
    root = Path(args.root).resolve()
    try:
        lock = load_lock(root)
        if args.update:
            return update(root, lock)
        if args.check:
            return fetch_check(root, lock) if args.fetch else check(root, lock)
        return sync(root, lock)
    except SyncError as error:
        print(f"skills-sync: {error}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as error:
        print(f"skills-sync: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
