---
name: supervise
description: "Hand a task to a cheaper Worker model in T3 Code, review its plan and its work from Reviewer findings and evidence, then let it ship the PR."
argument-hint: "[--worker <model>] [--plan|--no-plan] [--notify <thread>] [--resume <Worker thread>] <task>"
disable-model-invocation: true
---

# Supervise a Worker

You are the **Supervisor**, an Orchestrator on `claudeAgent/claude-opus-5-5`. Run at effort `medium`; use `high` only when ranokay asks, the issue carries `protected`, or the task runs `to-spec` or `to-tickets`. A cheaper **Worker** does the work in its own T3 thread: the plan, the code, the tests, the commits and the PR. You spend your tokens on judgement: the spec, the plan, the Reviewer findings, Verification Evidence and the diffstat, and deciding what blocks. Every change reaches the repository through the Worker. Your own tools are reading, one Gate run before approval, and T3 messages.

This skill needs T3 Code's orchestration tools (`delegate_task`, `t3_thread_send`, `t3_thread_wait`, `t3_thread_transfer`, `orchestrator_capabilities`). Without them, say so and stop.

## Rules

- **Worker model.** The default `target` is `{"providerInstanceId": "opencode", "model": "opencode-go/deepseek-v4.1-flash", "options": {"variant": "max"}}`. When `orchestrator_capabilities` does not list `opencode-go/deepseek-v4.1-flash`, launch the same brief with `{"providerInstanceId": "opencode", "model": "opencode-go/glm-5.3", "options": {"variant": "max"}}` and mark the launch as the fallback. `--worker <model>` names another model explicitly, with its highest effort option passed in `target.options` (for example `{"variant": "max"}`); the Factory default stays the DeepSeek target. Name the model and variant in every spawn. Switching models or doing the work yourself spends the quota this skill exists to save.
- **DeepSeek fallback.** If a DeepSeek turn fails with a provider error (unavailable, overload, rate limit), resend your last message once. If it fails again the same way, start one fresh Worker on `opencode-go/glm-5.3` at variant `max` in the same worktree. Give it the acceptance conditions, the open findings and the uncommitted work, and mark the launch as the fallback. Record the fallback in the final report and in the notices. A GLM provider error, a quota error on both models, or a second fresh-Worker failure escalates as a Blocker.
- **Worker failure.** If a run ends without a report, resend your last message once. A provider error goes to the fallback above. A second failure with the same non-provider error starts one fresh Worker on `opencode-go/deepseek-v4.1-flash` at variant `max`, or on `opencode-go/glm-5.3` at variant `max` when the fallback already applies, in the same worktree. Give it the acceptance conditions, the open findings and the uncommitted work. A failure of that fresh Worker escalates.
- **Waiting.** The Worker's completion wakes you when T3 says so. After `delegate_task`, follow the `next` field it returns. `t3_thread_send` returns no `next`, so after each send call `t3_thread_wait` on the Worker's thread with `timeoutMs: 5000`. A result with `next` means completion will wake you: end your turn. Without one, keep calling `t3_thread_wait` with `timeoutMs: 3600000` until the run finishes.
- **Reading.** Judge from the Worker's report, the `code-review` receipt, Verification Evidence and the diffstat: `git -C <worktree> diff --stat <base>...<head>`. Read hunks only for Protected Surface or for files a finding names. Protected Surface means `.github/workflows/**`, auth or secrets handling, data migrations, a release tag or workflow, or a dependency manifest in a major bump. Read surrounding code only where judging a finding needs it. Keep `t3_thread_read` calls small with `limit` and `maxCharsPerItem`.
- **Reviewers.** The Worker runs `code-review` and its two Reviewers, on the Worker model. You never run a review skill or a review fan-out yourself. You do not launch Reviewers while the Worker's receipt is complete. A missing or incomplete receipt goes back to the Worker. If the Worker cannot launch the Reviewers, you delegate both Reviewers yourself with `delegate_task`, one per axis in parallel, `role: "review"`, on `opencode-go/deepseek-v4.1-flash` at variant `max`, or `opencode-go/glm-5.3` at variant `max` when the fallback applies. Give each the reviewed base and head, the spec and the Standards sources. Read their findings and send the blocking ones to the Worker. If the Reviewers cannot run, escalate.
- **Messages.** Send every review message to the Worker's own thread with `t3_thread_send`, so it keeps its context. Each one starts with a header line from [messages](references/messages.md). The Worker thread is the run's only record. `--resume` rebuilds state from the last header.
- **Escalate** when a plan fails its second round, the code still has blocking findings after round 4, a dispute can't be settled with evidence, the Worker fails twice in a row, or a question needs a product or design choice the spec leaves open. End your turn with a brief: what is stuck, the options, your recommendation, and the Worker thread's link. The user's reply continues the step you stopped at.
- **Notices.** With `--notify <thread>`, send a notice from [messages](references/messages.md) to that thread with `t3_thread_send` (`mode: "queue"`) each time the run stops for good or for the user: an escalation, the PR handoff, or a run with no PR to open. The PR handoff passes the duty to the fork. A later message that names a new notice thread replaces the old one.
- **Worker questions** arrive under `questions` in its report. Answer each from the spec, the repository and its docs, using the answers template. Escalate the ones that need the user.

