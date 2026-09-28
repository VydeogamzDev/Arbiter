/** Pure logic of Arbiter's Pi edit tools (edit_tools.ts); runs under plain Node for tests. */
import * as fs from "node:fs";
import * as path from "node:path";

const lead = (s: string): number => s.length - s.trimStart().length;

function load(cwd: string, p: string): { file: string; lines: string[]; eol: string; trailing: boolean } {
  const file = path.isAbsolute(p) ? p : path.join(cwd, p);
  const raw = fs.readFileSync(file, "utf8");
  const eol = raw.includes("\r\n") ? "\r\n" : "\n";
  const trailing = /\r?\n$/.test(raw);
  const lines = raw.split(/\r?\n/);
  if (trailing) lines.pop();
  return { file, lines, eol, trailing };
}

function save(f: { file: string; lines: string[]; eol: string; trailing: boolean }): void {
  fs.writeFileSync(f.file, f.lines.join(f.eol) + (f.trailing ? f.eol : ""), "utf8");
}

/** Last line of the indented block that line `i` opens (blank lines inside it included). */
export function blockEnd(lines: string[], i: number): number {
  const base = lead(lines[i]);
  let last = i;
  for (let j = i + 1; j < lines.length; j++) {
    if (lines[j].trim() === "") continue;
    if (lead(lines[j]) <= base && !/^\s*[)\]}]/.test(lines[j])) break;
    last = j;
  }
  return last;
}

function textLines(text: string): string[] {
  return text.replace(/\r?\n$/, "").split(/\r?\n/);
}

export function insertCode(cwd: string, a: { path: string; after: string; after_block?: boolean; text: string }): string {
  const f = load(cwd, a.path);
  const want = a.after.replace(/\s+$/, "");
  let hits = f.lines.flatMap((l, i) => (l.replace(/\s+$/, "") === want ? [i] : []));
  if (!hits.length) hits = f.lines.flatMap((l, i) => (l.trim() === want.trim() && want.trim() ? [i] : []));
  if (!hits.length) throw new Error(`No line in ${a.path} matches ${JSON.stringify(a.after)}.`);
  if (hits.length > 1) {
    throw new Error(`${hits.length} lines in ${a.path} match ${JSON.stringify(a.after)} (lines ` +
      `${hits.slice(0, 5).map((h) => h + 1).join(", ")}); give a line that appears once.`);
  }
  const at = a.after_block ? blockEnd(f.lines, hits[0]) : hits[0];
  const add = textLines(a.text);
  f.lines.splice(at + 1, 0, ...add);
  save(f);
  return `Inserted ${add.length} line(s) after line ${at + 1} of ${a.path}.`;
}

export function replaceDef(cwd: string, a: { path: string; name: string; text: string }): string {
  const f = load(cwd, a.path);
  const parts = a.name.split(".").filter(Boolean);
  let from = 0;
  let to = f.lines.length - 1;
  let start = -1;
  for (const [k, part] of parts.entries()) {
    const rx = new RegExp(`^\\s*(async\\s+def|def|class)\\s+${part.replace(/[^\w]/g, "")}\\b`);
    const hits: number[] = [];
    for (let i = from; i <= to; i++) if (rx.test(f.lines[i])) hits.push(i);
    const top = hits.length > 1 ? hits.filter((h) => lead(f.lines[h]) === Math.min(...hits.map((x) => lead(f.lines[x])))) : hits;
    if (!top.length) throw new Error(`No def or class named ${JSON.stringify(parts.slice(0, k + 1).join("."))} in ${a.path}.`);
    if (top.length > 1) throw new Error(`${top.length} definitions of ${JSON.stringify(part)} in ${a.path}; qualify it (Class.method).`);
    start = top[0];
    from = start + 1;
    to = blockEnd(f.lines, start);
  }
  let first = start;
  while (first > 0 && /^\s*@/.test(f.lines[first - 1]) && lead(f.lines[first - 1]) === lead(f.lines[start])) first--;
  const end = blockEnd(f.lines, start);
  const add = textLines(a.text);
  f.lines.splice(first, end - first + 1, ...add);
  save(f);
  return `Replaced ${a.name} (lines ${first + 1}-${end + 1}) in ${a.path} with ${add.length} line(s).`;
}
