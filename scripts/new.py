#!/usr/bin/env python3
"""Create a repository from the Project Template, or ensure the Ideas board.

new creates ranokay/<name>, makes its Canonical Clone under ~/Developer, seeds
the Project Template through `sync-upstream install-files`, writes the chosen
Profiles and their pinned includes, symlinks CLAUDE.md, and applies repository
settings through `repo-settings.py --apply`. An idea on the Ideas Project seeds
the repository and is converted to an issue in it. Every step is idempotent and
never deletes or force-pushes.
"""

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

from adopt_common import Problem, gh, git, git_out, parse_json, problem, run, show
from profiles_reader import (
    AMENDS,
    FACTORY_INCLUDE,
    PROFILES_LINE,
    PROFILE_PATH,
    TAG,
    factory_ref,
)

ROOT = Path(__file__).resolve().parent
FACTORY = ROOT.parent
SYNC_UPSTREAM = FACTORY / "skills/sync-upstream/scripts/sync-upstream"
REPO_SETTINGS = ROOT / "repo-settings.py"
OWNER = "ranokay"
IDEAS = "Ideas"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
DESCRIPTION_LIMIT = 350
FETCH_LIMIT = 1000
HANDOFF = (
    "grill-with-docs",
    "brand-assets (offer it; ranokay may decline)",
    "to-spec",
    "to-tickets",
)


class Idea(NamedTuple):
    id: str | None
    title: str
    body: str
    url: str
    draft: bool


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


def ensure_include(text, key, section, url, profile):
    values = array_values(text, key, section)
    kind = "tasks" if "/tasks.toml?" in url else "mise"

    def same(entry):
        return same_profile(entry, profile, kind)

    if [entry for entry in values if same(entry)] == [url]:
        return text, False
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
    span = config_array(text, key, section)
    if span is None:
        problem(f"the Project Template's {key} array is missing")
    start, end = span
    inner = text[start + 1 : end]
    newline = "\r\n" if "\r\n" in text else "\n"
    simple = re.compile(
        r'^(?P<indent>\s*)(?P<quote>["\'])(?P<value>[^"\']*)(?P=quote)'
        r"\s*,?\s*(?P<comment>#.*)?$"
    )
    lines = inner.splitlines(keepends=True)
    total = sum(1 for entry in values if same(entry))
    handled = 0
    replaced = False
    output = []
    for line in lines:
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        body = line[: -len(ending)] if ending else line
        match = simple.match(body)
        if match and same(match.group("value")):
            handled += 1
            if not replaced:
                comment = f' {match.group("comment")}' if match.group("comment") else ""
                output.append(
                    f'{match.group("indent")}{json.dumps(url)},{comment}{ending}'
                )
                replaced = True
            elif match.group("comment"):
                output.append(f'{match.group("indent")}{match.group("comment")}{ending}')
        else:
            output.append(line)
    if "\n" in inner and handled == total and (replaced or not total):
        if not replaced:
            indent = "  "
            for line in lines:
                match = re.match(r'^(\s*)"', line)
                if match:
                    indent = match.group(1)
                    break
            if not inner.endswith(("\n", "\r")):
                inner += newline
            inner = with_trailing_comma(inner)
            output = [inner, f'{indent}{json.dumps(url)},{newline}']
        updated = text[: start + 1] + "".join(output) + text[end:]
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
        text, _ = ensure_include(
            text, "includes", "task_config", tasks_url, profile
        )
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


def symlink_claude(clone):
    path = clone / "CLAUDE.md"
    if path.is_symlink():
        if path.readlink().as_posix() == "AGENTS.md":
            return
        problem(f"CLAUDE.md is a symlink to {path.readlink()}, not AGENTS.md")
    if path.exists():
        problem("CLAUDE.md is a regular file; make it a relative symlink to AGENTS.md by hand")
    path.symlink_to("AGENTS.md")
    print("new: created CLAUDE.md -> AGENTS.md")


def seed_product(clone, idea):
    path = clone / "PRODUCT.md"
    if not path.is_file():
        return
    text = path.read_text()
    lines = text.splitlines(keepends=True)
    changed = False
    for index, line in enumerate(lines):
        if line.strip() == "# <repo>":
            lines[index] = f"# {idea.title}\n"
            changed = True
        elif line.strip() == "<One sentence: what this is and who it is for.>":
            body = idea.body.strip() or idea.title
            lines[index] = body + "\n"
            changed = True
    if changed:
        path.write_text("".join(lines))
        print("new: seeded PRODUCT.md from the idea")


def slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:100] or "idea"


def repo_exists(slug):
    result = run(["gh", "repo", "view", slug, "--json", "nameWithOwner"])
    if result.returncode == 0:
        return True
    detail = (result.stderr or result.stdout).strip()
    if "HTTP 404" in detail or "Not Found" in detail or "Could not resolve to a Repository" in detail:
        return False
    problem(f"cannot read {slug}: {detail}")


