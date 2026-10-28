// Session and shell commands: the project, budget and dashboard, the screen (panel, sidebar, mouse, theme), following
// the other keel windows and /settings (0.18.63), `!command`.

import { api, type Project } from "../sdk";
import * as fmt from "../format";
import { parseThemeName, type ThemeName } from "../theme";
import { P, errorReply, fail, join, needProject, ok, text } from "./base";
import type { Ctx, Impl, PanelName, Reply } from "./base";

const PANELS = ["flow", "agents", "inbox", "off"] as const;

export const THEME_HELP = [
  "/theme keel (the default), /theme dark, /theme light or /theme auto (follow the background) sets the palette (KEEL_TUI_THEME sets it at the start).",
  "Your own colours stay on top of any palette: KEEL_TUI_COLORS=\"ok=#3cc463,gate=ansi:yellow\" or a `colors` object in",
  "~/.config/keel2/tui.json (the env wins). Values: #rgb, #rrggbb, an ANSI colour name or ansi:<name>, with flags: ok=#3cc463+bold (+underline +italic +dim; ok=+bold keeps the colour; +nobold). Roles: person, keelbot,",
  "heading, accent, gate (waiting), question, option, recommended, chosen, suggest, hint, detail, meta, border, ok (success),",
  "warn (warning), bad (error), code, path, link, run, add, del. NO_COLOR turns all colour off.",
];

/** keel 0.18.63 what following the other keel windows means, on and off (/follow, /settings). */
export function followText(on: boolean): string {
  return on
    ? "following the other keel windows: this window goes to the project and KeelBot chat the dashboard or another keel TUI opens, and they follow what you open here"
    : "this window keeps its own project and KeelBot chat: it follows no other keel window, and what you open here moves none of them";
}

/** keel 0.18.63 /settings: keel TUI's settings in a picker, each with its value now; Enter changes the marked one.
 *  Follow and the sidebar are kept for the next start (settings.ts); the mouse and the palette are for this run. */
function settingsPick(ctx: Ctx): Reply {
  const core = ctx.core;
  const v = ctx.view;
  const onOff = (b: boolean) => (b ? "on" : "off");
  const rows: { label: string; value: string; now: string; hint: string }[] = [
    { label: "Follow other keel windows", value: "follow", now: onOff(core.follows),
      hint: "the project and KeelBot chat move with the dashboard and other keel TUIs, both ways (/follow); kept" },
    { label: "Sidebar", value: "sidebar", now: onOff(v ? v.sidebar : core.settings.get().sidebar !== false),
      hint: `on a terminal 120 columns wide or more, full screen (Ctrl+B, /sidebar); kept` },
    ...(v ? [
      { label: "Mouse", value: "mouse", now: onOff(v.mouse), hint: "the wheel scrolls, a drag copies (/mouse); this run (--no-mouse at the start)" },
      { label: "Theme", value: "theme", now: v.theme, hint: "keel, dark, light or auto (/theme); this run (KEEL_TUI_THEME at the start)" },
    ] : []),
  ];
  const w = Math.max(...rows.map((r) => r.label.length)) + 2;
  return { blocks: [], pick: {
    title: "keel TUI settings (Enter changes the marked one, Esc leaves)",
    choices: rows.map((r) => ({ label: `${r.label.padEnd(w)}${r.now}`, value: r.value, hint: r.hint })),
    then: (which): Reply => {
      if (which === "follow") {
        const on = !core.follows;
        core.setFollow(on);
        return { blocks: [{ kind: "ok", text: `follow ${onOff(on)}: ${followText(on)}` }] };
      }
      if (which === "sidebar") return { blocks: [], sidebar: "toggle" };
      if (which === "mouse") return { blocks: [], mouse: "toggle" };
      if (which === "theme") return { blocks: [], pick: { title: "Theme", index: Math.max(0, THEMES.indexOf(v?.theme as ThemeName)),
        choices: THEMES.map((t) => ({ label: t + (t === v?.theme ? " (now)" : ""), value: t })),
        then: (t) => ({ blocks: [], theme: t as ThemeName }) } };
      return fail(`No setting ${which}.`);
    },
  } };
}
const THEMES: ThemeName[] = ["keel", "dark", "light", "auto"];

