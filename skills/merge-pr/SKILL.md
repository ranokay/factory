---
name: merge-pr
description: "Merge a GitHub pull request that already meets the repository's merge gate, then clean up its branches. Use when the user says to merge, land, or ship a PR; when a watching session finished with no remaining findings but the PR is still open; or when only the merge remains. Do not use to fix findings, request reviews, or wait for a PR to become ready. Use babysit-pr for that."
---

# Merge a pull request

In the Factory, run this skill on the Worker model:
`opencode-go/deepseek-v4.1-flash` at variant `max`, or `opencode-go/glm-5.3` at
variant `max` when the Worker fell back. A Supervisor never runs it.

This is the terminal step after review and checks are done. Run it once: verify
readiness on the live head, merge, clean up, report. It does not fix findings,
request reviews, retry CI, or keep a review watch. A watching session that stops
with no remaining findings should hand the PR here rather than leaving it open.
If the PR is not ready now, name the blocker and stop so the user can send it
back to `babysit-pr`.

## Pin the target

- Resolve the PR from the URL or number the user gave. If none was given, use the
  current branch only when it maps to one unambiguous open PR. If several PRs
  were named, handle them in order, one at a time, and stop at the first blocked
  PR so a dependent later PR is not landed out of order.
- Pin forge operations to the PR's repository (`gh ... --repo owner/name`). Do
  not assume the working directory is that checkout; a scratch directory is
  enough for the merge itself.
- Read the repository's agent entry point and its declared Profiles.
  Repository merge method,
  required checks and reviews, signing, and merge queue rules override these
  defaults. Apply the repository's merge preflight when it has one.
- Read the repository's topics. `factory-upstream-fork` means upstream
  maintainers merge: never merge it yourself, and report the green open PR.
  `factory-personal-fork` and a Personal Project follow the Factory merge policy
  below.
- Confirm the request authorizes merging this PR. A readiness-only or
  observation-only handoff is not merge authorization. A push, update, or
  testing hold on this PR blocks the branch-update exception below; report the
  hold instead.
- Record the PR, repository, verified head and base, and any action restriction.
  Permission to merge one PR does not override a hold on another.

This step is complete when the target, repository requirements, and any
restriction on merging are known.

## Factory merge policy

Apply the policy in `AGENTS.md` and `babysit-pr`: a Done PR without `protected`
auto-merges, a PR carrying `protected` waits for ranokay, a Sync PR merges with a
merge commit, an Upstream Fork PR is merged by upstream maintainers, and an
Outside Contributor PR never auto-merges. If you cannot tell whether the PR is a
Sync PR, report the question to the user and stop. A PR that touches a Protected
Surface but lacks the `protected` label is not ready: report it and stop. Inside
a Run, readiness includes the Supervisor's `supervise · merge` for this head;
without it, report that the PR needs `babysit-pr` and stop.

## Verify readiness

Read the live PR state, not a cached badge or an earlier session's report. Use
`gh pr view` with the fields you need, and fetch full check and review data when
the summary is ambiguous.

Readiness requires all of:

- the PR is open, not a draft, and has no merge conflict;
- every repository-required check has completed successfully on the candidate
  head;
- every required review is present and approving on the candidate head, or is
  non-stale under repository rules, with no changes requested;
- required local review coverage is complete on that head; skipped, pending, quota-limited, or
  unavailable required reviews are incomplete coverage, even when a badge is
  green. A success badge without evidence of a completed review is also
  incomplete. A completed review explicitly reporting no findings is valid
  coverage;
- no unresolved thread contains an actionable finding that the current head has
  not addressed. A thread whose finding is already fixed but still open does not
  block unless repository rules require resolution;
- the PR is mergeable on that exact head.

Hosted AI reviews are optional under the shared workflow unless explicitly
requested for this run. Report incomplete bot coverage without waiting for it;
inspect available findings and respect actual forge-enforced protections.

Inside a Factory Run, the Verification Evidence is the single `evidence`
comment on the PR. Its Run receipt takes the Worker model and variant from the
launch target, whether the fallback ran from the executed model against the
requested one, the Run's Worker attempt count, the harness usage for token cost
(`unavailable (<reason>)` only when it reports none), and the gate commands that
actually ran. Every field is required.

Unknown mergeability, pending required checks, and missing required local or
human reviews are pending, not ready. Enabling GitHub auto-merge is acceptable only as the repository's
required queue mechanism for an otherwise-ready PR. Do not enable it, or request
a review, to turn a blocked PR into a deferred task; this skill ends with a
report instead.

