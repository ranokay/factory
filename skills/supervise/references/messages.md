# Messages to the Worker

Fill the `<...>` fields and keep the rest of the wording. The Worker reads each message as its instructions.

## Header

The first line of every message:

- launch: `supervise · launch`
- plan rounds: `supervise · plan <n>/2`
- review rounds: `supervise · round <n>/4 · reviewed <head SHA> · open <finding IDs, or none>`
- answers on their own: `supervise · answers · after <head SHA>`
- PR handoff: `supervise · approved <head SHA>`

Rounds count failed attempts. A delivery that fails the Gate or ends a review round with a blocking finding is one failed attempt. A Run gets up to four attempts: the first three failures may be followed by another, and a failure in round 4 makes it a Blocker. No fifth round runs. Plan rounds (`plan <n>/2`) are not attempts.

The fork wakes the Supervisor with these headers, and the Supervisor replies to the fork:

- fork wakes:
  - finding: `supervise · wake · finding <ID> · <head SHA>`
  - protected: `supervise · wake · protected <PR URL>`
  - done: `supervise · wake · done <PR URL> · <head SHA>`
- Supervisor replies:
  - finding ruling: `supervise · ruling <ID> · fix|drop|escalate · <reason>`
  - protected hold: `supervise · hold <PR URL> · waiting for ranokay`
  - merge confirmation: `supervise · merge <PR URL> · <head SHA>`
  - block: `supervise · block <PR URL> · <reason>`

A finding keeps its ID (`F1`, `F2`, ...) across rounds.

## Notices

Only with `--notify <thread>`. One header line each; the merged and stopped notices add their receipt lines under it. `<task>` is the issue reference, or the task's short title when there is none. A notice stays one header line plus its receipt lines; put anything longer in `<details>`.

- Supervisor escalation: `supervise · notice · <task> · stopped <Supervisor thread ID> · <one-line reason>` plus `blocker: <Blocker comment URL, or none>`, `unblock: <the exact step ranokay must take, one line>` and `processes: <the Worker's receipt>`
- PR handoff, after sending it: `supervise · notice · <task> · handed-off <fork thread ID>`
- no PR to open: `supervise · notice · <task> · done <branch>`
- the fork, after the merge: `supervise · notice · <task> · merged <PR URL> <merge SHA>` plus `processes: <receipt>`
- the fork, stopping before the merge: `supervise · notice · <task> · stopped <fork thread ID> · <one-line reason>` plus `blocker: <Blocker comment URL, or none>`, `unblock: <the exact step ranokay must take, one line>` and `processes: <receipt>`

## Blocker

One shape, three senders: the Supervisor before the PR exists, the PR fork after it, and the Dispatcher when it parks. Post it with the script beside `supervise`'s `SKILL.md`; never hand-write the comment.

```
<supervise path>/scripts/block-issue <owner>/<repo> <issue> \
  --reason '<what is stuck>' \
  --unblock '<the exact step ranokay must take, one line>' \
  [--evidence '<comment URL, cache path or report line>']... \
  [--finding '<F1: one line>']... \
  [--detail '<longer context>']...
```

The comment it posts, with the marker that `blocker-notify.yml` looks for:

```
<!-- factory:blocker -->

## Blocker

**Unblock step:** <the step, one command or question>

@ranokay <reason>

**Last evidence:**
- <evidence, or none>

**Open findings:**
- <findings, or none>

<details>
<summary>Details</summary>

<longer context, when --detail is used>

</details>
```

The unblock step is the first content line: one command or question. The script refuses a multi-line `--reason`, `--unblock`, `--evidence` or `--finding`; pass longer text with `--detail`, which renders inside the collapsed block. A budget Blocker always passes `--evidence` for the last evidence and `--finding` for every open finding; `none` is only for a Blocker that has neither, such as a quota failure before any work.

The script posts the comment first and adds `blocked` second, so the label event always sees the comment. Where the repository has the caller, `blocker-notify.yml` then comments the issue link and this comment's URL as `github-actions[bot]`, which is what produces the GitHub Mobile push: GitHub sends no notification for an @mention from ranokay's own account. The Project Template installs the caller; `ranokay/factory` has it. If the issue already carries `blocked` and a Blocker comment, the script prints that URL and changes nothing. Add or remove `blocked` only through this shape, and remove it when the Run resumes.

## Launch brief

Include the plan rule only when step 1 decided on a plan.

