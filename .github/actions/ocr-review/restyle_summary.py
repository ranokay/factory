from __future__ import annotations

import re
from typing import Any, Iterable, NamedTuple

SUMMARY_MARKER = "<!-- ocr-summary -->"
TITLE_LIMIT = 110

# OCR's marker formats must survive verbatim; see buildRunTags and
# buildCheckpointMarker in
# https://github.com/alibaba/open-code-review/blob/d29ca2044a42ea297a7fa93692d0f5ac8f38d84e/scripts/github-actions/post-review-comments.js
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
PRIORITY = {4: "🔴 P0", 3: "🟠 P1", 2: "🟡 P2", 1: "⚪ P3"}


class RewriteError(Exception):
    pass


class Policy(NamedTuple):
    severity_rank: int
    categories: frozenset[str]


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
    return PRIORITY[SEVERITY_RANK[normalize(comment.get("severity"))]]


def is_diagram_carrier(comment: dict[str, Any]) -> bool:
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


def diagram_blocks(carriers: Iterable[dict[str, Any]]) -> list[str]:
    blocks: list[str] = []
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


def status_line(result: dict[str, Any], excluded: int) -> str:
    raw = result.get("message")
    text = clean_sentence(raw.strip()) if isinstance(raw, str) else ""
    return decrement_finding_count(text, excluded) if text and excluded else text


def build_policy(severity_below: str, categories: str) -> Policy:
    threshold = 0
    named = normalize(severity_below)
    if named:
        if named not in SEVERITY_RANK:
            raise RewriteError(f"unknown route severity: {severity_below}")
        threshold = SEVERITY_RANK[named]
    tokens: set[str] = set()
    for token in (categories or "").split(","):
        value = normalize(token)
        if not value:
            continue
        if value not in CATEGORIES:
            raise RewriteError(f"unknown route category: {token}")
        tokens.add(value)
    return Policy(threshold, frozenset(tokens))


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
    existing = comment.get("existing_code")
    if not isinstance(code, str) or not code.strip():
        return ""
    if not isinstance(existing, str) or not existing.strip():
        return ""
    return (
        "\n\n<details><summary>Suggested change</summary>\n\n"
        "```suggestion\n"
        f"{code.rstrip()}\n"
        "```\n\n"
        "</details>"
    )


def summary_reason(body: str, comment: dict[str, Any]) -> str:
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
    return ""


def ordering(comment: dict[str, Any]) -> tuple[int, str, int, int]:
    return (
        -SEVERITY_RANK[normalize(comment.get("severity"))],
        str(comment.get("path") or ""),
        integer(comment.get("start_line")),
        integer(comment.get("end_line")),
    )


def finding_parts(comment: dict[str, Any]) -> tuple[str, str, str]:
    title = title_of(comment)
    return title, explanation_of(comment, title), suggestion_block(comment)


def finding_block(comment: dict[str, Any], note: str = "") -> str:
    title, explanation, suggestion = finding_parts(comment)
    block = f"**{badge_of(comment)} · {title}** · `{code_span(where_of(comment))}`"
    if note:
        block += f"\n{note}"
    if explanation:
        block += f"\n\n{explanation}"
    return block + suggestion


def warning_entry(warning: Any) -> str:
    if isinstance(warning, str):
        return safe_text(warning)
    if isinstance(warning, dict):
        parts: list[str] = []
        if warning.get("file"):
            parts.append(f"`{code_span(str(warning['file']))}`")
        if warning.get("type"):
            parts.append(f"(`{code_span(str(warning['type']))}`)")
        message = safe_text(str(warning.get("message") or ""))
        if message:
            parts.append(message)
        return " ".join(parts)
    return safe_text(str(warning))


def warning_block(warnings: Iterable[Any]) -> str:
    items = [warning_entry(warning) for warning in warnings]
    items = [item for item in items if item]
    if not items:
        return ""
    bullets = "\n".join(f"- {item}" for item in items)
    return (
        f"<details><summary>{len(items)} warnings</summary>\n\n"
        f"{bullets}\n\n</details>"
    )


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
    title, explanation, suggestion = finding_parts(comment)
    parts: list[str] = []
    if marks:
        parts.append("\n".join(marks))
    parts.append(f"**{badge_of(comment)} · {title}**")
    if explanation:
        parts.append(explanation)
    if suggestion:
        parts.append(suggestion.strip())
    return "\n\n".join(parts).strip() + "\n"


