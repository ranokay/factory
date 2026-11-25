#!/usr/bin/env python3
"""Deterministic parts of the pushback review.

A reviewer agent judges an issue against PRODUCT.md and returns one verdict as
JSON. This script assembles the context it reads, validates the verdict, posts
the single review comment, records ranokay's acceptance or override, and gates
ready-for-agent so only a recorded allowing verdict sets that label.
"""

import argparse
import json
import os
import re
import subprocess  # nosec B404 -- gh runs with an argument list, never a shell
import sys
import tempfile
from pathlib import Path

from triage import (
    CATEGORIES,
    DISCLAIMER,
    Problem,
    apply_labels,
    gh,
    parse_json,
    problem,
    valid_repo,
)

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
        if MARKER.search(comment.get("body") or "")
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


def context(args):
    project = Path(args.project).expanduser().resolve()
    product_text, non_goals = read_product(project)
    issue = issue_payload(
        args.repo, args.issue, "number,title,body,labels,author,comments"
    )
    labels = [item["name"] for item in issue.get("labels") or []]
    comments = issue.get("comments") or []
    kind = pass_kind(labels, comments)
    author = issue.get("author") or {}
    payload = {
        "repo": args.repo,
        "issue": {
            "number": issue.get("number"),
            "title": issue.get("title") or "",
            "body": issue.get("body") or "",
            "labels": labels,
            "author": author.get("login") or "",
        },
        "product": product_text,
        "non_goals": non_goals,
        "pass": kind,
        "checks": check_names(kind),
        "reproduced": kind == "light" and "promotion" not in labels,
        "prior": [
            {
                "kind": item["kind"],
                "verdict": item.get("verdict") or "",
                "reason": item.get("reason") or "",
                "url": item.get("url") or "",
            }
            for item in records(comments)
        ],
        "overrides": repo_overrides(args.repo),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


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


def record_verdict(args):
    values = verdict_values(args)
    project = Path(args.project).expanduser().resolve()
    canonical = None
    if "non_goal" in values:
        offered = values["non_goal"]
        _, non_goals = read_product(project)
        if not non_goals:
            problem(f"{project / 'PRODUCT.md'} has no non-goals to quote")
        matches = [item for item in non_goals if normalized(item) == normalized(offered)]
        if not matches:
            problem(f"the non-goal {offered!r} is not in PRODUCT.md: {', '.join(non_goals)}")
        canonical = matches[0]
    comments = issue_comments(args.repo, args.issue)
    reviews = [item for item in records(comments) if item["kind"] == "review"]
    if reviews:
        last = reviews[-1].get("verdict")
        if not args.again:
            problem(
                "the issue already has a pushback verdict; after a needs-info "
                "answer use --again, otherwise use accept-reshape, override or confirm-dont"
            )
        if last != "needs-info":
            problem(f"the last verdict is {last or 'unknown'}; --again only follows needs-info")
        last_review = review_positions(comments)[-1]
        author = (issue_payload(args.repo, args.issue, "author").get("author") or {}).get("login")
        trusted = {"ranokay"}
        if author:
            trusted.add(author)
        answered = any(
            index > last_review
            and not any(pattern.search(comment.get("body") or "") for pattern, _ in PATTERNS)
            and (comment.get("author") or {}).get("login") in trusted
            for index, comment in enumerate(comments)
        )
        if not answered:
            problem("--again needs an answer comment from the reporter or ranokay")
    category = category_for(args.repo, args.issue, args.category)
    kind = pass_kind(issue_labels(args.repo, args.issue), comments)
    body = comment_body(values["verdict"], values, kind, canonical)
    state = STATES[values["verdict"]]
    url = post_comment(args.repo, args.issue, body)
    apply_labels(args.repo, args.issue, category, state)
    print(
        json.dumps(
            {"ok": True, "verdict": values["verdict"], "state": state, "comment": url}
        )
    )
    return 0


def latest_review(repo, issue):
    reviews = [item for item in records(issue_comments(repo, issue)) if item["kind"] == "review"]
    if not reviews:
        problem("the issue has no pushback verdict; run the review first")
    return reviews[-1]


def accept_reshape(args):
    review = latest_review(args.repo, args.issue)
    if review.get("verdict") != "reshape":
        problem(f"the recorded verdict is {review.get('verdict') or 'unknown'}, not reshape")
    smaller = review.get("smaller")
    if not smaller:
        problem("the reshape comment has no smaller version to accept")
    issue = issue_payload(args.repo, args.issue, "body")
    body_text = issue.get("body") or ""
    comments = issue_comments(args.repo, args.issue)
    accepted = [item for item in records(comments) if item["kind"] == "accept"]
    if not accepted:
        if not args.reason:
            problem("accept-reshape needs --reason to record the acceptance")
        reason = one_line(args.reason, "reason")
    if "factory:reshape" not in body_text:
        new_body = (
            body_text.rstrip()
            + "\n\n<!-- factory:reshape -->\n\n## Reshaped scope\n\n"
            + smaller
            + "\n"
        )
        edit_body(args.repo, args.issue, new_body)
    if accepted:
        url = accepted[-1]["url"]
    else:
        comment = transition_comment(
            "<!-- factory:pushback-accept verdict=reshape accepted=true -->",
            "Reshaped scope",
            smaller,
            reason,
            "Accepted",
        )
        url = post_comment(args.repo, args.issue, comment)
    category = category_for(args.repo, args.issue, args.category)
    apply_labels(args.repo, args.issue, category, "ready-for-agent")
    print(
        json.dumps(
            {"ok": True, "verdict": "reshape", "accepted": True, "state": "ready-for-agent", "comment": url}
        )
    )
    return 0


def override(args):
    review = latest_review(args.repo, args.issue)
    verdict = review.get("verdict")
    if verdict == "go":
        problem("the recorded verdict is already go")
    if any(item["kind"] == "override" for item in records(issue_comments(args.repo, args.issue))):
        problem("the issue already has an override")
    reason = one_line(args.reason, "reason")
    body = transition_comment(
        f"<!-- factory:pushback-override verdict=go accepted={verdict or 'unknown'} -->",
        "Pushback override",
        f"**Override:** {verdict or 'unknown'} → go",
        reason,
    )
    url = post_comment(args.repo, args.issue, body)
    category = category_for(args.repo, args.issue, args.category)
    apply_labels(args.repo, args.issue, category, "ready-for-agent")
    print(json.dumps({"ok": True, "override": True, "state": "ready-for-agent", "comment": url}))
    return 0


def confirm_dont(args):
    review = latest_review(args.repo, args.issue)
    if review.get("verdict") != "don't":
        problem(f"the recorded verdict is {review.get('verdict') or 'unknown'}, not don't")
    if any(item["kind"] == "override" for item in records(issue_comments(args.repo, args.issue))):
        problem("the don't verdict has an override and cannot become wontfix")
    reason = one_line(args.reason, "reason")
    body = transition_comment(
        "<!-- factory:pushback-confirm verdict=wontfix -->",
        "Pushback confirmation",
        "**Confirmed:** don't → wontfix",
        reason,
    )
    url = post_comment(args.repo, args.issue, body)
    category = category_for(args.repo, args.issue, args.category)
    apply_labels(args.repo, args.issue, category, "wontfix")
    print(json.dumps({"ok": True, "confirmed": "don't", "state": "wontfix", "comment": url}))
    return 0


def gate(args):
    detail = require_allowed(args.repo, args.issue)
    print(json.dumps({"ok": True, "gate": detail}))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    inspect = subcommands.add_parser("context", help="print the reviewer's context as JSON")
    inspect.add_argument("repo")
    inspect.add_argument("issue", type=int)
    inspect.add_argument("--project", default=".", help="checkout holding PRODUCT.md")

    review = subcommands.add_parser("record", help="validate and record one verdict")
    review.add_argument("repo")
    review.add_argument("issue", type=int)
    review.add_argument(
        "--from", dest="from_json", required=True, help="verdict JSON file, or - for stdin"
    )
    review.add_argument("--category", choices=CATEGORIES)
    review.add_argument(
        "--again", action="store_true", help="record a fresh review after a needs-info answer"
    )
    review.add_argument("--project", default=".", help="checkout holding PRODUCT.md")

    accept = subcommands.add_parser("accept-reshape", help="accept a reshape and make it the spec")
    accept.add_argument("repo")
    accept.add_argument("issue", type=int)
    accept.add_argument("--reason")
    accept.add_argument("--category", choices=CATEGORIES)

    overrule = subcommands.add_parser("override", help="record ranokay's one-line override")
    overrule.add_argument("repo")
    overrule.add_argument("issue", type=int)
    overrule.add_argument("--reason", required=True)
    overrule.add_argument("--category", choices=CATEGORIES)

    confirm = subcommands.add_parser("confirm-dont", help="add wontfix after ranokay confirms a don't")
    confirm.add_argument("repo")
    confirm.add_argument("issue", type=int)
    confirm.add_argument("--reason", required=True)
    confirm.add_argument("--category", choices=CATEGORIES)

    guard = subcommands.add_parser("gate", help="exit non-zero unless ready-for-agent is allowed")
    guard.add_argument("repo")
    guard.add_argument("issue", type=int)

    args = parser.parse_args(argv)
    try:
        if not valid_repo(args.repo):
            problem(f"repository must be <owner>/<repo>, not {args.repo!r}")
        if args.issue < 1:
            problem("issue must be a positive number")
        if args.command == "context":
            return context(args)
        if args.command == "record":
            return record_verdict(args)
        if args.command == "accept-reshape":
            return accept_reshape(args)
        if args.command == "override":
            return override(args)
        if args.command == "confirm-dont":
            return confirm_dont(args)
        return gate(args)
    except Problem as error:
        print(f"pushback: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
