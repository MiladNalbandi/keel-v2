// KeelBot's tool calls as keel TUI shows them, the opencode way: one quiet line per call, "⚙ keel_status
// [project=demo-project]" or "$ git status --short", its result folded under it ("└ 220 lines", or the one line it
// printed, an error in the error colour), and the whole output in the transcript when the person opens it (Ctrl+O, or a
// click). No UI here: lines of styled text for any front end.
//
// A call comes from the engine's helper.tool event (name, args, status, summary, output), or, from an engine without
// it, from the helper.step the runner emitted for the same call (the step's n joins them).

import stringWidth from "string-width";
import type { JobStep } from "./sdk";
import { PLAIN, type Paint } from "./theme";

export type ToolStatus = "ok" | "error" | "refused";

export type ToolCall = {
  /** the step's number in the turn (helper.step and helper.tool share it) */
  n: number;
  name: string;
  server?: string;
  /** the arguments as one line: "project=demo-project", the command, the path */
  args: string;
  /** a shell command: shown as "$ <command>" */
  shell: boolean;
  status: ToolStatus;
  /** one line for the fold: "220 lines", what it printed, the error, what changed ("+3 -1") */
  summary: string;
  output: string;
  lines: number;
  path?: string;
  ms?: number;
};

const SHELL = new Set(["Bash", "bash", "shell", "run_command", "exec_command"]);
const FILE_TOOL: Record<string, string> = {
  read: "Read",
  write: "Write",
  edit: "Edit",
};
const MORE_LINES = /\n… \((\d+) more lines\)$/;
/** The step kinds that are a tool call. */
export const TOOL_KINDS = new Set(["tool", "read", "write", "edit", "guard"]);

