---
name: create-verification-skill
description: "Generate a project-local verification skill that drives your app the way a user does — any language, framework, or platform. Use for /create-verification-skill, \"make a control skill for this repo\", or when a project has no scripted way to prove UI/CLI/service behavior."
disable-model-invocation: true
---

# Create a verification skill

Every serious project needs a scripted way to drive the real app and prove behavior: launch it, exercise a feature the way a user would, and capture evidence. This skill generates that as a project-local skill at `.agents/skills/verify-<app>/`, plus a thin agent-facing CLI exposed as a `verify:<app>` mise task. You write the generator's output for the next agent, not for a human: it will be read cold, mid-task, by an agent that has never seen the app.

`.agents/skills/` is the one committed home: Codex scans it in every directory up to the repo root, and OpenCode reads it as a project compatibility source. Claude Code only reads project `.claude/skills/`, so commit a relative symlink there:

```sh
mkdir -p .claude/skills
ln -s ../../.agents/skills/verify-<app> .claude/skills/verify-<app>
```

Never write `.cursor/skills/`: no harness in the Factory reads it.

## 1. Interview the repo, not the user

Answer these from the codebase and only ask the user what you cannot observe:

- **Surface:** what does a user actually touch? A web UI, a CLI/TUI, a desktop app, an API, a mobile app, a library? A repo can have several; pick the primary one and note the rest.
- **Run:** how does the app start locally? Prefer the repo's own documented dev command (package scripts, Makefile, README quickstart). Note ports, env vars, seed data, auth.
- **Drive:** how can an agent interact with it programmatically? Existing harnesses first — Playwright/Cypress specs, expect scripts, PTY helpers, curl-able endpoints, a debug port. Then choose the driver the Factory's universal Verification table names for the surface. The project's Platform Profile adds the details; do not invent a driver outside that table.
- **Observe:** what evidence can be captured? Screenshots, terminal transcripts, response bodies, logs, exit codes, DB state.
- **Isolate:** can two instances run side by side (ports, data dirs, profiles)? If not, say so in the generated skill: refusing to double-drive a shared instance beats corrupting the user's session.

If the checkout doesn't build or start as-is, fix that first (or report it precisely) before generating; a skill written against a broken base teaches wrong steps. When an irrelevant missing asset blocks startup (a static dir the API never serves, a sample config), the generated skill may create it, clearly marked as verification scaffolding, and remove it in cleanup.

## 2. Generate the skill

Write `.agents/skills/verify-<app>/SKILL.md` with YAML frontmatter (`name: verify-<app>` and a `description` that names the app, the surface, and when to reach for it — without frontmatter the skill never registers) and these literal H2 sections, each grounded in what the interview actually found (no placeholders left):

- **`## Launch`** — the exact command that starts the app for verification, and how to tell it's ready (a log line, a prompt). Launch must reach a usable state from an empty data dir, including any migrations and seed data. Ready means usable, not just that a process answers. A database health check can pass while its init scripts are still running. Include teardown. For a short-lived CLI or TUI there is no server to keep alive: launch means build the binary (or install deps) once, then start each drive in its own isolated PTY or tmux session.
- **`## Doctor`** — one read-only check that answers "is this instance worth driving?": process up, right version/build, port owned by us, auth valid. An agent runs this first whenever anything looks off.
- **`## Drive`** — the harness recipe with real selectors/commands from this repo, not examples. Prefer stable handles (ARIA labels, data attributes, prompt strings, route paths) over coordinates and tab order.
- **`## Evidence`** — what to capture for a proof and where it goes. State the proof standards: exercise the real user path, not internal setters or test-only endpoints; capture the action and the resulting state, not just the final screen; verify side effects (files written, rows inserted, messages sent) alongside what's visible; mocks only where a production boundary already isolates the external system. When the safe path is a dry-run or test mode, verify what it actually skips by observing (files, network, git refs) rather than trusting its name: some dry-runs still touch the network or open a browser.
- **`## Cleanup`** — how to tear down instances the run created. Never kill by process name; kill what you started. Cleanup removes instances and scratch state, never the evidence: proof artifacts survive the teardown, in a location the skill names.
- **`## Helpers`** — any script the skill ships is executable and its invocation is shown in the skill body. A helper the reader has to reverse-engineer is not a helper.

The Factory's `verify-skill` shape check fails when `Launch`, `Doctor`, `Drive`, `Evidence` or `Cleanup` is missing, so keep all five even when a project has little to say in one of them.

## 3. Ship the CLI and its mise task

The skill owns one agent-facing CLI inside its own directory (for example `scripts/verify.py`), exposed as a mise task named `verify:<app>` in the project's root `mise.toml`:

```toml
[tasks."verify:<app>"]
description = "Drive <app> for verification"
run = "<interpreter> .agents/skills/verify-<app>/scripts/verify.py"
```

Show that invocation in the skill body. The CLI contract, which the shape check exercises:

- `mise run verify:<app> -- commands` prints one JSON object on stdout: `{"ok": true, "commands": [{"name": "doctor", "destructive": false, "description": "..."}, ...]}`. List every subcommand; this inventory is what the shape check trusts.
- `doctor` is read-only and prints JSON. When the app is stopped, exit non-zero with `ok: false` and an `alternative` that names the command to run first, for example `mise run verify:<app> -- launch`.
- Every destructive subcommand accepts `--dry-run` with no other arguments, prints a JSON object with `"dry_run": true`, exits 0, and changes nothing observable: no process, file, port or external state.
- An unknown subcommand or invalid argument exits non-zero and prints one JSON object on stdout with `ok: false`, a specific `error`, and an `alternative` that names the command to run instead.
- Write JSON on stdout only; send progress and logs to stderr.

The CLI is how later verification runs drive the app; the skill body should use it rather than ad-hoc shell.

## 4. Seed the feature map

Create `.agents/skills/verify-<app>/references/features/README.md` plus one file per user-facing feature you can identify (aim for the top 3-5 to start, from routes, commands, menus, or docs). Follow the shape in [`references/feature-map-example/`](references/feature-map-example/), with a README index and one file per feature. Each file answers, from the user's point of view: what the feature is, how to reach it, how to drive it with the harness, and what observable end state proves it works. The four H2s are `Sub-features`, `How to get to it (user POV)`, `Driving it with <harness>`, and `Gotchas`. If the seed data lacks a precondition that a journey needs, the journey reports a failure and the reason goes in that feature's `Gotchas`. Never weaken the journey to make it pass. The map is the repo's maintained verification source; a proof that drives one convenient entry point is incomplete when the map lists others. The Factory's shape check reads `references/features/` and requires every feature file to be indexed.

## 5. Prove the generated skill before handing it over

Run its own instructions end to end once: launch, doctor, drive ONE mapped feature (one is enough; the map exists so later runs can cover the rest), capture evidence, clean up. After cleanup, confirm the evidence still exists at the named location — a cleanup that eats the proof fails this step. Fix what fails, and run the generated cleanup after every failed iteration too, so broken attempts don't strand processes and ports. A generated skill that was never executed is a draft, not a deliverable. If you proved it in a stand-in for the real target (a container in place of a fresh VM, a local stack in place of a remote one), list every setup step the stand-in did that the target will not. Those steps stay unverified until a run on the real target covers them.

## 6. Offer the maintenance loop

Point the user at `/maintain-verification-skill` for keeping the map honest as the app changes. Suggest a cadence only if they ask.
