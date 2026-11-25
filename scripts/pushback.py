#!/usr/bin/env python3
"""Deterministic commands for the pushback review.

A reviewer agent judges an issue against PRODUCT.md and returns one verdict as
JSON. These commands assemble the context it reads, validate the verdict, post
the single review comment, record ranokay's acceptance or override, and gate
ready-for-agent so only a recorded allowing verdict sets that label.
"""

import argparse
import json
import sys
from pathlib import Path

from pushback_common import (
    PATTERNS,
    STATES,
    category_for,
    check_names,
    comment_body,
    edit_body,
    issue_comments,
    issue_labels,
    issue_payload,
    normalized,
    one_line,
    pass_kind,
    post_comment,
    read_product,
    records,
    repo_overrides,
    require_allowed,
    review_positions,
    transition_comment,
    verdict_values,
)
from triage import CATEGORIES, Problem, apply_labels, problem, valid_repo


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
                "question": item.get("question") or "",
                "smaller": item.get("smaller") or "",
                "non_goal": item.get("non_goal") or "",
                "url": item.get("url") or "",
            }
            for item in records(comments)
        ],
        "comments": [
            {
                "author": (comment.get("author") or {}).get("login") or "",
                "body": comment.get("body") or "",
                "url": comment.get("url") or "",
            }
            for comment in comments
        ],
        "overrides": repo_overrides(args.repo),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


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
            if last != values["verdict"]:
                if last == "needs-info":
                    problem("the issue has a needs-info verdict; after the answer use --again")
                problem(
                    "the issue already has a different pushback verdict; "
                    "use accept-reshape, override or confirm-dont"
                )
            category = category_for(args.repo, args.issue, args.category)
            state = STATES[values["verdict"]]
            apply_labels(args.repo, args.issue, category, state)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "resumed": True,
                        "verdict": last,
                        "state": state,
                        "comment": reviews[-1].get("url") or "",
                    }
                )
            )
            return 0
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
    saved = f"<!-- factory:reshape -->\n\n## Reshaped scope\n\n{smaller}" in body_text
    if "factory:reshape" in body_text and not saved:
        problem("the issue body already carries a different Reshaped scope; update it by hand")
    if not saved:
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
