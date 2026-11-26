import { spawn } from "node:child_process"
import { homedir } from "node:os"
import { join } from "node:path"

type ToolInput = { tool: string; input: Record<string, unknown> }
type ToolOutput = { args: Record<string, unknown> }
type GuardResult = { block: boolean; message?: string; warning?: string }
type PluginContext = {
  tool?: {
    hook: (
      name: string,
      callback: (event: { tool: string; input: Record<string, unknown> }) => Promise<void>,
    ) => Promise<unknown>
  }
}

function failedClosed(): Error {
  return new Error("Factory secret guard failed closed")
}

function runGuard(tool: string, input: Record<string, unknown>): Promise<GuardResult> {
  return new Promise((resolve, reject) => {
    const runner = join(process.env.HOME || homedir(), ".agents", "tools", "run")
    const child = spawn(runner, ["secret_guard", "opencode"], { stdio: ["pipe", "pipe", "ignore"] })
    let stdout = ""
    let settled = false
    const fail = () => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      reject(failedClosed())
    }
    const timer = setTimeout(() => child.kill("SIGKILL"), 4000)
    child.stdout.setEncoding("utf8")
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk
    })
    child.once("error", fail)
    child.once("close", (code) => {
      if (settled) return
      if (code !== 0) {
        fail()
        return
      }
      try {
        const result = JSON.parse(stdout) as Partial<GuardResult>
        if (
          typeof result !== "object" ||
          result === null ||
          typeof result.block !== "boolean" ||
          (result.block && typeof result.message !== "string") ||
          (!result.block && result.warning !== undefined && typeof result.warning !== "string")
        ) {
          fail()
          return
        }
        settled = true
        clearTimeout(timer)
        resolve(result as GuardResult)
      } catch {
        fail()
      }
    })
    child.stdin.once("error", fail)
    child.stdin.end(JSON.stringify({ tool, input }))
  })
}

async function beforeTool(input: ToolInput, output: ToolOutput): Promise<void> {
  const result = await runGuard(input.tool, output.args)
  if (result.block) throw new Error(result.message)
  if (result.warning) console.warn(result.warning)
}

export const FactorySecretGuardPlugin = async () => ({
  "tool.execute.before": beforeTool,
})

const plugin = {
  id: "factory-secret-guard",
  server: FactorySecretGuardPlugin,
  setup: async (context: PluginContext) => {
    if (!context.tool?.hook) throw failedClosed()
    await context.tool.hook("execute.before", async (event) => {
      await beforeTool({ tool: event.tool }, { args: event.input })
    })
  },
}

export default plugin
