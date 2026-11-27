#!/usr/bin/env python3

import argparse
import datetime as dt
import json
import math
import re
import subprocess  # nosec B404 -- gh runs with an argument list, never a shell
import sys
import tempfile
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from pushback_common import issue_comments, records  # noqa: E402 -- scripts/ must be on sys.path first

DEFAULT_PLAN = SCRIPTS / "promotion-consolidation.json"
DEFAULT_REPO = "ranokay/factory"
PROMOTION = "promotion"
PUSHBACK_LABEL = "needs-triage"
VERDICT_KINDS = frozenset({"review", "accept", "override", "confirm"})
BLOCKING_LABELS = frozenset({"ready-for-agent", "wontfix"})
TOP_DEFAULT = 5
TRACKING_LIST_LIMIT = 1000
PROMOTION_LIST_LIMIT = 1000
INTEGRATION_REF = "origin/main"
PROPOSAL_THRESHOLD = 0.25
MAX_PROPOSAL = 6
RUN_ID_RE = re.compile(r"[A-Za-z0-9.:-]{1,64}")
GATE_RE = re.compile(r"### Gate check\s*\n(.*)", re.S)
TRACKING_MARKER = "<!-- factory:promotion-consolidate-tracking -->"
PUSHBACK_MARKER = "<!-- factory:promotion-pushback -->"
MERGED_PREFIX = "<!-- factory:promotion-merged into="
COVERED_PREFIX = "<!-- factory:promotion-covered check="
LINKS_START = "<!-- factory:promotion-links -->"
LINKS_END = "<!-- /factory:promotion-links -->"
OCCURRENCES_HEADING = "### Merged occurrences"
OCCURRENCE_RE = re.compile(
    r"<!-- factory:promotion-occurrence source=(\d+) -->\n(.*?)\n<!-- /factory:promotion-occurrence -->",
    re.S,
)
CORRECTION_RE = re.compile(r"### Correction\s*\n(.*?)(?=\n###|\Z)", re.S)
STOPWORDS = frozenset(
    "a an and any are as at be by each every for from in into is it not of on or only out "
    "outside the this that to when while with without must new has have was were its their "
    "than then so if but which who what do does did one two also via".split()
)


class Refusal(Exception):
    pass


