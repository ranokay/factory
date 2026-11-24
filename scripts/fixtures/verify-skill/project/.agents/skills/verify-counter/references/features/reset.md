# Reset

Reset returns the in-memory count to zero.

## Sub-features

- `reset-counter` posts to `/reset` and returns `{"count": 0}`.

## How to get to it (user POV)

- The `reset` subcommand of the verification CLI.
- `POST /reset` over HTTP.

## Driving it with HTTP

Preconditions:

- A launched, healthy instance.
- A known non-zero count, produced by `mise run verify:counter -- increment`.

- **Reset.** Run `mise run verify:counter -- reset`. The response reports `"count": 0`.
- **Confirm.** Run `mise run verify:counter -- count`. The value is still zero.

## Gotchas

- Reset changes only the in-memory count; it does not stop the app.
- A dry-run reset changes nothing and reports `"dry_run": true`.