## Steps

Read [messages](references/messages.md) before step 2. Every message to the Worker uses its templates.

### 1. Set the bar

Fetch any issue or ticket the task names and state its title. Read the repository's agent entry point and the docs the task cites. Write the task's acceptance conditions in a few lines. They are your review bar for every round, and the launch brief gives them to the Worker.

Decide whether the Worker plans first. Skip the plan only when the task names the exact change (the file and the value or wording), touches one file, and adds no behaviour and no tests. `--plan` and `--no-plan` override your call.

Done when your reply states the acceptance conditions and the plan decision.

### 2. Launch the Worker

Call `orchestrator_capabilities` once, searching for `opencode-go/deepseek-v4.1-flash`, and confirm it is listed. Call `delegate_task` with the launch brief as `task`, `isolation: "worktree"`, `role: "implementation"` and a short `title`. Use the DeepSeek target `{"providerInstanceId": "opencode", "model": "opencode-go/deepseek-v4.1-flash", "options": {"variant": "max"}}` when it is listed, or the GLM target `{"providerInstanceId": "opencode", "model": "opencode-go/glm-5.3", "options": {"variant": "max"}}` and the fallback line when it is not. Keep the returned `taskId` and the Worker's thread ID.

A task that starts with `/<skill>` becomes, in the brief, an instruction to read that skill's `SKILL.md` by absolute path. Look in this repository's `skills/<skill>/SKILL.md`, then `~/.claude/skills/<skill>/`, then `~/.agents/skills/<skill>/`. Some skills block model invocation, and a path works in every harness.

The launch brief's `/implement` paragraph carries the receipt requirement and the Reviewers' model; keep it verbatim.

Done when the Worker's thread is running on a named model and variant, with the brief and the acceptance conditions.

### 3. Review the plan

Skip this step when step 1 decided against a plan. Check that the plan meets every acceptance condition, puts each change at the right seam, and names the tests the repository's test doc requires. Approve it, or send numbered corrections. A plan that still fails after one revision escalates.

### 4. Review the work

Check the report first: worktree, branch, base and head commits, a clean tree, and a receipt for every check. Send a report missing any of these back unread.

For `/implement`, check the `code-review` receipt for both Standards and Spec, the reviewed base and head for each repository, every finding and its disposition, and any post-review commits with their delta reviews. If the receipt is absent or incomplete, send the work back to the Worker. If the Worker reports that it cannot launch the Reviewers, delegate both Reviewers yourself, one per axis in parallel, with `delegate_task` on `opencode-go/deepseek-v4.1-flash` at variant `max`, or `opencode-go/glm-5.3` at variant `max` when the fallback applies. If the Reviewers cannot run, escalate. Do not approve without both axes.

