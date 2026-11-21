# Profile includes pin to a Factory tag

Repos pull Profile tooling from the Factory through mise `include` and `task_config.includes` entries shaped `git::https://github.com/ranokay/factory.git//profiles/<name>/<file>?ref=vX.Y.Z`, and an hk `amends` to `https://raw.githubusercontent.com/ranokay/factory/vX.Y.Z/profiles/<name>/hk.pkl`. The `profiles` Gate step rejects a branch, a commit SHA or a missing ref, and fails when the resulting Profile names disagree with the `Profiles:` line in the package's nearest `AGENTS.md`.

A branch would move tooling without review, and a commit SHA would turn every tooling update into a hand edit in every repo. A release tag gives Renovate one ref to bump as a reviewed PR per repo, while the release workflow and ranokay's approval keep the tag itself from moving. The trade-off is that tooling changes reach a consuming repo only after a release and a Renovate PR.

The Factory's own includes are local paths such as `./profiles/universal/mise.toml` and `amends "profiles/universal/hk.pkl"`. They are exempt from the tag rule because the checkout being checked is itself the pin. The Project Template shows the pinned remote form for new repos.