export function dashboardUrl(ctx: Ctx, view: string): string {
  const base = ctx.core.snapshot.publicUrl;
  const pid = ctx.core.pid;
  if (view === "inbox") return `${base}/#/inbox`;
  if (!pid) return base;
  const path = { flow: "flow", map: "map", er: "map/er", keelbot: "keelbot" }[view] ?? view;
  return `${base}/#/projects/${encodeURIComponent(pid)}/${path}`;
}


/** The most of a `!command`'s output the transcript shows (KeelBot gets up to 8,000 characters of it). */
const SHELL_LINES = 200;

/** 0.18.18 `!command`: keel runs it in the chat's folder through its guard; the output is a block, kept for the next
 *  question to KeelBot. `!` alone drops a kept output. */
export async function shellRun(ctx: Ctx, command: string): Promise<Reply> {
  const p = P(ctx);
  if (!command) {
    if (!ctx.core.shell) return text("!<command> runs a shell command in the project folder through keel's guard, for example !git status.");
    const was = ctx.core.shell.command;
    ctx.core.shell = null;
    return { blocks: [{ kind: "dim", text: `  the output of !${was} is dropped: your next question goes without it` }] };
  }
  try {
    const r = await ctx.core.runShell(command);
    if (r.refused) return { blocks: [{ kind: "error", text: `refused: ${command}\n${r.refused}` }] };
    const lines = r.output.replace(/\n+$/, "").split("\n");
    const shown = lines.length > SHELL_LINES ? [`… ${lines.length - SHELL_LINES} lines above`, ...lines.slice(-SHELL_LINES)] : lines;
    return { blocks: [
      { kind: "text", text: [`${p.bold(`$ ${command}`)}`, ...(r.output.trim() ? shown : [p.dim("(no output)")]),
        r.exit === 0 ? p.ok("exit 0") : p.warn(`exit ${r.exit}`)].join("\n") },
      { kind: "dim", text: "  your next question to KeelBot gets this output (! alone drops it)" },
    ] };
  } catch (e) {
    return errorReply(e);
  }
}


