# AI Review comments are restyled after OCR posts them

OCR stays the review engine. A step in `.github/actions/ocr-review` reads `/tmp/ocr-result.json`, finds this run's sticky summary and inline comments on GitHub, and rewrites them into the Factory's layout: text priority badges (🔴 P0, 🟠 P1, 🟡 P2, ⚪ P3), a computed score and verdict, a linked findings table, a collapsed block for policy-routed findings, a collapsed Diagram only when OCR produced one, and a footer naming the reviewed head, range, OCR version, model and effort. Inline comments become a bold badge and title, the explanation, and a collapsed suggested change.

OCR's action has no template option. Rewriting after posting is the only place that can keep OCR's hidden markers byte-for-byte: `<!-- ocr-summary -->`, the summary run tag, the `<!-- ocr-checkpoint:v1 … -->` payload and the per-comment fingerprints. That preservation is what keeps `checkpoint_range`, `incremental` and `resolve_outdated` working on the next push. Rendering from the result JSON alone would lose them.

The score is computed, never model-written: 5 with no P0–P2, minus one per P2 to a floor of 3; any P1 scores 2; any P0 scores 1. 5 is "Safe to merge", 3–4 is "Merge after fixes", and 1–2 is "Blocking issues". REVIEW.md states the formula so the model neither computes nor prints one.

Findings the publication policy routes to the summary (medium and below, plus style and documentation here) are collapsed under "N lower-priority findings (in summary only)" and lose the misleading "GitHub could not post this as an inline comment" line, which is upstream alibaba/open-code-review#2071. A finding that really failed to post keeps a short `⚠️ Not posted inline: <reason>` line, so a posting failure is never hidden with the routed ones.

A Mermaid diagram is carried by one finding titled `**Diagram**` with category `documentation` and severity `low`, which the routing policy keeps in the summary. REVIEW.md asks for it only when the PR adds a module, workflow or command, or changes a call path across three or more files. The restyler lifts the block into the collapsed Diagram section and excludes the carrier from the score, the findings table, the lower-priority list and every count. A Mermaid block inside a normal finding stays in that finding.

OCR review mode has no project summary and no PR-description prose: `project_summary` is empty on the review path. The one sentence under the heading is therefore OCR's own status line, such as "Review complete: 2 finding(s) across 5 selected item(s)", with the carrier subtracted and only an emoji or bold prefix removed. The restyler never invents prose about what the PR does.

Any bad JSON, unexpected shape or API failure is a warning, not a failed job. The step builds every body first, then writes; if a write fails it restores the bodies already written and leaves OCR's original comment in place. A `same_head_noop` or `skipped` run is a clean no-op. The restyler lives beside the action and is invoked through `github.action_path`, so a repository that calls this composite action remotely still has it.

## Considered Options

- **An OCR template**: not available in the pinned action, and OCR's own summary is written inside its posting script. Rejected.
- **Render only from the result JSON and create fresh comments**: would drop the checkpoint payload and the inline fingerprints, breaking checkpointing, incremental dedupe and outdated resolution. Rejected.
- **Enable OCR's `--analysis` pass for the diagram and prose**: the pinned action does not expose it and it doubles the LLM work. Rejected in favour of one documentation/low carrier finding.
- **Fail the job when the restyle breaks**: turns presentation into a required check and can leave a review half-rewritten. Rejected in favour of restoring the originals and warning.