def create_repo(slug, private, description):
    visibility = "--private" if private else "--public"
    print(f"new: creating {slug}")
    gh("repo", "create", slug, visibility, "--description", description)


def parse_remote(url):
    url = url.strip().rstrip("/")
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return match.group(1).lower() if match else None


def ensure_clone(clone, slug):
    if clone.exists():
        if not (clone / ".git").exists():
            problem(f"{clone} exists and is not a git checkout")
        origin = git(clone, "remote", "get-url", "origin")
        if origin.returncode != 0 or parse_remote(origin.stdout) != slug.lower():
            problem(f"{clone} does not have {slug} as origin")
        status = git(clone, "status", "--porcelain", "--untracked-files=all")
        if status.returncode != 0:
            problem(f"cannot read the status of {clone}")
        if status.stdout.strip():
            problem(f"{clone} has uncommitted changes; commit or stash them first")
        return False
    clone.parent.mkdir(parents=True, exist_ok=True)
    print(f"new: cloning into {clone}")
    result = run(["git", "clone", f"https://github.com/{slug}.git", str(clone)])
    if result.returncode != 0:
        problem(f"cannot clone {slug}: {(result.stderr or result.stdout).strip()}")
    return True


def has_head(clone):
    return git(clone, "rev-parse", "--verify", "HEAD").returncode == 0


def commit_and_push(clone, name):
    if not has_head(clone):
        git_out(clone, "checkout", "-b", "main")
    status = git(clone, "status", "--porcelain", "--untracked-files=all")
    if status.returncode != 0:
        problem("cannot read the new clone's status")
    committed = False
    signed = None
    if status.stdout.strip():
        git_out(clone, "add", "-A")
        message = f"chore: seed {name} from the Project Template"
        commit = run(["git", "-C", str(clone), "commit", "-qm", message])
        if commit.returncode == 0:
            signed = True
        else:
            print("new: signing the seed commit failed; committing unsigned", file=sys.stderr)
            commit = run(
                ["git", "-C", str(clone), "-c", "commit.gpgsign=false", "commit", "-qm", message]
            )
            if commit.returncode != 0:
                problem(f"cannot commit the seed: {(commit.stderr or commit.stdout).strip()}")
            signed = False
        committed = True
    pushed = False
    if committed or git(clone, "rev-parse", "--verify", "origin/main").returncode != 0:
        git_out(clone, "push", "-u", "origin", "main")
        pushed = True
    if committed:
        state = "signed" if signed else "unsigned"
        print(f"new: committed the seed ({state}){' and pushed' if pushed else ''}")
    return committed, pushed


def install_template(clone):
    result = run(
        [sys.executable, str(SYNC_UPSTREAM), "install-files", "--target", str(clone)]
    )
    show(result.stdout, sys.stdout)
    show(result.stderr, sys.stderr)
    if result.returncode != 0:
        problem("installing the Project Template failed")


def apply_repo_settings(slug):
    result = run([sys.executable, str(REPO_SETTINGS), slug, "--apply"])
    show(result.stdout, sys.stdout)
    show(result.stderr, sys.stderr)
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout).strip()
    problem(f"repository settings failed: {detail}")


def project_for(projects, title):
    for project in projects:
        if project.get("title") == title:
            return project
    return None


def list_projects():
    payload = parse_json(
        gh(
            "project",
            "list",
            "--owner",
            OWNER,
            "--format",
            "json",
            "--limit",
            str(FETCH_LIMIT),
        ),
        "gh project list",
    )
    projects = payload.get("projects", [])
    if payload.get("totalCount", len(projects)) > len(projects):
        problem(f"the owner has more than {FETCH_LIMIT} Projects; narrow the Ideas lookup")
    return projects


def ensure_ideas():
    project = project_for(list_projects(), IDEAS)
    if project:
        print(f"new: reusing the {IDEAS} Project (#{project['number']})")
        return project
    project = parse_json(
        gh("project", "create", "--owner", OWNER, "--title", IDEAS, "--format", "json"),
        "gh project create",
    )
    print(f"new: created the {IDEAS} Project (#{project['number']})")
    return project


def list_items(project):
    payload = parse_json(
        gh(
            "project",
            "item-list",
            str(project["number"]),
            "--owner",
            OWNER,
            "--format",
            "json",
            "--limit",
            str(FETCH_LIMIT),
        ),
        "gh project item-list",
    )
    items = payload.get("items", [])
    if payload.get("totalCount", len(items)) > len(items):
        problem(f"the {IDEAS} Project has more than {FETCH_LIMIT} items; narrow the lookup")
    return items


