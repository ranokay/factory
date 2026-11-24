import json
from pathlib import Path

FACTORY_ROOT = Path(__file__).resolve().parents[3]


def factory_authors(root=None):
    path = Path(root or FACTORY_ROOT) / "repo-settings.json"
    return {str(name).lower() for name in json.loads(path.read_text())["factory_authors"]}


def is_outside(pr, repo, authors=None):
    allowed = factory_authors() if authors is None else {name.lower() for name in authors}
    head = pr.get("head") or {}
    head_repo = (head.get("repo") or {}).get("full_name")
    author = str((pr.get("user") or {}).get("login") or "").lower()
    return head_repo is None or head_repo.lower() != repo.lower() or author not in allowed
