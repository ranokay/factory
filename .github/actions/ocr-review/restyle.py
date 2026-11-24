#!/usr/bin/env python3
"""Restyle OpenCodeReview's PR comments into the Factory's review layout.

OCR posts a sticky summary and inline review comments, and its action has no
template hook, so this script rewrites both after OCR finishes:

- the summary becomes a priority-sorted table with a computed score, a
  collapsed "in summary only" block for policy-routed findings, an optional
  Diagram, and a footer;
- each inline comment becomes a bold badge and title, an explanation, and a
  collapsed suggested change.

Every hidden OCR marker is copied verbatim, so checkpointing, incremental
dedupe and outdated-thread resolution keep working. A failure at any point
restores every body already written and leaves OCR's original comments in
place; the caller turns a non-zero exit into a job warning.

The result schema and comment shapes come from OCR at
d29ca2044a42ea297a7fa93692d0f5ac8f38d84e:
scripts/github-actions/post-review-comments.js and
cmd/opencodereview/output.go.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Iterable, NamedTuple

SUMMARY_MARKER = "<!-- ocr-summary -->"
MAX_BODY = 65000
TITLE_LIMIT = 110

# Hidden markers OCR writes: the summary marker, the per-run summary tag, the
# checkpoint payload, the per-run review tag and the inline comment id.
MARKER = re.compile(r"<!--\s*ocr-[^<>]*?-->")
MERMAID = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)
LONG_REASON = re.compile(r"⚠️ GitHub could not post this as an inline comment: ([^\n]+)")
SHORT_REASON = re.compile(r"⚠️ Not posted inline: ([^\n|]+)")
HTML_CONTAINER = re.compile(r"</?(?:details|summary)\b[^>]*>", re.IGNORECASE)

CATEGORIES = frozenset(
    (
        "bug",
        "security",
        "performance",
        "maintainability",
        "test",
        "style",
        "documentation",
        "other",
    )
)
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEVERITY_BADGE = {
    "critical": "🔴 P0",
    "high": "🟠 P1",
    "medium": "🟡 P2",
    "low": "⚪ P3",
}
UNKNOWN_BADGE = "⚪ P3"


class RewriteError(Exception):
    """A condition that must leave OCR's comments untouched."""


class Policy(NamedTuple):
    severity_rank: int
    categories: frozenset[str]


def warn(message: str) -> None:
    text = str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::warning::{text}")


def normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def markers(body: str) -> list[str]:
    return MARKER.findall(body or "")


def strip_markers(body: str) -> str:
    cleaned = MARKER.sub("", body or "")
    return re.sub(r"\n{3,}", "\n\n", cleaned)


def safe_text(text: str) -> str:
    """Keep a finding from closing the details/summary containers we render."""
    return HTML_CONTAINER.sub(
        lambda match: match.group(0).replace("<", "&lt;").replace(">", "&gt;"), text
    )


def plain_title(value: str) -> str:
    value = re.sub(r"^\s*#+\s*", "", value.strip())
    value = value.strip().strip("*").strip()
    value = value.replace("**", "").strip()
    return value.rstrip(".").strip()


def first_sentence(text: str) -> str:
    text = text.strip()
    match = re.search(r"(?<=[.!?])\s+", text)
    return text[: match.start()].strip() if match else text


def title_of(comment: dict[str, Any]) -> str:
    content = strip_markers(str(comment.get("content") or "")).strip()
    if not content:
        return "Finding"
    first = content.splitlines()[0].strip()
    candidate = plain_title(first)
    if len(candidate) > TITLE_LIMIT or candidate.endswith(":"):
        candidate = plain_title(first_sentence(content))
    candidate = re.sub(r"\s+", " ", candidate).strip()
    if len(candidate) > TITLE_LIMIT:
        candidate = candidate[:TITLE_LIMIT].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return safe_text(candidate) or "Finding"


