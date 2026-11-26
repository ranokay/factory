import assert from "node:assert/strict"
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"
import secretGuardPlugin, { FactorySecretGuardPlugin } from "./secret-guard.ts"

type ToolOutput = { args: Record<string, unknown> }

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
    let registered: ((event: { tool: string; input: Record<string, unknown> }) => Promise<void>) | undefined
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
  await withGuard("printf '%s\\n' 'not-json'\n", async (hook) => {
    await assert.rejects(() => hook({ tool: "read" }, { args: { path: "file.txt" } }), /failed closed/)
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
