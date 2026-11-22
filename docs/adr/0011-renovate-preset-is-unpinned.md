# The Renovate preset is unpinned

Every repository extends the Factory's Renovate preset with the single entry `"extends": ["github>ranokay/factory"]`. The Factory's root `renovate.json` and the Project Template's `project/renovate.json` use the same entry, so the Factory exercises what consumers get.

ADR 0010 pins Profile includes to a Factory tag because an hk fragment resolves its scripts and sibling includes relative to the tag of the file that amends it. The Renovate preset has no such relative coupling: it is one `default.json` that Renovate reads from the default branch. Pinning it would need a custom manager to bump the `extends` line itself and would delay every policy fix until a Factory release, without a second review: the Factory pull request and the `renovate-config` Gate are where the preset is reviewed.

The trade-off is that a preset change reaches every repository on its next Renovate run instead of through a per-repository PR. The Gate validates the preset and every `renovate.json`, and rollout still waits for the weekly schedule.
