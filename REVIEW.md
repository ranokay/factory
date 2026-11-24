# AI Review rules

Review for three things, in this order:

1. **Correctness** — a path that returns the wrong result, drops an error, races, or breaks a caller.
2. **Security** — secrets, injection, authorization, and unsafe handling of untrusted input.
3. **Spec** — the change does what the linked issue asks, and nothing it does not.

Point each finding at the line and the failure it causes. Prefer one precise comment over several variations of the same idea. Check existing review threads before reporting, and do not repeat a finding they already cover.

Begin every finding with a one-line bold title naming the failure, for example `**Stale checkouts skip maintenance**`, then a blank line and the explanation. The comment restyler turns that title into the bold badge line; never write the literal word Title in place of a title.

Do not report:

- formatting, whitespace, line length, spelling, or pinned-action syntax the Gate already enforces;
- `subprocess` use inside tests, where it exercises the real hook;
- hardening for callers, inputs, or deployments that do not exist.

## Score

The restyle step computes the score; never write one yourself.

- Start at 5.
- Subtract 1 per P2, to a floor of 3.
- Any P1 scores 2.
- Any P0 scores 1.

The one-line verdict follows the score: 5 is "Safe to merge", 3–4 is "Merge after fixes", and 1–2 is "Blocking issues".

## Diagram

Emit a Mermaid diagram only when the PR adds or changes a flow between components: a new module, workflow or command, or a changed call path across 3 or more files. When it applies, add exactly one finding whose content is `**Diagram**` followed by a blank line and one ` ```mermaid ` block, with category `documentation` and severity `low`. OCR's routing keeps that finding in the summary, and the comment restyler lifts the block into the collapsed Diagram section.

Omit the Diagram finding when no such flow changed, and never put a Mermaid block anywhere else.