If the PR is not ready, report the smallest concrete blocker: failing or pending
required checks, missing required reviews, unresolved finding links, draft state,
conflicts, or a branch-protection rule. Stop there. Do not push fixes, rerun CI,
or start a watch. The only exceptions are the branch update and merge queue
transitions below, which are part of completing an authorized merge.

## Merge

Refresh the live head, base, checks, and review state immediately before merging.
Use the repository's merge method; use squash when none is specified, and a merge
commit for a Sync PR. Bind the merge to the verified head with
`--match-head-commit <sha>` or the forge's equivalent guard. Apply the repository
merge preflight and execute only its printed, head-pinned command. Do not pass a
branch-deletion option to the merge command; all deletions happen in Clean up
after their guards. If a repository's printed command includes one, omit it or
report it. Changed state returns to verification once; a head that keeps moving
means another session is active, so report instead of racing it.

Respect branch protections, required approvals, and merge queues. Never bypass
them, force a merge, or weaken a check to finish.

Verify that the forge reports the PR merged on the verified head and record the
merge commit. A queued merge, enabled auto-merge, or an open PR after the command
is still pending, not merged. If permissions or protections block the merge,
report the precise blocker and the verified readiness state without claiming
completion.

### Work the merge itself started

Two repository requirements can make merging asynchronous. Wait for these, and
only these, with a bounded limit (30 minutes by default, or the user's limit):

- **Behind a protected base.** When a non-destructive branch update is the only
  blocker, update the branch through the forge, re-run the full readiness check
  on the new head, then merge. Never rebase or force-push the PR branch to
  satisfy this; if the repository requires a rebase, report it instead.
- **Required merge queue.** Enqueue with the repository's supported command,
  bound to the verified head where the forge supports it, and wait for the PR to
  become merged. If the PR is already queued, resume from that state. If a push
  or queue rebase changes the head, re-run the full readiness check before
  accepting the merge. If the queue rejects it, report the reason.

The wait is status-only for the merge transition. If required checks fail,
approvals are lost, or new actionable findings appear, report and stop; do not
diagnose, fix, rerun CI, or start a review watch. Use the host's PR watcher when
it can resume this task; otherwise use one quiet, bounded polling session. Do not
create a second watcher for the same PR. If the limit expires, report the pending
state and stop. Never describe a queued PR as merged.

## Clean up

Only after verifying the merge. Before touching a local checkout, verify each
local ref belongs to the PR's repositories: the topic branch to the head
repository, and the integration checkout to the base repository and base
branch. If either does not match, skip that part and report it.

In the Factory, run `~/Developer/factory/skills/evidence/scripts/evidence cleanup
--worktree <path> --pr <n> [--repo owner/name]` from the main checkout (also
`mise run evidence -- cleanup ...` in the Factory Repo). It deletes the remote
and local topic branches, the worktree with its build outputs, and the PR's
evidence directories. It refuses when the PR is not merged, when a surviving ref
or worktree tip is not the merged head, or when another open PR uses the branch;
the remote delete is guarded by an expected-SHA lease. A missing worktree,
branch or evidence directory is reported as already gone. The bullets below
state the requirement it enforces.

- Delete only the merged PR's remote topic branch, guarded by the verified merged
  head SHA; the Factory tool does so with `--force-with-lease`. When a safe
  expected-SHA deletion is unavailable, skip it and report that, so no ref is
  deleted on trust. Preserve a remote branch used by another open PR or active
  task. If the head is a fork, skip the remote deletion unless the user
  explicitly asked to delete that fork branch. Account for automatic deletion; a
  missing branch is success, not an error.
- Delete the local topic branch only when it is unused, belongs to the PR's head
  repository, and its tip is the verified merged head or its work is proven
  included in the merge. For a squash merge, ancestry does not prove inclusion;
  use the pinned head or tree/patch evidence. Preserve extra local commits.
- Fast-forward a clean local checkout of the integration branch when one is
  available and not owned by another active task. Preserve local-only commits,
  dirty or staged changes, active worktrees, and harness-managed checkout
  directories. Report skipped cleanup. Never reset or discard work as part of
  this cleanup.

## Report

Finish with a short receipt:

- PR link and verified head SHA;
- checks and review coverage that made it ready;
- merge method and merge commit, or the exact pending or blocked state;
- cleanup performed or skipped, and why;
- the smallest next action, if anything remains.

If the PR was already merged before this run, verify the merge and perform only
the safe cleanup. If a watcher owned by this task was used for a queue wait,
release it when the task ends; do not leave a watch on a merged PR.
