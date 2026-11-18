// keel TUI's full screen: the transcript as lines of styled text, so the middle of the screen can scroll while keel's
// name stays at the top and the prompt at the bottom. No UI here: a block (and the start screen) becomes lines no
// wider than the screen; App.tsx shows the part that fits and moves it with PgUp / PgDn, Shift+↑↓ and the wheel.

import stringWidth from "string-width";
import wrapAnsi from "wrap-ansi";
import type { Block } from "./commands";
import type { Snapshot } from "./keel";
import { colorDiagram } from "./colorize";
import { renderMarkdown } from "./markdown";
import { LOGO } from "./logo";
import * as fmt from "./format";
import { PLAIN as PLAIN_LOOK, type Paint, type Role } from "./theme";
import { PARKED, VERSION } from "./version";
import { toolLines } from "./tools";

/** A notice's colour: what waits for you amber, a failure red, a finished flow green, the rest the accent. */
export function noticeRole(text: string): "gate" | "bad" | "ok" | "accent" | "dim" {
  if (/^(waiting for you|budget)/.test(text)) return "gate";
  if (/^(flow failed|keel refused|agent .* (failed|guard))/.test(text)) return "bad";
  if (/^flow done| approved/.test(text)) return "ok";
  if (/^via /.test(text)) return "dim";
  return "accent";
}

