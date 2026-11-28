import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { test } from "bun:test"
import runGuardPlugin, { FactoryRunGuardPlugin } from "./run-guard.ts"

type ToolOutput = { args: Record<string, unknown> }
type EventHook = (input: { event: { type: string; properties?: { sessionID?: string } } }) => Promise<void>
const originalRunGuardOutput = process.env.RUN_GUARD_OUTPUT

async function withGuard(
  script: string | null,
  run: (hooks: { "tool.execute.before": (input: { tool: string; sessionID?: string }, output: ToolOutput) => Promise<void>; event: EventHook }, home: string) => Promise<void>,
): Promise<void> {
  const home = mkdtempSync(join(tmpdir(), "factory-run-guard-"))
  const previousHome = process.env.HOME
  process.env.HOME = home
  if (process.env.RUN_GUARD_OUTPUT === undefined) process.env.RUN_GUARD_OUTPUT = '{"block":false}'
  try {
    if (script !== null) {
      const runner = join(home, ".agents", "tools", "run")
      mkdirSync(join(home, ".agents", "tools"), { recursive: true })
      writeFileSync(
        runner,
        `#!/bin/sh\nset -eu\ncase "$1 $2" in\n  'run_guard opencode'|'run_guard opencode-idle') ;;\n  *) exit 9 ;;\nesac\ncat >"$HOME/guard-input.json"\n${script}\n`,
      )
      chmodSync(runner, 0o755)
    }
    const hooks = await FactoryRunGuardPlugin({ directory: "/repo" })
    await run(hooks, home)
  } finally {
    if (previousHome === undefined) delete process.env.HOME
    else process.env.HOME = previousHome
    if (originalRunGuardOutput === undefined) delete process.env.RUN_GUARD_OUTPUT
    else process.env.RUN_GUARD_OUTPUT = originalRunGuardOutput
    rmSync(home, { recursive: true, force: true })
  }
}

const FACTORY = resolve(import.meta.dir, "../..")
const python = spawnSync("mise", ["which", "python"], { encoding: "utf8" })
assert.equal(python.status, 0, python.stderr)
const PYTHON = python.stdout.trim()

function fixtureGit(cwd: string, ...args: string[]): void {
  const result = spawnSync("git", ["-C", cwd, ...args], { encoding: "utf8" })
  assert.equal(result.status, 0, result.stderr)
}

async function withRealGuard(run: (fixture: { home: string; repo: string; bin: string }) => Promise<void>): Promise<void> {
  const home = mkdtempSync(join(tmpdir(), "factory-run-guard-real-"))
  const repo = join(home, "repo")
  const bin = join(home, "bin")
  mkdirSync(repo)
  mkdirSync(bin)
  mkdirSync(join(home, ".agents", "tools"), { recursive: true })
  const runner = join(home, ".agents", "tools", "run")
  writeFileSync(runner, `#!/bin/sh\n[ "$1" = run_guard ] || exit 9\nshift\nexec ${JSON.stringify(PYTHON)} ${JSON.stringify(join(FACTORY, "tools", "run_guard.py"))} "$@"\n`)
  chmodSync(runner, 0o755)
  fixtureGit(repo, "init", "-q")
  fixtureGit(repo, "config", "user.name", "Run guard test")
  fixtureGit(repo, "config", "user.email", "run-guard@example.invalid")
  fixtureGit(repo, "config", "commit.gpgsign", "false")
  writeFileSync(join(repo, "README"), "fixture\n")
  fixtureGit(repo, "add", "README")
  fixtureGit(repo, "commit", "-qm", "baseline")
  const previousHome = process.env.HOME
  const previousPath = process.env.PATH
  const previousCache = process.env.XDG_CACHE_HOME
  const ghVariables = ["GH_HEAD", "GH_TOPICS", "GH_CHECKS"]
  const previousGh = Object.fromEntries(ghVariables.map((name) => [name, process.env[name]]))
  process.env.HOME = home
  process.env.PATH = bin + (previousPath ? ":" + previousPath : "")
  process.env.XDG_CACHE_HOME = join(home, "cache")
  try {
    await run({ home, repo, bin })
  } finally {
    if (previousHome === undefined) delete process.env.HOME
    else process.env.HOME = previousHome
    if (previousPath === undefined) delete process.env.PATH
    else process.env.PATH = previousPath
    if (previousCache === undefined) delete process.env.XDG_CACHE_HOME
    else process.env.XDG_CACHE_HOME = previousCache
    for (const name of ghVariables) {
      if (previousGh[name] === undefined) delete process.env[name]
      else process.env[name] = previousGh[name]
    }
    rmSync(home, { recursive: true, force: true })
  }
}

