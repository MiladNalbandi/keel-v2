// keel TUI's slash commands: names, arguments, help text, what each one does and how its answer reads. No UI here:
// a command returns a Reply (blocks of text, and maybe a choice to pick or a question to answer next), so keel TUI's
// Ink app, an xal plugin or an OpenTUI front end run the very same commands.

import { setCommand } from "./transport";
import { PLAIN, type Paint } from "./theme";
import { PARKED } from "./version";
import { COMMANDS_BY_NAME, TABLE, errorReply, fail, P, text, type Command, type Ctx, type Impl, type Reply } from "./commands/base";
import { FLOW_IMPLS } from "./commands/flow";
import { SKILLS_IMPLS } from "./commands/skills";
import { AGENTS_IMPLS } from "./commands/agents";
import { SESSION_IMPLS, shellRun } from "./commands/session";

// what this file always exported, now written in ./commands/ by topic
export { messageBlock, pickMatches, text, ok, fail, diagram, errorReply, TABLE, COMMANDS_BY_NAME } from "./commands/base";
export type { BlockKind, Block, Choice, Pick, Ask, PanelName, Reply, Ctx, CommandSpec, Command } from "./commands/base";
export { draw, questionChain, pairAnswers } from "./commands/flow";
export type { Question, Answers } from "./commands/flow";
export { setChanged } from "./commands/agents";
export { THEME_HELP, dashboardUrl } from "./commands/session";



/** What each command does; its name, arguments, help text and aliases are in the shared table (TABLE). The topics are in ./commands/. */
const IMPLS: Impl[] = [
  { name: "help", run: (ctx) => text(helpText(P(ctx))) },
  ...FLOW_IMPLS, ...SKILLS_IMPLS, ...AGENTS_IMPLS, ...SESSION_IMPLS,
];

/** keel's commands: the shared table's words with what each one does here. */
export const COMMANDS: Command[] = TABLE.commands.map((spec) => {
  const impl = IMPLS.find((i) => i.name === spec.name);
  if (!impl) throw new Error(`content/agent-clients/commands.json names /${spec.name}, but keel TUI does not have it`);
  const { complete: _from, ...words } = spec;      // `complete` in the table names the source; here it is the function
  return { ...words, run: impl.run, complete: impl.complete };
});
for (const i of IMPLS) if (!TABLE.commands.some((c) => c.name === i.name)) throw new Error(`/${i.name} is missing from content/agent-clients/commands.json`);

Object.assign(COMMANDS_BY_NAME, Object.fromEntries(COMMANDS.flatMap((c) => [[c.name, c], ...(c.aliases ?? []).map((a) => [a, c])])));

// ---- what keel's plugins add (core/plugins.ts): looked up after keel's own commands ----
let pluginCmds: Command[] = [];
export function setPluginCommands(cmds: Command[]) {
  pluginCmds = cmds.filter((c) => !COMMANDS_BY_NAME[c.name]);
}
export const pluginCommandList = () => pluginCmds;
/** a plugin's command that is off in this project → the plugin's title (so /name explains instead of "no command") */
let offCmds: Record<string, string> = {};
export function setOffPluginCommands(off: { title: string; commands: string[] }[]) {
  offCmds = Object.fromEntries(off.flatMap((p) => p.commands.filter((c) => !COMMANDS_BY_NAME[c]).map((c) => [c, p.title])));
}
const offText = (title: string) =>
  `The ${title} plugin is off for this project. Turn it on in Settings › Plugins (This project or All projects), then open keel TUI again.`;
/** keel's command, or a plugin's */
export const lookup = (name: string): Command | null => COMMANDS_BY_NAME[name] ?? pluginCmds.find((c) => c.name === name) ?? null;

