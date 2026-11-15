// The app's own log: logs/desktop.log (keel's own output is logs/keel.log). Kept small: 1 MB, then desktop.log.1.
import fs from "node:fs";
import path from "node:path";

let file = "";

export function openLog(dir: string): void {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  file = path.join(dir, "desktop.log");
  try {
    if (fs.statSync(file).size > 1_000_000) fs.renameSync(file, `${file}.1`);
  } catch {
    /* none yet */
  }
}

export function log(...parts: unknown[]): void {
  const line = `${new Date().toISOString()} ${parts.map((p) => (p instanceof Error ? p.stack || p.message : String(p))).join(" ")}\n`;
  if (process.env.KEEL_DESKTOP_SMOKE === "1") process.stdout.write(line);
  if (!file) return;
  try {
    fs.appendFileSync(file, line, { mode: 0o600 });
  } catch {
    /* a full disk: keel says so itself */
  }
}