function fixtureGh(bin: string): void {
  const script = join(bin, "gh")
  writeFileSync(
    script,
    "#!/bin/sh\ncase \"$1 $2\" in\n"
      + "  'pr view') printf '{\"number\":42,\"headRefName\":\"%s\",\"url\":\"https://github.com/o/one/pull/42\"}\\n' \"$GH_HEAD\" ;;\n"
      + "  'pr checks') printf '%s\\n' \"$GH_CHECKS\"; exit 8 ;;\n"
      + "  'repo view') printf '{\"repositoryTopics\":[\"%s\"]}\\n' \"$GH_TOPICS\" ;;\n"
      + "  *) exit 1 ;;\nesac\n",
  )
  chmodSync(script, 0o755)
}

test("passes shell input and the session directory to the shared Run guard", async () => {
  await withGuard("printf '%s\\n' '{\"block\":false}'\n", async (hooks, home) => {
    await hooks["tool.execute.before"]({ tool: "bash", sessionID: "ses_fixture" }, { args: { command: "git push" } })
    assert.deepEqual(JSON.parse(readFileSync(join(home, "guard-input.json"), "utf8")), {
      tool: "bash",
      input: { command: "git push" },
      cwd: "/repo",
    })
  })
})

test("throws each rule's actionable message before the tool runs", async () => {
  for (const [command, message] of [
    ["git push", "Run mise run check, then push"],
    ["gh pr merge 42 --admin", "--admin is reserved for Sync PRs"],
    [
      "skills/supervise/scripts/block-issue o/one 6 --reason 'opencode-go/hungerg-free unavailable quota' --unblock 'Launch fallback'",
      "Launch opencode-go/hy4-flash-free",
    ],
  ] as const) {
    process.env.RUN_GUARD_OUTPUT = JSON.stringify({ block: true, message })
    await withGuard(`printf '%s\\n' "$RUN_GUARD_OUTPUT"\n`, async (hooks, home) => {
      await assert.rejects(() => hooks["tool.execute.before"]({ tool: "bash" }, { args: { command } }), { message })
      assert.equal(JSON.parse(readFileSync(join(home, "guard-input.json"), "utf8")).input.command, command)
    })
  }
})

test("re-prompts an idle session when required checks are pending", async () => {
  process.env.RUN_GUARD_OUTPUT = JSON.stringify({ block: true, message: "gh run watch 11 --exit-status" })
  await withGuard(`printf '%s\\n' "$RUN_GUARD_OUTPUT"\n`, async (hooks, home) => {
    let prompt: Record<string, unknown> | undefined
    const plugin = await FactoryRunGuardPlugin({
      directory: "/repo",
      session: {
        get: async () => ({ location: { directory: "/repo" } }),
        prompt: async (input) => {
          prompt = input
          return {}
        },
      },
    })
    await plugin.event({ event: { type: "session.idle", properties: { sessionID: "ses_fixture" } } })
    assert.deepEqual(prompt, { sessionID: "ses_fixture", text: "gh run watch 11 --exit-status" })
    assert.equal(existsSync(join(home, "guard-input.json")), true)
  })
})

test("passes repeat only for a consecutive blocked idle", async () => {
  await withGuard(
    "count=0\n[ ! -f \"$HOME/idle-count\" ] || count=$(cat \"$HOME/idle-count\")\ncount=$((count + 1))\nprintf '%s' \"$count\" >\"$HOME/idle-count\"\nif [ \"$count\" -eq 1 ]; then printf '%s\\n' '{\"block\":true,\"message\":\"diagnostic\"}'; else printf '%s\\n' '{\"block\":false,\"warning\":\"still unavailable\"}'; fi\n",
    async (hooks, home) => {
      let prompts = 0
      const plugin = await FactoryRunGuardPlugin({
        directory: "/repo",
        session: {
          get: async () => ({ location: { directory: "/repo" } }),
          prompt: async () => {
            prompts += 1
            return {}
          },
        },
      })
      const event = { event: { type: "session.idle", properties: { sessionID: "ses_fixture" } } }
      await plugin.event(event)
      assert.equal(JSON.parse(readFileSync(join(home, "guard-input.json"), "utf8")).repeat, false)
      await plugin.event(event)
      assert.equal(JSON.parse(readFileSync(join(home, "guard-input.json"), "utf8")).repeat, true)
      assert.equal(prompts, 1)
    },
  )
})

