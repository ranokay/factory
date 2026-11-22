# The Renovate preset is unpinned

Every repository extends the Factory's Renovate preset with the single entry `"extends": ["github>ranokay/factory"]`. The Factory's root `renovate.json` and the Project Template's `project/renovate.json` use the same entry, so the Factory exercises what consumers get.

ADR 0010 pins Profile includes to a Factory tag because an hk fragment resolves its scripts and sibling includes relative to the tag of the file that amends it. The Renovate preset has no such relative coupling: it is one `default.json` that Renovate reads from the default branch. Renovate's built-in `renovate-config` manager can bump a tag-pinned preset (`github>ranokay/factory#vX.Y.Z`), so pinning would not need a custom manager. We keep the preset unpinned because a policy change should reach every repository on its next run rather than wait for a release and a per-repository PR. The Factory pull request and the `renovate-config` Gate are where the preset is reviewed.

The preset is the whole dependency policy, not a layer on `config:recommended`: the schedule, grouping, auto-merge, labels and Factory pin managers are set explicitly here, so one non-major group remains the policy. A repository that needs the dependency dashboard can add another preset to its own `extends` array.

The cost is that a preset change reaches every repository on its next Renovate run instead of through a per-repository PR. The Gate validates the preset and every `renovate.json`, and rollout still waits for the weekly schedule.