const plain = (s: string) => s.replace(/\x1b\[[0-9;]*m/g, "");

/** Text as lines no wider than `width` (long words broken), each line's colours closed and opened again. A folded
 *  line goes on under its own text: under the second column of a two-column line (/help), else under its first word. */
export function wrap(text: string, width: number): string[] {
  const w = Math.max(10, width);
  return text.split("\n").flatMap((line) => {
    if (stringWidth(line) <= w) return [line];
    const raw = plain(line);
    // a line with a mark (✗ ✓ ◆): under its text; a two-column line (/help): under the second column
    const col = /^[✗✓◆] /.test(raw) ? 2 : /^(\s*\S.*?\s{2,})\S/.exec(raw)?.[1].length ?? 0;
    const hang = col && col <= w * 0.6 ? col : Math.min(raw.length - raw.trimStart().length, Math.floor(w / 2));
    const [first, ...rest] = wrapAnsi(line, w, { hard: true, trim: false }).split("\n");
    // the space a row broke at goes with neither row (wrap-ansi keeps it at the next row's start: " it would break")
    const lead = (r: string) => r.replace(/^((?:\x1b\[[0-9;]*m)*) +/, "$1");
    if (!rest.length) return [first];
    if (!hang) return [first, ...rest.map(lead)];
    const more = wrapAnsi(rest.map(lead).join(" "), w - hang, { hard: true, trim: false }).split("\n");
    return [first.replace(/ +((?:\x1b\[[0-9;]*m)*)$/, "$1"), ...more.map((r) => " ".repeat(hang) + r)];
  });
}

const indent = (lines: string[], n: number) => lines.map((l) => " ".repeat(n) + l);

/** A note (a "dim" block: what was chosen, what was not done, where a suggestion waits) in its roles: "q → answer"
 *  with the answer as the chosen one, "it waits at:" as a gate, "not done" as a warning, the rest quiet meta text. */
export function noteText(text: string, p: Paint): string {
  if (!p.colored) return text;
  // KeelBot's thinking (shown with Ctrl+T or /thinking): its own words, never read as "q → answer" or a warning
  if (/^⋮ /.test(text)) return text.split("\n").map((l) => p.meta(l)).join("\n");
  return text.split("\n").map((l) => {
    let m: RegExpExecArray | null;
    if ((m = /^(\s*)(it waits at:)(.*)$/.exec(l))) return m[1] + p.strong("gate", m[2]) + p.detail(m[3]);
    if ((m = /^(\s*)(.*?)( → )(.*)$/.exec(l))) return m[1] + p.detail(m[2]) + p.meta(m[3]) + p.strong("chosen", m[4]);
    if (/^\s*(not done|(suggestion )?not sent|left as it is|cancel)/i.test(l)) return p.warn(l);
    return p.meta(l);
  }).join("\n");
}

/** A plain text block in its roles where it has a known shape: a question card ("1/2 the question?" and its why
 *  under it), the check of the answers ("Your answers:", each "→ answer"). Other text (and text that has its colours
 *  already) stays as it is. */
export function textBlock(text: string, p: Paint): string {
  if (!p.colored || /\x1b\[/.test(text)) return text;
  const [first, ...rest] = text.split("\n");
  const q = /^(\d+\/\d+) (.*)$/.exec(first);
  if (q) return [p.strong("gate", q[1]) + " " + p.strong("question", q[2]), ...rest.map((l) => fmt.labelLine(l, p))].join("\n");
  if (first === "Your answers:") return [p.strong("heading", first), ...rest.map((l) => {
    const a = /^(\s*)(→ )(.*)$/.exec(l);
    if (a) return a[1] + p.meta(a[2]) + p.strong("chosen", a[3]);
    const n = /^(\s*)(\d+\.)( .*)$/.exec(l);
    return n ? n[1] + p.meta(n[2]) + p.strong("question", n[3]) : l;
  })].join("\n");
  return text;
}

/** The bar down the left of the person's message (opencode's look): in the person's colour, the sign without colour. */
export const PERSON_BAR = "│";

/** One block of the transcript as lines. The person's message: a bar in the person's colour down its left side, a line
 *  of air above and below inside it, and bold words; KeelBot's answer: the terminal's own text colour, indented, no
 *  bar. A tool call: its one line and its folded result (or its whole output, open). */
export function blockLines(b: Block, width: number, p: Paint): string[] {
  const role = (r: Role, s: string) => p[r](s);
  if (b.reverted) {
    // 0.18.19 a KeelBot answer /undo took back: quiet, as plain text, and the person's message says it is undone
    if (b.kind === "user") return ["", ...wrap(`${b.text}  (undone)`, width - 3).map((r, i) => p.meta((i ? "   " : " ↶ ") + r))];
    if (b.kind === "tool" && b.tool) return toolLines(b.tool, width, PLAIN_LOOK).map((l) => p.meta(l));
    return indent(wrap(b.text, width - 2), 2).map((l) => p.meta(l));
  }
  switch (b.kind) {
    case "markdown": return indent(wrap(renderMarkdown(b.text, p, width - 2), width - 2), 2);
    // a diagram keeps its lines whole: a line wider than the screen is cut by the view, never folded
    case "diagram": return indent(colorDiagram(b.text, p).split("\n"), 1);
    case "error": return wrap(p.bold(role("bad", "✗ ")) + role("bad", b.text), width);
    case "ok": return wrap(p.bold(role("ok", "✓ ")) + role("ok", b.text), width);
    case "notice": {
      const r = noticeRole(b.text);
      const s = role(r, "◆ " + b.text);
      return wrap(r === "gate" ? p.bold(s) : s, width);
    }
    case "user": {
      const bar = p.person(PERSON_BAR);
      return ["", bar, ...wrap(b.text, width - 4).map((r) => bar + "  " + p.bold(r)), bar];
    }
    case "tool": return b.tool ? toolLines(b.tool, width, p, !!b.open, b.hint) : wrap(b.text, width);
    case "dim": return wrap(noteText(b.text, p), width);
    default: return wrap(textBlock(b.text, p), width);
  }
}

/** The start screen as lines: keel's logo in a round box, the keel, the project, the flow, what waits and what next. */
export function startLines(s: Snapshot, width: number, p: Paint): string[] {
  const w = Math.max(40, width);
  const inner = w - 4;
  const fit = (x: string) => {
    const cut = wrapAnsi(x, inner, { hard: true, trim: false }).split("\n")[0];
    return cut + " ".repeat(Math.max(0, inner - stringWidth(cut)));
  };
  const row = (x: string) => p.accent("│ ") + fit(x) + p.accent(" │");
  const label = (x: string) => p.dim(x);
  const t = s.flow?.thread ?? null;
  const pr = s.project;
  const body = [...LOGO.map((l) => p.accent(l)), "",
    p.bold(`keel v2 ${s.health?.version ?? VERSION}`) + p.dim(` · keel TUI ${VERSION}`),
    label("keel     ") + p.run(s.publicUrl) + p.dim(fmt.which(s.publicUrl, s.health).slice(s.publicUrl.length))];
  if (s.error) body.push(p.bad(`keel is not reachable: ${s.error}`), p.bad("Start it: keel2 start (or pass --url)."));
  else {
    body.push(label("project  ") + (pr ? p.bold(pr.name || pr.id) + p.dim(" · branch ") + p.run(pr.branch || "-") : "none yet: add one in the dashboard"));
    if (t) body.push(label("flow     ") + p.accent(t.workflow_id) + ` "${t.title}" · ` + fmt.badge(t.status, p) + (t.current ? p.dim(` at ${t.current}`) : ""));
    // keel 0.18.55 a project keel does not know yet: the setup line (wrapped, not cut: it names /flow init)
    const setup = fmt.initHint(pr, t, p);
    if (setup) wrapAnsi(setup, inner - 9, { hard: true, trim: false }).split("\n").forEach((l, i) => body.push(label(i ? "         " : "setup    ") + l));
    const waits = fmt.waitingBrief(s.inbox, pr);
    body.push(label("waiting  ") + (s.inbox.length ? p.bold(p.gate(waits)) : p.ok(waits)));
    body.push(label("next     ") + fmt.next(pr, s.flow, p).split("\n")[0]);
  }
  const k = (x: string) => p.accent(x);
  return [
    p.accent("╭" + "─".repeat(w - 2) + "╮"), ...body.map(row), p.accent("╰" + "─".repeat(w - 2) + "╯"),
    p.dim("  Ask KeelBot anything · ") + k("/help") + p.dim(" · ") + k("PgUp") + p.dim("/") + k("PgDn") + p.dim(" or the wheel scroll · ")
      + k("Ctrl+F") + p.dim(" flow · ") + k("Ctrl+A") + p.dim(" agents · ") + k("Ctrl+N") + p.dim(" inbox"),
    p.dim(`  ${PARKED}`),
    "",
  ];
}

/** The line that stays at the top: keel itself — its version, the logins it can use now (0.18.61: only those; the ones
 *  not set up are in Connections), and what it spent today and this month. Where the person is (the project, its branch, the
 *  flow) is the bottom line's (0.18.8). */
export function headerLine(s: Snapshot, width: number, p: Paint): string {
  const ready = s.logins.filter((l) => l.ready).map((l) => p.ok(`${l.label.toLowerCase()} ✓`));
  const logins = ready.length || !s.logins.length ? ready : [p.warn("no model login")];
  // short, so the whole line fits 80 columns: "today 1.9M $9.03"
  const spend = (label: string, x: { tokens: number; cost_usd: number }) =>
    p.dim(`${label} `) + fmt.short(x.tokens) + " " + p.bold(`$${x.cost_usd.toFixed(2)}`);
  const parts = [
    p.dim(`v2 ${s.health?.version ?? VERSION}`),
    logins.length ? logins.join(" ") : "",
    s.spent ? spend("today", s.spent.today) : "",
    s.spent ? spend("month", s.spent.month) : "",
  ].filter(Boolean);
  const name = p.colored ? p.bold(p.inverse(p.accent(" keel "))) : "[ keel ]";
  return wrapAnsi(`${name} ${parts.join(p.faint(" · "))}`, width, { hard: true, trim: false }).split("\n")[0];
}

/** Where the view is: `offset` lines up from the newest, never past the oldest. */
export function view(lines: string[], height: number, offset: number): { shown: string[]; offset: number; above: number; below: number } {
  const h = Math.max(1, height);
  const max = Math.max(0, lines.length - h);
  const off = Math.max(0, Math.min(offset, max));
  const end = lines.length - off;
  const start = Math.max(0, end - h);
  return { shown: lines.slice(start, end), offset: off, above: start, below: off };
}

/** The mouse's wheel in a chunk of input (SGR mode: ESC [ < 64 ; x ; y M is up, 65 down): +1 per notch up, -1 down.
 *  Shift, Meta and Ctrl (4, 8, 16) and the motion bit (32) do not change it; 66 and 67 are the wheel sideways (a
 *  trackpad's swipe that is not quite straight sends them between its up or down notches): no scroll. */
export function wheel(chunk: string): number {
  let n = 0;
  for (const m of chunk.matchAll(/\x1b?\[<(\d+);\d+;\d+M/g)) {
    const b = Number(m[1]) & ~(4 | 8 | 16 | 32);
    if (b === 64) n += 1;
    else if (b === 65) n -= 1;
  }
  return n;
}

/** A chunk that is only the mouse's reports (clicks and the wheel): the prompt never takes them as text. */
export const isMouse = (chunk: string) => /^(\x1b?\[<\d+;\d+;\d+[Mm])+$/.test(chunk);

/** The mouse's reports: the buttons and the wheel (DECSET 1000), a drag with a button held (1002, to select text), in
 *  SGR form (1006); and the terminal's own screen for the app (1049). */
export const MOUSE = { on: "\x1b[?1000h\x1b[?1002h\x1b[?1006h", off: "\x1b[?1006l\x1b[?1002l\x1b[?1000l" };
export const SCREEN = { on: "\x1b[?1049h\x1b[H\x1b[2J", off: "\x1b[?1049l" };

/** Ink's frame, painted over the last one instead of erased and drawn again (that erase is what flashes in many
 *  terminals): Ink writes "erase the last N lines" (or "clear the screen") and then the frame; on the full screen the
 *  frame always starts at the top, so it becomes "to the top-left corner", each line cleared after its text, the rest of
 *  the screen cleared below, all inside the terminal's synchronized update (DECSET 2026: it shows the frame at once;
 *  a terminal without it ignores it). Anything else Ink writes (the cursor's hide and show) goes as it is. */
export function paintFrame(chunk: string): string {
  const erase = /^(?:(?:\x1b\[2K(?:\x1b\[1A)?)+\x1b\[G|\x1b\[2J\x1b\[3J\x1b\[H)/;
  if (!chunk.includes("\n") && !erase.test(chunk)) return chunk;
  const body = chunk.replace(erase, "");
  if (!body) return "";
  const lines = body.replace(/\n$/, "").split("\n");
  return SYNC.on + "\x1b[H" + lines.map((l) => l + "\x1b[K").join("\r\n") + "\x1b[J" + SYNC.off;
}

export const SYNC = { on: "\x1b[?2026h", off: "\x1b[?2026l" };

/** process.stdout for Ink on the full screen: every write goes through paintFrame. */
export function screenStdout(out: NodeJS.WriteStream): NodeJS.WriteStream {
  const write = (chunk: string | Uint8Array, ...rest: unknown[]) =>
    (out.write as (c: string | Uint8Array, ...r: unknown[]) => boolean)(typeof chunk === "string" ? paintFrame(chunk) : chunk, ...rest);
  return new Proxy(out, {
    get(target, key) {
      if (key === "write") return write;
      const v = Reflect.get(target, key, target);
      return typeof v === "function" ? v.bind(target) : v;
    },
  });
}

