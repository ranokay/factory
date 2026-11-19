# Messages to the Worker

Fill the `<...>` fields and keep the rest of the wording. The Worker reads each message as its instructions.

## Header

The first line of every message:

- launch: `supervise · launch`
- plan rounds: `supervise · plan <n>/2`
- review rounds: `supervise · round <n>/4 · reviewed <head SHA> · open <finding IDs, or none>`
- answers on their own: `supervise · answers · after <head SHA>`
- PR handoff: `supervise · approved <head SHA>`

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

Only with `--notify <thread>`. One line each. `<task>` is the issue reference, or the task's short title when there is none.

- escalation: `supervise · notice · <task> · stopped <Supervisor thread ID> · <one-line reason>`
- PR handoff, after sending it: `supervise · notice · <task> · handed-off <fork thread ID>`
- no PR to open: `supervise · notice · <task> · done <branch>`
- the fork, after the merge: `supervise · notice · <task> · merged <PR URL> <merge SHA>`
- the fork, stopping before the merge: `supervise · notice · <task> · stopped <fork thread ID> · <one-line reason>`

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
- When you need a decision the spec and the repository don't answer, list it under `questions` in your report and end your turn.
- Your first reply is a plan, written before you edit any file: the files you will change and why, where the seams and tests go, how you will meet each acceptance condition, and open questions. Put the plan above the report, leave `checks` empty, and end your turn.

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

Send this to the fork, with the absolute path to `babysit-pr`'s `SKILL.md`, found the same way as in step 2.

```
supervise · approved <head SHA>
The Supervisor approved this work at <head SHA> and won't review it again. You own it through merge. You run on the Worker model: `opencode-go/deepseek-v4.1-flash` at variant `max`, or `opencode-go/glm-5.3` at variant `max` when the fallback ran.
1. If the base branch moved, rebase onto it and rerun the checks.
2. Push the branch and open the PR following the repository's conventions.
3. Read <absolute path>/babysit-pr/SKILL.md and follow it through merge. Inside this Run, do not merge until the Supervisor confirms the final Definition of Done. If the repository is an Upstream Fork or the PR is from an Outside Contributor, do not merge at all: stop at the green open PR and send the stopped notice; the responsible maintainers own the merge.
4. When the Gates are green, the Verification Evidence is attached, every finding is settled and the PR is mergeable, send `supervise · wake · done <PR URL> · <head SHA>` to thread <Supervisor thread ID> with `t3_thread_send` (`mode: "queue"`). Start the body with `Reply to thread <this thread's ID>.` Merge only after `supervise · merge <PR URL> · <head SHA>`. A `supervise · block <PR URL> · <reason>` stops the run with that reason.
5. If the change touches a Protected Surface, add the `protected` label when you open the PR. If the PR carries `protected`, send `supervise · wake · protected <PR URL>` instead of the done wake, and wait. Merge only after ranokay approves and the Supervisor replies `supervise · merge`.
6. Merge as the reply authorises: squash, or a merge commit for a Sync PR.
7. After the merge, remove this worktree, because T3's sweep keeps any worktree a PR fork shares. Set `<main>` to the parent of `git rev-parse --path-format=absolute --git-common-dir`, run `git -C <main> worktree remove <this worktree>`, then apply babysit-pr's local-branch cleanup with `git -C <main>`. If git refuses because the tree has changes, leave the worktree in place and report the changes.
8. Send notices to thread <notice thread ID> with `t3_thread_send` (`mode: "queue"`). After step 7: `supervise · notice · <task> · merged <PR URL> <merge SHA>`. When you stop before the merge for a human decision or a blocker: `supervise · notice · <task> · stopped <this fork's thread ID> · <one-line reason>`. A later message that names a new notice thread replaces this one.
```

Include step 8 only with `--notify`, with the IDs filled in.

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
