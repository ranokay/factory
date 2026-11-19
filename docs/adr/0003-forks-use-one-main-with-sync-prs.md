# Forks use one main, reconciled through Sync PRs

A Personal Fork's `main` holds its own work; upstream changes arrive through a Sync PR that merges `upstream/main` with an adopt / keep-ours / entangled verdict per change, passes Gates and AI Review, and is merged with a merge commit. An Upstream Fork's `main` only fast-forwards. No fork keeps a mirror branch: the `upstream/main` remote-tracking ref already is the mirror, and a pushed mirror made the default branch differ from where work lives and let reconcile merges skip Gates.
