#!/usr/bin/env python3
"""Shared markers, context and label rules for the pushback review."""

import os
import re
import subprocess  # nosec B404 -- gh runs with an argument list, never a shell
import sys
import tempfile
from pathlib import Path

from triage import CATEGORIES, DISCLAIMER, gh, parse_json, problem

MARKER = re.compile(r"<!-- factory:pushback (?P<attrs>[^>]*?) -->")
ACCEPT = re.compile(r"<!-- factory:pushback-accept (?P<attrs>[^>]*?) -->")
OVERRIDE = re.compile(r"<!-- factory:pushback-override (?P<attrs>[^>]*?) -->")
CONFIRM = re.compile(r"<!-- factory:pushback-confirm (?P<attrs>[^>]*?) -->")
ATTR = re.compile(r"([a-z-]+)=([^\s]+)")
TRIAGE_MARKER = "<!-- factory:triage -->"
REPRODUCED = "**Outcome:** reproduced"
FULL_CHECKS = ("evidence", "simplest", "overlap", "maintenance", "reversibility", "non-goals")
LIGHT_CHECKS = ("overlap", "simplest")
VERDICTS = ("go", "reshape", "needs-info", "don't")
STATES = {
    "go": "ready-for-agent",
    "reshape": "needs-triage",
    "needs-info": "needs-info",
    "don't": "needs-triage",
}
FIELDS = ("verdict", "reason", "smaller", "question", "non_goal")
MAX_LINE = 600
SEARCH_PAGE_SIZE = 50
SEARCH_MAX_PAGES = 10
TRUSTED_MARKER_AUTHOR = "ranokay"
PATTERNS = ((MARKER, "review"), (ACCEPT, "accept"), (OVERRIDE, "override"), (CONFIRM, "confirm"))


def one_line(value, label):
    if not isinstance(value, str):
        problem(f"{label} must be a string")
    text = " ".join(value.split())
    if not text:
        problem(f"{label} must not be empty")
    if len(text) > MAX_LINE:
        problem(f"{label} is longer than {MAX_LINE} characters; keep the review short")
    return text


def normalized(value):
    return " ".join(value.split()).casefold()


def issue_payload(repo, issue, fields):
    return parse_json(
        gh("issue", "view", str(issue), "-R", repo, "--json", fields),
        f"issue {repo}#{issue}",
    )


def issue_labels(repo, issue):
    payload = issue_payload(repo, issue, "labels")
    return [item["name"] for item in payload.get("labels") or []]


def issue_comments(repo, issue):
    payload = issue_payload(repo, issue, "comments")
    return payload.get("comments") or []


def extract_lines(body):
    found = {}
    labels = (
        ("**Reason:**", "reason"),
        ("**Smaller version:**", "smaller"),
        ("**Question:**", "question"),
        ("**Non-goal:**", "non_goal"),
    )
    for line in body.splitlines():
        if line.startswith("**Verdict:**") and " — " in line:
            found["reason"] = line.split(" — ", 1)[1].strip()
        for label, key in labels:
            if line.startswith(label):
                found[key] = line[len(label) :].strip().strip('"').strip()
    return found


def marker_record(marker_kind, match, comment):
    item = dict(ATTR.findall(match.group("attrs")))
    item["kind"] = marker_kind
    item["url"] = comment.get("url") or ""
    item.update(extract_lines(comment.get("body") or ""))
    return item


def records(comments):
    found = []
    for comment in comments:
        author = (comment.get("author") or {}).get("login")
        if author != TRUSTED_MARKER_AUTHOR:
            continue
        body = comment.get("body") or ""
        for pattern, marker_kind in PATTERNS:
            match = pattern.search(body)
            if match:
                found.append(marker_record(marker_kind, match, comment))
                break
    return found


def review_positions(comments):
    return [
        index
        for index, comment in enumerate(comments)
        if (comment.get("author") or {}).get("login") == TRUSTED_MARKER_AUTHOR
        and MARKER.search(comment.get("body") or "")
    ]


def allowed(items):
    if any(item["kind"] == "review" and item.get("verdict") == "go" for item in items):
        return True, "go"
    if any(item["kind"] == "accept" for item in items):
        return True, "reshape accepted"
    if any(item["kind"] == "override" for item in items):
        return True, "override"
    reviews = [item for item in items if item["kind"] == "review"]
    if not reviews:
        return False, "no pushback verdict; run the pushback review and record it"
    verdict = reviews[-1].get("verdict")
    if verdict == "reshape":
        return False, "reshape needs ranokay's acceptance: run pushback accept-reshape"
    if verdict == "needs-info":
        return False, "needs-info is waiting on the answer in its comment"
    if verdict == "don't":
        return False, "don't is not ready for an agent; wontfix needs ranokay's confirmation"
    return False, f"verdict {verdict or 'unknown'} does not allow ready-for-agent"


def allowed_for_issue(repo, issue):
    return allowed(records(issue_comments(repo, issue)))


def require_allowed(repo, issue):
    ok, detail = allowed_for_issue(repo, issue)
    if not ok:
        problem(f"ready-for-agent needs an allowing pushback verdict: {detail}")
    return detail


def require_confirmed(repo, issue):
    comments = issue_comments(repo, issue)
    positions = review_positions(comments)
    if not positions:
        return
    review = records([comments[positions[-1]]])
    if not review or review[0].get("verdict") != "don't":
        return
    confirmed = any(
        any(
            item["kind"] == "confirm" and item.get("verdict") == "wontfix"
            for item in records([comment])
        )
        for index, comment in enumerate(comments)
        if index > positions[-1]
    )
    if not confirmed:
        problem("wontfix needs ranokay's confirmation of the don't: run pushback confirm-dont")


