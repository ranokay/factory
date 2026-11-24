---
name: evidence
description: "Record Verification Evidence outside the repository and post the single PR evidence comment with its Run receipt."
---

# Verification Evidence

Use this skill for the visual proof a reviewer cannot get from tests: a screenshot of a UI state, a recording of a flow, or a rendered diagram or statechart. The CLI stores evidence in the Factory cache, never in the repository, and turns it into one PR comment that ends with a collapsed Run receipt.

## What to attach

An attachment proves something a reviewer has to see:

- a UI state, before or after the change;
- a recording of a flow that a static image cannot show;
- a rendered diagram or statechart.

Never attach logs, command output, test output or diffs, and never render them as images. That proof belongs in the summary, the Run receipt, or the link to the CI run that holds the full log. A change with no visual surface, such as a CLI, workflow, infra, docs or Factory skill change, attaches nothing: the comment is just the summary and the Run receipt.

The command is `scripts/evidence` beside this file; from any other repository it is `~/Developer/factory/skills/evidence/scripts/evidence`. In the Factory Repo it is also `mise run evidence -- <command> ...`. It uses `git`, the GitHub CLI `gh`, and `ffmpeg` with its `ffprobe`; `ffmpeg` and Python 3.13 are declared in `mise.toml`. In the Factory, run it on the Worker model: `opencode-go/deepseek-v4.1-flash` at variant `max`, or `opencode-go/glm-5.3` at variant `max` when the Worker fell back.

## Where evidence lives

- PR known: `~/.cache/factory/<owner>/<repo>/<pr>/`
- PR not open yet: `~/.cache/factory/<owner>/<repo>/branch/<branch-slug>/`

`add` refuses a source file or a cache directory inside the repository, and nothing under the cache is ever added to git. A branch staging directory is moved into the PR directory by `attach`.

## Commands

| Command | What it does |
| --- | --- |
| `add <file>... --caption <text>... [--pr N]` | Copies images and videos into the evidence directory. One `--caption` per file, in order, says what visible behaviour it proves and renders under it. Refuses other file types: put text in `--summary`. At most 5 attachments per PR, counting files already stored: a sixth is refused and must be dropped or merged. A video over 10 MB is compressed with ffmpeg to fit before it is stored or attached. |
| `path`, `list` | Print the evidence directory, or every file with its byte size. |
| `receipt ...` | Writes `receipt.json`: the comment summary and the Run receipt. Every field is required. The summary is at most 10 lines: one per command with its exit code and a link to the CI run that holds the full log. A longer summary is refused. |
| `advisory --text <line>` | Adds or replaces the `Advisory reviews` row in an existing `receipt.json`, for the bounded wait's result (`all terminal` or `timed out: <names>`). The text is one line. `attach` renders the row in the receipt. |
| `attach [--pr N] [--dry-run]` | Moves staged evidence into the PR directory, renders `body.md`, and posts or replaces the single evidence comment with `gh pr comment --attach`. It prints the posted comment URL as its last line. With attachments it uploads them and renders each caption under it; with none it posts just the summary and the receipt, without any `--attach` flags. `--dry-run` copies staged files into the PR directory, writes `body.md` and prints the exact command, but posts nothing, prints no URL and leaves the staging directory. It still compresses an over-limit video and updates its caption, so the printed command matches the real attach. |
| `cleanup --worktree <path> --pr N` | After a merge: deletes the remote and local branch, the worktree with its build outputs, and both evidence directories. Run it from the main checkout. |

`--repo` defaults to the `origin` remote, or `gh repo view`; `--pr` defaults to the current branch's PR. Tests override the cache with `EVIDENCE_CACHE_ROOT` and the limit with `EVIDENCE_MAX_BYTES`.

## Attachment limits

Every attachment carries the one-line caption given to `add`, and the comment renders it under the image or video; each file name maps to one caption, and re-adding a name replaces that attachment. At most 5 attachments are stored per PR, counting files already in the evidence directory; a sixth is refused with a message to drop or merge files. GitHub accepts images and videos: png, jpg, jpeg, gif, webp, svg, mp4, mov and webm; at most 10 MB each. A video over 10 MB is compressed when it is added or attached, and the compressed file keeps its caption. The comment body references every attachment, so `gh` rewrites those references to the uploaded asset URLs and appends nothing after the receipt. A file still over the limit fails before any upload, with the size, the limit and what to do.

## The Run receipt

The receipt is the last block of the comment, inside a collapsed `<details>` block so the summary stays readable. Every value is required except `Advisory reviews`, which is present only after the bounded wait records it:

| Field | Source |
| --- | --- |
| Worker model and variant | the launch target given to the Worker |
| Fallback used | the executed model compared with the requested one |
| Attempts | the Run's Worker attempt count |
| Token cost | the harness's reported usage; `unavailable (<reason>)` only when it reports none |
| Advisory reviews | the bounded wait's result when `advisory` ran: `all terminal` or `timed out: <names>` |
| Gates run | the gate commands that actually ran, with their results |

## One comment per PR

The comment starts with `<!-- factory-evidence -->`. `attach` posts the new comment, then deletes the previous marked ones; if a delete fails it withdraws the new comment and exits non-zero. A run therefore replaces the previous evidence; if the withdrawal also fails, the error names both comments that remain. Attachments cannot be edited in place, which is why a rerun replaces the comment instead of updating it.

## Merge cleanup

Run `cleanup` from the main checkout, not from inside the worktree. It refuses unless `gh` reports the PR merged and every surviving ref and worktree tip is the merged head, and it refuses a dirty worktree. A missing worktree, branch or evidence directory is reported as already gone, so a rerun after a partial cleanup succeeds. It never deletes a ref whose tip is not the merged head, so unpushed commits survive the refusal. The remote branch is deleted under an expected-SHA lease, and another open PR using that branch refuses the cleanup.
