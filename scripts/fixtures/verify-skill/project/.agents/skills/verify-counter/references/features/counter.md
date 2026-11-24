# Counter

Counter keeps one in-memory integer. Reading returns `{"count": n}` and incrementing adds one.

## Sub-features

- `counter-read` returns the current count.
- `counter-increment` adds one and returns the new count.

## How to get to it (user POV)

- The `count` and `increment` subcommands of the verification CLI.
- `GET /count` and `POST /increment` over HTTP.

## Driving it with HTTP

Preconditions:

- A launched, healthy instance.
- The count is known before the drive; start from a reset instance when the value matters.

- **Read the count.** Run `mise run verify:counter -- count`. The JSON `count` matches the current state.
- **Increment.** Run `mise run verify:counter -- increment`. The JSON `count` is one higher than the previous read.
- **Confirm persistence.** Run `mise run verify:counter -- count` again. The value matches the increment response.

## Gotchas

- The count lives in memory; restarting the app resets it.
- A dry-run increment changes nothing and reports `"dry_run": true`; do not use it as a proof of mutation.
