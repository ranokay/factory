# <repo>

<One sentence: what this is and who it is for.>

Profiles: universal

## Commands

- `mise run check`: every Gate
- `mise run dev`: <what it starts>
- `mise run build`: <what it produces>

## Constraints

- <An architecture rule tooling does not enforce yet. Delete it once a check does.>

## Profiles

The `Profiles:` line and the includes must agree; the `profiles` Gate checks them. To add a Language or Platform Profile, add its pinned mise and task includes and amend its hk file. A web app, for example:

```md
Profiles: universal, typescript, web
```

```toml
include = [
  "git::https://github.com/ranokay/factory.git//profiles/universal/mise.toml?ref=v0.2.0",
  "git::https://github.com/ranokay/factory.git//profiles/typescript/mise.toml?ref=v0.2.0",
  "git::https://github.com/ranokay/factory.git//profiles/web/mise.toml?ref=v0.2.0",
]

[task_config]
includes = [
  "git::https://github.com/ranokay/factory.git//profiles/universal/tasks.toml?ref=v0.2.0",
  "git::https://github.com/ranokay/factory.git//profiles/typescript/tasks.toml?ref=v0.2.0",
  "git::https://github.com/ranokay/factory.git//profiles/web/tasks.toml?ref=v0.2.0",
]
```

```pkl
amends "https://raw.githubusercontent.com/ranokay/factory/v0.2.0/profiles/web/hk.pkl"
```

Terms are in `GLOSSARY.md`; decisions are in `docs/adr/`.
