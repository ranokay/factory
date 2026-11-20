# Vendored Skills are pinned in a lock and patched only when unavoidable

Every third-party skill the Factory vendors lives under `skills/<name>` byte-for-byte at the commit recorded in `skills.lock`; `mise run skills:sync` re-materializes it and `--check` fails on any hand edit. The lock also records a SHA-256 for every file and patch, so the Gate is offline and deterministic, while `--check --fetch` proves the bytes against upstream. A Skill Patch lives at `patches/<skill>/NNNN-<name>.patch`, is re-applied on every sync, and fails loudly on conflict.

Tool-owned skills are not vendored: the Ardia, cua and Context7 CLIs install them into the same flat folder through the harness symlinks. They are ignored by git, `scripts/tool-skills.txt` declares them, and `mise run bootstrap -- --check-tool-skills` fails if one is tracked, if an undeclared skill is untracked, or if the manifest and `.gitignore` disagree (issue #8). The lock records only what the Factory installs itself, while every harness still sees one folder.

Git submodules were rejected because a harness must see one flat `skills/` folder, and hand-copying was rejected because it cannot detect drift or show updates. The trade-off is a small sync CLI and its fixture tests inside the Factory.

Typos and editorconfig-checker skip vendored trees, and shellcheck and shfmt skip the individual vendored files that fail them, because those bytes cannot be edited; `gitleaks` still scans them.

`skills:sync` rewrites the vendored tree and is the repair path; `skills:sync --check` is the Gate that fails on a hand edit, added file or deletion.
