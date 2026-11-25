# A pushback review gates ready-for-agent

Every issue passes one review before it can carry `ready-for-agent`. A reviewer on the Worker model (`opencode-go/deepseek-v4.1-flash` at variant `max`, or `opencode-go/glm-5.3` at variant `max` on the fallback), never the model that wrote the issue, reads the issue against `PRODUCT.md` and the codebase on six checks: the problem is real, the simplest alternative, overlap, maintenance cost, reversibility and non-goal conflict. Promotions and bugs with reproduction evidence get a light pass over overlap and the simplest fix. The reviewer answers go, reshape with the smaller version, needs-info with the exact question, or don't with the reason and any quoted non-goal; `scripts/pushback.py` validates that answer, posts the one comment and applies the state.

Only a recorded go or an accepted reshape sets `ready-for-agent`. `triage.py labels` refuses the label without that record, `file-promotion` refuses it as an option, and adopt and verify-skill-maintenance file `needs-triage` so the review sees their issues too. A reshape stays `needs-triage` until ranokay accepts it with `accept-reshape`, which appends the smaller version to the issue body as `## Reshaped scope`; a don't adds `wontfix` only after `confirm-dont`. ranokay can override any verdict with one line, recorded as its own comment, and those comments are read back into every later review's context as precedent.

A tenth label-writing workflow to police `gh issue edit --add-label ready-for-agent` would itself be a Protected Surface, so the direct-editing gap is a separate follow-up (#170). The deterministic gate covers every scripted path the Factory uses; a human label click still bypasses it until that guard lands.

## Considered Options

- **A reviewer bot with its own check**: a Required Check runs only on pull requests, and the gate must answer before an issue becomes agent-ready. Rejected.
- **Let triage judge without a separate agent**: the triage session is often the model that shaped or filed the issue, so it cannot be the independent reviewer. Rejected.
- **Keep overrides in a local memory file**: GitHub is the System of Record, and a file would not travel with the issue. Rejected in favour of marked comments read through the issue search.