test("tool activity resets consecutive idle tracking", async () => {
  await withGuard(
    "if [ \"$2\" = opencode ]; then printf '%s\\n' '{\"block\":false}'; exit 0; fi\ncount=0\n[ ! -f \"$HOME/idle-count\" ] || count=$(cat \"$HOME/idle-count\")\ncount=$((count + 1))\nprintf '%s' \"$count\" >\"$HOME/idle-count\"\nif [ \"$count\" -eq 1 ]; then printf '%s\\n' '{\"block\":true,\"message\":\"diagnostic\"}'; else printf '%s\\n' '{\"block\":false,\"warning\":\"unavailable\"}'; fi\n",
    async (hooks, home) => {
      let prompts = 0
      const plugin = await FactoryRunGuardPlugin({
        directory: "/repo",
        session: {
          get: async () => ({ location: { directory: "/repo" } }),
          prompt: async () => {
            prompts += 1
            return {}
          },
        },
      })
      const event = { event: { type: "session.idle", properties: { sessionID: "ses_fixture" } } }
      await plugin.event(event)
      await plugin["tool.execute.before"]({ tool: "bash", sessionID: "ses_fixture" }, { args: { command: "gh run watch 11 --exit-status" } })
      await plugin.event(event)
      assert.equal(JSON.parse(readFileSync(join(home, "guard-input.json"), "utf8")).repeat, false)
      assert.equal(prompts, 1)
    },
  )
})

test("Run-rule process crashes warn and fail open", async () => {
  await withGuard("exit 2\n", async (hooks) => {
    const warnings: string[] = []
    const originalWarn = console.warn
    console.warn = (message) => warnings.push(String(message))
    try {
      await hooks["tool.execute.before"]({ tool: "bash" }, { args: { command: "git push" } })
      assert.equal(warnings.length, 1)
      assert.match(warnings[0], /Factory Run guard warning/)
    } finally {
      console.warn = originalWarn
    }
  })
})

test("session lookup failure warns without inspecting another checkout", async () => {
  await withGuard("printf '%s\\n' '{\"block\":false}'\n", async (hooks, home) => {
    const warnings: string[] = []
    const originalWarn = console.warn
    console.warn = (message) => warnings.push(String(message))
    try {
      const plugin = await FactoryRunGuardPlugin({
        directory: "/wrong-repo",
        session: {
          get: async () => {
            throw new Error("session unavailable")
          },
          prompt: async () => ({}),
        },
      })
      await plugin["tool.execute.before"]({ tool: "bash", sessionID: "ses_fixture" }, { args: { command: "git push" } })
      assert.equal(warnings.length, 1)
      assert.equal(existsSync(join(home, "guard-input.json")), false)
    } finally {
      console.warn = originalWarn
    }
  })
})

test("malformed Run-rule output warns and fails open", async () => {
  await withGuard("printf 'not-json\\n'\n", async (hooks) => {
    const warnings: string[] = []
    const originalWarn = console.warn
    console.warn = (message) => warnings.push(String(message))
    try {
      await hooks["tool.execute.before"]({ tool: "bash" }, { args: { command: "git push" } })
      assert.equal(warnings.length, 1)
    } finally {
      console.warn = originalWarn
    }
  })
})

test("non-serializable tool input warns and fails open", async () => {
  await withGuard("printf '%s\\n' '{\"block\":false}'\n", async (hooks) => {
    const circular: Record<string, unknown> = {}
    circular.self = circular
    const warnings: string[] = []
    const originalWarn = console.warn
    console.warn = (message) => warnings.push(String(message))
    try {
      await hooks["tool.execute.before"]({ tool: "write" }, { args: circular })
      assert.equal(warnings.length, 1)
    } finally {
      console.warn = originalWarn
    }
  })
})

test("V2 setup registers the tool hook and consumes idle session.status", async () => {
  process.env.RUN_GUARD_OUTPUT = JSON.stringify({ block: true, message: "gh run watch 11 --exit-status" })
  await withGuard(`printf '%s\\n' "$RUN_GUARD_OUTPUT"\n`, async () => {
    let registered: ((event: { tool: string; input: Record<string, unknown>; sessionID?: string }) => Promise<void>) | undefined
    let resolvePrompt: (() => void) | undefined
    const prompted = new Promise<void>((resolve) => {
      resolvePrompt = resolve
    })
    let subscription: AsyncIterable<{ type: string; data: { sessionID: string; status: { type: string } } }> | undefined
    let subscriptionTypes: string[] | undefined
    const cleanup = await runGuardPlugin.setup({
      directory: "/repo",
      tool: {
        hook: async (name, callback) => {
          assert.equal(name, "execute.before")
          registered = callback
        },
      },
      event: {
        subscribe: (options: { types?: string[] }) => {
          subscriptionTypes = options.types
          subscription = {
            async *[Symbol.asyncIterator]() {
              yield { type: "session.status", data: { sessionID: "ses_fixture", status: { type: "idle" } } }
            },
          }
          return subscription
        },
      },
      session: {
        get: async () => ({ location: { directory: "/repo" } }),
        prompt: async () => {
          resolvePrompt?.()
          return {}
        },
      },
    })
    assert.ok(registered)
    assert.ok(subscription)
    assert.deepEqual(subscriptionTypes, ["session.status"])
    await prompted
    if (typeof cleanup === "function") cleanup()
  })
})

