from dataclasses import dataclass
from promotion_consolidate_text import PUSHBACK_LABEL, cosine, plain, weights


PROPOSAL_THRESHOLD = 0.25


MAX_PROPOSAL = 6


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


@dataclass(frozen=True)
class Summary:
    run_id: str
    date: str
    dry_run: bool
    before: int
    after: int
    merged: list
    covered_done: list
    sent: list
    proposals: list
    oversized: list
    finished: list


def merged_section(summary):
    lines = []
    if summary.merged:
        for survivor, sources, reason, count in summary.merged:
            links = ", ".join(f"#{n}" for n in sources)
            lines.append(f"- #{survivor} (survivor) absorbs {links}; {count} occurrences: {reason}")
    else:
        lines.append("None.")
    for survivor in summary.finished:
        lines.append(f"- #{survivor} (survivor) is complete: every source is merged into it and closed; skipped")
    return lines


def covered_section(summary):
    if not summary.covered_done:
        return ["", "Closed as covered: none."]
    lines = ["", "### Closed as covered"]
    for number, check, reason in summary.covered_done:
        lines.append(f"- #{number} covered by `{check}`: {reason}")
    return lines


def sent_section(summary):
    lines = ["", f"### Sent to pushback (`{PUSHBACK_LABEL}`, top {len(summary.sent)})"]
    if not summary.sent:
        lines.append("None.")
    for number, score, title in summary.sent:
        lines.append(f"- #{number} (score {score}): {plain(title)}")
    return lines


def proposal_section(summary):
    lines = ["", "### Proposed clusters (not applied)"]
    if not summary.proposals:
        lines.append("None.")
    for members, shared in summary.proposals:
        joined = ", ".join(f"#{n}" for n in members)
        words = ", ".join(shared) if shared else "none"
        lines.append(f"- {joined} (shared terms: {words})")
    for members in summary.oversized:
        joined = ", ".join(f"#{n}" for n in members)
        lines.append(f"- not proposed, {len(members)} members is over the cap of {MAX_PROPOSAL}: {joined}")
    if summary.proposals or summary.oversized:
        lines += ["", "Confirm a proposal by adding it to `scripts/promotion-consolidation.json`."]
    return lines


def render_summary(summary):
    mode = "dry run, nothing written" if summary.dry_run else "live"
    lines = [
        f"<!-- factory:promotion-consolidate-run id={summary.run_id} -->",
        f"## Promotion consolidation, {summary.date}, run {summary.run_id}",
        "",
        f"Mode: {mode}. Open promotions: {summary.before} before, {summary.after} after this run.",
        "",
        "### Merged clusters",
    ]
    lines += merged_section(summary)
    lines += covered_section(summary)
    lines += sent_section(summary)
    lines += proposal_section(summary)
    return "\n".join(lines) + "\n"
