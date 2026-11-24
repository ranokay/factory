# Counter verification map

This directory is the maintained source for verifying Counter. Read the index before driving, then use the matching feature file as the recipe.

## Baseline preconditions

- Run `mise run verify:counter -- doctor` before the first drive. It must report a running instance.
- Start with `VERIFY_COUNTER_STATE=$(mktemp -d)` so concurrent runs do not share a pid or port.
- Never drive an instance this run did not launch.

## Driving conventions

- Drive through `mise run verify:counter -- <command>`, not a hand-written port.
- Treat every command as literal.
- Capture the action and the resulting state in `$VERIFY_COUNTER_EVIDENCE`.
- A stopped app is not a failure of the feature; launch it first.

## Features

- [Health](./health.md) covers the liveness endpoint used by doctor.
- [Counter](./counter.md) covers reading and incrementing the count.
- [Reset](./reset.md) covers returning the count to zero.
