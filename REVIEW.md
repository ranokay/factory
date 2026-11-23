# AI Review rules

Review for three things, in this order:

1. **Correctness** — a path that returns the wrong result, drops an error, races, or breaks a caller.
2. **Security** — secrets, injection, authorization, and unsafe handling of untrusted input.
3. **Spec** — the change does what the linked issue asks, and nothing it does not.

Point each finding at the line and the failure it causes. Prefer one precise comment over several variations of the same idea. Check existing review threads before reporting, and do not repeat a finding they already cover.

Do not report:

- formatting, whitespace, line length, spelling, or pinned-action syntax the Gate already enforces;
- `subprocess` use inside tests, where it exercises the real hook;
- hardening for callers, inputs, or deployments that do not exist.
