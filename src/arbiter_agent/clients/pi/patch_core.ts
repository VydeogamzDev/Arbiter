/**
 * Codex's apply_patch format for Pi (pure; see edit_tools.ts for the tool).
 *
 *   *** Begin Patch
 *   *** Update File: src/a.ts          (optionally followed by *** Move to: new/path.ts)
 *   @@ optional line that locates the hunk
 *    context line
 *   -removed line
 *   +added line
 *   *** Add File: src/b.ts             (every line starts with +)
 *   *** Delete File: src/c.ts
 *   *** End Patch
 *
 * All files are checked before any is written: a patch applies whole or not at all.
 */
import * as fs from "node:fs";
import * as path from "node:path";

export interface FileChange {
  path: string;
  kind: "update" | "add" | "delete";
  moveTo?: string;
  content?: string;
}

interface Hunk {
  header: string;
  oldLines: string[];
  newLines: string[];
}

const eq = [
  (a: string, b: string) => a === b,
  (a: string, b: string) => a.trimEnd() === b.trimEnd(),
  (a: string, b: string) => a.trim() === b.trim(),
];

function find(lines: string[], want: string[], from: number): number {
  for (const same of eq) {
    for (let i = from; i + want.length <= lines.length; i++) {
      let ok = true;
      for (let j = 0; j < want.length && ok; j++) ok = same(lines[i + j], want[j]);
      if (ok) return i;
    }
  }
  return -1;
}

function inside(cwd: string, rel: string): string {
  const abs = path.resolve(cwd, rel);
  const root = path.resolve(cwd);
  if (abs !== root && !abs.startsWith(root + path.sep)) throw new Error(`${rel} is outside the working directory`);
  return abs;
}

function applyHunks(rel: string, text: string, hunks: Hunk[]): string {
  const crlf = text.includes("\r\n");
  const trailingNewline = text.endsWith("\n");
  let lines = text.replace(/\r\n/g, "\n").split("\n");
  if (trailingNewline) lines.pop();
  let cursor = 0;
  for (const h of hunks) {
    let from = cursor;
    if (h.header) {
      const at = find(lines, [h.header], cursor);
      if (at < 0) throw new Error(`${rel}: can't find the line "${h.header}" named after @@`);
      from = h.oldLines.length ? at : at + 1;
    }
    let at = h.oldLines.length ? find(lines, h.oldLines, from) : from;
    if (at < 0 && from > 0) at = find(lines, h.oldLines, 0);
    if (at < 0) {
      throw new Error(`${rel}: these lines are not in the file (context and - lines must match it):\n` +
        h.oldLines.slice(0, 8).join("\n"));
    }
    if (!h.oldLines.length && !h.header) at = lines.length;          // bare additions go at the end
    lines = [...lines.slice(0, at), ...h.newLines, ...lines.slice(at + h.oldLines.length)];
    cursor = at + h.newLines.length;
  }
  let out = lines.join("\n") + (trailingNewline || !text ? "\n" : "");
  if (crlf) out = out.replace(/\n/g, "\r\n");
  return out;
}

export function parsePatch(patch: string): { path: string; kind: FileChange["kind"]; moveTo?: string; body: string[] }[] {
  const rows = patch.replace(/\r\n/g, "\n").split("\n");
  let i = rows.findIndex((r) => r.trim() === "*** Begin Patch");
  if (i < 0) throw new Error("the patch must start with *** Begin Patch");
  const files: { path: string; kind: FileChange["kind"]; moveTo?: string; body: string[] }[] = [];
  for (i += 1; i < rows.length; i++) {
    const r = rows[i];
    if (r.trim() === "*** End Patch") return files;
    const m = /^\*\*\* (Update|Add|Delete) File: (.+)$/.exec(r);
    if (m) {
      files.push({ path: m[2].trim(), kind: m[1].toLowerCase() as FileChange["kind"], body: [] });
      continue;
    }
    const mv = /^\*\*\* Move to: (.+)$/.exec(r);
    if (mv && files.length) {
      files[files.length - 1].moveTo = mv[1].trim();
      continue;
    }
    if (r.startsWith("*** End of File")) continue;
    if (!files.length) {
      if (r.trim()) throw new Error(`expected *** Update/Add/Delete File, got: ${r}`);
      continue;
    }
    files[files.length - 1].body.push(r);
  }
  throw new Error("the patch must end with *** End Patch");
}

