---
name: new
description: "Create a Personal Project from the Project Template: repository, Canonical Clone, Profiles, repository settings, and the hand-off to grill-with-docs, brand-assets, to-spec and to-tickets. Also ensures the Ideas Project and promotes a draft idea into a new repository."
disable-model-invocation: true
metadata:
  author: ranokay
---

# New

Start a project from the Factory's Project Template. Run the commands from the Factory checkout, or from any cwd as `mise -C ~/Developer/factory run new -- <arguments>`; the script resolves the Template and the Canonical Clone root itself.

## Ideas

Ideas are draft items on the user-level `Ideas` Project. Add one in the project UI or with `gh project item-create <number> --owner ranokay --title "<title>" --body "<body>"`.

Ensure the project when it may not exist:

```sh
mise run new -- --ensure-ideas
```

Promote one draft into a new repository:

```sh
mise run new -- --idea <item-id-or-exact-title> [name]
```

The title and body become the repository description and seed `PRODUCT.md`. After the repository exists, the draft is converted to its first issue, so the board links the repository. With no name, the title becomes the slug.

## A project from scratch

```sh
mise run new -- <name> [--profile <name> ...] [--public]
```

The default is a private repository with the `universal` Profile. Repeat `--profile` for a chain such as `universal`, `typescript`, `web`; parents are added automatically, and Profiles that would need two different `hk.pkl` amends are refused.

The command:

- creates `ranokay/<name>` only when absent;
- makes its Canonical Clone at `~/Developer/<name>`;
- seeds the Project Template through `sync-upstream install-files`;
- writes the `Profiles:` line and each Profile's mise and task includes, pinned to the checkout's current Factory release tag (the Template's pin is the fallback when no release tag is reachable), and amends the leaf `hk.pkl`;
- makes `CLAUDE.md` a relative symlink to `AGENTS.md`;
- commits the seed (signed when signing works) and pushes `main`;
- runs `scripts/repo-settings.py ranokay/<name> --apply` for canonical labels, default-label removal, merge settings, the ruleset, and the `OPENCODE_ZEN_API_KEY` secret;
- promotes the idea, when one was given.

It is done when it prints `<owner>/<repo> is ready at <clone>` and the hand-off below.

Re-running is safe: it reuses the repository, clone and commit, keeps the repository's existing release pins, rewrites nothing that differs from the chosen Profiles, and never deletes or force-pushes. If repository settings still report drift, the command exits non-zero without the hand-off; fix the drift it names and rerun. A public repository's Codacy check can take a moment to report, so its first run may stop there and a rerun completes it. The first PR needs `Lint`, `Security`, `Tests` and `OCR` green.

## Hand-off

After the repository exists, continue in this order:

1. `grill-with-docs` to sharpen the idea and write the first ADRs and glossary.
2. Offer `brand-assets`; ranokay may decline, and the choice of masters stays with him.
3. `to-spec` to publish the spec and its user stories.
4. `to-tickets` to split the spec into work.

The script's final output prints the same four steps in the same order.
