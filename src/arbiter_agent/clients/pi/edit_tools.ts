/**
 * Two edit tools that don't make the model repeat code it isn't changing (opt-in: ARBITER_PI_EDIT_TOOLS=1).
 *
 * Pi's `edit` takes oldText + newText, so adding a method means sending an anchor twice and changing
 * one line of a function means sending its surroundings twice. On sympy, edit arguments were 59% of
 * the model's output tokens with Arbiter on, and output is the priciest token (2026-09-27):
 *   insert_code  text after one unique existing line, or after the whole block that line opens
 *   replace_def  a Python def/class (or Class.method) replaced by name
 * Neither needs line numbers (Pi's read shows none) or a copy of the old code.
 */
import * as path from "node:path";
import { withFileMutationQueue } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { insertCode, replaceDef } from "./edit_core.ts";
import { planPatch, summary, writeChange } from "./patch_core.ts";

export function registerEditTools(pi: any): void {
  const run = (fn: (cwd: string) => string, file: string, ctx: any) =>
    withFileMutationQueue(path.resolve(ctx.cwd, file), async () => ({ content: [{ type: "text", text: fn(ctx.cwd) }],
      details: undefined }));
  pi.registerTool({
    name: "insert_code",
    label: "Insert code",
    description: "Insert text after one existing line of a file, or after the whole indented block that line starts " +
      "(after_block: true, e.g. after an entire method). The line must appear exactly once. Nothing already in the " +
      "file is repeated.",
    promptSnippet: "Insert new code after a unique existing line or block",
    promptGuidelines: ["To add code (a new function, method, test or import), prefer insert_code over edit: name one " +
      "existing line and send only the new text."],
    parameters: Type.Object({
      path: Type.String({ description: "File to change" }),
      after: Type.String({ description: "An existing line, exactly as in the file (leading spaces may be omitted)" }),
      after_block: Type.Optional(Type.Boolean({ description: "Insert after the whole block this line opens" })),
      text: Type.String({ description: "The lines to insert, with their indentation" }),
    }),
    async execute(_id: string, params: any, _signal: any, _onUpdate: any, ctx: any) {
      return run((cwd) => insertCode(cwd, params), params.path, ctx);
    },
  });
  pi.registerTool({
    name: "replace_def",
    label: "Replace definition",
    description: "Replace one Python function, method or class, found by name (`name` or `Class.method`), with new " +
      "code. Decorators directly above it are replaced too. The old code is not needed.",
    promptSnippet: "Replace a Python def/class by name with new code",
    promptGuidelines: ["To rewrite most of a Python function or method, prefer replace_def over edit; for a small " +
      "change inside a long function, edit is shorter."],
    parameters: Type.Object({
      path: Type.String({ description: "File to change" }),
      name: Type.String({ description: "Function or class name, or Class.method" }),
      text: Type.String({ description: "The complete new definition, including the def/class line and decorators" }),
    }),
    async execute(_id: string, params: any, _signal: any, _onUpdate: any, ctx: any) {
      return run((cwd) => replaceDef(cwd, params), params.path, ctx);
    },
  });
}

/**
 * Codex's apply_patch as a Pi tool (opt-in: ARBITER_PI_APPLY_PATCH=1). gpt-6-luna in Pi made one edit
 * call per request and never two calls in one response, so a change to a file and its test took two
 * requests (E-E-F in 32 of 45 real-repo prompts, 2026-09-28). A patch edits several files in one call,
 * in the format GPT models are trained on (the custom tools above were not, and degenerated).
 */
export function registerApplyPatch(pi: any): void {
  pi.registerTool({
    name: "apply_patch",
    label: "Apply patch",
    description: "Edit files with a patch: one call can change several files (for example code and its test). " +
      "Format:\n*** Begin Patch\n*** Update File: path/to/file\n@@ optional line near the change\n context line\n" +
      "-removed line\n+added line\n*** Add File: path/to/new\n+line of the new file\n*** Delete File: path\n" +
      "*** End Patch\nContext and - lines must match the file. The whole patch applies or none of it does.",
    promptSnippet: "Edit one or more files in one call with a Codex-style patch",
    promptGuidelines: ["Prefer apply_patch for changes: put every file a change needs (the code, its tests, an " +
      "export) in one patch."],
    parameters: Type.Object({
      patch: Type.String({ description: "The patch, from *** Begin Patch to *** End Patch" }),
    }),
    async execute(_id: string, params: any, _signal: any, _onUpdate: any, ctx: any) {
      const changes = planPatch(ctx.cwd, String(params.patch ?? ""));
      for (const c of changes) {
        await withFileMutationQueue(path.resolve(ctx.cwd, c.path), async () => writeChange(ctx.cwd, c));
      }
      return { content: [{ type: "text", text: summary(changes) }], details: undefined };
    },
  });
}