/** /help: the commands by group (the shared table's groups), names in the accent colour, arguments in a second one. */
export function helpText(c: Paint = PLAIN): string {
  const w = Math.max(...[...COMMANDS, ...pluginCmds].map((x) => (x.name + (x.args ? " " + x.args : "")).length));
  const cut = Math.min(w, 34);
  const groups = TABLE.groups ?? [...new Set(COMMANDS.map((x) => x.group ?? ""))];
  const out = [c.dim(PARKED), "", `Type a question for ${c.accent("KeelBot")}, or a command:`];
  for (const g of groups) {
    const cmds = [...COMMANDS, ...pluginCmds].filter((x) => (x.group ?? "") === g);
    if (!cmds.length) continue;
    out.push("", c.bold(c.gate(g)));
    for (const x of cmds) {
      const plainHead = `/${x.name}${x.args ? " " + x.args : ""}`;
      const head = c.bold(c.accent(`/${x.name}`)) + (x.args ? " " + c.attr(x.args) : "");
      const pad = " ".repeat(Math.max(1, cut + 3 - plainHead.length));
      out.push(plainHead.length > cut + 1 ? `  ${head}\n  ${" ".repeat(cut + 3)}${c.dim(x.summary)}` : `  ${head}${pad}${c.dim(x.summary)}`);
    }
  }
  out.push("", c.bold(c.gate("Keys")), "  " + TABLE.keys.map((k) => `${c.bold(c.accent(k.key))} ${c.dim(k.does)}`).join(c.faint(" · ")),
    "", c.dim("KeelBot's own commands (/gate, /where …) go to KeelBot as they are."));
  return out.join("\n");
}

// ------------------------------------------------------------------ parse, complete, run

export type Parsed = { kind: "command"; name: string; args: string; command: Command | null } | { kind: "chat"; text: string } | { kind: "empty" };

export function parse(input: string): Parsed {
  const t = input.trim();
  if (!t) return { kind: "empty" };
  if (!t.startsWith("/") || t.startsWith("//")) return { kind: "chat", text: t.startsWith("//") ? t.slice(1) : t };
  const m = /^\/([\w?-]*)\s*([\s\S]*)$/.exec(t)!;
  const name = m[1].toLowerCase();
  return { kind: "command", name, args: m[2] ?? "", command: lookup(name) };
}

export type Completion = { value: string; label: string; hint?: string };

/** What Tab offers for the line typed so far: command names, then each command's argument. */
export function complete(ctx: Ctx, input: string, extra: { name: string; description: string }[] = []): Completion[] {
  if (!input.startsWith("/")) return [];
  const m = /^\/([\w?-]*)(\s+([\s\S]*))?$/.exec(input);
  if (!m) return [];
  const [, name, space, arg] = m;
  if (space === undefined) {
    const mine = [...COMMANDS, ...pluginCmds].filter((c) => c.name.startsWith(name.toLowerCase()))
      .map((c) => ({ value: `/${c.name}${c.args ? " " : ""}`, label: `/${c.name}${c.args ? " " + c.args : ""}`, hint: c.summary }));
    const off = Object.entries(offCmds).filter(([c]) => c.startsWith(name.toLowerCase()) && !lookup(c))
      .map(([c, title]) => ({ value: `/${c}`, label: `/${c}`, hint: `${title} plugin · off in this project` }));
    const bot = extra.filter((c) => c.name.startsWith(name) && !COMMANDS_BY_NAME[c.name])
      .map((c) => ({ value: `/${c.name} `, label: `/${c.name}`, hint: `KeelBot · ${c.description}` }));
    return [...mine, ...off, ...bot];
  }
  const cmd = lookup(name.toLowerCase());
  if (!cmd?.complete) return [];
  return cmd.complete(ctx, arg ?? "").map((v) => ({ value: `/${cmd.name} ${v}`, label: v }));
}

/** Run one line the person typed. Plain text (and KeelBot's own commands) asks KeelBot: the Reply says so in `chat`. */
export async function run(ctx: Ctx, input: string, keelbot: { name: string }[] = []): Promise<Reply> {
  if (input.trimStart().startsWith("!")) return shellRun(ctx, input.trim().slice(1).trim());
  const p = parse(input);
  if (p.kind === "empty") return { blocks: [] };
  if (p.kind === "chat") return { blocks: [], chat: p.text };
  if (!p.command) {
    if (keelbot.some((c) => c.name === p.name)) return { blocks: [], chat: input.trim() };
    if (offCmds[p.name]) return fail(offText(offCmds[p.name]));
    return fail(`No command /${p.name}. /help lists them.`);
  }
  setCommand(`/${p.command.name}`);
  try {
    return await p.command.run(ctx, p.args);
  } catch (e) {
    return errorReply(e);
  } finally {
    setCommand("");
  }
}

/** Run what a Pick or an Ask hands back, with the command's name still on its requests. */
export async function follow(name: string, fn: () => Promise<Reply> | Reply): Promise<Reply> {
  setCommand(name);
  try {
    return await fn();
  } catch (e) {
    return errorReply(e);
  } finally {
    setCommand("");
  }
}
