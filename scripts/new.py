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
import re
import sys
from pathlib import Path

from adopt_common import Problem, problem
from new_common import DESCRIPTION_LIMIT, FACTORY, HANDOFF, NAME_RE, OWNER
from new_ideas import ensure_ideas, find_idea, list_items, promote
from new_profiles import (
    apply_profiles,
    check_existing_pins,
    existing_hk_tag,
    existing_tag,
    profile_chain,
    release_tag,
)
from new_repo import (
    apply_repo_settings,
    commit_and_push,
    create_repo,
    ensure_clone,
    has_head,
    install_template,
    main_requires_pr,
    repo_exists,
    restore_clone,
)


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
    idea = find_idea(list_items(project), args.idea) if args.idea else None
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
    protected = main_requires_pr(slug)
    agents_absent = not (clone / "AGENTS.md").exists()
    hk_absent = not (clone / "hk.pkl").exists()
    tag = existing_tag(clone) or existing_hk_tag(clone) or current_tag
    try:
        install_template(clone, factory / "project")
        apply_profiles(clone, chain, tag, agents_absent, hk_absent)
        symlink_claude(clone)
        if idea and idea.draft and not protected:
            seed_product(clone, idea)
        elif idea and idea.draft:
            print(
                "new: the repository requires a pull request; "
                "promoting without changing PRODUCT.md"
            )
    except Problem:
        restore_clone(clone)
        raise
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