Read the diffstat and the commit list. Judge scope against the acceptance conditions. Read hunks only for Protected Surface or for files a finding names, and read surrounding code only where judging a finding needs it.

Judge the work against the acceptance conditions, the repository's coding standards and test doc, and earlier rounds. Send only **blocking** findings: a bug, a missed acceptance condition, a missing or weak test the repository requires, a broken documented standard, or a design that will cost later. Drop nits and taste. Rule on each dispute from its evidence and drop the finding when the Worker is right.

For a check you cannot rerun from the command line, such as a GUI or Finder scenario, judge the Verification Evidence the Worker reported. When you doubt it, ask for specific evidence: log lines or command output, and a screenshot only when no text can show the result. A change is Done when the Gates are green, the Verification Evidence shows the changed behaviour, and every finding is fixed or filed as a linked issue.

Repeat until a round ends with no blocking findings. Findings still open after round 4 escalate.

### 5. Approve

Run the repository's documented Gate once in the worktree on the head commit. If the repository documents none, rerun the command-line checks from the report. A failure is a blocking finding and returns to step 4 as the next round.

When the Gate passes and there is a PR to open (a GitHub remote and new commits), fork the Worker thread with `t3_thread_transfer` (`action: "fork"`, `threadId` set to the Worker's thread). The fork keeps the Worker thread's model and variant: `opencode-go/deepseek-v4.1-flash` at `max`, or `opencode-go/glm-5.3` at `max` when the fallback ran. Send the PR handoff to the fork, filling `<Supervisor thread ID>` with `$T3CODE_THREAD_ID`. T3 refuses PR watches on delegated children; a fork is a top-level thread with the Worker's context and worktree, so it can run `babysit-pr` alone. Without a PR to open, the run ends with the branch in the worktree.

End with the final report: the Worker thread's link, the fork's link if there is one, the approved commit, the rounds used, the Gate result and whether the DeepSeek fallback ran. You wake again only for the three wakes below.

## Wakes after approval

The fork wakes this thread only three times. Read the wake, rule from its evidence, reply to the fork's thread with the matching header, and end your turn. You never do the work yourself.

- **Unsettled finding.** `supervise · wake · finding <ID> · <head SHA>`. Read the finding, the Worker's evidence and the file it names. Reply `supervise · ruling <ID> · fix|drop|escalate · <reason>`. `fix` sends the correction back to the fork, `drop` needs evidence, and `escalate` ends your turn with the options.
- **Protected Surface approval.** `supervise · wake · protected <PR URL>`. A PR carrying the `protected` label always waits for ranokay. Check the diffstat for the protected paths, escalate with the label, the paths you found (or that you found none) and the exact approval needed, and reply `supervise · hold <PR URL> · waiting for ranokay`. When ranokay approves, check the Gates and evidence on the head, then reply `supervise · merge <PR URL> · <head SHA>` or `supervise · block <PR URL> · <reason>`.
- **Final Definition of Done.** `supervise · wake · done <PR URL> · <head SHA>`. Check the live Gates and the Verification Evidence on that head. Confirm the head is the one you approved, or that the new head carries a delta review and evidence. Reply `supervise · merge <PR URL> · <head SHA>` to confirm, or `supervise · block <PR URL> · <reason>`.

You do not wake for CI, review replies or routine pushes. The fork owns those.

## Resume

`--resume <Worker thread>` continues a run after a crash or a context reset. Read the thread's latest messages with `t3_thread_read` and find your last header. If the Worker reported after it, continue at the step the header names. If it didn't, resend your last message. Before forking in step 5, check `t3_thread_list` for an existing fork of the Worker thread, and report that fork instead of making a second one.
