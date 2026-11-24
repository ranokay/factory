---
name: adopt
description: "Inventory a repository, apply the Factory's repository settings, and file one issue per unseen cleanup finding, so existing repos reach the standard through ordinary Runs."
disable-model-invocation: true
metadata:
  author: ranokay
---

# Adopt

Bring an existing repository up to the Factory standard through ordinary Runs.
`adopt` only reads the checkout, applies repository settings and files issues.
It never edits the target repository; every change arrives through a Run that
works a filed issue.

## Run it

Work from the target's Canonical Clone under `~/Developer`. The tree must be
clean and `origin` must match `<owner>/<repo>`.

Dry run:

```sh
mise -C ~/Developer/factory run adopt -- <owner>/<repo> --project ~/Developer/<repo> --dry-run
```

Apply:

```sh
mise -C ~/Developer/factory run adopt -- <owner>/<repo> --project ~/Developer/<repo>
```

The task runs the Factory's pinned Python from any cwd. The script also carries
an executable `#!/usr/bin/env python3` shebang for direct use.

The script runs `~/Developer/factory/scripts/repo-settings.py <owner>/<repo>
--apply` first: canonical labels, removal of GitHub's default labels, merge
settings and the ruleset are the same code `new` uses. It then files issues
labelled `ready-for-agent` plus `enhancement`; the brand issue is
`ready-for-human` plus `enhancement`.

If repo-settings still reports manual drift after applying (a missing vendor
file, Codacy not reporting yet), the inventory issues are filed and the command
exits non-zero. Fix the drift through the tooling issue or the repo-settings
output and rerun; the marker search prevents duplicates.

## Categories and detectors

| Category | Detector | Issue tells the Run |
| --- | --- | --- |
| tooling baseline | `mise.toml` missing a Factory Profile include (local `./profiles/` in the Factory repo, or a `v*`-pinned Factory URL in a consuming repo), `hk.pkl` not amending a pinned Factory Profile, `renovate.json` not extending `github>ranokay/factory`, or a missing advisory bot config. | Add the Factory baseline from `project/`. |
| comment purge | The universal Profile's `comment-ban.sh` over every tracked non-Markdown file: banned `TODO`, `FIXME`, `HACK`, `XXX` and commented-out code. | Use the `no-comments` skill; run `mise run check`. |
| dead code | Source files under `src/`, `lib/`, `Sources/`, `cmd/`, `internal/` or `pkg/` whose stem no other tracked file references; files with a NUL byte in the first 1 KiB are treated as binary and skipped, tests are not candidates, and entry points are excluded. If a text reference file cannot be read, `adopt` warns and files no dead-code issue rather than guess. | Confirm each candidate, delete the provably dead code, keep tests green. |
| AGENTS.md rewrite | Missing `AGENTS.md`, missing `Profiles:` line, no `mise run check`, `CLAUDE.md` not a relative symlink, or over 120 lines. | Rewrite from `project/AGENTS.md`; keep `CLAUDE.md -> AGENTS.md`. |
| stale files | Tracked OS/editor/backup artifacts (`*.orig`, `*.bak`, `*~`, `.DS_Store`, ...), logs, and tracked cache/build directories. | Remove them, ignore generated ones. |
| verification skill | No `.agents/skills/verify-<app>/SKILL.md`, or a skill missing its `.claude/skills` symlink, `references/features/` map, or a `verify:<app>` task when `mise.toml` exists. | Use `create-verification-skill`. |
| test pruning | Any tracked test file (`*.test.*`, `*.spec.*`, `test_*`, `tests/`, ...). | Work it with `prune-tests`. |
| brand | No `icon` SVG master under `assets/brand/`. This issue is `ready-for-human`. | Run `brand-assets` when it lands ([Factory #24](https://github.com/ranokay/factory/issues/24)); the choice of masters stays with ranokay. |

## Gardening and markers

Every finding has a stable key of category, repo-relative path and rule, never a
line number or file content. Its marker is:

```html
<!-- factory:adopt category=<category> finding=<sha256(stable key)[:12]> -->
```

The body carries one marker per finding it reports. A run searches open and
closed adopt issues, collects every marker, and files one issue per category
containing only unseen findings. A category with more than 100 unseen findings
is split across numbered issues so every finding is shown and marked. An
unchanged tree files nothing. After a cleanup PR merges, rerun; a new finding
gets one new issue for its category while earlier issues keep suppressing their
own findings.

## Upstream Forks

When the repo carries the `factory-upstream-fork` topic, `adopt` applies only
the fork settings `repo-settings.py` allows and files no issues: upstream's
conventions and contribution rules win. It prints that reason.

## Safety

- Never run `adopt` against a repository whose checkout is not the target; the
  origin check refuses a mismatch.
- Never let `adopt` edit files. Commit or stash before running; the clean-tree
  check enforces it.
- Never create labels, rulesets or merge settings outside `repo-settings.py`.
