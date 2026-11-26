"""Ideas Project lookup and promotion for the new-repository command."""

from typing import NamedTuple

from adopt_common import gh, parse_json, problem
from new_common import FETCH_LIMIT, IDEAS, OWNER


class Idea(NamedTuple):
    id: str | None
    title: str
    body: str
    url: str
    draft: bool


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
