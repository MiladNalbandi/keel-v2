// keel 0.29.2 keel keeps running when the window closes: the tray (Linux) / menu bar (macOS) icon's menu, the
// "waits for you" notifications while no window shows, and "Start keel when I log in". Pure parts, tested in
// test/unit.test.ts; main.ts wires them to Electron.
import path from "node:path";

/** What keel does now, from GET /api/projects (each project's running and waiting counts). */
export type Now = { running: number; waiting: number; items: { project: string; waiting: number }[] };

export function nowOf(projects: unknown): Now | null {
  if (!Array.isArray(projects)) return null;
  const out: Now = { running: 0, waiting: 0, items: [] };
  for (const p of projects) {
    if (!p || typeof p !== "object") continue;
    const o = p as { id?: unknown; name?: unknown; running?: unknown; waiting?: unknown };
    const r = Number(o.running) || 0;
    const w = Number(o.waiting) || 0;
    out.running += r;
    out.waiting += w;
    if (w > 0) out.items.push({ project: String(o.name ?? o.id ?? "a project"), waiting: w });
  }
  return out;
}

/** The icon menu's line about keel's work: "2 flows run · 1 waits for you" or "Nothing runs". */
export function nowLine(n: Now | null, up: boolean): string {
  if (!up) return "keel is not running";
  if (!n) return "keel runs";
  const bits: string[] = [];
  if (n.running) bits.push(`${n.running} flow${n.running === 1 ? " runs" : "s run"}`);
  if (n.waiting) bits.push(`${n.waiting} wait${n.waiting === 1 ? "s" : ""} for you`);
  return bits.length ? bits.join(" · ") : "Nothing runs";
}

/** The icon's tooltip. */
export function tooltipOf(n: Now | null, up: boolean): string {
  return `keel — ${nowLine(n, up)}`;
}

/**
 * The notifications to show for a new count: a project whose waiting count went up since the last look. The first
 * look ([before] null) shows nothing (what waited before the app opened is in the Inbox).
 */
export function newWaits(before: Now | null, now: Now): { title: string; body: string }[] {
  if (!before) return [];
  const had = new Map(before.items.map((i) => [i.project, i.waiting]));
  const out: { title: string; body: string }[] = [];
  for (const i of now.items) {
    const more = i.waiting - (had.get(i.project) ?? 0);
    if (more > 0) out.push({ title: "keel waits for you", body: `${i.project}: ${more === 1 ? "a flow waits" : `${more} flows wait`} for your answer` });
  }
  return out;
}

/** Linux "Start keel when I log in": an XDG autostart entry that opens the AppImage in the background. */
export function autostartFile(home: string, env: NodeJS.ProcessEnv): string {
  const conf = env.XDG_CONFIG_HOME && path.isAbsolute(env.XDG_CONFIG_HOME) ? env.XDG_CONFIG_HOME : path.join(home, ".config");
  return path.join(conf, "autostart", "keel.desktop");
}

export function autostartEntry(exe: string): string {
  const q = /[\s"'\\$`]/.test(exe) ? `"${exe.replace(/(["\\$`])/g, "\\$1")}"` : exe;
  return [
    "[Desktop Entry]",
    "Type=Application",
    "Name=keel",
    "Comment=keel in the background (its icon only)",
    `Exec=${q} --background`,
    "Terminal=false",
    "X-GNOME-Autostart-enabled=true",
    "",
  ].join("\n");
}

/** Opened by the login item / autostart: no window, only the icon. */
export function startsHidden(argv: string[], wasOpenedAtLogin: boolean): boolean {
  return wasOpenedAtLogin || argv.includes("--background") || argv.includes("--hidden");
}
