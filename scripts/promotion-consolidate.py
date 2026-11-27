#!/usr/bin/env python3

import argparse
import datetime as dt
import json
import re
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from pushback_common import records  # noqa: E402 -- scripts/ must be on sys.path first
from promotion_consolidate_github import (  # noqa: E402 -- scripts/ must be on sys.path first
    PROMOTION_LIST_LIMIT,
    TRACKING_LIST_LIMIT,
    Runner,
    Trust,
    fetch_integration,
    fetch_login,
    find_tracking,
    load_promotions,
)
from promotion_consolidate_plan import completed, load_plan, planned_closures, validate  # noqa: E402 -- scripts/ must be on sys.path first
from promotion_consolidate_render import MAX_PROPOSAL, Summary, propose, render_summary  # noqa: E402 -- scripts/ must be on sys.path first
from promotion_consolidate_text import (  # noqa: E402 -- scripts/ must be on sys.path first
    COVERED_PREFIX,
    INTEGRATION_REF,
    MERGED_PREFIX,
    OCCURRENCE_RE,
    PUSHBACK_LABEL,
    PUSHBACK_MARKER,
    TRACKING_MARKER,
    Refusal,
    merged_body,
    occurrences,
)

__all__ = [
    "COVERED_PREFIX",
    "MAX_PROPOSAL",
    "MERGED_PREFIX",
    "PROMOTION_LIST_LIMIT",
    "PUSHBACK_MARKER",
    "Refusal",
    "TRACKING_LIST_LIMIT",
    "TRACKING_MARKER",
]


DEFAULT_PLAN = SCRIPTS / "promotion-consolidation.json"


DEFAULT_REPO = "ranokay/factory"


VERDICT_KINDS = frozenset({"review", "accept", "override", "confirm"})


BLOCKING_LABELS = frozenset({"ready-for-agent", "wontfix"})


TOP_DEFAULT = 5


RUN_ID_RE = re.compile(r"[A-Za-z0-9.:-]{1,64}")


def refuse(problems):
    for problem in problems:
        print(f"promotion-consolidate: {problem}", file=sys.stderr)
    print("promotion-consolidate: the plan is refused; nothing was written", file=sys.stderr)


def close_duplicate(number, survivor, promotions, trust, runner, repo):
    marker = f"{MERGED_PREFIX}{survivor} -->"
    if not trust.has(number, marker):
        path = runner.file(
            f"{marker}\nMerged into #{survivor}. Its correction and occurrences now live "
            "on that survivor, so this duplicate is closed."
        )
        runner.write(
            f"comment on #{number} pointing to #{survivor}",
            "issue", "comment", str(number), "-R", repo, "--body-file", path,
        )
    if promotions[number]["state"] == "OPEN":
        runner.write(f"close duplicate #{number}", "issue", "close", str(number), "-R", repo)


def merge_one(cluster, promotions, trust, runner, repo):
    members = sorted(cluster["members"])
    survivor, sources = members[0], members[1:]
    survivor_issue = promotions[survivor]
    source_bodies = [(n, promotions[n]["body"]) for n in sources]
    carried = {}
    for number in sources:
        for block_number, inner in OCCURRENCE_RE.findall(promotions[number]["body"]):
            carried.setdefault(int(block_number), inner)
    new_body = merged_body(survivor, survivor_issue["body"], source_bodies, carried)
    if new_body != survivor_issue["body"]:
        path = runner.file(new_body)
        runner.write(
            f"edit the survivor #{survivor} body",
            "issue", "edit", str(survivor), "-R", repo, "--body-file", path,
        )
    for number in sources:
        close_duplicate(number, survivor, promotions, trust, runner, repo)
    return sum(len(occurrences(promotions[n]["body"])) for n in members)


def merge_clusters(plan, promotions, trust, runner, repo):
    merged, finished = [], []
    for cluster in plan["clusters"]:
        members = sorted(cluster["members"])
        if completed(members, promotions, trust):
            finished.append(members[0])
            continue
        count = merge_one(cluster, promotions, trust, runner, repo)
        merged.append((members[0], members[1:], cluster["reason"], count))
    return merged, finished