function hunksOf(body: string[]): Hunk[] {
  const hunks: Hunk[] = [];
  let cur: Hunk | null = null;
  for (const r of body) {
    if (r.startsWith("@@")) {
      cur = { header: r.slice(2).trim(), oldLines: [], newLines: [] };
      hunks.push(cur);
      continue;
    }
    if (!cur) {
      cur = { header: "", oldLines: [], newLines: [] };
      hunks.push(cur);
    }
    const tag = r[0];
    const line = r.slice(1);
    if (tag === "+") cur.newLines.push(line);
    else if (tag === "-") cur.oldLines.push(line);
    else {
      const ctx = tag === " " ? line : r;                 // a bare empty line is an empty context line
      cur.oldLines.push(ctx);
      cur.newLines.push(ctx);
    }
  }
  for (const h of hunks) {                               // blank lines closing a hunk are separators
    while (h.oldLines.length && h.newLines.length && h.oldLines.at(-1) === "" && h.newLines.at(-1) === "" &&
           h.oldLines.length + h.newLines.length > 2) {
      h.oldLines.pop();
      h.newLines.pop();
    }
  }
  return hunks.filter((h) => h.oldLines.length || h.newLines.length);
}

/** The changes a patch makes (nothing written yet). Throws with a message for the model on any mismatch. */
export function planPatch(cwd: string, patch: string): FileChange[] {
  const out: FileChange[] = [];
  for (const f of parsePatch(patch)) {
    const abs = inside(cwd, f.path);
    if (f.kind === "add") {
      if (fs.existsSync(abs)) throw new Error(`${f.path} already exists; use *** Update File`);
      const bad = f.body.find((r) => r && !r.startsWith("+"));
      if (bad !== undefined) throw new Error(`${f.path}: every line of an added file starts with +`);
      const lines = f.body.map((r) => r.slice(1));
      while (lines.length && lines.at(-1) === "") lines.pop();
      out.push({ path: f.path, kind: "add", content: lines.join("\n") + "\n" });
    } else if (f.kind === "delete") {
      if (!fs.existsSync(abs)) throw new Error(`${f.path} does not exist`);
      out.push({ path: f.path, kind: "delete" });
    } else {
      if (!fs.existsSync(abs)) throw new Error(`${f.path} does not exist; use *** Add File`);
      const text = fs.readFileSync(abs, "utf8");
      const content = applyHunks(f.path, text, hunksOf(f.body));
      if (f.moveTo) inside(cwd, f.moveTo);
      out.push({ path: f.path, kind: "update", moveTo: f.moveTo, content });
    }
  }
  if (!out.length) throw new Error("the patch changes no file");
  return out;
}

export function writeChange(cwd: string, c: FileChange): void {
  const abs = path.resolve(cwd, c.path);
  if (c.kind === "delete") {
    fs.rmSync(abs);
    return;
  }
  const dest = c.moveTo ? path.resolve(cwd, c.moveTo) : abs;
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.writeFileSync(dest, c.content ?? "", "utf8");
  if (c.moveTo && dest !== abs) fs.rmSync(abs);
}

export function summary(changes: FileChange[]): string {
  const tag = { update: "M", add: "A", delete: "D" } as const;
  return "Success. Updated the following files:\n" +
    changes.map((c) => `${tag[c.kind]} ${c.moveTo ?? c.path}`).join("\n");
}

/** Plan, then write every file: the whole patch or nothing. */
export function applyPatch(cwd: string, patch: string): string {
  const changes = planPatch(cwd, patch);
  for (const c of changes) writeChange(cwd, c);
  return summary(changes);
}