```
supervise · launch
You are the Worker on this task. A Supervisor reviews your work in this thread, and you push only after the Supervisor approves.

Task: <the task text, or: Read <absolute path>/SKILL.md and follow it for: <arguments>>

The Supervisor reviews against these acceptance conditions:
<acceptance conditions from step 1>

Read the repository's agent entry point (AGENTS.md or CLAUDE.md) and follow it. These rules from your Supervisor take precedence over it:
- You run on `opencode-go/deepseek-v4.1-flash` at variant `max`. On a fallback launch the Supervisor replaces this line with `opencode-go/glm-5.3` at variant `max`. Run every subagent you start, including both `code-review` Reviewers, on the model named in this line and its provider, and name it in each subagent task. Start no Claude models.
- Commit locally and stop before pushing or opening a PR. The Supervisor tells you when.
- Before your first commit, rename the branch after the change, following the repository's branch conventions.
- Follow the repository's `## Tools` policy: mise-declared tools need no permission; anything needing sudo, a GUI app, a system extension, money or a new runtime dependency asks first.
- When you need a decision the spec and the repository don't answer, list it under `questions` in your report and end your turn.
- Your first reply is a plan, written before you edit any file: the files you will change and why, where the seams and tests go, how you will meet each acceptance condition, and open questions. Put the plan above the report, leave `checks` empty, and end your turn.
- Before you start anything, record what is already running: `<supervise path>/scripts/process-receipt baseline <worktree>`. Before you report, stop every process you started (dev servers, simulators, watchers) and run `<supervise path>/scripts/process-receipt check <worktree>`. Paste its output in the report's `processes:` line.

For `/implement`, record each repository's base, commit the implementation, then run `code-review` against that base and the committed head, using the skill tool or by reading its `SKILL.md`. Run it after the implementation and again on every fix delta, so your final report carries the last review. Start its two Reviewers as parallel subagents on `opencode-go/deepseek-v4.1-flash` at variant `max`, or on `opencode-go/glm-5.3` at variant `max` when the fallback line applies. Return the two-axis receipt with the reviewed base and head, every finding and its disposition, and any fix commits with their delta reviews. If you cannot run it, report why rather than silently omitting it.

End every turn with this report:

REPORT
worktree: <absolute path>
branch: <name>
base: <full SHA of the commit you started from>
head: <full SHA>, all work committed, clean tree
checks:
- `<command>` exit <code> on <short SHA>
- <manual or GUI check>: <evidence: command output, log lines, file paths>
processes: <process-receipt output: processes: none, simulators: none>
answers:
- F<n> fixed in <short SHA>
- F<n> disputed: <evidence>
questions: <none, or a numbered list>
```

## Plan reply

```
supervise · plan <n>/2
Approved. Implement the plan, then report.
```

```
supervise · plan <n>/2
Revise the plan:
1. <correction>
Reply with the revised plan and the report.
```

## Findings

```
supervise · round <n>/4 · reviewed <head SHA> · open F1, F3
F1 <path:line> <what is wrong>. Done when <observable condition>.
F3 <path:line> <what is wrong>. Done when <observable condition>.
Fix each finding, or dispute it with evidence. Commit, rerun the checks your changes affect, and report.
```

## Answers

Put answers under the header of the next plan reply or findings message. When nothing else is due, send them on their own:

```
supervise · answers · after <head SHA>
1. <answer>
Continue the task, then report.
```

## PR handoff

Send this to the fork with the absolute paths to `babysit-pr`'s `SKILL.md` and to this skill's directory.

```
supervise · approved <head SHA>
The Supervisor approved this work at <head SHA>. You own it through merge and wake the Supervisor only for an unsettled finding, Protected Surface approval and the final Definition of Done. You run on the Worker model: `opencode-go/deepseek-v4.1-flash` at variant `max`, or `opencode-go/glm-5.3` at variant `max` when the fallback ran.
Supervisor skill: <supervise path>.
Issue: <owner>/<repo>#<n>, or none.