def explanation_of(comment: dict[str, Any], title: str) -> str:
    content = strip_markers(str(comment.get("content") or "")).strip()
    if not content:
        return ""
    lines = content.splitlines()
    if len(lines) > 1 and safe_text(plain_title(lines[0])) == title:
        rest = "\n".join(lines[1:]).strip()
        if rest:
            return safe_text(rest)
    return safe_text(content)


def badge_of(comment: dict[str, Any]) -> str:
    return SEVERITY_BADGE.get(normalize(comment.get("severity")), UNKNOWN_BADGE)


def is_diagram_carrier(comment: dict[str, Any]) -> bool:
    """A documentation/low finding titled Diagram carries the flow diagram.

    OCR's routing policy moves it to the summary, so it is never posted inline.
    The carrier is not a finding: it is excluded from the score, the table, the
    lower-priority list and every count.
    """
    if normalize(comment.get("category")) != "documentation":
        return False
    if normalize(comment.get("severity")) != "low":
        return False
    return title_of(comment).casefold() == "diagram"


def split_mermaid(text: str) -> tuple[str, list[str]]:
    blocks: list[str] = []

    def take(match: re.Match[str]) -> str:
        blocks.append(match.group(1).strip())
        return ""

    return MERMAID.sub(take, text or "").strip(), blocks


def diagram_blocks(result: dict[str, Any], carriers: Iterable[dict[str, Any]]) -> list[str]:
    blocks: list[str] = []
    for key in ("project_summary", "analysis"):
        value = result.get(key)
        if isinstance(value, str):
            blocks.extend(split_mermaid(value)[1])
    for comment in carriers:
        blocks.extend(split_mermaid(strip_markers(str(comment.get("content") or "")))[1])
    return blocks


def score(comments: Iterable[dict[str, Any]]) -> int:
    severities = [
        normalize(comment.get("severity"))
        for comment in comments
        if not is_diagram_carrier(comment)
    ]
    if "critical" in severities:
        return 1
    if "high" in severities:
        return 2
    return max(3, 5 - severities.count("medium"))


def verdict(value: int) -> str:
    if value == 5:
        return "Safe to merge"
    if value >= 3:
        return "Merge after fixes"
    return "Blocking issues"


def clean_sentence(text: str) -> str:
    text = re.sub(r"^\s*[^\w`*]+", "", text.strip())
    text = re.sub(r"^\*\*[^*]+\*\*:?\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def decrement_finding_count(text: str, amount: int) -> str:
    def replace(match: re.Match[str]) -> str:
        value = max(0, int(match.group(1)) - amount)
        return f"{value}{match.group(2)}"

    return re.sub(r"\b(\d+)(\s+finding(?:\(s\)|s)?)", replace, text, count=1)


def sentence(result: dict[str, Any], excluded: int) -> str:
    raw = result.get("message")
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        findings = max(0, len(result.get("comments") or []) - excluded)
        files = 0
        summary = result.get("summary")
        if isinstance(summary, dict):
            files = integer(summary.get("files_reviewed"))
        if files:
            text = f"Review complete: {findings} finding(s) across {files} file(s)."
        else:
            text = f"Review complete: {findings} finding(s)."
    text = clean_sentence(text)
    return decrement_finding_count(text, excluded) if excluded else text


def build_policy(severity_below: str, categories: str) -> Policy:
    threshold = SEVERITY_RANK.get(normalize(severity_below), 0)
    tokens = frozenset(
        token for token in (categories or "").split(",") if normalize(token) in CATEGORIES
    )
    return Policy(threshold, tokens)


def has_line(comment: dict[str, Any]) -> bool:
    return integer(comment.get("start_line")) >= 1 or integer(comment.get("end_line")) >= 1


def is_routed(comment: dict[str, Any], policy: Policy) -> bool:
    severity = normalize(comment.get("severity"))
    category = normalize(comment.get("category"))
    rank = SEVERITY_RANK.get(severity)
    if policy.severity_rank and rank is not None and rank <= policy.severity_rank:
        return True
    return category in policy.categories


def where_of(comment: dict[str, Any]) -> str:
    path = str(comment.get("path") or "").strip()
    start = integer(comment.get("start_line"))
    end = integer(comment.get("end_line"))
    if start and end and start != end:
        return f"{path}:{start}-{end}" if path else f"L{start}-L{end}"
    line = start or end
    if line:
        return f"{path}:{line}" if path else f"L{line}"
    return path or "this PR"


def cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).replace("|", r"\|").strip()