def partition_comments(
    body: str,
    comments: list[dict[str, Any]],
    links: dict[int, str],
    policy: Policy,
) -> tuple[
    list[tuple[dict[str, Any], str]],
    list[dict[str, Any]],
    list[tuple[dict[str, Any], str]],
    list[dict[str, Any]],
]:
    inline: list[tuple[dict[str, Any], str]] = []
    routed: list[dict[str, Any]] = []
    failed: list[tuple[dict[str, Any], str]] = []
    skipped: list[dict[str, Any]] = []
    for index, comment in enumerate(comments):
        if is_diagram_carrier(comment):
            continue
        if index in links:
            inline.append((comment, links[index]))
        elif has_line(comment) and is_routed(comment, policy):
            routed.append(comment)
        elif reason := summary_reason(body, comment):
            failed.append((comment, reason))
        elif not has_line(comment):
            failed.append((comment, "No line information provided"))
        else:
            skipped.append(comment)
    inline.sort(key=lambda item: ordering(item[0]))
    routed.sort(key=ordering)
    failed.sort(key=lambda item: ordering(item[0]))
    return inline, routed, failed, skipped


def check_counts(
    expected: dict[str, int | None] | None,
    inline: list[tuple[dict[str, Any], str]],
    skipped: list[dict[str, Any]],
) -> None:
    if not expected:
        return
    for name, actual in (("inline", len(inline)), ("skipped", len(skipped))):
        wanted = expected.get(name)
        if wanted is not None and wanted != actual:
            raise RewriteError(
                f"OCR reported {wanted} {name} comment(s) but the result has {actual}"
            )


def findings_table(
    inline: list[tuple[dict[str, Any], str]],
    failed: list[tuple[dict[str, Any], str]],
) -> str:
    rows = ["| | Finding | Where |", "|---|---|---|"]
    for comment, url in inline:
        title = link_text(title_of(comment))
        rows.append(
            f"| {badge_of(comment)} | [{title}]({url}) | `{code_span(where_of(comment))}` |"
        )
    for comment, reason in failed:
        rows.append(
            f"| {badge_of(comment)} | {cell(title_of(comment))} | "
            f"`{code_span(where_of(comment))}`"
            f"<br>⚠️ Not posted inline: {cell(reason)} |"
        )
    return "\n".join(rows) if len(rows) > 2 else ""


def lower_priority(routed: list[dict[str, Any]]) -> str:
    if not routed:
        return ""
    details = "\n\n".join(finding_block(comment) for comment in routed)
    return (
        f"<details><summary>{len(routed)} lower-priority findings "
        f"(in summary only)</summary>\n\n{details}\n\n</details>"
    )


def failed_details(failed: list[tuple[dict[str, Any], str]]) -> str:
    if not failed:
        return ""
    details = "\n\n".join(
        finding_block(comment, f"⚠️ Not posted inline: {cell(reason)}")
        for comment, reason in failed
    )
    return (
        f"<details><summary>{len(failed)} finding(s) not posted inline</summary>"
        f"\n\n{details}\n\n</details>"
    )


def diagram_section(carriers: Iterable[dict[str, Any]]) -> str:
    diagrams = diagram_blocks(carriers)
    if not diagrams:
        return ""
    inner = "\n\n".join(f"```mermaid\n{block}\n```" for block in diagrams)
    return f"<details><summary>Diagram</summary>\n\n{inner}\n\n</details>"


def warnings_section(result: dict[str, Any]) -> str:
    warnings = result.get("warnings")
    if not isinstance(warnings, list):
        return ""
    return warning_block(warnings)


def rewrite_summary(
    body: str,
    result: dict[str, Any],
    links: dict[int, str],
    meta: dict[str, str],
    policy: Policy,
    expected: dict[str, int | None] | None = None,
) -> str:
    marks = markers(body)
    raw_comments = list(result.get("comments") or [])
    carriers = [comment for comment in raw_comments if is_diagram_carrier(comment)]
    rating = score(raw_comments)
    text = status_line(result, len(carriers))
    inline, routed, failed, skipped = partition_comments(
        body, raw_comments, links, policy
    )
    check_counts(expected, inline, skipped)
    blocks = [f"## AI Review · {rating}/5 · {verdict(rating)}", text]
    table = findings_table(inline, failed)
    if table:
        blocks.append(table)
    if skipped:
        blocks.append(
            f"> {len(skipped)} finding(s) were already covered by another review thread."
        )
    for section in (
        lower_priority(routed),
        failed_details(failed),
        diagram_section(carriers),
        warnings_section(result),
        footer(meta),
    ):
        if section:
            blocks.append(section)
    prefix = "\n".join(marks)
    body_text = "\n\n".join(block for block in blocks if block).strip()
    return (prefix + "\n\n" if prefix else "") + body_text + "\n"
