/**
 * Arbiter for Pi (https://pi.dev): forwards Pi's lifecycle events to the local Arbiter daemon.
 *
 * Pi has no hook config, so this extension plays the part of Claude Code's `http` hooks: it posts
 * Claude-Code-shaped hook payloads to the daemon's loopback endpoint (POST /hook/pi/<Event>, header
 * X-Arbiter-Token) and applies the answer:
 *   before_agent_start  -> UserPromptSubmit  additionalContext becomes a hidden context message
 *   tool_call           -> PreToolUse        a deny/block answer blocks the tool
 *   tool_result         -> PostToolUse(Failure) additionalContext is appended to the tool result
 *   agent_before_settle -> Stop              a block answer adds Arbiter's reason and continues once
 * Every call fails open: if the daemon is down, slow or answers badly, Pi carries on unchanged.
 *
 * The daemon's port and hook token are read from the Arbiter home (ARBITER_HOME, else the platform
 * default) on each session start. Install with `arbiter setup` or `pi -e <path to this file>`.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { registerApplyPatch, registerEditTools } from "./edit_tools.ts";

const CLIENT = "pi";
const TOKEN_HEADER = "X-Arbiter-Token";
// Mirrors the daemon's budgets (pack, post-edit test run, test run at a claim). Pi has no hook timeout,
// so the post-edit wait is longer than other clients get (completion.auto_test_client_budget_s.pi).
const DEADLINE_MS: Record<string, number> = { UserPromptSubmit: 6500, PostToolUse: 9500, PostToolUseFailure: 9500,
  Stop: 4600 };
const MAX_STOP_BLOCKS = 3;
// Pi can run several tool calls from one response together, but on sympy it sent all 94 of its edits
// one per request, each re-sending the conversation (2026-09-27). One line in the system prompt.
const BATCH_GUIDELINE = "When a change needs several edits (code and its tests, or more than one file), make all of " +
  "those tool calls in the same response: they run together, and Arbiter tests the result once.";

function arbiterState(): string {
  const home = process.env.ARBITER_HOME;
  if (home) return path.join(path.resolve(home), "data", "state");
  if (process.platform === "win32") return path.join(os.homedir(), ".arbiter", "data", "state");
  if (process.platform === "darwin") return path.join(os.homedir(), "Library", "Application Support", "arbiter", "data", "state");
  return path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"), "arbiter", "data", "state");
}

function endpoint(): { port: number; token: string } | undefined {
  try {
    const dir = arbiterState();
    const record = JSON.parse(fs.readFileSync(path.join(dir, "daemon.json"), "utf8"));
    const port = Number(record?.http?.port);
    const token = fs.readFileSync(path.join(dir, "hook.token"), "utf8").trim();
    return port > 0 && token ? { port, token } : undefined;
  } catch {
    return undefined;
  }
}

function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.filter((c: any) => c?.type === "text").map((c: any) => String(c.text ?? "")).join("\n");
}

export default function (pi: any) {
  if (process.env.ARBITER_PI_EDIT_TOOLS === "1") registerEditTools(pi);
  if (process.env.ARBITER_PI_APPLY_PATCH === "1") registerApplyPatch(pi);
  let target: { port: number; token: string } | undefined;
  let prompts = 0;
  let stopBlocks = 0;

  async function send(event: string, ctx: any, extra: Record<string, unknown>): Promise<any> {
    target = target ?? endpoint();
    if (!target) return {};
    const payload = {
      hook_event_name: event,
      session_id: ctx.sessionManager?.getSessionId?.() ?? "pi",
      transcript_path: ctx.sessionManager?.getSessionFile?.() ?? null,
      cwd: ctx.cwd,
      model: ctx.model?.id ?? null,
      turn_id: `p${prompts}`,
      ...extra,
    };
    try {
      const res = await fetch(`http://127.0.0.1:${target.port}/hook/${CLIENT}/${event}`, {
        method: "POST",
        headers: { "content-type": "application/json", [TOKEN_HEADER]: target.token },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(DEADLINE_MS[event] ?? 1500),
      });
      if (res.status === 401) target = undefined;   // token rotated: re-read it next time
      const out = res.ok ? await res.json() : {};
      return out && typeof out === "object" ? out : {};
    } catch {
      target = undefined;                           // daemon restarted on another port, or is down
      return {};
    }
  }

  const context = (out: any): string => String(out?.hookSpecificOutput?.additionalContext ?? "").trim();

  pi.on("session_start", async (event: any, ctx: any) => {
    target = endpoint();
    await send("SessionStart", ctx, { source: event?.reason ?? "startup" });
  });

  pi.on("before_agent_start", async (event: any, ctx: any) => {
    prompts += 1;
    stopBlocks = 0;
    const out = await send("UserPromptSubmit", ctx, { prompt: event.prompt });
    // Effort per thread (system1.effort_advice): set before the first request, since the provider caches a
    // prompt per reasoning level and a later change re-reads the history uncached.
    const thinking = out?.arbiterRouting?.thinking;
    if ((thinking === "low" || thinking === "high") && typeof pi.setThinkingLevel === "function") pi.setThinkingLevel(thinking);
    const guides = event.systemPromptOptions?.promptGuidelines;
    if (target && Array.isArray(guides) && !guides.includes(BATCH_GUIDELINE)) guides.push(BATCH_GUIDELINE);
    else if (target && event.systemPromptOptions && !guides) event.systemPromptOptions.promptGuidelines = [BATCH_GUIDELINE];
    const text = context(out);
    return text ? { message: { customType: "arbiter", content: text, display: false } } : undefined;
  });

  pi.on("tool_call", async (event: any, ctx: any) => {
    const out = await send("PreToolUse", ctx, { tool_name: event.toolName, tool_input: event.input,
      tool_use_id: event.toolCallId });
    const hso = out?.hookSpecificOutput ?? {};
    if (out?.decision === "block" || hso.permissionDecision === "deny") {
      return { block: true, reason: String(out?.reason ?? hso.permissionDecisionReason ?? "Blocked by Arbiter") };
    }
    return undefined;
  });

  pi.on("tool_result", async (event: any, ctx: any) => {
    const output = textOf(event.content);
    const exit = typeof event.details?.exitCode === "number" ? event.details.exitCode : undefined;
    const out = await send(event.isError ? "PostToolUseFailure" : "PostToolUse", ctx, {
      tool_name: event.toolName, tool_input: event.input, tool_use_id: event.toolCallId,
      tool_response: { output: output.slice(-20000), is_error: Boolean(event.isError),
        ...(exit === undefined ? {} : { exit_code: exit }) },
    });
    const text = context(out);
    return text ? { content: [...event.content, { type: "text", text }] } : undefined;
  });

  pi.on("agent_before_settle", async (event: any, ctx: any) => {
    if (event.outcome !== "completed") return undefined;
    const msgs = event.context?.contextMessages ?? [];
    let last = "";
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i]?.role === "assistant") { last = textOf(msgs[i].content); break; }
    }
    // stop_seq keeps a repeated identical claim from being dropped as a duplicate delivery.
    const out = await send("Stop", ctx, { last_assistant_message: last, stop_hook_active: stopBlocks > 0,
      stop_seq: stopBlocks });
    // Routing (routing.enabled): tests kept failing on a cheap model, so the rest of the task runs on the
    // strong one. Pi, unlike Codex, lets an extension switch the session's model.
    const escalate = out?.arbiterRouting?.model;
    if (typeof escalate === "string" && ctx.model?.id !== escalate) {
      const strong = ctx.modelRegistry?.find?.(ctx.model?.provider ?? "", escalate);
      if (strong) await pi.setModel(strong);
    }
    // (context.canContinue describes the context before these entries: false after an assistant reply.)
    if (out?.decision === "block" && stopBlocks < MAX_STOP_BLOCKS) {
      stopBlocks += 1;
      return { entries: [{ type: "custom_message", customType: "arbiter", content: String(out.reason ?? ""),
        display: true }], continue: true };
    }
    return undefined;
  });

  pi.on("session_shutdown", async (_event: any, ctx: any) => {
    await send("SessionEnd", ctx, {});
  });
}
