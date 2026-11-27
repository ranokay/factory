# The promotion backlog consolidates weekly from a reviewed plan

Open `promotion` issues pile up: each Supervisor correction files one, the same mistake recurs under several titles, and nothing closes a promotion whose check already exists. A weekly pass, `scripts/promotion-consolidate.py`, works the backlog from one reviewed file, `scripts/promotion-consolidation.json`. The file names each **Promotion Cluster** and each promotion a verified check covers, and gives a one-line reason for every entry.

A cluster merges into its **Promotion Survivor**, the lowest-numbered member. The survivor's body links every source and copies each source's correction as an occurrence. Each duplicate gets one comment pointing at the survivor, then closes. A covered promotion gets one comment naming its check, then closes. The plan is validated before any write: every number must be a promotion issue, no issue may appear twice, a covered issue may not be a cluster member, and each check must be defined in its file at run time. Otherwise the run refuses and writes nothing.

The pass ranks the open survivors and standalone promotions by source count plus occurrences, ties to the oldest, and adds `needs-triage` to the top five. It never adds `ready-for-agent`: ADR 0019 reserves that for a recorded pushback verdict. Issues already carrying `needs-triage`, `ready-for-agent`, `wontfix`, or a recorded verdict are skipped. The pass also proposes clusters that the plan does not name, when two open titles share at least a quarter of their words (Jaccard ≥ 0.25), and lists them in the summary without applying them.

Each run edits one summary comment per day on a single tracking issue. Every step carries a marker, so a re-run after a stopped run finishes the remaining steps without repeating a comment, an occurrence or a handoff.

The pass runs weekly under launchd, Tuesday 03:00, written and drift-checked by `scripts/bootstrap.py` the same way `gc` and `verify-skill-maintenance` are. It runs no GitHub Actions workflow, which would be a Protected Surface.

## Considered Options

- **A scheduled GitHub Actions workflow**: it would hold repository write credentials in CI and count as a Protected Surface. Rejected; the launchd agent already runs the other weekly passes.
- **Let the pass infer clusters itself**: a similarity rule merges promotions that only look alike, and a wrong merge closes a correction nobody re-checks. Rejected for the write path. The rule proposes clusters for a human to confirm in the plan.
- **Close promotions when a check name appears in the repository**: a Gate check is prose, and a file match would close almost any promotion. Rejected in favour of a named check that must be defined in its file.
- **Let a cluster merge keep all source issues open as duplicates**: the backlog would keep growing, and the survivor would not show its sources. Rejected.