export const SESSION_IMPLS: Impl[] = [
  {
    name: "project",
    complete: (ctx, a) => ctx.core.snapshot.projects.map((p) => p.id).filter((x) => x.startsWith(a)),
    run: async (ctx, a) => {
      const name = a.trim();
      // 0.18.14 a short card of the project, not keel's whole start box again (the logo printed once per switch)
      const switched = (id: string): Reply => {
        const { project: p, flow, inbox } = ctx.core.snapshot;
        const t = flow?.thread;
        return { blocks: [
          { kind: "ok", text: `now on project ${p?.name || id}${p?.branch ? ` · branch ${p.branch}` : ""}` },
          { kind: "dim", text: [
            ...(t ? [`  flow     ${t.workflow_id} "${t.title}" · ${t.status}${t.current ? ` at ${t.current}` : ""}`] : []),
            // keel 0.18.55 a project keel does not know yet (with no flow, the next line says it)
            ...(t && fmt.initHint(p, t) ? [`  setup    ${fmt.initHint(p, t)}`] : []),
            `  waiting  ${fmt.waitingBrief(inbox, p)}`,
            `  next     ${fmt.next(p, flow).split("\n")[0]}`,
          ].join("\n") },
        ] };
      };
      if (name) return (await ctx.core.useProject(name)) ? switched(ctx.core.snapshot.project!.id)
        : fail(`No project "${name}". keel knows: ${ctx.core.snapshot.projects.map((p) => p.id).join(", ") || "none"}`);
      await ctx.core.loadProjects().catch(() => undefined);
      const ps = ctx.core.snapshot.projects;
      if (!ps.length) return fail("keel has no project yet. Add one in the dashboard (All projects › Add projects).");
      const cur = ctx.core.snapshot.project?.id;
      // the cursor starts on the project you are in, so Enter keeps it and one ↓ is the next one
      return { blocks: [], pick: { title: "Project (Enter keeps the one marked now)", index: Math.max(0, ps.findIndex((p) => p.id === cur)),
        choices: ps.map((p) => ({ label: p.id + (p.id === cur ? " (now)" : ""), value: p.id,
          hint: `${p.branch || "-"} · ${p.flow ? `${p.flow} ${p.phase}` : "no flow"}${p.waiting ? ` · ${p.waiting} waiting` : ""}` })),
        then: async (v) => (v === cur ? text(`Still on ${v}.`) : (await ctx.core.useProject(v)) ? switched(v) : fail(`No project ${v}`)) } };
    },
  },
  { name: "budget", run: async (ctx) => text(fmt.budget(await api.budgetNow(needProject(ctx).id), P(ctx))) },
  {
    name: "dashboard",
    complete: (_c, a) => ["flow", "map", "er", "keelbot", "inbox"].filter((x) => x.startsWith(a)),
    run: (ctx, a) => text(dashboardUrl(ctx, a.trim() || "flow") + "\nOpen it in your browser: the same keel, the same flows and chats."),
  },
  {
    name: "panel",
    complete: (_c, a) => PANELS.filter((x) => x.startsWith(a)),
    run: (_c, a) => {
      const p = a.trim() as PanelName | "off";
      if (!PANELS.includes(p)) return text("/panel flow, /panel agents, /panel inbox or /panel off");
      return { blocks: [], panel: p };
    },
  },
  { name: "clear", run: () => ({ blocks: [], clear: true }) },
  { name: "copy", run: (_ctx, a) => {
    const n = a.trim() ? Number(a.trim()) : 1;
    if (!Number.isInteger(n) || n < 1) return fail("/copy copies the last answer; /copy 2 the block before it, and so on.");
    return { blocks: [], copy: n };
  } },
  { name: "thinking", run: () => ({ blocks: [], thinking: true }) },
  { name: "sidebar", run: (_ctx, a) => {
    const w = a.trim().toLowerCase();
    if (w && w !== "on" && w !== "off") return fail("/sidebar on or /sidebar off (no word switches it, as Ctrl+B).");
    return { blocks: [], sidebar: w ? w === "on" : "toggle" };
  } },
  // keel 0.18.63 this window follows the other keel windows (the shared focus), or keeps its own project and chat
  { name: "follow", complete: (_c, a) => ["on", "off"].filter((x) => x.startsWith(a)), run: (ctx, a) => {
    const w = a.trim().toLowerCase();
    if (!w) return { blocks: [{ kind: "ok", text: `follow ${ctx.core.follows ? "on" : "off"}: ${followText(ctx.core.follows)}` },
      { kind: "dim", text: `  /follow ${ctx.core.follows ? "off keeps this window's own" : "on follows the other windows again"} (kept for the next start; /settings shows it)` }] };
    if (w !== "on" && w !== "off") return fail("/follow on or /follow off (no word says which it is now).");
    ctx.core.setFollow(w === "on");
    return { blocks: [{ kind: "ok", text: `follow ${w}: ${followText(w === "on")}` },
      { kind: "dim", text: w === "on" ? "  kept for the next start; nothing moves now: the next project or chat opened here or elsewhere is shared again"
        : "  kept for the next start (/follow on joins the other windows again)" }] };
  } },
  { name: "settings", run: (ctx) => settingsPick(ctx) },
  { name: "mouse", run: (_ctx, a) => {
    const w = a.trim().toLowerCase();
    if (w && w !== "on" && w !== "off") return fail("/mouse on or /mouse off (no word switches it).");
    return { blocks: [], mouse: w ? w === "on" : "toggle" };
  } },
  { name: "theme", run: (_ctx, a) => {
    // 0.18.26 no word (or "colors"): the palette and the per-role overrides (KEEL_TUI_COLORS) that are active, with the help
    if (!a.trim() || a.trim().toLowerCase() === "colors") return { blocks: [], themeInfo: true };
    const name = parseThemeName(a);
    if (!name) return fail("/theme keel, /theme dark, /theme light, /theme auto or /theme colors.");
    return { blocks: [], theme: name };
  } },
  // 0.18.18 write the prompt in the person's own editor: the words after /editor (or nothing) open in it, what they
  // save comes back into the prompt to read and send (Ctrl+X E opens the prompt as it is)
  { name: "editor", run: (_ctx, a) => ({ blocks: [], editor: a }) },
  { name: "quit", run: () => ({ blocks: [], quit: true }) },
];
