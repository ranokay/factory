# typescript

## Toolchain

- `bun` owns packages and scripts: `bun install`, `bun run`, `bun <file>`. Commit `bun.lock` and never add another package manager's lockfile.
- Start from the Profile's `tsconfig.json`: `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `verbatimModuleSyntax` and `noEmit`. A looser flag needs a reason in the PR.
- oxlint and oxfmt own lint and formatting. knip owns dead code; an ignore entry needs a linked issue.

## Types

- Parse external data into a typed value at the boundary. Inside the program, trust the type.
- Use `unknown` for unvalidated input. A cast is a claim you must be able to defend in one line; `as any` is never the answer.
- Model states as a discriminated union on a `type` or `kind` field, so an impossible combination does not compile.
- Prefer `readonly` and `ReadonlyArray` for data that crosses a module boundary.
- Derive a type from its source of truth (`typeof`, `satisfies`, schema inference) instead of restating the shape.
- Use `type` for unions and aliases; `interface` only for an object shape that is extended or declaration-merged.

## Modules and async

- ESM only; no `require`. Node built-ins use the `node:` prefix.
- Use `import type` for a type-only import.
- No barrel files. Import from the module that defines the value.
- Await every promise. A floating promise or an empty `catch` is a bug.
- Return a discriminated result type for an expected failure; reserve exceptions for the unexpected.
- Use `const` by default and `let` only when the binding is reassigned.

## JavaScript and the web

- `public/assets/` conventions come from the web Profile when it is declared; do not invent a second asset layout.
- Keep browser globals out of a module that Node also loads. Split the platform-specific part behind a narrow interface.
