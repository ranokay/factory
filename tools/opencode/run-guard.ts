import { spawn } from "node:child_process"
import { homedir } from "node:os"
import { join } from "node:path"

type GuardResult = { block: boolean; message?: string; warning?: string }
type ToolInput = { tool: string; sessionID?: string }
type ToolOutput = { args: Record<string, unknown> }
type IdleEvent = {
  type: string
  data?: { sessionID?: string; status?: { type?: string } }
  properties?: { sessionID?: string; status?: { type?: string } }
}
type PluginContext = {
  directory?: string
  worktree?: string
  location?: { directory?: string }
  tool?: {
    hook: (
      name: string,
      callback: (event: { tool: string; input: Record<string, unknown>; sessionID?: string }) => Promise<void>,
    ) => Promise<unknown>
  }
  event?: {
    subscribe: (options: { types?: string[]; signal: AbortSignal }) => AsyncIterable<IdleEvent>
  }
  session?: {
    get: (input: { sessionID: string }) => Promise<unknown>
    prompt: (input: { sessionID: string; text: string }) => Promise<unknown>
  }
  client?: {
    session?: {
      get: (input: { sessionID: string }) => Promise<unknown>
      prompt: (input: { sessionID: string; text: string }) => Promise<unknown>
    }
  }
}

function failedOpen(error: unknown): GuardResult {
  const detail = error instanceof Error ? error.message : String(error)
  const warning = `Factory Run guard warning: ${detail}`
  return { block: false, warning }
}

function runGuard(mode: "opencode" | "opencode-idle", payload: Record<string, unknown>): Promise<GuardResult> {
  return new Promise((resolve) => {
    const runner = join(process.env.HOME || homedir(), ".agents", "tools", "run")
    let encoded: string
    try {
      encoded = JSON.stringify(payload)
    } catch (error) {
      resolve(failedOpen(error))
      return
    }
    let child: ReturnType<typeof spawn>
    try {
      child = spawn(runner, ["run_guard", mode], { detached: true, stdio: ["pipe", "pipe", "ignore"] })
    } catch (error) {
      resolve(failedOpen(error))
      return
    }
    let stdout = ""
    let settled = false
    const killGroup = () => {
      try {
        if (child.pid) process.kill(-child.pid, "SIGKILL")
        else child.kill("SIGKILL")
      } catch {
        try {
          child.kill("SIGKILL")
        } catch {
          return
        }
      }
    }
    const fail = (error: unknown) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      killGroup()
      resolve(failedOpen(error))
    }
    const finish = (code: number | null) => {
      if (settled) return
      if (code !== 0) {
        fail(new Error(`Run guard exited with ${code}`))
        return
      }
      let result: Partial<GuardResult>
      try {
        result = JSON.parse(stdout) as Partial<GuardResult>
      } catch (error) {
        fail(error)
        return
      }
      if (
        typeof result !== "object" ||
        result === null ||
        typeof result.block !== "boolean" ||
        (result.block && typeof result.message !== "string") ||
        (result.warning !== undefined && typeof result.warning !== "string")
      ) {
        fail(new Error("Run guard returned malformed output"))
        return
      }
      settled = true
      clearTimeout(timer)
      resolve(result as GuardResult)
    }
    const timer = setTimeout(() => fail(new Error("Run guard timed out")), 35000)
    child.stdout.setEncoding("utf8")
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk
    })
    child.once("error", fail)
    child.once("close", finish)
    child.stdin.once("error", fail)
    try {
      child.stdin.end(encoded)
    } catch (error) {
      fail(error)
    }
  })
}

function directory(context: PluginContext): string {
  return context.directory || context.worktree || context.location?.directory || process.cwd()
}

async function sessionDirectory(context: PluginContext, sessionID: string): Promise<string | undefined> {
  if (!sessionID || !context.session?.get) return directory(context)
  try {
    const session = (await context.session.get({ sessionID })) as {
      location?: { directory?: string }
    } | null
    const resolved = session?.location?.directory
    if (!resolved) console.warn("Factory Run guard warning: session has no directory")
    return resolved
  } catch (error) {
    console.warn(`Factory Run guard warning: cannot resolve session directory: ${String(error)}`)
    return undefined
  }
}

function hooks(context: PluginContext) {
  const blockedIdles = new Set<string>()
  const activityVersions = new Map<string, number>()
  const beforeTool = async (input: ToolInput, output: ToolOutput) => {
    const sessionID = input.sessionID || ""
    activityVersions.set(sessionID, (activityVersions.get(sessionID) || 0) + 1)
    blockedIdles.delete(sessionID)
    const cwd = await sessionDirectory(context, input.sessionID || "")
    if (!cwd) return
    const result = await runGuard("opencode", {
      tool: input.tool,
      input: output.args,
      cwd,
    })
    if (result.block) throw new Error(result.message)
    if (result.warning) console.warn(result.warning)
  }
  const idle = async (sessionID: string) => {
    const cwd = await sessionDirectory(context, sessionID)
    if (!cwd) return
    const activityVersion = activityVersions.get(sessionID) || 0
    const repeat = blockedIdles.has(sessionID)
    const result = await runGuard("opencode-idle", { cwd, repeat })
    if ((activityVersions.get(sessionID) || 0) !== activityVersion) return
    if (!result.block) {
      blockedIdles.delete(sessionID)
      if (result.warning) console.warn(result.warning)
      return
    }
    blockedIdles.add(sessionID)
    if (!context.session?.prompt) {
      blockedIdles.delete(sessionID)
      console.warn("Factory Run guard warning: session.prompt is unavailable")
      return
    }
    try {
      await context.session.prompt({ sessionID, text: result.message || "Continue waiting for required checks." })
    } catch (error) {
      if ((activityVersions.get(sessionID) || 0) === activityVersion) blockedIdles.delete(sessionID)
      console.warn(`Factory Run guard warning: cannot re-prompt session: ${String(error)}`)
    }
  }
  return {
    "tool.execute.before": beforeTool,
    sessionIdle: idle,
    event: async ({ event }: { event: IdleEvent }) => {
      if (event.type === "session.idle" && event.properties?.sessionID) {
        await idle(event.properties.sessionID)
      }
    },
  }
}

export const FactoryRunGuardPlugin = async (context: PluginContext = {}) =>
  hooks({ ...context, session: context.session || context.client?.session })

const plugin = {
  id: "factory-run-guard",
  server: FactoryRunGuardPlugin,
  setup: async (context: PluginContext) => {
    const registered = hooks(context)
    if (context.tool?.hook) {
      await context.tool.hook("execute.before", async (event) => {
        await registered["tool.execute.before"]({ tool: event.tool, sessionID: event.sessionID }, { args: event.input })
      })
    } else {
      console.warn("Factory Run guard warning: tool.execute.before is unavailable")
    }
    if (!context.event?.subscribe) {
      console.warn("Factory Run guard warning: session.status subscription is unavailable")
      return
    }
    const controller = new AbortController()
    void (async () => {
      try {
        for await (const event of context.event!.subscribe({ types: ["session.status"], signal: controller.signal })) {
          if (event.type === "session.status" && event.data?.status?.type === "idle" && event.data.sessionID) {
            await registered.sessionIdle(event.data.sessionID)
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) console.warn(`Factory Run guard warning: idle subscription failed: ${String(error)}`)
      }
    })()
    return () => controller.abort()
  },
}

export default plugin
