import assert from "node:assert/strict"
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "bun:test"
import runGuardPlugin, { FactoryRunGuardPlugin } from "./run-guard.ts"

type ToolOutput = { args: Record<string, unknown> }
type EventHook = (input: { event: { type: string; data: { sessionID: string } } }) => Promise<void>

async function withGuard(
  script: string | null,
  run: (hooks: { "tool.execute.before": (input: { tool: string; sessionID?: string }, output: ToolOutput) => Promise<void>; event: EventHook }, home: string) => Promise<void>,
): Promise<void> {
  const home = mkdtempSync(join(tmpdir(), "factory-run-guard-"))
  const previousHome = process.env.HOME
  const previousOutput = process.env.RUN_GUARD_OUTPUT
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
    if (previousOutput === undefined) delete process.env.RUN_GUARD_OUTPUT
    else process.env.RUN_GUARD_OUTPUT = previousOutput
    rmSync(home, { recursive: true, force: true })
  }
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
  for (const message of [
    "Run mise run check, then push",
    "--admin is reserved for Sync PRs",
    "Launch opencode-go/hy4-flash-free",
  ]) {
    process.env.RUN_GUARD_OUTPUT = JSON.stringify({ block: true, message })
    await withGuard(`printf '%s\\n' "$RUN_GUARD_OUTPUT"\n`, async (hooks) => {
      await assert.rejects(() => hooks["tool.execute.before"]({ tool: "bash" }, { args: { command: "fixture" } }), {
        message,
      })
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
        get: async () => ({ directory: "/repo" }),
        prompt: async (input) => {
          prompt = input
          return {}
        },
      },
    })
    await plugin.event({ event: { type: "session.idle", data: { sessionID: "ses_fixture" } } })
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
          get: async () => ({ directory: "/repo" }),
          prompt: async () => {
            prompts += 1
            return {}
          },
        },
      })
      const event = { event: { type: "session.idle", data: { sessionID: "ses_fixture" } } }
      await plugin.event(event)
      assert.equal(JSON.parse(readFileSync(join(home, "guard-input.json"), "utf8")).repeat, false)
      await plugin.event(event)
      assert.equal(JSON.parse(readFileSync(join(home, "guard-input.json"), "utf8")).repeat, true)
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

test("V2 setup registers the tool hook and consumes session.idle", async () => {
  process.env.RUN_GUARD_OUTPUT = JSON.stringify({ block: true, message: "gh run watch 11 --exit-status" })
  await withGuard(`printf '%s\\n' "$RUN_GUARD_OUTPUT"\n`, async () => {
    let registered: ((event: { tool: string; input: Record<string, unknown>; sessionID?: string }) => Promise<void>) | undefined
    let resolvePrompt: (() => void) | undefined
    const prompted = new Promise<void>((resolve) => {
      resolvePrompt = resolve
    })
    let subscription: AsyncIterable<{ type: string; data: { sessionID: string } }> | undefined
    const cleanup = await runGuardPlugin.setup({
      directory: "/repo",
      tool: {
        hook: async (name, callback) => {
          assert.equal(name, "execute.before")
          registered = callback
        },
      },
      event: {
        subscribe: () => {
          subscription = {
            async *[Symbol.asyncIterator]() {
              yield { type: "session.idle", data: { sessionID: "ses_fixture" } }
            },
          }
          return subscription
        },
      },
      session: {
        get: async () => ({ directory: "/repo" }),
        prompt: async () => {
          resolvePrompt?.()
          return {}
        },
      },
    })
    assert.ok(registered)
    assert.ok(subscription)
    await prompted
    if (typeof cleanup === "function") cleanup()
  })
})