def link_text(value: str) -> str:
    return cell(value).replace("[", r"\[").replace("]", r"\]")


def code_span(value: str) -> str:
    return cell(value).replace("`", r"\`")


def suggestion_block(comment: dict[str, Any]) -> str:
    code = comment.get("suggestion_code")
    if not isinstance(code, str) or not code.strip():
        return ""
    return (
        "\n\n<details><summary>Suggested change</summary>\n\n"
        "```suggestion\n"
        f"{code.rstrip()}\n"
        "```\n\n"
        "</details>"
    )


def reason_for(body: str, comment: dict[str, Any]) -> str:
    content = strip_markers(str(comment.get("content") or "")).strip()
    index = body.find(content[:120]) if content else -1
    if index >= 0:
        window = body[max(0, index - 1200) : index]
        found = LONG_REASON.findall(window) + SHORT_REASON.findall(window)
        if found:
            reason = found[-1].strip()
            if not reason.lower().startswith("routed to summary"):
                return reason
    title = title_of(comment)
    titles = {title, cell(title)}
    for line in body.splitlines():
        if not any(candidate and candidate in line for candidate in titles):
            continue
        match = SHORT_REASON.search(line)
        if match:
            return match.group(1).strip()
        match = LONG_REASON.search(line)
        if match and not match.group(1).lower().startswith("routed to summary"):
            return match.group(1).strip()
    if not has_line(comment):
        return "No line information provided"
    return "Not posted"


def ordering(comment: dict[str, Any]) -> tuple[int, str, int, int]:
    return (
        SEVERITY_ORDER.get(normalize(comment.get("severity")), 9),
        str(comment.get("path") or ""),
        integer(comment.get("start_line")),
        integer(comment.get("end_line")),
    )


def finding_block(comment: dict[str, Any]) -> str:
    title = title_of(comment)
    explanation = explanation_of(comment, title)
    block = f"**{badge_of(comment)} · {title}** · `{code_span(where_of(comment))}`"
    if explanation:
        block += f"\n\n{explanation}"
    return block + suggestion_block(comment)


def footer(meta: dict[str, str]) -> str:
    head = (meta.get("head") or meta.get("to") or "")[:7]
    start = (meta.get("from") or "")[:7]
    end = (meta.get("to") or meta.get("head") or "")[:7]
    bits: list[str] = []
    if head:
        reviewed = f"Reviewed `{head}`"
        if start and end:
            reviewed += f" (range `{start}..{end}`)"
        bits.append(reviewed)
    version = str(meta.get("version") or "").lstrip("v")
    if version:
        bits.append(f"OCR {version}")
    if meta.get("model"):
        bits.append(meta["model"])
    if meta.get("effort"):
        bits.append(f"effort {meta['effort']}")
    return f"<sub>{' · '.join(bits)}</sub>" if bits else ""


def rewrite_inline(body: str, comment: dict[str, Any]) -> str:
    marks = markers(body)
    title = title_of(comment)
    explanation = explanation_of(comment, title)
    parts: list[str] = []
    if marks:
        parts.append("\n".join(marks))
    parts.append(f"**{badge_of(comment)} · {title}**")
    if explanation:
        parts.append(explanation)
    suggestion = suggestion_block(comment)
    if suggestion:
        parts.append(suggestion.strip())
    return "\n\n".join(parts).strip() + "\n"


