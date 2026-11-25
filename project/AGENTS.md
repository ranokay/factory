# <repo>

<One sentence: what this is and who it is for.>

Profiles: universal

## Commands

- `mise run check`: every Gate
- `mise run assets`: every brand export from `assets/brand`; add `--check` to verify
- `mise run dev`: <what it starts>
- `mise run build`: <what it produces>

## Constraints

- <An architecture rule tooling does not enforce yet. Delete it once a check does.>

## Profiles

The `Profiles:` line and the includes must agree. The Factory's `profiles` Gate checks the Factory's own packages; keep a consuming repo's line and includes aligned the same way. To add a Language or Platform Profile, add its pinned mise and task includes and amend its hk file. A web app, for example:

```md
Profiles: universal, typescript, web
```

```toml
include = [
  "git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=vX.Y.Z",
  "git::https://github.com/ranokay/factory.git//profiles/typescript/mise.toml?ref=vX.Y.Z",
  "git::https://github.com/ranokay/factory.git//profiles/web/mise.toml?ref=vX.Y.Z",
]

[settings]
experimental = true

[task_config]
includes = [
  "git::https://github.com/ranokay/factory.git//profiles/universal/tasks.toml?ref=vX.Y.Z",
  "git::https://github.com/ranokay/factory.git//profiles/typescript/tasks.toml?ref=vX.Y.Z",
  "git::https://github.com/ranokay/factory.git//profiles/web/tasks.toml?ref=vX.Y.Z",
]
```

Mise refuses `settings` in a config include and gates remote `git::` task includes behind the experimental flag, so the root `mise.toml` sets it; a Profile fragment cannot carry it.

```pkl
amends "https://raw.githubusercontent.com/ranokay/factory/vX.Y.Z/profiles/web/hk.pkl"
```

Replace `vX.Y.Z` with the Factory release that carries the Profiles you declare. The `v0.5.0` pins in `mise.toml` and `hk.pkl` carry the universal fragments the Template callers use: re-pin both files whenever you add a Profile from a newer release.

Terms are in `GLOSSARY.md`; decisions are in `docs/adr/`.