test("V2 busy session.status does not re-prompt", async () => {
  await withGuard("printf '%s\\n' '{\"block\":false}'\n", async (_hooks, home) => {
    let resolveConsumed: (() => void) | undefined
    const consumed = new Promise<void>((resolve) => {
      resolveConsumed = resolve
    })
    let prompts = 0
    await runGuardPlugin.setup({
      directory: "/repo",
      tool: { hook: async () => ({}) },
      event: {
        subscribe: () => ({
          async *[Symbol.asyncIterator]() {
            try {
              yield { type: "session.status", data: { sessionID: "ses_fixture", status: { type: "busy" } } }
            } finally {
              resolveConsumed?.()
            }
          },
        }),
      },
      session: {
        get: async () => ({ location: { directory: "/repo" } }),
        prompt: async () => {
          prompts += 1
          return {}
        },
      },
    })
    await consumed
    assert.equal(prompts, 0)
    assert.equal(existsSync(join(home, "guard-input.json")), false)
  })
})

test("real shared guard blocks a git -C push without a check record", async () => {
  await withRealGuard(async ({ repo }) => {
    writeFileSync(join(repo, "mise.toml"), '[tasks.check]\nrun = "~/.agents/tools/run check_result -- true"\n')
    fixtureGit(repo, "add", "mise.toml")
    fixtureGit(repo, "commit", "-qm", "check task")
    const plugin = await FactoryRunGuardPlugin({ directory: repo })
    await assert.rejects(
      () => plugin["tool.execute.before"]({ tool: "bash" }, { args: { command: `git -C ${repo} push` } }),
      /mise run check/,
    )
  })
})

test("real shared guard blocks an ineligible admin merge", async () => {
  await withRealGuard(async ({ repo, bin }) => {
    fixtureGh(bin)
    process.env.GH_HEAD = "feature/example"
    process.env.GH_TOPICS = "factory-personal-fork"
    const plugin = await FactoryRunGuardPlugin({ directory: repo })
    await assert.rejects(
      () => plugin["tool.execute.before"]({ tool: "bash" }, { args: { command: "gh pr merge 42 --admin" } }),
      /without --admin/,
    )
  })
})

test("real shared guard blocks a model-only Blocker with an untried fallback", async () => {
  await withRealGuard(async ({ home, repo }) => {
    const path = join(home, "cache", "factory", "runs", "o", "one", "6", "targets.json")
    mkdirSync(dirname(path), { recursive: true })
    writeFileSync(path, JSON.stringify({ targets: [
      { providerInstanceId: "opencode", model: "opencode-go/hungerg-free", options: {}, tried: true },
      { providerInstanceId: "opencode", model: "opencode-go/hy4-flash-free", options: { variant: "high" }, tried: false },
    ] }))
    const plugin = await FactoryRunGuardPlugin({ directory: repo })
    const command = "skills/supervise/scripts/block-issue o/one 6 --reason 'opencode-go/hungerg-free unavailable quota' --unblock 'Launch fallback'"
    await assert.rejects(
      () => plugin["tool.execute.before"]({ tool: "bash" }, { args: { command } }),
      /opencode-go\/hy4-flash-free/,
    )
  })
})

test("real shared guard re-prompts pending required checks from session.idle", async () => {
  await withRealGuard(async ({ repo, bin }) => {
    fixtureGh(bin)
    process.env.GH_HEAD = "sync/upstream-2026-11-28"
    process.env.GH_TOPICS = "factory-personal-fork"
    process.env.GH_CHECKS = JSON.stringify([
      { name: "Lint", state: "PENDING", bucket: "pending", link: "https://github.com/o/one/actions/runs/11" },
    ])
    let prompt: { sessionID: string; text: string } | undefined
    const plugin = await FactoryRunGuardPlugin({
      directory: repo,
      session: {
        get: async () => ({ location: { directory: repo } }),
        prompt: async (input) => {
          prompt = input
          return {}
        },
      },
    })
    await plugin.event({ event: { type: "session.idle", properties: { sessionID: "ses_fixture" } } })
    assert.match(prompt?.text || "", /gh run watch 11 --exit-status/)
  })
})