def rewrite_summary(
    body: str,
    result: dict[str, Any],
    links: dict[int, str],
    meta: dict[str, str],
    policy: Policy,
) -> str:
    marks = markers(body)
    raw_comments = list(result.get("comments") or [])
    carriers = [comment for comment in raw_comments if is_diagram_carrier(comment)]
    value = score(raw_comments)
    text = sentence(result, len(carriers))

    inline: list[tuple[dict[str, Any], str]] = []
    routed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for index, comment in enumerate(raw_comments):
        if is_diagram_carrier(comment):
            continue
        if index in links:
            inline.append((comment, links[index]))
        elif has_line(comment) and is_routed(comment, policy):
            routed.append(comment)
        else:
            failed.append(comment)
    inline.sort(key=lambda item: ordering(item[0]))
    routed.sort(key=ordering)
    failed.sort(key=ordering)

    blocks = [f"## AI Review · {value}/5 · {verdict(value)}", text]

    rows = ["| | Finding | Where |", "|---|---|---|"]
    for comment, url in inline:
        title = link_text(title_of(comment))
        rows.append(
            f"| {badge_of(comment)} | [{title}]({url}) | `{code_span(where_of(comment))}` |"
        )
    for comment in failed:
        reason = cell(reason_for(body, comment))
        rows.append(
            f"| {badge_of(comment)} | {cell(title_of(comment))} | "
            f"`{code_span(where_of(comment))}`<br>⚠️ Not posted inline: {reason} |"
        )
    if inline or failed:
        blocks.append("\n".join(rows))

    if routed:
        details = "\n\n".join(finding_block(comment) for comment in routed)
        blocks.append(
            f"<details><summary>{len(routed)} lower-priority findings "
            f"(in summary only)</summary>\n\n{details}\n\n</details>"
        )

    diagrams = diagram_blocks(result, carriers)
    if diagrams:
        inner = "\n\n".join(f"```mermaid\n{block}\n```" for block in diagrams)
        blocks.append(f"<details><summary>Diagram</summary>\n\n{inner}\n\n</details>")

    warnings = result.get("warnings")
    if isinstance(warnings, list) and warnings:
        blocks.append(
            f"> ⚠️ {len(warnings)} warning(s) during review; some changes may be unreviewed."
        )

    end = footer(meta)
    if end:
        blocks.append(end)

    prefix = "\n".join(marks)
    body_text = "\n\n".join(block for block in blocks if block).strip()
    return (prefix + "\n\n" if prefix else "") + body_text + "\n"