def read_product(project):
    path = project / "PRODUCT.md"
    if not path.is_file():
        return None, []
    text = path.read_text()
    match = re.search(
        r"^##\s+Non-goals\s*$(.*?)(?=^##\s+|\Z)", text, re.MULTILINE | re.DOTALL | re.IGNORECASE
    )
    if not match:
        return text, []
    found = []
    for line in match.group(1).splitlines():
        bullet = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if bullet:
            found.append(" ".join(bullet.group(1).split()))
    return text, found


def pass_kind(labels, comments):
    if "promotion" in labels:
        return "light"
    for comment in comments:
        if (comment.get("author") or {}).get("login") != TRUSTED_MARKER_AUTHOR:
            continue
        body = comment.get("body") or ""
        if TRIAGE_MARKER in body and REPRODUCED in body:
            return "light"
    return "full"


def check_names(kind):
    return list(LIGHT_CHECKS if kind == "light" else FULL_CHECKS)


def repo_overrides(repo):
    label = f"override search for {repo}"
    found = []
    seen = set()
    for page in range(1, SEARCH_MAX_PAGES + 1):
        payload = parse_json(
            gh(
                "api",
                "--method",
                "GET",
                "search/issues",
                "-f",
                f'q=repo:{repo} "pushback-override" in:comments',
                "-f",
                f"per_page={SEARCH_PAGE_SIZE}",
                "-f",
                f"page={page}",
            ),
            label,
        )
        items = payload.get("items")
        if not isinstance(items, list):
            problem(f"{label} did not return items")
        if payload.get("incomplete_results") is True:
            problem(f"{label} returned incomplete results")
        for item in items:
            number = item.get("number")
            if not isinstance(number, int):
                problem(f"{label} returned an item without a number")
            if number in seen:
                continue
            seen.add(number)
            for entry in records(issue_comments(repo, number)):
                if entry["kind"] != "override":
                    continue
                accepted = entry.get("accepted")
                if not accepted:
                    problem(f"the override on {repo}#{number} has no accepted verdict")
                found.append(
                    {
                        "issue": number,
                        "verdict": accepted,
                        "reason": entry.get("reason") or "",
                        "url": entry.get("url") or "",
                    }
                )
        if len(items) < SEARCH_PAGE_SIZE:
            break
    else:
        problem(f"{label} filled all {SEARCH_MAX_PAGES} pages")
    return sorted(found, key=lambda item: (item["issue"], item["url"]))


def comment_body(verdict, values, kind, canonical):
    lines = [
        DISCLAIMER,
        "",
        f"<!-- factory:pushback verdict={verdict} pass={kind} -->",
        "",
        "## Pushback review",
        "",
        f"**Verdict:** {verdict} — {values['reason']}",
    ]
    if verdict == "reshape":
        lines.extend(["", f"**Smaller version:** {values['smaller']}"])
    if verdict == "needs-info":
        lines.extend(["", f"**Question:** {values['question']}"])
    if verdict == "don't" and canonical:
        lines.extend(["", f'**Non-goal:** "{canonical}"'])
    lines.append("")
    return "\n".join(lines)


def transition_comment(marker, heading, text, reason, reason_label="Reason"):
    return "\n".join(
        [
            DISCLAIMER,
            "",
            marker,
            "",
            f"## {heading}",
            "",
            text,
            "",
            f"**{reason_label}:** {reason}",
            "",
        ]
    )


def gh_write(arguments, body, failure):
    handle = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
    try:
        with handle:
            handle.write(body)
        result = subprocess.run(  # nosec B603 B607 -- gh from PATH with an argument list
            ["gh", *arguments, "--body-file", handle.name],
            capture_output=True,
            text=True,
        )
    finally:
        os.unlink(handle.name)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        problem(f"{failure}: {detail}")
    return result.stdout.strip()


def post_comment(repo, issue, body):
    return gh_write(
        ["issue", "comment", str(issue), "-R", repo],
        body,
        "posting the pushback comment failed",
    )


def edit_body(repo, issue, body):
    return gh_write(
        ["issue", "edit", str(issue), "-R", repo],
        body,
        "updating the issue body failed",
    )


def category_for(repo, issue, requested):
    if requested:
        return requested
    existing = [name for name in issue_labels(repo, issue) if name in CATEGORIES]
    if len(existing) > 1:
        problem(f"the issue has more than one category: {', '.join(existing)}")
    return existing[0] if existing else None


def verdict_values(args):
    text = sys.stdin.read() if args.from_json == "-" else Path(args.from_json).read_text()
    data = parse_json(text, "verdict")
    if not isinstance(data, dict):
        problem("the verdict must be a JSON object")
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        problem(f"unknown verdict field(s): {', '.join(unknown)}")
    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        problem(f"verdict must be one of {', '.join(VERDICTS)}")
    values = {"verdict": verdict, "reason": one_line(data.get("reason"), "reason")}
    for key, owner in (("smaller", "reshape"), ("question", "needs-info")):
        if data.get(key) is not None and verdict != owner:
            problem(f"only a {owner} verdict may carry {key}")
    if verdict == "reshape":
        values["smaller"] = one_line(data.get("smaller"), "smaller version")
    if verdict == "needs-info":
        values["question"] = one_line(data.get("question"), "question")
    if data.get("non_goal") is not None:
        if verdict != "don't":
            problem("only a don't verdict may cite a non-goal")
        values["non_goal"] = one_line(data.get("non_goal"), "non-goal")
    return values
