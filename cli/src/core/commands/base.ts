// The shared ground of keel TUI's slash commands: the Reply a command returns, the table of commands, and the little
// helpers every topic file uses. The commands themselves are by topic in this folder; ../commands.ts puts them together.

import { ApiError, errorParts, type Project, type ThreadState } from "../sdk";
import type { KeelCore } from "../keel";
import { PLAIN, type Paint, type ThemeName } from "../theme";
import table from "../../../../content/agent-clients/commands.json";
import type { ToolCall } from "../tools";

// ------------------------------------------------------------------ replies

export type BlockKind = "text" | "markdown" | "diagram" | "error" | "notice" | "user" | "ok" | "dim" | "tool";
/** `reverted` (0.18.19): part of a KeelBot answer /undo took back, drawn dim. A "tool" block is one of KeelBot's tool
 *  calls (`tool`), its output folded, or whole with `open` (Ctrl+O, a click); `hint` says how to open it. */
export type Block = { kind: BlockKind; text: string; reverted?: boolean; tool?: ToolCall; open?: boolean; hint?: string };
/** A stored chat message as a block: the person's own, KeelBot's answer, or a note (an undone turn's are reverted). */
export const messageBlock = (m: { role: string; text: string; data?: { reverted?: unknown } | null }): Block => ({
  kind: m.role === "user" ? "user" : m.role === "note" ? "dim" : "markdown", text: m.text, ...(m.data?.reverted ? { reverted: true } : {}) });
/** `search`: more text that typing in the picker matches besides the label (P1-3 /chats: the last message). */
export type Choice = { label: string; value: string; hint?: string; search?: string };
/** The choices a picker shows for what the person typed: the label or `search` contains it, in any case. */
export const pickMatches = (choices: Choice[], filter: string): Choice[] => {
  const f = filter.toLowerCase();
  return f ? choices.filter((c) => c.label.toLowerCase().includes(f) || !!c.search?.toLowerCase().includes(f)) : choices;
};
/** Pick one of these; `then` runs with the chosen value. */
/** `index`: the choice the cursor starts on (the current one), else the first. keel 0.18.9 `multi`: Space ticks
 *  choices, Enter sends them; `then` gets the ticked values as a JSON list. */
export type Pick = { title: string; choices: Choice[]; then: (value: string) => Promise<Reply> | Reply; index?: number; multi?: boolean;
  /** keel 0.18.18 Esc: what cancelling answers (a KeelBot permission card says no, so its command does not wait 10 minutes) */
  onCancel?: () => Promise<Reply> | Reply;
  /** 0.18.23 a KeelBot suggestion: what the person types that no choice matches is a new message for KeelBot, and the
   *  suggestion is dropped with this note (why it was not sent) */
  newMessage?: string };
/** The next line the person types answers this; `then` runs with it. */
export type Ask = { prompt: string; then: (text: string) => Promise<Reply> | Reply };
export type PanelName = "flow" | "agents" | "inbox";
export type Reply = { blocks: Block[]; pick?: Pick; ask?: Ask; quit?: boolean; clear?: boolean; mouse?: boolean | "toggle"; panel?: PanelName | "off"; chat?: string;
  /** 0.18.15 copy the n-th block from the end (1 = the last answer) to the person's clipboard (OSC 52) */
  copy?: number;
  /** 0.18.23 /thinking: KeelBot's thinking block opens or folds (as Ctrl+T) */
  thinking?: boolean;
  /** /sidebar: the right sidebar on, off, or the other way (as Ctrl+B) */
  sidebar?: boolean | "toggle";
  /** an agent of a flow (its job id) to follow on screen: its steps and the text it writes, live */
  follow?: string;
  /** the start box again (keel, the project, its flow, what waits): after a switch to another project */
  start?: boolean;
  /** 0.18.18 open this text in the person's editor ($VISUAL / $EDITOR); what they save comes back into the prompt */
  editor?: string;
  /** 0.18.19 this text goes into the prompt, not sent (/undo gives the undone question back) */
  prompt?: string;
  /** 0.18.19 a KeelBot answer was undone (undo) or redone (not): its question's turn on screen is drawn dim, or not any more */
  revert?: { undo: boolean; text: string };
  /** /theme: keel TUI's palette from now on (keel, dark, light, auto) */
  theme?: ThemeName;
  /** /theme with no word, or /theme colors: say the palette and the colour overrides that are active */
  themeInfo?: boolean;
  /** 0.18.6 a plugin's mode, opened: it takes the screen until Esc (core/modes.ts) */
  mode?: import("../modes").ModeSession };

export const text = (t: string): Reply => ({ blocks: [{ kind: "text", text: t }] });
export const ok = (t: string): Reply => ({ blocks: [{ kind: "ok", text: t }] });
export const fail = (t: string): Reply => ({ blocks: [{ kind: "error", text: t }] });
export const diagram = (t: string): Reply => ({ blocks: [{ kind: "diagram", text: t.replace(/\n+$/, "") }] });
export const join = (...rs: Reply[]): Reply => ({ ...Object.assign({}, ...rs), blocks: rs.flatMap((r) => r.blocks) });

export function errorReply(e: unknown): Reply {
  const p = errorParts(e);
  return fail(p.message + (p.hint ? `\n${p.hint}` : "") + (p.details?.length ? "\n" + p.details.map((d) => "  " + d).join("\n") : ""));
}

// ------------------------------------------------------------------ the registry

/** keel 0.18.63 what the app shows now, for /settings: the sidebar, the mouse, the palette, and whether it runs on the
 *  full screen (not --inline). -c has no app: /settings then shows only what keel TUI keeps in its settings. */
export type ViewState = { sidebar: boolean; mouse: boolean; theme: string; fullScreen: boolean };
/** What a command runs with: keel, the terminal's width, and the colours to write in (PLAIN: none). */
export type Ctx = { core: KeelCore; width: number; paint?: Paint; view?: ViewState };
export const P = (ctx: Ctx): Paint => ctx.paint ?? PLAIN;

/** One row of content/agent-clients/commands.json, the command table keel's terminal clients share. */
export type CommandSpec = { name: string; args?: string; summary: string; aliases?: string[]; writes: boolean; complete?: string; local?: boolean;
  group?: string };
export const TABLE = table as { version: number; commands: CommandSpec[]; keys: { key: string; does: string }[]; diagrams: string[];
  groups?: string[] };
export type Impl = { name: string; run: Command["run"]; complete?: Command["complete"] };

export type Command = Omit<CommandSpec, "complete" | "writes"> & {
  writes?: boolean;
  /** the words after the name; "" when none */
  run: (ctx: Ctx, args: string) => Promise<Reply> | Reply;
  /** completions for the argument being typed */
  complete?: (ctx: Ctx, arg: string) => string[];
};

export const needProject = (ctx: Ctx): Project => {
  const p = ctx.core.snapshot.project;
  if (!p) throw new ApiError("No project yet.", 0, "Add one in the dashboard (All projects › Add projects), then /project.");
  return p;
};
export const flowNow = (ctx: Ctx): ThreadState | null => ctx.core.snapshot.flow?.thread ?? null;

/** Every command by name and alias; ../commands.ts fills it (a topic's command can run another by name). */
export const COMMANDS_BY_NAME: Record<string, Command> = {};
