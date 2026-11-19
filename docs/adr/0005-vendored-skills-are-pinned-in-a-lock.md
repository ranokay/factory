# Vendored Skills are pinned in a lock and patched only when unavoidable

Every third-party skill lives under `skills/<name>` byte-for-byte at the commit recorded in `skills.lock`; `mise run skills:sync` re-materializes it and `--check` fails on any hand edit. The lock also records a SHA-256 for every file and patch, so the Gate is offline and deterministic, while `--check --fetch` proves the bytes against upstream. A Skill Patch lives at `patches/<skill>/NNNN-<name>.patch`, is re-applied on every sync, and fails loudly on conflict.

Git submodules were rejected because a harness must see one flat `skills/` folder, and hand-copying was rejected because it cannot detect drift or show updates. The trade-off is a small sync CLI and its fixture tests inside the Factory.

Content Gates that judge prose or formatting skip the vendored trees, because their bytes cannot be edited; `gitleaks` still scans them.
