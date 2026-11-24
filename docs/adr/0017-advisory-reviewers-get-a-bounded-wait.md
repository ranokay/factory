# Advisory Reviewers get a bounded wait before merge

`request-advisory-reviews` asks Copilot, CodeRabbit and Greptile to review a PR, and `babysit-pr` then waits for each requested reviewer before the done wake and the merge. `advisory-review-status` reports every reviewer as pending or terminal for the current head: terminal is a review or summary posted for that head, an explicit skip or quota message, or a reviewer that was never requested. A skip or quota message from an earlier head does not count, and an in-progress placeholder such as CodeRabbit's "Currently processing new changes" stays pending until it is replaced by the finished summary. The wait is bounded to 20 minutes by default; a timeout merges and names the reviewers in the Run receipt and the merged notice, and their late findings become a `needs-triage` issue.

PR #133 merged at 03:30:42Z; Greptile posted two P1 findings at 03:31:37Z. The old rule waited for no advisory reviewer at all, so a requested review that was still running was treated as if it did not exist. Waiting without a bound would let a quota-limited bot hold a merge indefinitely, and a forge-enforced check would turn optional reviewers into required Gates whose quota failures block unrelated work. ADR 0002 still holds: the CI AI Review passes when it runs, and this decision only changes when an optional, requested reviewer is worth waiting for.

## Considered Options

- **Never wait**, the previous rule: missed the #133 findings by 55 seconds. Rejected.
- **Wait without a timeout**: a reviewer that never answers blocks the merge forever. Rejected.
- **Make advisory reviews a required check**: quota-limited bots then block merges and an optional reviewer becomes a Gate. Rejected in favour of a bounded wait and a late-review issue.
