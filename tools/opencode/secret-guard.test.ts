import assert from "node:assert/strict"
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "bun:test"
import secretGuardPlugin, { FactorySecretGuardPlugin } from "./secret-guard.ts"

type ToolOutput = { args: Record<string, unknown> }

function stopGrandchild(home: string): void {
  const pidFile = join(home, "guard-grandchild.pid")
  if (!existsSync(pidFile)) return
  const pid = Number(readFileSync(pidFile, "utf8"))
  if (!Number.isInteger(pid) || pid <= 0) return
  try {
    process.kill(pid, "SIGKILL")
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error
  }
}

async function withGuard(
  script: string | null,
  run: (hook: (input: { tool: string }, output: ToolOutput) => Promise<void>, home: string) => Promise<void>,
): Promise<void> {
  const home = mkdtempSync(join(tmpdir(), "factory-secret-guard-"))
  const previousHome = process.env.HOME
  const previousCapture = process.env.GUARD_CAPTURE
  process.env.HOME = home
  process.env.GUARD_CAPTURE = join(home, "guard-input.json")
  try {
    if (script !== null) {
      const runner = join(home, ".agents", "tools", "run")
      mkdirSync(join(home, ".agents", "tools"), { recursive: true })
      writeFileSync(runner, `#!/bin/sh\nset -eu\n[ "$1 $2" = "secret_guard opencode" ] || exit 9\ncat >"$GUARD_CAPTURE"\n${script}\n`)
      chmodSync(runner, 0o755)
    }
    const plugin = await FactorySecretGuardPlugin()
    await run(plugin["tool.execute.before"], home)
  } finally {
    stopGrandchild(home)
    if (previousHome === undefined) delete process.env.HOME
    else process.env.HOME = previousHome
    if (previousCapture === undefined) delete process.env.GUARD_CAPTURE
    else process.env.GUARD_CAPTURE = previousCapture
    rmSync(home, { recursive: true, force: true })
  }
}

test("passes every tool's complete input to the shared guard", async () => {
  await withGuard("printf '%s\\n' '{\"block\":false}'\n", async (hook, home) => {
    const args = { path: "notes.txt", content: "ordinary text" }
    await hook({ tool: "write" }, { args })
    assert.deepEqual(JSON.parse(readFileSync(join(home, "guard-input.json"), "utf8")), {
      tool: "write",
      input: args,
    })
  })
})

test("throws the guard's rule-only message to block a tool", async () => {
  await withGuard("printf '%s\\n' '{\"block\":true,\"message\":\"Factory secret guard: fixture-rule\"}'\n", async (hook) => {
    await assert.rejects(() => hook({ tool: "bash" }, { args: { command: "fixture" } }), {
      message: "Factory secret guard: fixture-rule",
    })
  })
})

test("registers the same blocking hook with the OpenCode 2 plugin API", async () => {
  await withGuard("printf '%s\\n' '{\"block\":true,\"message\":\"Factory secret guard: fixture-rule\"}'\n", async () => {
    let registered: ((event: { tool: string; input: Record<string, unknown>; sessionID?: string }) => Promise<void>) | undefined
    await secretGuardPlugin.setup({
      tool: {
        hook: async (name, callback) => {
          assert.equal(name, "execute.before")
          registered = callback
        },
      },
    })
    assert.ok(registered)
    await assert.rejects(() => registered!({ tool: "write", input: { path: "file.txt" } }), {
      message: "Factory secret guard: fixture-rule",
    })
  })
})

test("fails closed when the guard process exits with an error", async () => {
  await withGuard("exit 2\n", async (hook) => {
    await assert.rejects(() => hook({ tool: "read" }, { args: { path: "file.txt" } }), /failed closed/)
  })
})

test("fails closed when the guard output is malformed", async () => {
  for (const output of ["not-json", '{"block":false,"warning":42}', '{"block":true,"message":"blocked","warning":42}']) {
    await withGuard(`printf '%s\\n' '${output}'\n`, async (hook) => {
      await assert.rejects(() => hook({ tool: "read" }, { args: { path: "file.txt" } }), /failed closed/)
    })
  }
})

test("fails closed when tool input is not JSON serializable", async () => {
  await withGuard(null, async (hook) => {
    await assert.rejects(() => hook({ tool: "write" }, { args: { value: 1n } }), /failed closed/)
    const circular: Record<string, unknown> = {}
    circular.self = circular
    await assert.rejects(() => hook({ tool: "write" }, { args: circular }), /failed closed/)
  })
})

test("fails closed when a descendant keeps stdout open after valid output", async () => {
  await withGuard("printf '%s\\n' '{\"block\":false}'\n( sleep 16; : >\"$HOME/grandchild-ran\" ) &\nprintf '%s\\n' \"$!\" > \"$HOME/guard-grandchild.pid\"\nwait\n", async (hook, home) => {
    let timer: ReturnType<typeof setTimeout> | undefined
    try {
      const outcome = await Promise.race([
        hook({ tool: "read" }, { args: { path: "file.txt" } }).then(
          () => "resolved",
          () => "rejected",
        ),
        new Promise<string>((resolve) => {
          timer = setTimeout(() => resolve("pending"), 16000)
        }),
      ])
      assert.equal(outcome, "rejected")
      await new Promise((resolve) => setTimeout(resolve, 1500))
      assert.equal(existsSync(join(home, "grandchild-ran")), false)
    } finally {
      if (timer) clearTimeout(timer)
      stopGrandchild(home)
    }
  })
}, 25000)