def gh(*args, timeout=120):
    result = subprocess.run(  # nosec B603 B607 -- gh with an argument list
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {detail}")
    return result.stdout


def occurrences(body):
    match = CORRECTION_RE.search(body or "")
    if not match:
        raise Refusal("a promotion has no ### Correction section")
    text = match.group(1)
    bullets = [" ".join(line[2:].split()) for line in text.splitlines() if line.startswith("- ")]
    if bullets:
        return bullets
    collapsed = " ".join(text.split())
    return [collapsed] if collapsed else []


def tokens(text):
    words = re.findall(r"[a-z][a-z0-9]+", text.lower())
    return [word for word in words if word not in STOPWORDS and len(word) > 2]


def document(item):
    correction = CORRECTION_RE.search(item["body"])
    gate = GATE_RE.search(item["body"])
    return " ".join([item["title"], correction.group(1) if correction else "", gate.group(1) if gate else ""])


def weights(pool, promotions):
    counts = {n: Counter(tokens(document(promotions[n]))) for n in pool}
    frequency = Counter()
    for count in counts.values():
        frequency.update(count.keys())
    idf = {word: math.log((1 + len(pool)) / (1 + df)) + 1 for word, df in frequency.items()}
    vectors = {}
    for number, count in counts.items():
        raw = {word: tf * idf[word] for word, tf in count.items()}
        norm = math.sqrt(sum(value * value for value in raw.values())) or 1.0
        vectors[number] = {word: value / norm for word, value in raw.items()}
    return vectors


def cosine(first, second):
    small, large = (first, second) if len(first) <= len(second) else (second, first)
    return sum(weight * large.get(word, 0.0) for word, weight in small.items())


def survivor_of(members):
    return min(members)


def merged_body(survivor, body, sources):
    body = body or ""
    base = body.split(LINKS_START, 1)[0].rstrip()
    blocks = {int(number): inner for number, inner in OCCURRENCE_RE.findall(body)}
    for number, source_body in sources:
        if number not in blocks:
            lines = [f"- {item}" for item in occurrences(source_body)]
            blocks[number] = "\n".join([f"From #{number}:", *lines])
    members = ", ".join([f"#{survivor} (survivor)", *(f"#{number}" for number in sorted(blocks))])
    rendered = [
        f"<!-- factory:promotion-occurrence source={number} -->\n{blocks[number]}\n"
        "<!-- /factory:promotion-occurrence -->"
        for number in sorted(blocks)
    ]
    return "\n".join(
        [
            base,
            "",
            LINKS_START,
            f"Cluster members: {members}. Their corrections and occurrences are copied below.",
            LINKS_END,
            "",
            OCCURRENCES_HEADING,
            "",
            *rendered,
            "",
        ]
    )


def check_defined(root, check):
    file, name = check["file"], check["name"]
    path = Path(file)
    if path.is_absolute() or ".." in path.parts:
        raise Refusal(f"check file {file} must be a path inside the Factory")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise Refusal(f"check name {name!r} is not a plain identifier")
    shown = subprocess.run(  # nosec B603 B607 -- git with an argument list
        ["git", "-C", str(root), "show", f"{INTEGRATION_REF}:{file}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if shown.returncode != 0:
        detail = shown.stderr.strip() or "git show failed"
        raise Refusal(f"cannot read {file} from {INTEGRATION_REF}: {detail}")
    text = shown.stdout
    escaped = re.escape(name)
    if file.endswith(".toml"):
        pattern = rf'^\s*id\s*=\s*"{escaped}"\s*$'
    elif file.endswith(".pkl"):
        pattern = rf'^\s*\["{escaped}"\]\s*=\s*new Step\b'
    elif file.endswith((".yml", ".yaml")):
        pattern = rf"^\s*{escaped}:\s*$|^\s*-?\s*name:\s*{escaped}\s*$"
    else:
        raise Refusal(f"check file {file} has no supported check format (.toml, .pkl, .yml, .yaml)")
    if not re.search(pattern, text, re.M):
        raise Refusal(f"{file} does not define the check {name}")


def load_plan(path):
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Refusal(f"cannot read the plan {path}: {error}") from None
    if not isinstance(plan, dict):
        raise Refusal("the plan must be a JSON object")
    for key in ("clusters", "covered"):
        if not isinstance(plan.get(key), list):
            raise Refusal(f"the plan needs a {key} list, even an empty one")
    return plan


def load_promotions(repo):
    raw = json.loads(
        gh(
            "issue", "list", "-R", repo, "--label", PROMOTION, "--state", "all",
            "--limit", str(PROMOTION_LIST_LIMIT), "--json", "number,title,body,state,labels,createdAt",
        )
    )
    if len(raw) >= PROMOTION_LIST_LIMIT:
        raise Refusal(f"the promotion listing reached its {PROMOTION_LIST_LIMIT}-issue cap; it cannot see the whole backlog")
    return {
        item["number"]: {
            "number": item["number"],
            "title": item["title"],
            "body": item["body"],
            "state": item["state"].upper(),
            "labels": [label["name"] for label in item["labels"]],
            "createdAt": item["createdAt"],
        }
        for item in raw
    }


def has_marker(repo, number, marker):
    return any(marker in comment["body"] for comment in issue_comments(repo, number))


def closed_by_pass(repo, number):
    bodies = [comment["body"] for comment in issue_comments(repo, number)]
    return any(MERGED_PREFIX in body or COVERED_PREFIX in body for body in bodies)


def validate(plan, promotions, repo, root):
    problems = []
    claims = {}

    def claim(number, where):
        if number in claims:
            problems.append(f"#{number} appears in {claims[number]} and in {where}")
        claims[number] = where

    def known(number, where):
        if not isinstance(number, int) or isinstance(number, bool):
            problems.append(f"{where} lists {number!r}, which is not an issue number")
            return False
        if number not in promotions:
            problems.append(f"#{number} is not a promotion issue ({where})")
            return False
        if promotions[number]["state"] == "CLOSED" and not closed_by_pass(repo, number):
            problems.append(f"#{number} is closed and this pass did not close it ({where})")
            return False
        return True

    for index, cluster in enumerate(plan["clusters"]):
        where = f"cluster {index + 1}"
        if not isinstance(cluster, dict):
            problems.append(f"{where} must be an object")
            continue
        members = cluster.get("members")
        if not isinstance(members, list) or len(members) < 2:
            problems.append(f"{where} needs at least two members")
            continue
        if not isinstance(cluster.get("reason"), str) or not cluster["reason"].strip() or "\n" in cluster["reason"]:
            problems.append(f"{where} needs a one-line reason")
        for number in members:
            if known(number, where):
                claim(number, where)
        numbers = [m for m in members if isinstance(m, int) and not isinstance(m, bool)]
        survivor = survivor_of(numbers) if numbers else None
        if survivor in promotions and promotions[survivor]["state"] != "OPEN":
            problems.append(f"survivor #{survivor} of {where} must be open")

    touched = {m for cluster in plan["clusters"] if isinstance(cluster, dict) for m in cluster.get("members", [])}
    touched |= {entry.get("issue") for entry in plan["covered"] if isinstance(entry, dict)}
    for number, item in promotions.items():
        if (item["state"] == "OPEN" or number in touched) and not CORRECTION_RE.search(item["body"]):
            problems.append(f"#{number} has no ### Correction section")

    for index, entry in enumerate(plan["covered"]):
        where = f"covered entry {index + 1}"
        if not isinstance(entry, dict):
            problems.append(f"{where} must be an object")
            continue
        number = entry.get("issue")
        if known(number, where):
            claim(number, where)
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip() or "\n" in entry["reason"]:
            problems.append(f"{where} needs a one-line reason")
        check = entry.get("check")
        if not isinstance(check, dict) or not isinstance(check.get("file"), str) or not isinstance(check.get("name"), str):
            problems.append(f"{where} needs a check with file and name")
            continue
        try:
            check_defined(root, check)
        except Refusal as error:
            problems.append(f"{where} (#{number}): {error}")
    return problems


def planned_closures(plan):
    closures = set()
    for cluster in plan["clusters"]:
        members = sorted(cluster["members"])
        closures.update(members[1:])
    closures.update(entry["issue"] for entry in plan["covered"])
    return closures


class Runner:
    def __init__(self, repo, dry_run, scratch):
        self.repo = repo
        self.dry_run = dry_run
        self.scratch = Path(scratch)
        self.actions = []
        self.counter = 0

    def file(self, text):
        self.counter += 1
        path = self.scratch / f"body-{self.counter}.md"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def write(self, description, *args):
        self.actions.append(description)
        if self.dry_run:
            return ""
        return gh(*args)


def fetch_integration(root):
    result = subprocess.run(  # nosec B603 B607 -- git with an argument list
        ["git", "-C", str(root), "fetch", "--quiet", "origin", "main"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "git fetch failed"
        raise Refusal(f"cannot fetch origin main, so checks cannot be verified against it: {detail}")


def run(args):
    if not args.no_fetch:
        fetch_integration(args.root)
    plan = load_plan(args.plan)
    promotions = load_promotions(args.repo)
    problems = validate(plan, promotions, args.repo, args.root)
    if problems:
        for problem in problems:
            print(f"promotion-consolidate: {problem}", file=sys.stderr)
        print("promotion-consolidate: the plan is refused; nothing was written", file=sys.stderr)
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

    found_tracking = find_tracking(args.repo)
    with tempfile.TemporaryDirectory(prefix="promotion-consolidate-") as scratch:
        runner = Runner(args.repo, args.dry_run, scratch)
        merged = []
        for cluster in plan["clusters"]:
            members = sorted(cluster["members"])
            survivor, sources = members[0], members[1:]
            survivor_issue = promotions[survivor]
            source_bodies = [(n, promotions[n]["body"]) for n in sources]
            new_body = merged_body(survivor, survivor_issue["body"], source_bodies)
            if new_body != survivor_issue["body"]:
                path = runner.file(new_body)
                runner.write(
                    f"edit the survivor #{survivor} body",
                    "issue", "edit", str(survivor), "-R", args.repo, "--body-file", path,
                )
            occurrence_count = sum(len(occurrences(promotions[n]["body"])) for n in members)
            for number in sources:
                marker = f"{MERGED_PREFIX}{survivor} -->"
                if not has_marker(args.repo, number, marker):
                    path = runner.file(
                        f"{marker}\nMerged into #{survivor}. Its correction and occurrences now live "
                        "on that survivor, so this duplicate is closed."
                    )
                    runner.write(
                        f"comment on #{number} pointing to #{survivor}",
                        "issue", "comment", str(number), "-R", args.repo, "--body-file", path,
                    )
                if promotions[number]["state"] == "OPEN":
                    runner.write(f"close duplicate #{number}", "issue", "close", str(number), "-R", args.repo)
            merged.append((survivor, sources, cluster["reason"], occurrence_count))

        covered_done = []
        for entry in plan["covered"]:
            number = entry["issue"]
            check = f"{entry['check']['file']}#{entry['check']['name']}"
            marker = f"{COVERED_PREFIX}{check} -->"
            if not has_marker(args.repo, number, marker):
                link = f"https://github.com/{args.repo}/blob/main/{entry['check']['file']}"
                path = runner.file(
                    f"{marker}\nCovered by `{entry['check']['name']}` in {link}: "
                    f"{entry['reason']} Closing; the check is verified on {INTEGRATION_REF}."
                )
                runner.write(
                    f"comment on #{number} linking its covering check",
                    "issue", "comment", str(number), "-R", args.repo, "--body-file", path,
                )
            if promotions[number]["state"] == "OPEN":
                runner.write(f"close covered #{number}", "issue", "close", str(number), "-R", args.repo)
            covered_done.append((number, check, entry["reason"]))

        in_plan = planned | {m for c in plan["clusters"] for m in c["members"]}
        candidates = []
        for cluster in plan["clusters"]:
            members = sorted(cluster["members"])
            candidates.append((members[0], members))
        for number, item in promotions.items():
            if number not in in_plan and item["state"] == "OPEN":
                candidates.append((number, [number]))

        ranked = []
        for number, members in candidates:
            if promotions[number]["state"] != "OPEN":
                continue
            score = len(members) + sum(len(occurrences(promotions[m]["body"])) for m in members)
            ranked.append((-score, promotions[number]["createdAt"], number, score))
        ranked.sort()

        sent = []
        for _, _, number, score in ranked:
            if len(sent) >= args.top:
                break
            labels = promotions[number]["labels"]
            comments = issue_comments(args.repo, number)
            if any(record["kind"] in VERDICT_KINDS for record in records(comments)):
                continue
            if set(labels) & BLOCKING_LABELS:
                continue
            handed_off = any(PUSHBACK_MARKER in comment["body"] for comment in comments)
            if PUSHBACK_LABEL in labels and not handed_off:
                continue
            if PUSHBACK_LABEL not in labels:
                runner.write(
                    f"add {PUSHBACK_LABEL} to #{number}",
                    "issue", "edit", str(number), "-R", args.repo, "--add-label", PUSHBACK_LABEL,
                )
            if not handed_off:
                path = runner.file(
                    f"{PUSHBACK_MARKER}\nSent to the Pushback Review by the promotion consolidation pass "
                    f"(score {score}). The pass adds `{PUSHBACK_LABEL}` and never sets `ready-for-agent`."
                )
                runner.write(
                    f"comment on #{number} recording the handoff",
                    "issue", "comment", str(number), "-R", args.repo, "--body-file", path,
                )
            sent.append((number, score, promotions[number]["title"]))

        proposals, oversized = propose(promotions, in_plan)

        tracking = found_tracking
        if tracking is None:
            path = runner.file(
                f"{TRACKING_MARKER}\n# Promotion backlog consolidation\n\n"
                "The weekly promotion consolidation pass posts one summary comment per run here. "
                "Its merges, covered closures and pushback handoffs are listed in those comments."
            )
            url = runner.write(
                "create the tracking issue",
                "issue", "create", "-R", args.repo, "--title",
                "Promotion backlog consolidation (tracking)", "--body-file", path,
            )
            if not args.dry_run:
                if not url.strip():
                    raise Refusal("gh created the tracking issue but printed no URL")
                try:
                    tracking = int(url.strip().rstrip("/").split("/")[-1])
                except ValueError:
                    raise Refusal(f"gh printed an unexpected tracking issue URL: {url.strip()}") from None

        summary = render_summary(
            run_id, date, args.dry_run, before, after, merged, covered_done, sent, proposals, oversized
        )
        summary_path = runner.file(summary)
        post_summary(args, runner, tracking, run_id, summary_path)

        if args.dry_run:
            print("DRY RUN: nothing was written. Writes that a live run would make:")
            for action in runner.actions:
                print(f"  - {action}")
        print(summary)
    return 0


def find_tracking(repo):
    found = json.loads(
        gh("issue", "list", "-R", repo, "--state", "all", "--limit", str(TRACKING_LIST_LIMIT), "--json", "number,body")
    )
    if len(found) >= TRACKING_LIST_LIMIT:
        raise Refusal(
            f"the tracking issue lookup reached its {TRACKING_LIST_LIMIT}-issue cap; "
            "it cannot prove there is one tracking issue"
        )
    matches = [item["number"] for item in found if TRACKING_MARKER in item["body"]]
    if len(matches) > 1:
        raise Refusal(f"more than one tracking issue carries the marker: {matches}")
    return matches[0] if matches else None


def post_summary(args, runner, tracking, run_id, path):
    marker = f"<!-- factory:promotion-consolidate-run id={run_id} -->"
    if tracking is None:
        if not runner.dry_run:
            raise Refusal("the tracking issue is missing after creation")
        runner.actions.append("post the run's summary on the new tracking issue")
        return
    if any(marker in comment["body"] for comment in issue_comments(args.repo, tracking)):
        return
    runner.write(
        f"post the run {run_id} summary on #{tracking}",
        "issue", "comment", str(tracking), "-R", runner.repo, "--body-file", path,
    )


def propose(promotions, in_plan):
    pool = sorted(n for n, item in promotions.items() if item["state"] == "OPEN" and n not in in_plan)
    vectors = weights(pool, promotions)
    parent = {n: n for n in pool}

    def find(number):
        while parent[number] != number:
            parent[number] = parent[parent[number]]
            number = parent[number]
        return number

    for index, first in enumerate(pool):
        for second in pool[index + 1 :]:
            if cosine(vectors[first], vectors[second]) >= PROPOSAL_THRESHOLD:
                parent[find(first)] = find(second)
    groups = {}
    for number in pool:
        groups.setdefault(find(number), []).append(number)
    proposals, oversized = [], []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort()
        if len(members) > MAX_PROPOSAL:
            oversized.append(members)
            continue
        common = set.intersection(*(set(vectors[m]) for m in members))
        ranked = sorted(common, key=lambda word: (-sum(vectors[m][word] for m in members), word))
        proposals.append((members, ranked[:3]))
    return sorted(proposals), sorted(oversized)


def render_summary(run_id, date, dry_run, before, after, merged, covered_done, sent, proposals, oversized):
    lines = [
        f"<!-- factory:promotion-consolidate-run id={run_id} -->",
        f"## Promotion consolidation, {date}, run {run_id}",
        "",
        f"Mode: {'dry run, nothing written' if dry_run else 'live'}. "
        f"Open promotions: {before} before, {after} after this run.",
        "",
        "### Merged clusters",
    ]
    if merged:
        for survivor, sources, reason, count in merged:
            links = ", ".join(f"#{n}" for n in sources)
            lines.append(f"- #{survivor} (survivor) absorbs {links}; {count} occurrences: {reason}")
    else:
        lines.append("None.")
    if covered_done:
        lines += ["", "### Closed as covered"]
        for number, check, reason in covered_done:
            lines.append(f"- #{number} covered by `{check}`: {reason}")
    else:
        lines += ["", "Closed as covered: none."]
    lines += ["", f"### Sent to pushback (`{PUSHBACK_LABEL}`, top {len(sent)})"]
    if sent:
        for number, score, title in sent:
            lines.append(f"- #{number} (score {score}): {title}")
    else:
        lines.append("None.")
    lines += ["", "### Proposed clusters (not applied)"]
    if proposals:
        for members, shared in proposals:
            joined = ", ".join(f"#{n}" for n in members)
            words = ", ".join(shared) if shared else "none"
            lines.append(f"- {joined} (shared terms: {words})")
    else:
        lines.append("None.")
    for members in oversized:
        joined = ", ".join(f"#{n}" for n in members)
        lines.append(f"- not proposed, {len(members)} members is over the cap of {MAX_PROPOSAL}: {joined}")
    if proposals or oversized:
        lines += ["", "Confirm a proposal by adding it to `scripts/promotion-consolidation.json`."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Consolidate the promotion backlog from one reviewed plan.")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN, help="reviewed consolidation plan")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name of the Factory repository")
    parser.add_argument("--root", type=Path, default=ROOT, help="checkout whose check files are verified")
    parser.add_argument("--date", help="summary date, YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--run-id", help="this run's id; a resumed run passes the same id (default: a UTC timestamp)")
    parser.add_argument("--top", type=int, default=TOP_DEFAULT, help="survivors sent to pushback per run")
    parser.add_argument("--dry-run", action="store_true", help="print what a run would do and write nothing")
    parser.add_argument("--no-fetch", action="store_true", help="check covered files against the local origin/main without fetching")
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
