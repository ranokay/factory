#!/usr/bin/env python3
"""Category titles, labels and work instructions for adopt."""

from adopt_detectors import (
    agents_findings,
    brand_findings,
    comment_findings,
    dead_code_findings,
    stale_findings,
    test_pruning_findings,
    verification_findings,
)
from adopt_tooling import tooling_findings

TOOLING_INSTRUCTION = (
    "Add the Factory baseline in a PR: `mise.toml` including the Factory Profile "
    "fragments, `hk.pkl` amending the matching Profile, `renovate.json` extending "
    "`github>ranokay/factory`, and the advisory bot configs. The Factory's "
    "`project/` directory holds the template. Run `mise run check`."
)
COMMENTS_INSTRUCTION = (
    "Run the `no-comments` skill or the universal Profile's `comment-ban.sh`; "
    "delete every banned marker and commented-out line. Run `mise run check`."
)
DEAD_CODE_INSTRUCTION = (
    "In a Run, confirm each candidate is unreferenced (including dynamic imports "
    "and reflection), delete the dead code, and let tests prove behaviour. If a "
    "candidate is not provably dead, comment on this issue instead of deleting it."
)
AGENTS_INSTRUCTION = (
    "Rewrite `AGENTS.md` from the Factory's `project/AGENTS.md` for this repo's "
    "declared Profiles, keep it as short as the repo allows, and make `CLAUDE.md` "
    "a relative symlink to `AGENTS.md`."
)
STALE_INSTRUCTION = (
    "Remove each tracked artifact in a PR and add its pattern to `.gitignore` when "
    "it is generated. If a file is referenced by the app or a test, keep it and "
    "comment why on this issue."
)
VERIFICATION_INSTRUCTION = (
    "Run `/create-verification-skill` to add `.agents/skills/verify-<app>/SKILL.md`, "
    "its `references/features/` map, the `.claude/skills` symlink, and the "
    "`verify:<app>` mise task."
)
TEST_PRUNING_INSTRUCTION = (
    "Work this issue with `/prune-tests`. Prune every test that guards no "
    "behaviour, keep the behaviour tests, and run `mise run check`."
)
BRAND_INSTRUCTION = (
    "Run `brand-assets` when it lands "
    "(https://github.com/ranokay/factory/issues/24) in the interactive session. "
    "Choose one concept; the masters land in `assets/brand/` and the `assets` "
    "task generates every export. `adopt` files this once."
)

CATEGORIES = (
    {
        "key": "tooling",
        "title": "Adopt: tooling baseline",
        "state": "ready-for-agent",
        "findings": tooling_findings,
        "instruction": TOOLING_INSTRUCTION,
    },
    {
        "key": "comments",
        "title": "Adopt: purge slop comments",
        "state": "ready-for-agent",
        "findings": comment_findings,
        "instruction": COMMENTS_INSTRUCTION,
    },
    {
        "key": "dead-code",
        "title": "Adopt: remove dead code",
        "state": "ready-for-agent",
        "findings": dead_code_findings,
        "instruction": DEAD_CODE_INSTRUCTION,
    },
    {
        "key": "agents",
        "title": "Adopt: rewrite AGENTS.md",
        "state": "ready-for-agent",
        "findings": agents_findings,
        "instruction": AGENTS_INSTRUCTION,
    },
    {
        "key": "stale-files",
        "title": "Adopt: remove stale files",
        "state": "ready-for-agent",
        "findings": stale_findings,
        "instruction": STALE_INSTRUCTION,
    },
    {
        "key": "verification",
        "title": "Adopt: add a verification skill",
        "state": "ready-for-agent",
        "findings": verification_findings,
        "instruction": VERIFICATION_INSTRUCTION,
    },
    {
        "key": "test-pruning",
        "title": "Adopt: prune tests",
        "state": "ready-for-agent",
        "findings": test_pruning_findings,
        "instruction": TEST_PRUNING_INSTRUCTION,
    },
    {
        "key": "brand",
        "title": "Brand assets needed",
        "state": "ready-for-human",
        "findings": brand_findings,
        "instruction": BRAND_INSTRUCTION,
    },
)