test("waits for stdout chunks that arrive after child exit", async () => {
  await withGuard(
    "printf '{\"block\":false,'\n( sleep 0.1; printf '\"warning\":\"delayed\"}\\n' ) &\nexit 0\n",
    async () => {
      let toast: Record<string, unknown> | undefined
      const plugin = await FactorySecretGuardPlugin({
        ui: {
          toast: {
            show: (options) => {
              toast = options
            },
          },
        },
      })
      await plugin["tool.execute.before"]({ tool: "read" }, { args: { path: "file.txt" } })
      assert.equal(toast?.message, "delayed")
    },
  )
})

test("rejects trailing data after a parseable JSON prefix", async () => {
  await withGuard(
    "printf '{\"block\":false}\\n'\n( sleep 0.05; printf '{\"block\":true}\\n' ) &\nexit 0\n",
    async (hook) => {
      await assert.rejects(() => hook({ tool: "read" }, { args: { path: "file.txt" } }), /failed closed/)
    },
  )
})

test("shows fallback warnings through the OpenCode 2 toast API", async () => {
  await withGuard("printf '%s\\n' '{\"block\":false,\"warning\":\"fixture warning\"}'\n", async () => {
    let registered: ((event: { tool: string; input: Record<string, unknown>; sessionID?: string }) => Promise<void>) | undefined
    let toast: Record<string, unknown> | undefined
    await secretGuardPlugin.setup({
      tool: {
        hook: async (name, callback) => {
          assert.equal(name, "execute.before")
          registered = callback
        },
      },
      ui: {
        toast: {
          show: (options) => {
            toast = options
          },
        },
      },
    })
    assert.ok(registered)
    await registered!({ tool: "read", input: { path: "ordinary.txt" } })
    assert.deepEqual(toast, {
      title: "Factory secret guard warning",
      message: "fixture warning",
      variant: "error",
      duration: 10000,
    })
  })
})

test("surfaces server fallback warnings as session notifications", async () => {
  await withGuard("printf '%s\\n' '{\"block\":false,\"warning\":\"fixture warning\"}'\n", async () => {
    let registered: ((event: { tool: string; input: Record<string, unknown>; sessionID?: string }) => Promise<void>) | undefined
    let notification: Record<string, unknown> | undefined
    await secretGuardPlugin.setup({
      tool: {
        hook: async (_name, callback) => {
          registered = callback
        },
      },
      session: {
        synthetic: async (input) => {
          notification = input
        },
      },
    })
    assert.ok(registered)
    await registered!({ tool: "read", input: { path: "ordinary.txt" }, sessionID: "ses_fixture" })
    assert.deepEqual(notification, {
      sessionID: "ses_fixture",
      text: "fixture warning",
      description: "Factory secret guard warning",
    })
  })
})

test("shows fallback warnings through the OpenCode 1 toast API", async () => {
  await withGuard("printf '%s\\n' '{\"block\":false,\"warning\":\"fixture warning\"}'\n", async () => {
    let request: Record<string, unknown> | undefined
    const plugin = await FactorySecretGuardPlugin({
      client: {
        tui: {
          showToast: async (options) => {
            request = options
            return true
          },
        },
      },
    })
    await plugin["tool.execute.before"]({ tool: "read" }, { args: { path: "ordinary.txt" } })
    assert.deepEqual(request, { body: { message: "fixture warning", variant: "error" } })
  })
})

test("keeps a passed tool allowed when warning delivery fails", async () => {
  await withGuard("printf '%s\\n' '{\"block\":false,\"warning\":\"fixture warning\"}'\n", async () => {
    const warnings: string[] = []
    const originalWarn = console.warn
    console.warn = (message) => warnings.push(String(message))
    try {
      const plugin = await FactorySecretGuardPlugin({
        client: {
          tui: {
            showToast: async () => {
              throw new Error("toast unavailable")
            },
          },
        },
      })
      await plugin["tool.execute.before"]({ tool: "read" }, { args: { path: "ordinary.txt" } })
      assert.deepEqual(warnings, ["fixture warning"])
    } finally {
      console.warn = originalWarn
    }
  })
})

test("fails closed when the OpenCode 2 hook API is unavailable", async () => {
  await assert.rejects(() => secretGuardPlugin.setup({}), /failed closed/)
})

test("fails closed when the guard runner is missing", async () => {
  await withGuard(null, async (hook) => {
    await assert.rejects(() => hook({ tool: "read" }, { args: { path: "file.txt" } }), /failed closed/)
  })
})

test("kills the guard process group when the runner closes stdin early", async () => {
  await withGuard(null, async (hook, home) => {
    const runner = join(home, ".agents", "tools", "run")
    mkdirSync(join(home, ".agents", "tools"), { recursive: true })
    writeFileSync(
      runner,
      `#!/bin/sh\nexec 0<&-\n( sleep 2; : >"$HOME/grandchild-ran" ) &\nprintf '%s\\n' "$!" >"$HOME/guard-grandchild.pid"\nsleep 30\n`,
    )
    chmodSync(runner, 0o755)
    const started = Date.now()
    await assert.rejects(() => hook({ tool: "write" }, { args: { content: "x".repeat(1 << 20) } }), /failed closed/)
    assert.ok(Date.now() - started < 10000)
    await new Promise((resolve) => setTimeout(resolve, 2500))
    assert.equal(existsSync(join(home, "grandchild-ran")), false)
  })
}, 20000)