def load_result(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            result = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise RewriteError(f"cannot read {path}: {error}") from error
    validate(result)
    return result


def validate(result: Any) -> None:
    if not isinstance(result, dict):
        raise RewriteError("OCR result is not a JSON object")
    comments = result.get("comments")
    if comments is None:
        result["comments"] = []
    elif not isinstance(comments, list):
        raise RewriteError("OCR result comments is not a list")
    else:
        for index, comment in enumerate(comments):
            if not isinstance(comment, dict):
                raise RewriteError(f"OCR result comment {index} is not an object")
    for key in ("summary", "manifest"):
        value = result.get(key)
        if value is not None and not isinstance(value, dict):
            raise RewriteError(f"OCR result {key} is not an object")
    for key in ("warnings",):
        value = result.get(key)
        if value is not None and not isinstance(value, list):
            raise RewriteError(f"OCR result {key} is not a list")
    for key in ("message", "project_summary", "analysis", "status"):
        value = result.get(key)
        if value is not None and not isinstance(value, str):
            raise RewriteError(f"OCR result {key} is not a string")


def meta_from(result: dict[str, Any], environment: dict[str, str]) -> dict[str, str]:
    manifest = result.get("manifest") if isinstance(result.get("manifest"), dict) else {}
    source = manifest.get("input") if isinstance(manifest.get("input"), dict) else {}
    execution = (
        manifest.get("execution") if isinstance(manifest.get("execution"), dict) else {}
    )
    head = str(source.get("resolved_head") or "")
    target = str(environment.get("RANGE_TO") or "")
    return {
        "head": head or target,
        "from": str(environment.get("RANGE_FROM") or source.get("resolved_base") or ""),
        "to": target or head,
        "version": str(execution.get("ocr_version") or environment.get("VERSION") or ""),
        "model": str(execution.get("model") or environment.get("MODEL") or ""),
        "effort": str(environment.get("EFFORT") or ""),
    }


def match_inline(
    result: dict[str, Any], posted: list[dict[str, Any]]
) -> tuple[dict[int, str], list[tuple[dict[str, Any], dict[str, Any]]]]:
    links: dict[int, str] = {}
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    used: set[Any] = set()
    for index, comment in enumerate(result.get("comments") or []):
        if is_diagram_carrier(comment):
            continue
        content = strip_markers(str(comment.get("content") or "")).strip()
        title = title_of(comment)
        expected = f"**{badge_of(comment)} · {title}**"
        for item in posted:
            identifier = item.get("id")
            if identifier in used:
                continue
            body = str(item.get("body") or "")
            if (content and content in body) or expected in body:
                links[index] = str(item.get("html_url") or "")
                pairs.append((item, comment))
                used.add(identifier)
                break
    return links, pairs


class GitHub:
    def __init__(self, api_url: str, token: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self.api_url + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "factory-ocr-restyle",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 -- the base URL is the trusted GitHub API, overridable only by this workflow
                body = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            raise RewriteError(
                f"{method} {path} failed with HTTP {error.code}: {detail}"
            ) from error
        except urllib.error.URLError as error:
            raise RewriteError(f"{method} {path} failed: {error.reason}") from error
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise RewriteError(f"{method} {path} returned invalid JSON") from error

    def paginate(self, path: str) -> Iterable[dict[str, Any]]:
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            batch = self.request("GET", f"{path}{separator}per_page=100&page={page}")
            if not isinstance(batch, list):
                raise RewriteError(f"GET {path} did not return a list")
            yield from batch
            if len(batch) < 100:
                return
            page += 1

    def update_issue_comment(self, repo: str, identifier: Any, body: str) -> None:
        self.request("PATCH", f"/repos/{repo}/issues/comments/{identifier}", {"body": body})

    def update_review_comment(self, repo: str, identifier: Any, body: str) -> None:
        self.request("PATCH", f"/repos/{repo}/pulls/comments/{identifier}", {"body": body})


def find_summary(
    github: GitHub, repo: str, pr: Any, run_id: str, attempt: str
) -> dict[str, Any]:
    tag = f"<!-- ocr-summary-run:{run_id}-{attempt} -->"
    found: dict[str, Any] | None = None
    for comment in github.paginate(f"/repos/{repo}/issues/{pr}/comments"):
        body = comment.get("body")
        if isinstance(body, str) and SUMMARY_MARKER in body and tag in body:
            found = comment
    if found is None:
        raise RewriteError("no sticky summary comment for this run")
    return found


def find_inline(
    github: GitHub, repo: str, pr: Any, run_id: str, attempt: str
) -> list[dict[str, Any]]:
    pattern = re.compile(
        rf"<!--\s*ocr-{re.escape(run_id)}-{re.escape(attempt)}-[a-f0-9]+\s*-->"
    )
    return [
        comment
        for comment in github.paginate(f"/repos/{repo}/pulls/{pr}/comments")
        if pattern.search(str(comment.get("body") or ""))
    ]


def apply(
    github: GitHub,
    repo: str,
    summary: dict[str, Any],
    summary_body: str,
    inline_bodies: list[tuple[dict[str, Any], str]],
    dry_run: bool,
) -> list[tuple[str, Any, str, str]]:
    writes: list[tuple[str, Any, str, str]] = [
        ("issue", summary.get("id"), str(summary.get("body") or ""), summary_body)
    ]
    for posted, body in inline_bodies:
        writes.append(("review", posted.get("id"), str(posted.get("body") or ""), body))
    for kind, identifier, _, body in writes:
        if len(body) > MAX_BODY:
            raise RewriteError(f"{kind} comment {identifier} would exceed GitHub's size limit")
        if identifier is None:
            raise RewriteError(f"{kind} comment has no id")
    if dry_run:
        return writes
    applied: list[tuple[str, Any, str]] = []
    try:
        for kind, identifier, original, body in writes:
            if kind == "issue":
                github.update_issue_comment(repo, identifier, body)
            else:
                github.update_review_comment(repo, identifier, body)
            applied.append((kind, identifier, original))
    except Exception:
        for kind, identifier, original in reversed(applied):
            try:
                if kind == "issue":
                    github.update_issue_comment(repo, identifier, original)
                else:
                    github.update_review_comment(repo, identifier, original)
            except Exception as error:
                warn(f"OCR restyle rollback failed for {kind} {identifier}: {error}")
        raise
    return writes


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restyle OCR's PR comments")
    parser.add_argument("--result")
    parser.add_argument("--repo")
    parser.add_argument("--pr")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--token")
    parser.add_argument("--api-url")
    parser.add_argument("--range-reason")
    parser.add_argument("--range-from")
    parser.add_argument("--range-to")
    parser.add_argument("--route-severity-below")
    parser.add_argument("--route-categories")
    parser.add_argument("--effort")
    parser.add_argument("--model")
    parser.add_argument("--version")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    environment = {
        "RANGE_TO": args.range_to or os.environ.get("OCR_RANGE_TO", ""),
        "RANGE_FROM": args.range_from or os.environ.get("OCR_RANGE_FROM", ""),
        "EFFORT": args.effort or os.environ.get("OCR_EFFORT", ""),
        "MODEL": args.model or os.environ.get("OCR_MODEL", ""),
        "VERSION": args.version or os.environ.get("OCR_VERSION", ""),
    }
    try:
        repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
        token = args.token or os.environ.get("GITHUB_TOKEN", "")
        pr = args.pr or os.environ.get("PR_NUMBER") or os.environ.get("OCR_PR_NUMBER") or ""
        run_id = args.run_id or os.environ.get("GITHUB_RUN_ID", "")
        attempt = args.run_attempt or os.environ.get("GITHUB_RUN_ATTEMPT", "1")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise RewriteError("GITHUB_REPOSITORY is not owner/repo")
        if not re.fullmatch(r"\d+", str(pr)):
            raise RewriteError("PR number is missing or not numeric")
        if not re.fullmatch(r"\d+", str(run_id)) or not re.fullmatch(r"\d+", str(attempt)):
            raise RewriteError("run id or attempt is not numeric")
        if not token:
            raise RewriteError("GITHUB_TOKEN is missing")

        result = load_result(
            args.result or os.environ.get("OCR_RESULT", "/tmp/ocr-result.json")
        )
        range_reason = args.range_reason or os.environ.get("OCR_RANGE_REASON", "")
        if range_reason == "same_head_noop":
            print("OCR restyle: same head as the checkpoint; nothing to rewrite")
            return 0
        if result.get("status") == "skipped":
            print("OCR restyle: review skipped; nothing to rewrite")
            return 0

        policy = build_policy(
            args.route_severity_below
            or os.environ.get("OCR_ROUTE_SEVERITY_BELOW", ""),
            args.route_categories or os.environ.get("OCR_ROUTE_CATEGORIES", ""),
        )
        meta = meta_from(result, environment)
        github = GitHub(
            args.api_url or os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            token,
        )
        summary = find_summary(github, repo, pr, str(run_id), str(attempt))
        posted = find_inline(github, repo, pr, str(run_id), str(attempt))
        links, pairs = match_inline(result, posted)
        summary_body = rewrite_summary(
            str(summary.get("body") or ""), result, links, meta, policy
        )
        inline_bodies = [
            (item, rewrite_inline(str(item.get("body") or ""), comment))
            for item, comment in pairs
        ]
        writes = apply(
            github, repo, summary, summary_body, inline_bodies, args.dry_run
        )
        if args.dry_run:
            for kind, identifier, _, body in writes:
                print(f"--- {kind} {identifier} ---")
                print(body)
        else:
            print(
                f"OCR restyle: updated the summary and {len(inline_bodies)} inline comment(s)"
            )
    except Exception as error:
        warn(f"OCR restyle skipped: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
