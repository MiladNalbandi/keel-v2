// desktop.json: what the person chose in the app (the warning, the folders, the port, the window's place).
import fs from "node:fs";
import path from "node:path";

export type Settings = {
  /** the person read "keel's agents run as you" and went on */
  accepted: boolean;
  /** the project folders keel may work in (KEEL_FOLDERS); empty: keel's demo project */
  folders: string[];
  /** keel's port, kept so open browser tabs and paired phones keep working */
  port?: number;
  bounds?: { x?: number; y?: number; width: number; height: number };
};

export const DEFAULTS: Settings = { accepted: false, folders: [] };

export function readSettings(file: string): Settings {
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8")) as Partial<Settings>;
    return {
      accepted: raw.accepted === true,
      folders: Array.isArray(raw.folders) ? raw.folders.filter((f): f is string => typeof f === "string" && path.isAbsolute(f)) : [],
      port: Number.isInteger(raw.port) && (raw.port as number) > 1023 && (raw.port as number) < 65536 ? raw.port : undefined,
      bounds: raw.bounds && typeof raw.bounds.width === "number" && typeof raw.bounds.height === "number" ? raw.bounds : undefined,
    };
  } catch {
    return { ...DEFAULTS, folders: [] };
  }
}

export function writeSettings(file: string, s: Settings): void {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(s, null, 2) + "\n", { mode: 0o600 });
  fs.renameSync(tmp, file);
}

/** Adds a folder once; a folder inside one already there adds nothing (keel finds its repos there). */
export function addFolder(folders: string[], f: string): string[] {
  const abs = path.resolve(f);
  if (folders.some((x) => abs === x || abs.startsWith(x.endsWith(path.sep) ? x : x + path.sep))) return folders;
  return [...folders, abs];
}
