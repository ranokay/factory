# universal

## Scope

- Make the smallest change that closes the issue. Anything else you notice becomes a new issue.
- Fix root causes. A workaround that hides a symptom (a timer, a retry, a swallowed error, a special case) needs an issue for the real fix and ranokay's approval.
- Fail loudly. No silent fallbacks, empty catch blocks or defaults that mask missing data.

## Comments

Write none, except:

- a why-comment for a non-obvious external constraint, with a link (an upstream bug, a spec quirk);
- a tool directive with its reason (`// oxlint-disable-next-line <rule> -- <reason>`);
- a legally required licence header;
- a doc comment on a published library API.

`TODO`, `FIXME`, `HACK`, `XXX` and commented-out code fail Gates. Express intent through names, types and tests instead. The rule and exemptions are in the universal Profile's `comment-ban.sh` (`--help`).

## Tests

- Test behaviour through public interfaces. Prefer a few integration-level tests over many unit tests.
- Every bug fix carries a regression test that fails without the fix.
- Never test private helpers or trivial mappings, mock the unit under test, keep snapshots nobody reads, or restate the implementation.
- No coverage targets.

## Dependencies

- Prefer the standard library, the platform, and dependencies already in the repo.
- A new dependency needs a one-line reason in the PR body.
- Tools come from `mise.toml`. JavaScript uses bun; Python uses uv.

## Repo contents

- Use the terms in the repo's `GLOSSARY.md` in code, issues and PRs.
- The only Markdown the repo authors is `README.md`, `AGENTS.md`, `GLOSSARY.md`, `docs/adr/`, a Profile's `standards.md`, the `verify-<app>` skill, and `skills/<name>/` for an Own Skill, meaning a skill authored in this repo and absent from `skills.lock`. Markdown under directories recorded in `skills.lock` is vendored verbatim and excluded from that rule.
- Write an ADR only for a decision that is hard to reverse, surprising without context, and the result of a real trade-off.
- Never commit scratch, generated reports, evidence or temporary files.
- `mise run check` is the only Gate command. Never bypass hooks.

## Contracts and changes

- Read affected callers and tests; verify dependent behaviour. Validate external input and use explicit types at boundaries; derive local types where possible.
- Test meaningful public behaviour and edge cases; keep fixtures simple. Review dependency maintenance, vulnerabilities and release notes before an update; avoid unrelated version churn.
- Inspect the checkout and index before Git changes. Respect ownership, use a branch named for the change, stage only intended paths and keep commits atomic. Run rebases with `GIT_EDITOR=:` and `GIT_SEQUENCE_EDITOR=:` and resolve conflicts against intended behaviour.
- Verify scanner findings against current source. A snippet result does not prove a project-wide analysis.

## Review and evidence

- Avoid abstractions for hypothetical callers. Record the commands and results of required checks, including checks that could not run.
- Verify assertions in permitted comments and update or remove them when behaviour changes.
- Test current behaviour, not deleted functionality. Follow the project's test configuration; parameterize cases when it clarifies a shared contract. Use coverage to find missing checks, not as proof of correctness.
- Before selecting a new dependency, check its maintenance and known vulnerabilities. Update dependencies for a concrete need and verify affected behaviour.
- Stage and commit explicit paths, preserve unrelated index entries, and unstage only your own changes. Coordinate with the author before reverting their work; quote paths that the shell could expand.
- Before opening a PR, identify the integration branch and reconcile with its current head. Follow the repository's title and PR template, and mention a person only when they need to act.
- Preserve test isolation and sequencing. Report actual exit status; a dirty run or a before-and-after snapshot does not prove a clean frozen Gate.
- For scanner analysis, use the correct project and branch, source language and complete context. A stale remote finding does not verify a local fix; reread instructions that have changed.
