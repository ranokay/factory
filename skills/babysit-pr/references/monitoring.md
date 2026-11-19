# Monitoring across harnesses

Use this reference when checks, reviews, or a merge queue still need external
progress. Select capabilities from the running harness, not from the model name.
Codex, Claude, or DeepSeek may run inside T3 Code or in another host with different
tools and lifecycle rules.

## Event-driven hosts

Prefer the host's supported PR watcher or incoming auto-fix events. Process
already-existing comments and finish needed fixes and pushes before yielding to
the watcher. Follow the live tool's continuation instructions. Avoid creating a
second polling loop, scheduled task, or watcher for the same PR.

In T3 Code, use the available `link_pull_request`, `watch_pull_request`, and
`unwatch_pull_request` tools under their current contracts. A child doing review
or implementation returns its result to the parent; the parent owns monitoring.
Keep PR links current when creating, taking over, or pushing a PR. The host may
already have a persistent watch after a wake, so check the wake and tool contract
before registering it again.

For Claude desktop or other native auto-fix events, use their existing PR scope
and continuation behavior. Verify event content against the live PR. A wake is
not proof that checks passed or permission to bypass requirements.

Before yielding, preserve the current head, pending items, state path, and any
action restrictions. Describe the task as waiting for external progress, not
finished. Registering a watcher is useful only when the tool confirms that it
can resume this task. At terminal completion, a persistent blocker defined in the
entrypoint, or the user's stop request, cancel the monitoring owned by this task
as the host requires.

## Hosts without a usable event watcher

If the harness permits waiting and can continue running, use one bounded polling
session. Start near 60 seconds between checks and increase toward five minutes
when unchanged. Honor a user-specified cadence. Keep each wait within the host's
limits and remain interruptible. Reuse existing repository polling helpers when
they fit; avoid introducing a new daemon or scheduler.

Set an elapsed-time limit before polling, using the user's limit or 30 minutes
by default. Check only changed state, while retaining older unresolved findings.
Stop polling at the requested milestone, an explicit stop, a persistent blocker,
or the time limit. At a time limit, report what is pending and that the watch is
inactive. Do not describe the PR as ready merely because time expired.

If the harness cannot remain active or resume automatically, perform the current
inspection and authorized corrections, then report that continued monitoring is
unavailable. State exactly what still needs a later inspection. Never promise a
background watch that the host has not established.

## Resuming without repeated work

Reload the compact state and fetch the live PR before acting. Compare the full
head SHA, base SHA, check attempts, review and comment IDs, update times, and
previous dispositions. Notifications can arrive late, repeat, or reference an
older head. Fix a newly validated issue, not the mere arrival of a notification.

Preserve the one-rerun budget for an unchanged CI failure across wakes and
compaction. Report exhausted quota or a required reviewer that cannot run as a
specific coverage gap. Re-requesting reviews or rerunning CI does not itself
make the PR ready.
