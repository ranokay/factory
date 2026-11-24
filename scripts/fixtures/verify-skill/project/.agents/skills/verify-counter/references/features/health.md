# Health

Counter answers `/health` with `{"ok": true}` when the HTTP server is accepting requests. Doctor uses it to decide whether the instance is worth driving.

## Sub-features

- `health-ok` returns `{"ok": true}` on a running instance.
- `health-stopped` makes doctor exit non-zero with the launch alternative.

## How to get to it (user POV)

- Any client that can reach the loopback URL printed by launch.

## Driving it with HTTP

Preconditions:

- `mise run verify:counter -- launch` printed a URL.
- `mise run verify:counter -- doctor` exits 0.

- **Read health.** Run `curl -fsS http://127.0.0.1:<port>/health`. The response is `{"ok": true}`.
- **Check the stopped case.** Run `mise run verify:counter -- stop`, then `mise run verify:counter -- doctor`. Doctor exits non-zero with `alternative` naming launch.

## Gotchas

- A stale state directory can point at a port another process now owns; doctor catches the unhealthy case.
- Do not treat a successful TCP connect as healthy; read the JSON body.