1. If the base branch moved, rebase onto it and rerun the checks.
2. Push the branch and open the PR following the repository's conventions, with the body in `~/Developer/factory/skills/pr/SKILL.md`'s shape and its Evidence line left for step 3.
3. Record and attach the Verification Evidence with `~/Developer/factory/skills/evidence/scripts/evidence` (also `mise run evidence -- ...` in the Factory Repo). Attach visual proof only: a UI state, a recording of a flow, or a rendered diagram or statechart, each with a one-line `--caption` saying what visible behaviour it proves. Never attach logs, command output, test output or diffs. A change with no visual surface (CLI, workflow, infra, docs, Factory skills) attaches nothing; the comment is just the summary and the receipt. Then `receipt` with the model and variant from this handoff's launch target, `yes` for fallback when this Run executed a different model from the one requested, the Run's Worker attempt count, the harness usage for token cost (`unavailable (<reason>)` only when the harness reports none), every gate command that actually ran, and a `--summary` of at most 10 lines: one per command with its exit code and a link to the CI run that holds the full log. Then `attach --pr <number>`: it posts the single `<!-- factory-evidence -->` comment with `gh pr comment --attach`, replaces any earlier one, and prints the comment URL as its last line. Put that URL in the PR body's one-line Evidence section, fetch the final body with `gh pr view <number> --json body --jq .body`, run `~/Developer/factory/skills/pr/scripts/pr-text body -`, and update the PR until it passes. Every receipt field is required.
4. Read <babysit path>/SKILL.md and follow it through merge. Inside this Run, do not merge until the Supervisor confirms the final Definition of Done. If the repository is an Upstream Fork or the PR is from an Outside Contributor, do not merge at all: stop at the green open PR and send the stopped notice; the responsible maintainers own the merge.
5. When the Gates are green, the evidence comment is attached, every finding is settled and the PR is mergeable, send `supervise · wake · done <PR URL> · <head SHA>` to thread <Supervisor thread ID> with `t3_thread_send` (`mode: "queue"`). Start the body with `Reply to thread <this thread's ID>.` Merge only after `supervise · merge <PR URL> · <head SHA>`. A `supervise · block <PR URL> · <reason>` reply stops the run: post the Blocker shape first, then stop.
6. If the change touches a Protected Surface, add the `protected` label when you open the PR. If the PR carries `protected`, send `supervise · wake · protected <PR URL>` instead of the done wake, and wait. Merge only after ranokay approves and the Supervisor replies `supervise · merge`.
7. If a Blocker stops you, such as a missing permission, a protection that refuses the merge, a required reviewer that cannot run, an exhausted CI retry budget, or a decision only ranokay can make, post the shape in <supervise path>/references/messages.md with <supervise path>/scripts/block-issue for the issue named above, or keep it in this thread when that line says none. Send the stopped notice with the comment URL, stop monitoring, and never report the work as done.
8. Merge as the reply authorises: squash, or a merge commit for a Sync PR.
9. After the merge, and while the worktree still exists, stop every process this thread started (dev servers, simulators, watchers) and run <supervise path>/scripts/process-receipt check <worktree>; the notice carries its output.
10. Run `~/Developer/factory/skills/evidence/scripts/evidence cleanup --worktree <this worktree> --pr <number>` from the main checkout, because T3's sweep keeps any worktree a PR fork shares. Set `<main>` to the parent of `git -C <this worktree> rev-parse --path-format=absolute --git-common-dir` and `cd <main>` first: cleanup refuses to run from inside the worktree. It deletes the remote and local branch, the worktree with its build outputs and the evidence directory, and it refuses unless every surviving ref and worktree tip is the merged head. A missing worktree, branch or evidence directory is already gone, not an error.
11. Send notices to thread <notice thread ID> with `t3_thread_send` (`mode: "queue"`). After step 10: `supervise · notice · <task> · merged <PR URL> <merge SHA>` and a `processes:` line. When you stop before the merge for a human decision or a Blocker: `supervise · notice · <task> · stopped <this fork's thread ID> · <one-line reason>`, a `blocker:` line with the Blocker comment URL or none, an `unblock:` line with the exact step ranokay must take, and a `processes:` line. A later message that names a new notice thread replaces this one.
```

Include step 11 only with `--notify`, with the IDs filled in.

## Wakes to the Supervisor

The fork sends these to the Supervisor's thread named in the PR handoff, with `t3_thread_send` (`mode: "queue"`). Start each body with `Reply to thread <this fork's thread ID>.` Wake only for these three:

```
supervise · wake · finding <ID> · <head SHA>
<the finding, the evidence already gathered, and the ruling needed>
```

```
supervise · wake · protected <PR URL>
<the Protected Surface paths and the approval needed>
```

```
supervise · wake · done <PR URL> · <head SHA>
<Gates, the Verification Evidence comment URL, and the resolved thread count>
```
