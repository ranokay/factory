"""Shared constants for the new-repository command."""

import re
from pathlib import Path

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
ROOT = Path(__file__).resolve().parent
FACTORY = ROOT.parent
SYNC_UPSTREAM = FACTORY / "skills/sync-upstream/scripts/sync-upstream"
REPO_SETTINGS = ROOT / "repo-settings.py"