def close_covered(plan, promotions, trust, runner, repo):
    covered_done = []
    for entry in plan["covered"]:
        number = entry["issue"]
        check = f"{entry['check']['file']}#{entry['check']['name']}"
        marker = f"{COVERED_PREFIX}{check} -->"
        if not trust.has(number, marker):
            branch = INTEGRATION_REF.removeprefix("origin/")
            link = f"https://github.com/{repo}/blob/{branch}/{entry['check']['file']}"
            path = runner.file(
                f"{marker}\nCovered by `{entry['check']['name']}` in {link}: "
                f"{entry['reason']} Closing; the check is verified on {INTEGRATION_REF}."
            )
            runner.write(
                f"comment on #{number} linking its covering check",
                "issue", "comment", str(number), "-R", repo, "--body-file", path,
            )
        if promotions[number]["state"] == "OPEN":
            runner.write(f"close covered #{number}", "issue", "close", str(number), "-R", repo)
        covered_done.append((number, check, entry["reason"]))
    return covered_done


def ranked(plan, promotions, in_plan):
    candidates = []
    for cluster in plan["clusters"]:
        members = sorted(cluster["members"])
        candidates.append((members[0], members))
    for number, item in promotions.items():
        if number not in in_plan and item["state"] == "OPEN":
            candidates.append((number, [number]))
    scored = []
    for number, members in candidates:
        if promotions[number]["state"] != "OPEN":
            continue
        score = len(members) + sum(len(occurrences(promotions[m]["body"])) for m in members)
        scored.append((-score, promotions[number]["createdAt"], number, score))
    scored.sort()
    return scored


def eligible(number, promotions, trust):
    labels = promotions[number]["labels"]
    if any(record["kind"] in VERDICT_KINDS for record in records(trust.by_login(number))):
        return False
    if set(labels) & BLOCKING_LABELS:
        return False
    handed_off = trust.has(number, PUSHBACK_MARKER)
    return not (PUSHBACK_LABEL in labels and not handed_off)


def hand_off_one(number, score, promotions, trust, runner, repo):
    labels = promotions[number]["labels"]
    if not trust.has(number, PUSHBACK_MARKER):
        path = runner.file(
            f"{PUSHBACK_MARKER}\nSent to the Pushback Review by the promotion consolidation pass "
            f"(score {score}). The pass adds `{PUSHBACK_LABEL}` and never sets `ready-for-agent`."
        )
        runner.write(
            f"comment on #{number} recording the handoff",
            "issue", "comment", str(number), "-R", repo, "--body-file", path,
        )
    if PUSHBACK_LABEL not in labels:
        runner.write(
            f"add {PUSHBACK_LABEL} to #{number}",
            "issue", "edit", str(number), "-R", repo, "--add-label", PUSHBACK_LABEL,
        )


def hand_off(scored, promotions, trust, runner, repo, top):
    sent = []
    for _, _, number, score in scored:
        if len(sent) >= top:
            break
        if not eligible(number, promotions, trust):
            continue
        hand_off_one(number, score, promotions, trust, runner, repo)
        sent.append((number, score, promotions[number]["title"]))
    return sent


def create_tracking(runner, repo, dry_run):
    path = runner.file(
        f"{TRACKING_MARKER}\n# Promotion backlog consolidation\n\n"
        "The weekly promotion consolidation pass posts one summary comment per run here. "
        "Its merges, covered closures and pushback handoffs are listed in those comments."
    )
    url = runner.write(
        "create the tracking issue",
        "issue", "create", "-R", repo, "--title",
        "Promotion backlog consolidation (tracking)", "--body-file", path,
    )
    if dry_run:
        return None
    if not url.strip():
        raise Refusal("gh created the tracking issue but printed no URL")
    try:
        return int(url.strip().rstrip("/").split("/")[-1])
    except ValueError:
        raise Refusal(f"gh printed an unexpected tracking issue URL: {url.strip()}") from None


