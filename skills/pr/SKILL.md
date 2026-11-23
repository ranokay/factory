---
name: pr
description: "Use when writing a PR body."
metadata:
  credits:
    skill: show-me
    author: Dex Horthy
    organisation: Humanlayer
    url: "https://github.com/humanlayer/skills/blob/main/plugins/show-me/skills/show-me/SKILL.md"
---

# PR body

Write the body in this order. The important line comes first; everything else is collapsed.

## Shape

````markdown
## Summary

<1-3 lines: what changed and the result>

## Why

<the problem, with the issue link>

## Evidence

[Verification Evidence](<comment URL>) - <one line saying what it proves>

## Merge Danger

**Door:** <one-way or two-way> - <why>
**Blast Radius:** <scope>

<details>
<summary>Details</summary>

<design notes, diffs, screenshots>

</details>
````

## Limits

| Part | Limit |
| --- | --- |
| Summary | 1-3 lines, 160 characters each |
| Why | 400 characters |
| Evidence | exactly 1 line, 300 characters, linking the comment and saying what it proves |
| Merge Danger | 400 characters, with `**Door:**` and `**Blast Radius:**` |
| Body outside `<details>` | at most 39 rendered lines |
| Fenced block outside `<details>` | at most 5 lines |

## Evidence

The evidence is the single `<!-- factory-evidence -->` comment the evidence skill posts. Link it on one line with a short statement of what it proves; never repeat the comment's summary, receipt or attachments. A non-visual change still posts that comment, so it still links it. Tests and CI logs belong behind the link, never pasted into the body.

## Write and check it

1. Open the PR with the rest of the body in this shape. Leave the Evidence link for the next step.
2. Run `~/Developer/factory/skills/evidence/scripts/evidence attach --pr <n>` (or `mise run evidence -- attach ...`). It prints the posted comment URL as its last line.
3. Put that URL and the one-line statement in the Evidence section, then check the final body with `gh pr view <n> --json body --jq .body | ~/Developer/factory/skills/pr/scripts/pr-text body -`.
4. Edit the PR until the checker passes. It exits non-zero and names the line that breaks.

Put anything beyond these sections in the one `<details>` block after Merge Danger. Recordings, long diffs and design notes go there, or behind the CI run link. No command transcripts: the visible body carries no logs, test output or diffs, and a fenced block outside `<details>` is capped.

## Merge Danger

`**Door:**` is `one-way` when a rollback is expensive or destructive, `two-way` otherwise. `**Blast Radius:**` names the scope in a word or two.
