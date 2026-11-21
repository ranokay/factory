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