def run(args):
    fetch_integration(args.root)
    plan = load_plan(args.plan)
    promotions = load_promotions(args.repo)
    trust = Trust(args.repo, fetch_login(args.repo))
    problems = validate(plan, promotions, trust, args.root)
    if problems:
        refuse(problems)
        return 1
    now = dt.datetime.now(dt.timezone.utc)
    date = args.date or now.strftime("%Y-%m-%d")
    run_id = args.run_id or now.strftime("%Y%m%dT%H%M%S.%fZ")
    if not RUN_ID_RE.fullmatch(run_id):
        raise Refusal(f"run id {run_id!r} must be 1 to 64 letters, digits, dots, colons or hyphens")
    planned = planned_closures(plan)
    open_now = [n for n, item in promotions.items() if item["state"] == "OPEN"]
    before = len(open_now)
    after = before - len([n for n in planned if promotions[n]["state"] == "OPEN"])
    found_tracking = find_tracking(args.repo, trust.login)
    with tempfile.TemporaryDirectory(prefix="promotion-consolidate-") as scratch:
        runner = Runner(args.repo, args.dry_run, scratch)
        merged, finished = merge_clusters(plan, promotions, trust, runner, args.repo)
        covered_done = close_covered(plan, promotions, trust, runner, args.repo)
        in_plan = planned | {m for c in plan["clusters"] for m in c["members"]}
        sent = hand_off(ranked(plan, promotions, in_plan), promotions, trust, runner, args.repo, args.top)
        proposals, oversized = propose(promotions, in_plan)
        tracking = found_tracking
        if tracking is None:
            tracking = create_tracking(runner, args.repo, args.dry_run)
        summary = Summary(
            run_id=run_id,
            date=date,
            dry_run=args.dry_run,
            before=before,
            after=after,
            merged=merged,
            covered_done=covered_done,
            sent=sent,
            proposals=proposals,
            oversized=oversized,
            finished=finished,
        )
        text = render_summary(summary)
        summary_path = runner.file(text)
        post_summary(args, runner, trust, tracking, run_id, summary_path)
        if args.dry_run:
            print("DRY RUN: nothing was written. Writes that a live run would make:")
            for action in runner.actions:
                print(f"  - {action}")
        print(text)
    return 0


def post_summary(args, runner, trust, tracking, run_id, path):
    marker = f"<!-- factory:promotion-consolidate-run id={run_id} -->"
    if tracking is None:
        if not runner.dry_run:
            raise Refusal("the tracking issue is missing after creation")
        runner.actions.append("post the run's summary on the new tracking issue")
        return
    if trust.has(tracking, marker):
        return
    runner.write(
        f"post the run {run_id} summary on #{tracking}",
        "issue", "comment", str(tracking), "-R", args.repo, "--body-file", path,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Consolidate the promotion backlog from one reviewed plan.")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN, help="reviewed consolidation plan")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name of the Factory repository")
    parser.add_argument("--root", type=Path, default=ROOT, help="checkout whose check files are verified")
    parser.add_argument("--date", help="summary date, YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--run-id", help="this run's id; a resumed run passes the same id (default: a UTC timestamp)")
    parser.add_argument("--top", type=int, default=TOP_DEFAULT, help="survivors sent to pushback per run")
    parser.add_argument("--dry-run", action="store_true", help="print what a run would do and write nothing to GitHub")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Refusal as error:
        print(f"promotion-consolidate: {error}", file=sys.stderr)
        return 1
    except RuntimeError as error:
        print(f"promotion-consolidate: {error}; re-run to finish the remaining steps", file=sys.stderr)
        return 1
    except KeyError as error:
        print(f"promotion-consolidate: gh returned JSON without the expected field {error}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as error:
        print(f"promotion-consolidate: cannot run a required command or read its output: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
