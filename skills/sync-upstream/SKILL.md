---
name: sync-upstream
description: "Reconcile a fork with upstream. A Personal Fork gets a Sync PR that merges upstream with one verdict per upstream change and a merge commit; an Upstream Fork only fast-forwards. Also installs the Project Template with the smallest diff."
---

# Sync a fork with upstream

Implement the one-`main` model in `docs/adr/0003-forks-use-one-main-with-sync-prs.md`.
Every deterministic step below is the `script` beside this file:
`<this skill's directory>/scripts/sync-upstream`.

## Read the fork kind

The repository topic is the only source of the kind:

```
<this skill's directory>/scripts/sync-upstream kind
```

- `personal` when the topic is `factory-personal-fork`.
- `upstream` when the topic is `factory-upstream-fork`.
- `personal-project` for a repository that is not a fork; there is no upstream to sync.

When `kind` exits 3, the fork has no topic. Ask ranokay once, then record the answer
from the Factory checkout and rerun `kind`:

```
uv run scripts/repo-settings.py OWNER/REPO --fork-kind personal --apply
uv run scripts/repo-settings.py OWNER/REPO --fork-kind upstream --apply
```

A later Run reads the topic and never asks again. In a Run, put the question under
`questions` and stop; never guess the kind and never set the topic yourself.

Never create or push a mirror or a `develop` branch. `upstream/<default>` is the
mirror.

## Personal Fork

### 1. Install Factory files first

```
<this skill's directory>/scripts/sync-upstream install-files --check
```

If it reports a change, run it without `--check` on an ordinary branch, commit, open
a normal PR and squash-merge it. Do not mix these changes into a Sync PR: the Sync
PR carries upstream changes only.

`install-files` copies the Project Template where the upstream has no such file. Where
upstream already has one, it adds only what the matching `project/` file requires:

- `AGENTS.md`: the missing `Profiles:` line from `project/AGENTS.md`;
- `mise.toml`: the Factory includes and settings from `project/mise.toml`;
- `renovate.json`: each Factory `extends` entry from `project/renovate.json`;
- `.greptile/config.json`: each missing key from `project/.greptile/config.json`;
- `.coderabbit.yaml`: the missing `enabled` value from `project/.coderabbit.yaml`;
- an existing `hk.pkl` is untouched and reports
  `hk.pkl: upstream has its own; Factory Gates not amended`.

### 2. Prepare the branch and the merge

```
<this skill's directory>/scripts/sync-upstream sync
```

This reads the topic, fetches `origin` and `upstream`, branches
`sync/upstream-<YYYY-MM-DD>` from the fork's default branch, and merges upstream's
default branch with `git merge --no-ff`. The two defaults may differ; each is read
from GitHub. It never rebases, squashes, creates a mirror or pushes a `develop`
branch. A dirty tree, a missing topic, or a conflict stops it.

Resolve a conflict from the verdicts: `keep-ours` keeps the fork's version,
`entangled` is a hand resolution that mixes both. Never rebase or squash the merge;
the upstream ancestry must stay.

### 3. Write the verdicts

```
<this skill's directory>/scripts/sync-upstream body --from origin/<default> --to upstream/<default> > body.md
```

`--from` is the fork's default branch and `--to` is upstream's; `verdicts` reads
both defaults from GitHub when they are omitted. The body lists one row per
**first-parent** upstream commit. Give each row exactly one verdict:

- `adopt` takes upstream's version;
- `keep-ours` keeps the fork's version;
- `entangled` mixes both and needs a hand resolution.

An upstream merge commit stands for its whole merged PR, so its side-branch commits
need no rows of their own.

```
<this skill's directory>/scripts/sync-upstream verdicts --from origin/<default> --to upstream/<default> --body-file body.md
```

The Gate fails, naming every commit without a verdict, and rejects a literal
`<verdict>` placeholder. Fix the body before going further.

### 4. Open the Sync PR

Push `sync/upstream-<YYYY-MM-DD>` and open a PR against `<default>` with `body.md`.
Run the repository's documented Gate on the merge commit. The PR must pass its
required checks and AI Review.

Hand the green PR to `babysit-pr`. It recognises a Sync PR by the verdict body and
merges it with a **merge commit**, never a squash. At merge readiness it runs
`verdicts --pr <n>`; a missing verdict is a blocking finding.

Never merge a Sync PR whose verdicts fail. Never force-push its branch.

## Upstream Fork

An Upstream Fork follows upstream's conventions, commits no Factory files, and never
merges its PR.

### 1. Fast-forward only

```
<this skill's directory>/scripts/sync-upstream sync
```

This reads the topic and pushes upstream's default branch to the fork's default
branch only when the fork's default is an ancestor. The two defaults may differ; each
is read from GitHub. A non-fast-forward prints the divergent commits and stops; change
the branch by an ordinary PR instead, never by a force-push.

### 2. A Run from an Upstream Fork

- Branch from `upstream/<default>`, not from `origin/<default>`.
- Read upstream's `AGENTS.md` and contribution rules; they win over the Factory's.
- Commit no Factory files. Check with:

  ```
  <this skill's directory>/scripts/sync-upstream factory-files --from upstream/<default>
  ```

  The check takes its Factory path set from `project/` at runtime plus `CLAUDE.md` and
  `GLOSSARY.md`. Adding a set path that upstream lacks fails. Changing a set path that
  upstream has fails only when an added line carries `ranokay/factory` or a `Profiles:`
  line. A workflow whose added lines reference `ranokay/factory` fails. Upstream's own
  edits, such as a tool bump in its own `mise.toml`, pass.
- Review with `ocr review --from upstream/<default>`.
- End at a green open PR. Never merge it and never run a merge skill against it.

## Stop conditions

Stop and report rather than working around any of these:

- `kind` exits 3: the fork has no topic and only ranokay can answer.
- `sync` refuses a non-fast-forward on an Upstream Fork.
- A Personal Fork merge conflicts: resolve from the verdicts, or record an
  `entangled` verdict and stop if the resolution needs a decision only ranokay can make.
- `verdicts` fails: every upstream change needs a verdict before the PR is ready.
- `factory-files` fails on an Upstream Fork: remove the Factory change from the branch.
- `install-files` finds an existing Factory file with no merge rule: add the rule,
  never replace the file.
- The repository's Gate fails: it is a real finding, not a flake to bypass.
