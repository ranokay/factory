import { spawn } from "node:child_process"
import { homedir } from "node:os"
import { join } from "node:path"

type ToolInput = { tool: string; sessionID?: string }
type ToolOutput = { args: Record<string, unknown> }
type GuardResult = { block: boolean; message?: string; warning?: string }
type ToastOptions = { title: string; message: string; variant: "error"; duration: number }
type PluginContext = {
  tool?: {
    hook: (
      name: string,
      callback: (event: { tool: string; input: Record<string, unknown>; sessionID?: string }) => Promise<void>,
    ) => Promise<unknown>
  }
  ui?: {
    toast?: {
      show: (options: ToastOptions) => unknown
    }
  }
  client?: {
    tui?: {
      showToast: (options: { body: { message: string; variant: "error" } }) => Promise<unknown>
    }
  }
  session?: {
    synthetic: (input: { sessionID: string; text: string; description: string }) => Promise<unknown>
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
    const finish = (code: number | null) => {
      if (settled) return
      if (code !== 0) {
        fail()
        return
      }
      let result: Partial<GuardResult>
      try {
        result = JSON.parse(stdout) as Partial<GuardResult>
      } catch {
        fail()
        return
      }
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
    }
    const timer = setTimeout(() => {
      child.kill("SIGKILL")
      fail()
    }, 4000)
    child.stdout.setEncoding("utf8")
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk
    })
    child.once("error", fail)
    child.once("close", finish)
    child.stdin.once("error", fail)
    child.stdin.end(JSON.stringify({ tool, input }))
  })
}

async function showWarning(context: PluginContext, warning: string, sessionID?: string): Promise<void> {
  if (context.ui?.toast?.show) {
    context.ui.toast.show({
      title: "Factory secret guard warning",
      message: warning,
      variant: "error",
      duration: 10000,
    })
    return
  }
  if (context.client?.tui?.showToast) {
    await context.client.tui.showToast({ body: { message: warning, variant: "error" } })
    return
  }
  if (context.session?.synthetic && sessionID) {
    await context.session.synthetic({
      sessionID,
      text: warning,
      description: "Factory secret guard warning",
    })
    return
  }
  console.warn(warning)
}

async function beforeTool(input: ToolInput, output: ToolOutput, context: PluginContext = {}): Promise<void> {
  const result = await runGuard(input.tool, output.args)
  if (result.block) throw new Error(result.message)
  if (result.warning) await showWarning(context, result.warning, input.sessionID)
}

export const FactorySecretGuardPlugin = async (context: PluginContext = {}) => ({
  "tool.execute.before": (input: ToolInput, output: ToolOutput) => beforeTool(input, output, context),
})

const plugin = {
  id: "factory-secret-guard",
  server: FactorySecretGuardPlugin,
  setup: async (context: PluginContext) => {
    if (!context.tool?.hook) throw failedClosed()
    await context.tool.hook("execute.before", async (event) => {
      await beforeTool({ tool: event.tool, sessionID: event.sessionID }, { args: event.input }, context)
    })
  },
}

export default plugin