/** Arguments as one line: a JSON object as `key=value` pairs, anything else with its spaces folded. */
export function argsText(args: unknown): string {
  let v = args;
  if (typeof v === "string" && /^\s*\{/.test(v)) {
    try {
      v = JSON.parse(v);
    } catch {
      /* not JSON: as it is */
    }
  }
  if (v && typeof v === "object" && !Array.isArray(v)) {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${k}=${typeof x === "string" ? x : JSON.stringify(x)}`)
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
  }
  return String(v ?? "")
    .replace(/\s+/g, " ")
    .trim();
}

/** How many lines the tool printed: the lines kept, plus the ones the runner cut ("… (N more lines)"). */
export function lineCount(output: string): number {
  if (!output) return 0;
  const m = MORE_LINES.exec(output);
  const kept = m ? output.slice(0, m.index) : output;
  return kept.split("\n").length + (m ? Number(m[1]) : 0);
}

/** "+3 -1": the lines a diff adds and removes. */
export function diffStat(diff: string): string {
  const ls = diff.split("\n");
  return `+${ls.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length} -${ls.filter((l) => l.startsWith("-") && !l.startsWith("---")).length}`;
}

const firstLine = (s: string) =>
  (s.split("\n").find((l) => l.trim()) ?? "").trim();

/** The fold's line when the engine sent none. */
function summaryOf(
  status: ToolStatus,
  kind: string,
  text: string,
  output: string,
  lines: number,
): string {
  if (status === "refused") return `refused: ${firstLine(text)}`;
  if (status === "error") return firstLine(output) || "failed";
  if (kind === "write" || kind === "edit")
    return /\(([^()]*)\)\s*$/.exec(text)?.[1] ?? diffStat(output);
  return !lines ? "" : lines === 1 ? firstLine(output) : `${lines} lines`;
}

/** A tool call from the step the runner emitted (an engine without helper.tool); null for a step that is not one. */
export function toolFromStep(s: JobStep): ToolCall | null {
  if (!TOOL_KINDS.has(s.kind)) return null;
  const name = s.tool || FILE_TOOL[s.kind] || s.kind;
  const status: ToolStatus =
    s.kind === "guard" ? "refused" : s.ok === false ? "error" : "ok";
  const output =
    s.kind === "read"
      ? s.text
      : s.kind === "write" || s.kind === "edit"
        ? s.diff || s.output || ""
        : s.kind === "guard"
          ? s.text
          : s.output || "";
  const args =
    s.kind === "tool"
      ? argsText(s.text)
      : s.path || (s.kind === "guard" ? "" : argsText(s.text));
  const lines = lineCount(output);
  return {
    n: s.n,
    name,
    ...(s.server ? { server: s.server } : {}),
    args,
    shell: SHELL.has(name),
    status,
    summary: summaryOf(status, s.kind, s.text, output, lines),
    output,
    lines,
    ...(s.path ? { path: s.path } : {}),
    ...(s.ms ? { ms: s.ms } : {}),
  };
}

/** A tool call from the engine's helper.tool event; null when it is not one. */
export function toolFromEvent(d: Record<string, unknown>): ToolCall | null {
  const n = Number(d.n);
  if (!Number.isFinite(n) || typeof d.name !== "string") return null;
  const status: ToolStatus =
    d.status === "error" || d.status === "refused" ? d.status : "ok";
  const output = typeof d.output === "string" ? d.output : "";
  const lines = typeof d.lines === "number" ? d.lines : lineCount(output);
  return {
    n,
    name: d.name,
    ...(typeof d.server === "string" && d.server ? { server: d.server } : {}),
    args: argsText(d.args),
    shell: d.shell === true || SHELL.has(d.name),
    status,
    summary:
      typeof d.summary === "string"
        ? d.summary
        : summaryOf(status, "tool", "", output, lines),
    output,
    lines,
    ...(typeof d.path === "string" && d.path ? { path: d.path } : {}),
    ...(typeof d.ms === "number" ? { ms: d.ms } : {}),
  };
}

/** A list of calls with this one in it: a newer record of the same call (same n) takes its place. */
export function upsertTool(list: ToolCall[], t: ToolCall): ToolCall[] {
  const i = list.findIndex((x) => x.n === t.n);
  if (i < 0) return [...list, t];
  const out = list.slice();
  out[i] = t;
  return out;
}

/** A line cut to `width` columns, with "…" when it was longer. */
export function cutTo(s: string, width: number): string {
  if (stringWidth(s) <= width) return s;
  let out = "";
  for (const ch of s) {
    if (stringWidth(out + ch) > width - 1) break;
    out += ch;
  }
  return out + "…";
}

/** The call's line: "⚙ keel_status [project=demo-project]", "$ git status --short", cut to one line. */
export function toolHead(t: ToolCall, width: number, p: Paint = PLAIN): string {
  const bad = t.status !== "ok";
  const glyph = (s: string) => (bad ? p.bad(s) : p.meta(s));
  if (t.shell)
    return (
      "  " +
      glyph("$ ") +
      p.detail(cutTo(t.args || t.name, Math.max(8, width - 4)))
    );
  const name =
    t.server && t.server !== "keel" ? `${t.server} · ${t.name}` : t.name;
  const room = Math.max(8, width - 4 - stringWidth(name) - 3);
  return (
    "  " +
    glyph("⚙ ") +
    p.detail(name) +
    (t.args ? " " + p.meta(`[${cutTo(t.args, room)}]`) : "")
  );
}

/** The folded result: "└ 220 lines", what it printed or the error; `hint` says how to open it. */
export function toolFold(
  t: ToolCall,
  width: number,
  p: Paint = PLAIN,
  open = false,
  hint = "",
): string {
  const bad = t.status !== "ok";
  const more = t.output && (t.lines > 1 || t.summary !== firstLine(t.output));
  const words =
    t.summary ||
    (t.output ? `${t.lines} line${t.lines === 1 ? "" : "s"}` : "no output");
  const tail = more ? (open ? " (open)" : hint ? ` (${hint})` : "") : "";
  const line = cutTo(`    └ ${words}`, Math.max(10, width - stringWidth(tail)));
  return (bad ? p.bad(line) : p.meta(line)) + p.meta(tail);
}

/** The whole output, under the fold, indented (an open call). */
export function toolBody(
  t: ToolCall,
  width: number,
  p: Paint = PLAIN,
): string[] {
  return t.output
    .replace(/\n$/, "")
    .split("\n")
    .map((l) =>
      (t.status !== "ok" ? p.bad : p.detail)(cutTo("      " + l, width)),
    );
}

/** A call's lines: its head and its fold, and with `open` its whole output. */
export function toolLines(
  t: ToolCall,
  width: number,
  p: Paint = PLAIN,
  open = false,
  hint = "",
): string[] {
  return [
    toolHead(t, width, p),
    toolFold(t, width, p, open, hint),
    ...(open && t.output ? toolBody(t, width, p) : []),
  ];
}

/** Whether a call has more to show than its fold. */
export const canOpen = (t: ToolCall) =>
  !!t.output && (t.lines > 1 || t.summary !== firstLine(t.output));
