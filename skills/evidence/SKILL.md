---
name: evidence
description: "Record Verification Evidence outside the repository and post the single PR evidence comment with its Run receipt."
---

# Verification Evidence

Use this skill for the proof a reviewer cannot get from tests: a screenshot, a screen recording, or a rendered statechart. The CLI stores evidence in the Factory cache, never in the repository, and turns it into one PR comment that ends with a Run receipt.

The command is `scripts/evidence` beside this file. In the Factory Repo it is also `mise run evidence -- <command> ...`. It needs `git`, `gh`, `ffmpeg`, `ffprobe` and Python 3.13, all declared in `mise.toml`.

## Where evidence lives

- PR known: `~/.cache/factory/<owner>/<repo>/<pr>/`
- PR not open yet: `~/.cache/factory/<owner>/<repo>/branch/<branch-slug>/`

`add` refuses a source file or a cache directory inside the repository, and nothing under the cache is ever added to git. A branch staging directory is moved into the PR directory by `attach`.

## Commands

| Command | What it does |
| --- | --- |
| `add <file>... [--pr N]` | Copies images and videos into the evidence directory. Refuses other file types: put text in `--summary`. A video over 10 MB is compressed with ffmpeg to fit before it is stored. |
| `path`, `list` | Print the evidence directory, or every file with its byte size. |
| `receipt ...` | Writes `receipt.json`: the comment summary and the Run receipt. Every field is required. |
| `attach [--pr N] [--dry-run]` | Moves staged evidence into the PR directory, renders `body.md`, and posts or replaces the single evidence comment with `gh pr comment --attach`. `--dry-run` copies nothing into place, posts nothing, and prints the body path and exact command. |
| `cleanup --worktree <path> --pr N` | After a merge: deletes the remote and local branch, the worktree with its build outputs, and both evidence directories. Run it from the main checkout. |

`--repo` defaults to the `origin` remote, or `gh repo view`; `--pr` defaults to the current branch's PR. Tests override the cache with `EVIDENCE_CACHE_ROOT` and the limit with `EVIDENCE_MAX_BYTES`.

## Attachments

GitHub accepts images and videos: png, jpg, jpeg, gif, webp, svg, mp4, mov and webm; at most 50 files of 10 MB each. The comment body references every attachment, so `gh` rewrites those references to the uploaded asset URLs and appends nothing after the receipt. A file still over the limit fails before any upload, with the size, the limit and what to do.

## The Run receipt

The receipt is the last block of the comment. Every value is required:

| Field | Source |
| --- | --- |
| Worker model and variant | the launch target given to the Worker |
| Fallback used | the executed model compared with the requested one |
| Attempts | the Run's Worker attempt count |
| Token cost | the harness's reported usage; `unavailable (<reason>)` only when it reports none |
| Gates run | the gate commands that actually ran, with their results |

## One comment per PR

The comment starts with `<!-- factory-evidence -->`. `attach` finds every marked comment through `gh api`; after posting the new one it deletes the previous ones, so a rerun leaves exactly one evidence comment and never a second. Attachments cannot be edited in place, which is why a rerun replaces the comment instead of updating it.

## Merge cleanup

Run `cleanup` from the main checkout, not from inside the worktree. It refuses unless `gh` reports the PR merged and every surviving ref and worktree tip is the merged head, and it refuses a dirty worktree. A missing worktree, branch or evidence directory is reported as already gone, so a rerun after a partial cleanup succeeds. It never deletes a ref whose tip is not the merged head, so unpushed commits survive the refusal.
