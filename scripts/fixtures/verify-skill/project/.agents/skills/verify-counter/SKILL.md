---
name: verify-counter
description: "Drive the Counter fixture app over HTTP on loopback to prove its health, count and reset behaviour. Use when a change touches app.py or the counter endpoints."
---

# Verify Counter

Counter is a tiny HTTP app. It keeps one in-memory count and exposes `/health`, `/count`, `/increment` and `/reset` on a loopback port chosen at launch.

## Launch

Start one isolated instance and keep the state directory outside the repository:

```sh
export VERIFY_COUNTER_STATE=$(mktemp -d)
mise run verify:counter -- launch
```

Launch prints `{"ok": true, "pid": ..., "url": "http://127.0.0.1:<port>"}`. It is ready when the command returns, because launch waits for `/health` before reporting. The app exits when its process is stopped; there is no database or seed data.

## Doctor

```sh
mise run verify:counter -- doctor
```

Doctor exits 0 and prints `{"ok": true, "running": true, "url": ...}` only for a healthy instance this state directory started. A stopped app exits non-zero and names `mise run verify:counter -- launch` in `alternative`. Run it before the first drive and after any failed drive.

## Drive

```sh
mise run verify:counter -- count
mise run verify:counter -- increment
mise run verify:counter -- reset
```

`count` reads `GET /count`; `increment` posts `POST /increment`; `reset` posts `POST /reset`. Each prints one JSON object. Prefer these commands over hand-written curl so the app stays on the port the state directory recorded.

## Evidence

Save the command and its stdout for the action and the resulting state:

```sh
mkdir -p "$VERIFY_COUNTER_EVIDENCE"
mise run verify:counter -- increment | tee "$VERIFY_COUNTER_EVIDENCE/increment.json"
mise run verify:counter -- count | tee "$VERIFY_COUNTER_EVIDENCE/count.json"
```

A proof shows the increment response and a second read of the count, not only the final number. Evidence lives outside the repository and survives cleanup.

## Cleanup

```sh
mise run verify:counter -- stop
```

Stop kills only the pid in `$VERIFY_COUNTER_STATE`, waits for it to exit and removes that state directory. It never kills by process name and never removes `$VERIFY_COUNTER_EVIDENCE`. If launch failed, run stop anyway; it is safe with no recorded pid.

## Helpers

`scripts/verify.py` is the CLI the mise task runs. Every destructive command accepts `--dry-run`, prints `"dry_run": true` and changes nothing. An unknown command exits non-zero with an `alternative` naming `mise run verify:counter -- commands`.
