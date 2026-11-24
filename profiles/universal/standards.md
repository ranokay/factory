# universal

## Scope

- Make the smallest change that closes the issue. Anything else you notice becomes a new issue. Avoid abstractions for hypothetical callers.
- Read affected callers and tests; verify dependent behaviour. Validate external input and use explicit types at boundaries; derive local types where possible.
- Fix root causes. A workaround that hides a symptom (a timer, a retry, a swallowed error, a special case) needs an issue for the real fix and ranokay's approval.
- Fail loudly. No silent fallbacks, empty catch blocks or defaults that mask missing data.

## Comments

Write none, except:

- a why-comment for a non-obvious external constraint, with a link (an upstream bug, a spec quirk);
- a tool directive with its reason (`// oxlint-disable-next-line <rule> -- <reason>`);
- a legally required licence header;
- a doc comment on a published library API.

Verify assertions in permitted comments and update or remove them when behaviour changes.

`TODO`, `FIXME`, `HACK`, `XXX` and commented-out code fail Gates. Express intent through names, types and tests instead. The rule and exemptions are in the universal Profile's `comment-ban.sh` (`--help`).

## Tests

- Test behaviour through public interfaces. Prefer a few integration-level tests over many unit tests.
- Every bug fix carries a regression test that fails without the fix.
- Never test private helpers or trivial mappings, mock the unit under test, keep snapshots nobody reads, or restate the implementation.
- Test current behaviour and meaningful edge cases, not deleted functionality. Keep fixtures simple. Name tests for behaviour, follow the project's test configuration and isolate external dependencies with test doubles where appropriate. Parameterize cases when it clarifies a shared contract; use coverage to find missing checks, not as proof of correctness.
- No coverage targets.

## Verification

- The Platform Profile names the verification driver; this table is the one mapping. Never invent a driver outside it.

  | Surface | Driver |
  | --- | --- |
  | Web page | Ardia CLI for a signed-in flow, Playwright for a reproducible browser check |
  | macOS app | cua-driver |
  | iOS or Android app | T3 device tools (`device_list`, `device_open`, `device_screenshot`) |
  | CLI or TUI | tmux |
  | Server or API | HTTP |

- A project's committed `verify-<app>` skill lives at `.agents/skills/verify-<app>/`; commit `.claude/skills/verify-<app>` as a relative symlink for Claude Code. Its feature map is `references/features/`, and its agent-facing CLI is the project's `verify:<app>` mise task.
- Verification Evidence comes from that skill's CLI, not from a throwaway script.

## Dependencies

- Prefer the standard library, the platform, and dependencies already in the repo.
- A new dependency needs a one-line reason in the PR body. Check its maintenance and known vulnerabilities before selecting it.
- Update dependencies for a concrete need, review release notes, avoid unrelated churn and verify affected behaviour.
- Tools come from `mise.toml`. JavaScript uses bun; Python uses uv.

## Repo contents

- Use the terms in the repo's `GLOSSARY.md` in code, issues and PRs.
- The only Markdown the repo authors is `README.md`, `AGENTS.md`, `GLOSSARY.md`, `REVIEW.md` (the AI Review rule file), `docs/adr/`, a Profile's `standards.md`, the `verify-<app>` skill, and `skills/<name>/` for an Own Skill, meaning a skill authored in this repo and absent from `skills.lock`. Markdown under directories recorded in `skills.lock` is vendored verbatim and excluded from that rule.
- Write an ADR only for a decision that is hard to reverse, surprising without context, and the result of a real trade-off.
- Never commit scratch, generated reports, evidence or temporary files.
- `mise run check` is the only Gate command. Never bypass hooks.


## Git and review

- Inspect the checkout and index before Git changes. Respect ownership, use a branch named for the change without tool or agent prefixes, stage and commit explicit paths, preserve unrelated index entries, unstage only your changes and keep commits atomic. Coordinate with the author before reverting their work; quote paths that the shell could expand.
- Run rebases with `GIT_EDITOR=:` and `GIT_SEQUENCE_EDITOR=:` and resolve conflicts against intended behaviour.
- Before opening an ordinary PR, identify the integration branch and rebase onto its current head. Preserve upstream ancestry in Sync PRs. Follow the repository's title and PR template; describe the problem, result, solution and validation. Mention a person only when they need to act; identify them through OWNERS or the team list, or ask if no owner is identifiable.
- For scanner analysis, use the correct project and branch, source language and complete context. Verify findings against current source; a stale remote finding does not verify a local fix, and a snippet result does not prove a project-wide analysis.

## Evidence

- Record the commands and results of required checks, including checks that could not run. Preserve test isolation and sequencing. Report actual exit status; a dirty run or a before-and-after snapshot does not prove a clean frozen Gate.
- Reread instructions that have changed.