def find_idea(items, reference):
    matches = []
    for item in items:
        content = item.get("content") or {}
        if reference in (
            item.get("id"),
            content.get("id"),
            item.get("title"),
            content.get("title"),
        ):
            matches.append(item)
    if not matches:
        problem(f"the {IDEAS} Project has no item matching {reference!r}")
    drafts = [item for item in matches if (item.get("content") or {}).get("type") == "DraftIssue"]
    if not drafts:
        content = matches[0].get("content") or {}
        return Idea(
            id=None,
            title=item_title(matches[0]),
            body=content.get("body") or "",
            url=content.get("url") or "",
            draft=False,
        )
    if len(drafts) > 1:
        problem(f"{reference!r} matches more than one draft item; pass the item ID")
    item = drafts[0]
    content = item.get("content") or {}
    return Idea(
        id=item["id"],
        title=item_title(item),
        body=content.get("body") or "",
        url="",
        draft=True,
    )


def item_title(item):
    content = item.get("content") or {}
    return item.get("title") or content.get("title") or ""


def promote(project, idea, slug):
    node = gh("api", f"repos/{slug}", "--jq", ".node_id").strip()
    if not node:
        problem(f"cannot read the repository node ID for {slug}")
    query = (
        "mutation($itemId: ID!, $repositoryId: ID!) {"
        " convertProjectV2DraftIssueItemToIssue("
        "input: {itemId: $itemId, repositoryId: $repositoryId}"
        ") { item { content { ... on Issue { number url } } } } }"
    )
    payload = parse_json(
        gh(
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            f"itemId={idea.id}",
            "-f",
            f"repositoryId={node}",
        ),
        "gh api graphql",
    )
    converted = (payload.get("data") or {}).get("convertProjectV2DraftIssueItemToIssue")
    content = ((converted or {}).get("item") or {}).get("content") or {}
    if not content.get("url"):
        problem(f"could not convert {idea.title!r} to an issue in {slug}")
    print(f"new: promoted {idea.title!r} to {content['url']}")


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


def handoff(slug, clone):
    print(f"new: {slug} is ready at {clone}")
    print("new: hand-off, in this order:")
    for number, step in enumerate(HANDOFF, start=1):
        print(f"  {number}. {step}")


def new(args):
    factory = args.factory_root.expanduser().resolve()
    if not args.name and not args.ensure_ideas and not args.idea:
        problem("give a repository name, --ensure-ideas, or --idea")
    if args.name and not NAME_RE.match(args.name):
        problem(f"repository name must match {NAME_RE.pattern}")
    if not args.name and not args.idea:
        ensure_ideas()
        return 0
    current_tag = release_tag(factory)
    chain = profile_chain(args.profile or ["universal"], factory)
    project = ensure_ideas()
    items = list_items(project)
    idea = find_idea(items, args.idea) if args.idea else None
    name = args.name or slugify(idea.title)
    if not NAME_RE.match(name):
        problem(f"cannot derive a repository name from {idea.title!r}; pass a name")
    slug = f"{OWNER}/{name}"
    if idea and not idea.draft and f"/{slug}/issues/" not in idea.url:
        problem(f"{args.idea!r} is already an issue in {idea.url or 'another repository'}")
    description = idea.title[:DESCRIPTION_LIMIT] if idea else f"{name}, a Personal Project"
    clone = args.developer_root.expanduser().resolve() / name
    created = not repo_exists(slug)
    if created:
        create_repo(slug, not args.public, description)
    else:
        print(f"new: reusing {slug}")
    ensure_clone(clone, slug)
    check_existing_pins(clone)
    agents_absent = not (clone / "AGENTS.md").exists()
    hk_absent = not (clone / "hk.pkl").exists()
    tag = existing_tag(clone) or existing_hk_tag(clone) or current_tag
    install_template(clone)
    apply_profiles(clone, chain, tag, agents_absent, hk_absent)
    symlink_claude(clone)
    if created and idea and idea.draft:
        seed_product(clone, idea)
    committed, _ = commit_and_push(clone, name)
    if not has_head(clone):
        problem("the new clone has no commit")
    apply_repo_settings(slug)
    if idea and idea.draft:
        promote(project, idea, slug)
    elif idea:
        print(f"new: {idea.title!r} is already promoted; nothing to convert")
    if committed:
        print("new: the seed is on main; open the first PR when the project has a change")
    handoff(slug, clone)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", nargs="?", help="repository name under ranokay")
    parser.add_argument(
        "--profile",
        action="append",
        help="Profile to apply; repeat for a chain such as universal, typescript, web (default: universal)",
    )
    parser.add_argument(
        "--idea",
        help="draft Ideas item ID or exact title to seed and promote",
    )
    parser.add_argument(
        "--ensure-ideas",
        action="store_true",
        help="create the Ideas Project when absent, with no repository",
    )
    parser.add_argument("--public", action="store_true", help="create a public repository")
    parser.add_argument(
        "--developer-root",
        type=Path,
        default=Path.home() / "Developer",
        help="root for Canonical Clones (default: ~/Developer)",
    )
    parser.add_argument(
        "--factory-root",
        type=Path,
        default=FACTORY,
        help="Factory checkout holding the Project Template (default: this checkout)",
    )
    args = parser.parse_args(argv)
    try:
        return new(args)
    except Problem as error:
        print(f"new: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
