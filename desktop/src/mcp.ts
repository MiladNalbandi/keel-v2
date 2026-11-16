// keel 0.29.2 keel's MCP server for the desktop app's keel: Claude Code, OpenCode or Claude Desktop start it (the
// connected session, keel's tools). The app writes <keel folder>/keel-mcp at every start: the app's own Python (inside
// the app, whose place can change: an AppImage mounts somewhere new each run) and this keel's port and token file.
import path from "node:path";
import type { Bundle, Paths } from "./paths";

export const MODES = ["--read-only", "--write", "--admin"] as const;

/** Single-quoted for sh. */
function q(s: string): string {
  return `'${s.replace(/'/g, `'\\''`)}'`;
}

export function mcpLauncherPath(paths: Paths): string {
  return path.join(paths.root, "keel-mcp");
}

/**
 * The launcher: keel_engine.mcp with the app's Python, against the app's keel at [port]. When the app is closed (the
 * Python is gone, or keel does not answer) it says so on stderr, which the MCP client shows.
 */
export function mcpLauncher(o: { bundle: Bundle; paths: Paths; port: number }): string {
  const py = o.bundle.python;
  return [
    "#!/bin/sh",
    "# keel desktop's MCP server (written by the keel app at each start; do not edit).",
    "# Claude Code:  claude mcp add keel -- \"$0\" --write      OpenCode / Claude Desktop: this file as the command",
    `PY=${q(py)}`,
    `if [ ! -x "$PY" ]; then echo "keel is not running: open the keel app (it starts keel), then try again." >&2; exit 1; fi`,
    "MODE=--read-only",
    'case "${1:-}" in --write|--admin|--read-only) MODE="$1"; shift ;; esac',
    `export KEEL_API_URL=${q(`http://127.0.0.1:${o.port}`)}`,
    `export KEEL_PUBLIC_URL=${q(`http://localhost:${o.port}`)}`,
    `export KEEL_INTERNAL_TOKEN_FILE=${q(path.join(o.paths.data, "run", "internal-token"))}`,
    `export KEEL_DATA=${q(o.paths.data)}`,
    `export KEEL_PLUGINS_IMAGE=${q(path.join(o.bundle.keel, "plugins"))}`,
    "export PYTHONDONTWRITEBYTECODE=1",
    "[ -n \"${TZ:-}\" ] || { z=$(readlink /etc/localtime 2>/dev/null | sed -n 's|.*zoneinfo/||p'); [ -n \"$z\" ] && export TZ=\"$z\"; }",
    'exec "$PY" -P -m keel_engine.mcp "$MODE" "$@"',
    "",
  ].join("\n");
}

/** What Connect Claude Code or OpenCode… shows: one command per client, for this launcher. */
export function connectText(launcher: string): { claude: string; opencode: string; desktop: string } {
  const sh = /[\s"'\\$`]/.test(launcher) ? `"${launcher.replace(/(["\\$`])/g, "\\$1")}"` : launcher;
  return {
    claude: `claude mcp add keel -- ${sh} --write`,
    opencode: JSON.stringify({ mcp: { keel: { type: "local", command: [launcher, "--write"], enabled: true } } }),
    desktop: JSON.stringify({ mcpServers: { keel: { command: launcher, args: ["--write"] } } }),
  };
}
