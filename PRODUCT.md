# Factory

The machinery that takes ranokay's work from idea to merged, verified change across every project.

## Who it's for

ranokay, working alone across his own repositories and forks, and the agents that run his Runs. Agents read these instructions, skills and Profiles as their operating manual.

## Goals

- Keep the shared instructions, skills, Profiles and reusable CI in one place, so every project starts from the same standard.
- Give the bulk work to cheap Worker models and the judgement to frontier Orchestrators.
- Take every change through an issue, a Run, deterministic Gates, Verification Evidence and review.
- Keep GitHub as the only System of Record.

## Non-goals

- No project knowledge, secrets or per-project code in this repository: it holds machinery only.
- No hosted service, dashboard or web UI: the GitHub issue tracker and PRs are the interface.
- No local plan, todo or progress files: they would compete with the System of Record.
- No speculative platform or language support: a Profile lands when a real project needs it.
- No prose rules where a Gate, lint rule, type or API change can make the mistake impossible.

## Quality bars

- **Platforms:** macOS for the local tooling; GitHub Actions `ubuntu-latest` for CI.
- **Runtime:** Python 3.13 and POSIX `sh` from `mise`; tests run without network access.
- **Performance:** `mise run check` stays inside the 20-minute CI timeout for each Gate job; the slowest fixture test (adopt) runs in under two minutes.
- **Privacy:** the public repository holds no secrets or project knowledge; credentials live in 1Password and reach tools through `op` or launchd.
