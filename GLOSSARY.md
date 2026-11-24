# Factory

The machinery that takes ranokay's work from idea to merged, verified change across every project, with frontier models judging and cheap models doing the bulk work.

## Language

### Places

**Canonical Clone**:
The chosen clone of a repository under `~/Developer`, used as its primary local copy.
_Avoid_: master clone, main checkout

**Factory Repo**:
The public repository that holds the global instructions, skills, shared configs and reusable CI workflows. It holds machinery only, never project knowledge or secrets.
_Avoid_: wiki, dotfiles, agent-workflows

**Project Template**:
The starting files under `project/` that seed a repository.
_Avoid_: repo template, boilerplate

**System of Record**:
GitHub issues, labels, pull requests and their comments: the only durable home of work state.
_Avoid_: plan file, todo list, progress file

### Projects

**Personal Project**:
A repository ranokay owns, where every Factory convention applies.
_Avoid_: own repo, main repo

**Personal Fork**:
A fork kept for ranokay's own use; treated as a Personal Project, with Factory files placed where upstream never writes.
_Avoid_: private fork, custom fork

**Upstream Fork**:
A fork used to contribute back; upstream's conventions win and no Factory files are committed.
_Avoid_: contribution fork, PR fork

**Outside Contributor**:
Anyone other than ranokay or the Factory opening issues or pull requests on a Personal Project.
_Avoid_: external user, third party

### Roles

**Orchestrator**:
A frontier-model agent that plans, delegates, and judges evidence and findings; it never does bulk reading, writing or review fan-out. Either a Dispatcher or a Supervisor.
_Avoid_: watcher, manager

**Dispatcher**:
The Orchestrator that picks ready issues and starts one Run per issue, reading only issue metadata.
_Avoid_: coordinator, scheduler

**Supervisor**:
The Orchestrator that owns one Run: it approves the Worker's plan, judges findings and evidence, and decides when the Definition of Done holds.
_Avoid_: watcher, lead

**Worker**:
A cheap-model agent that implements an issue, runs Gates and produces Verification Evidence.
_Avoid_: runner, implementer, executor

**Reviewer**:
A cheap-model agent or bot that inspects a change and reports findings; it never edits the change.
_Avoid_: judge, critic

### Work

**Run**:
One issue taken from ready to merged by an Orchestrator and its Worker.
_Avoid_: thread, session, job

**Run budget**:
The four Worker attempts a Run gets; the first three failures may be followed by another attempt, and a fourth failure makes it a Blocker.
_Avoid_: retry limit, attempt counter

**Sync PR**:
A pull request that merges upstream's default branch into a Personal Fork's `main`, carrying a verdict for every upstream change.
_Avoid_: mirror sync, upstream merge, develop branch

**Blocker**:
A condition a Run cannot resolve with its own access, such as missing credentials or permissions, an OS permission prompt, an exhausted Orchestrator quota or attempt budget, a required reviewer that cannot run, or a decision only ranokay can make.
_Avoid_: issue, problem, error

**Gate**:
A deterministic check (format, lint, type, test, build) that must pass locally and in CI.
_Avoid_: hook, validation

**Verification Evidence**:
Artifacts proving a change works as intended at runtime, such as screenshots, videos, traces or statecharts, inspected by the Orchestrator and attached to the pull request only when the change has a visual surface.
_Avoid_: proof files, test output, scratch

**Verification Skill**:
The committed `verify-<app>` skill that launches, doctors, drives and cleans up a project's app, with its feature map at `references/features/` and its CLI exposed as the project's `verify:<app>` mise task.
_Avoid_: control skill, run skill, `.cursor` skill

**Verification Driver**:
The tool a Platform Profile names for a surface: Ardia CLI or Playwright for web, cua-driver for macOS, T3 device tools for iOS, tmux for a CLI or TUI, and HTTP for a server or API.
_Avoid_: test harness, automation framework

**Maintenance Issue**:
The weekly `ready-for-agent` issue that runs `/maintain-verification-skill` in a repository whose app changed since the verification skill's last maintenance, with the filed head recorded in its body.
_Avoid_: upkeep ticket, weekly chore

**GC**:
The `mise run gc` task that prunes stale worktrees, old Verification Evidence, package caches and unused mise tool versions; it never deletes work that is not on a remote.
_Avoid_: cleanup, janitor

**Definition of Done**:
Gates green, Verification Evidence inspected, and every review finding fixed or filed as a linked issue.
_Avoid_: done, complete, ready

**Protected Surface**:
The parts of a change that always need ranokay's approval before merge: CI workflows, auth, secrets handling, data migrations and major dependency bumps.
_Avoid_: sensitive files, risky code

### Checks

**Required Check**:
A pull-request check that must pass to merge; always deterministic or backed by a key ranokay pays for, never quota-limited.
_Avoid_: blocking review, mandatory bot

**Advisory Reviewer**:
A quota-limited review bot whose comments are read when they arrive and never waited for.
_Avoid_: optional check, nice-to-have bot

### Profiles

**Profile**:
A named bundle of coding standards and tooling that a repo or package opts into.
_Avoid_: domain, stack, standards file

**Language Profile**:
A Profile for one programming language: its tooling, lint and format rules, and idioms.
_Avoid_: language standards

**Platform Profile**:
A Profile for one target platform: its stack choices, architecture rules and verification driver.
_Avoid_: domain, target standards

### Skills

**Vendored Skill**:
A third-party skill copied byte-for-byte at a pinned commit and never hand-edited.
_Avoid_: outsourced skill, external skill, upstream skill

**Own Skill**:
A skill authored in the Factory Repo.
_Avoid_: custom skill, local skill

**Skill Patch**:
A last-resort diff against a Vendored Skill, re-applied on every sync and failing loudly on conflict.
_Avoid_: fork, modification, override

**Promotion**:
Turning an agent correction into a lint rule, check, type or API change, and deleting any prose rule it replaces.
_Avoid_: new rule, reminder
