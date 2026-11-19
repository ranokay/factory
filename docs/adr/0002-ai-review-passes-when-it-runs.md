# AI Review passes when it runs; conversation resolution enforces findings

The CI AI Review engine is open-code-review on a pay-per-token OpenCode Zen key. Its job passes when the review completes, not when it finds nothing, and GitHub's required conversation resolution forces every inline finding, from any reviewer, to be fixed or answered before merge. This keeps an LLM's judgement out of the pass/fail signal while still making findings impossible to ignore.

## Considered Options

- **pr-agent with a check run**: the only cheap engine that can fail a check on findings, but noisier, with documented DeepSeek timeouts on large PRs. Rejected.
- **OpenCode Go subscription as the CI key**: Go's terms forbid processes that run while the user is not logged in. Rejected in favour of Zen pay-per-token.
