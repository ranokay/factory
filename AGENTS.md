# Factory
Profiles: universal

You work in ranokay's Factory. Capitalised terms are defined in `~/.agents/GLOSSARY.md`; use them exactly.

## Roles

- Orchestrators run on a frontier model. They plan, delegate, and judge from Reviewer findings, Verification Evidence and the diffstat. Read diff hunks only for Protected Surface or for files a finding names. Never run a review skill or a fan-out yourself as an Orchestrator; delegate it.
- Workers and Reviewers run on `opencode-go/deepseek-v4.1-flash`, variant `max`, falling back to `opencode-go/glm-5.3`. Every agent you start names its model; never let bulk work inherit a frontier model.
- The Worker runs `code-review` as its last step before opening the PR.
- Effort: Dispatcher `low`, Supervisor `medium`. Use `high` only when ranokay asks, on a `protected` issue, or for `to-spec` / `to-tickets`.
- At most 2 Runs per machine and 1 per repo.

## System of Record

GitHub issues, labels and PRs via `gh`. Labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`, `bug`, `enhancement`, `blocked`, `promotion`, `protected`. Never write plan, todo or progress files.

## Project kinds

Read the repo topics. `factory-personal-fork` or `factory-upstream-fork` names a fork's kind; an unlabelled fork means ask ranokay once, then set the topic. A repo owned by ranokay without a fork topic is a Personal Project.

In an Upstream Fork, upstream's conventions, commands and contribution rules win. Commit no Factory files, branch from `upstream/main`, and review locally with `ocr review --from upstream/main`.

## Profiles

Before editing a package, read `~/.agents/profiles/<name>/standards.md` for every name on the `Profiles:` line of the nearest `AGENTS.md`.

## Definition of Done

1. `mise run check` passes locally and in CI.
2. Verification Evidence comes from the repo's `verify-<app>` skill, is inspected by the Supervisor, and, when the change has a visual surface, is attached through the `evidence` skill (images and videos only, at most 5). A non-visual change attaches nothing; its evidence comment is the summary and receipt.
3. Every review finding is fixed, or filed as an issue linked from the PR.

Deferred work becomes an issue: never a TODO, a follow-up note in the PR body, or a skipped test.

## Blockers

If your own access can't resolve something, first try remediation you are already allowed to do. Otherwise apply the Blocker shape in `skills/supervise/references/messages.md` with `skills/supervise/scripts/block-issue`: the `blocked` label plus one comment with the exact unblock step mentioning @ranokay, the last evidence and the open findings. The comment is posted before the label, so the repository's `blocked.yml` caller runs `blocker-notify.yml` and sends the GitHub Mobile push (the Project Template installs the caller; `ranokay/factory` has both). Remove `blocked` when the Run resumes. Never work around a Blocker by mocking it or narrowing scope. An exhausted Orchestrator quota is a Blocker; never downgrade the Supervisor model.

## Tools

Install tools declared in `mise.toml` without asking. For a new dev tool available through mise, bun, uv or brew, say so, install it, and declare it in the same PR. Anything needing sudo, a GUI app, a system extension, money, or a new runtime dependency of the product: ask and wait.

## Session guards

`mise run bootstrap` installs the Factory's Claude Code hooks and OpenCode plugin while preserving unrelated entries:

- Claude Code keeps its existing `command_guard` and `environment_check` entries; bootstrap adds `~/.agents/tools/run secret_guard` to `~/.claude/settings.json`.
- `secret_guard prompt` checks `UserPromptSubmit`; `secret_guard tool` checks every `PreToolUse` tool input.
- OpenCode loads `~/.config/opencode/plugins/factory-secret-guard.ts`, which calls the same guard before every tool execution.
- `tools/secret_guard.py` uses a generated, version-checked keyword catalog to skip `gitleaks stdin` only when no merged rule can match. Keyword-bearing input is scanned with `.gitleaks.toml`; findings block with rule IDs only. If gitleaks is unavailable or fails, built-in credential patterns fail closed and a clean fallback scan warns visibly.

The old `sonar-secrets` entries remain until the separately approved cutover.

## Merge

- Personal Project or Personal Fork: squash-merge once Done, unless the PR is `protected` (CI workflows, auth, secrets handling, data migrations, major dependency bumps, releases). Sync PRs use a merge commit.
- Upstream Fork: upstream maintainers merge.
- Outside Contributor PRs: never auto-merge, and never run workflows that hold secrets on them.

## Release

Tags are a Protected Surface: after a merge, the Supervisor proposes a release tag for ranokay to approve, and never pushes one without that approval. A `v*` tag runs Gates, then a build when the repo has one, then a GitHub release with generated notes. No CHANGELOG.

## Corrections

When you correct a Worker, file a `promotion` issue for the lint rule, check, type or API change that makes the mistake impossible: in the project repo if it is project-specific, in `ranokay/factory` otherwise. Use `skills/supervise/scripts/file-promotion` or `.github/ISSUE_TEMPLATE/promotion.yml`; the Gate check field is required, and `No check possible: <reason>` is the only alternative. Do not add prose rules.

## Cleanup

Verification Evidence lives in `~/.cache/factory/<owner>/<repo>/<pr>/` until attached, never in the repo. At merge, delete the worktree, local and remote branch, that evidence directory, and the worktree's build outputs.
