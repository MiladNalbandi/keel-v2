// keel TUI's own settings: what the person chose in keel TUI that it keeps for the next start (the sidebar on or off,
// whether it follows the other keel windows; /settings shows them).
// One small JSON file: KEEL_TUI_SETTINGS, else $XDG_CONFIG_HOME/keel2/tui.json, else ~/.config/keel2/tui.json. A file
// that cannot be read or written is no error: keel TUI starts with the defaults and keeps the choice while it runs.

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export type TuiSettings = {
  /** the sidebar (Ctrl+B, /sidebar): shown on a wide terminal; false hides it */
  sidebar?: boolean;
  /** per-role colour overrides, as KEEL_TUI_COLORS: { "ok": "#3cc463", "gate": "ansi:yellow" } (the env wins) */
  colors?: Record<string, string>;
  /** keel 0.18.63 follow other keel windows (/follow, /settings): on (the default) keel TUI goes to the project and
   *  KeelBot chat the dashboard or another keel TUI opens, and they follow what it opens; false keeps its own (core/keel.ts) */
  follow?: boolean;
};

export type SettingsStore = {
  get: () => TuiSettings;
  set: (patch: TuiSettings) => void;
};

/** Settings kept in memory only (tests, --script): nothing is read or written. */
export function memorySettings(init: TuiSettings = {}): SettingsStore {
  let now = { ...init };
  return { get: () => now, set: (patch) => void (now = { ...now, ...patch }) };
}

type Env = Record<string, string | undefined>;

/** Where the file is. */
export function settingsPath(env: Env = process.env): string {
  return (
    env.KEEL_TUI_SETTINGS ||
    join(env.XDG_CONFIG_HOME || join(homedir(), ".config"), "keel2", "tui.json")
  );
}

/** Settings in the person's file. */
export function fileSettings(env: Env = process.env): SettingsStore {
  const file = settingsPath(env);
  const read = (): TuiSettings | null => {
    try {
      const v = JSON.parse(readFileSync(file, "utf8"));
      return v && typeof v === "object" ? (v as TuiSettings) : null;
    } catch {
      return null;     // none yet, or not readable: the defaults
    }
  };
  let now: TuiSettings = read() ?? {};
  return {
    get: () => now,
    set: (patch) => {
      // keel 0.18.63 the file as it is now, with this change on top: another keel TUI (or this one's other store) may
      // have written another setting since this one read it
      now = { ...now, ...read(), ...patch };
      try {
        mkdirSync(dirname(file), { recursive: true });
        writeFileSync(file, JSON.stringify(now, null, 1) + "\n");
      } catch {
        /* kept while keel TUI runs */
      }
    },
  };
}
