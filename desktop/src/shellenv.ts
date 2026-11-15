// The person's own PATH. An app opened from the Dock, Finder or a desktop menu does not get the PATH of the person's
// shell (no Homebrew, nvm, mise, ~/.local/bin): keel's agents would not find npm, node, uv or mvn. So the app asks the
// login shell once, at start, and keel runs with that PATH.
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const MARK = "__KEEL_PATH__";

/** The PATH between the two marks in a login shell's output (its start files may print other lines around it). */
export function parseMarked(out: string): string | null {
  const a = out.indexOf(MARK);
  if (a < 0) return null;
  const b = out.indexOf(MARK, a + MARK.length);
  if (b < 0) return null;
  const p = out.slice(a + MARK.length, b).trim();
  return p.includes("/") ? p : null;
}

/** Runs `$SHELL -ilc` and reads its PATH; null when it fails or takes longer than `ms`. */
export function loginShellPath(shell: string | undefined, env: NodeJS.ProcessEnv, ms = 5000): Promise<string | null> {
  const sh = shell && path.isAbsolute(shell) && fs.existsSync(shell) ? shell : "/bin/bash";
  return new Promise((resolve) => {
    let out = "";
    let done = false;
    const finish = (v: string | null) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(v);
    };
    let child;
    try {
      child = spawn(sh, ["-ilc", `printf '${MARK}%s${MARK}' "$PATH"`], {
        stdio: ["ignore", "pipe", "ignore"],
        env: { ...env, KEEL_DESKTOP_SHELL_PROBE: "1" },
        detached: true,
      });
    } catch {
      resolve(null);
      return;
    }
    const timer = setTimeout(() => {
      try {
        if (child.pid) process.kill(-child.pid, "SIGKILL");
      } catch {
        /* gone */
      }
      finish(null);
    }, ms);
    child.stdout?.on("data", (d: Buffer) => {
      out += d.toString("utf8");
      if (out.length > 1_000_000) out = out.slice(-100_000);
    });
    child.on("error", () => finish(null));
    child.on("close", () => finish(parseMarked(out)));
  });
}

/**
 * The person's PATH first, then the rest of the inherited one, then the app's own tools (rg) last: theirs win. What the
 * AppImage put on the inherited PATH (its mount, `appDir`) is left out: those folders go away with the app.
 */
export function mergePath(login: string | null, inherited: string | undefined, appBin: string, appDir?: string): string {
  const seen = new Set<string>();
  const out: string[] = [];
  const inApp = (p: string) => !!appDir && (p === appDir || p.startsWith(appDir.endsWith("/") ? appDir : appDir + "/"));
  for (const part of [login ?? "", inherited ?? ""].join(":").split(":")) {
    if (!part || seen.has(part) || inApp(part)) continue;
    seen.add(part);
    out.push(part);
  }
  if (!seen.has(appBin)) out.push(appBin);
  return out.join(":");
}

/** Finds a program on a PATH (an executable file). */
export function which(name: string, pathValue: string): string | null {
  for (const dir of pathValue.split(":")) {
    if (!dir) continue;
    const f = path.join(dir, name);
    try {
      fs.accessSync(f, fs.constants.X_OK);
      if (fs.statSync(f).isFile()) return f;
    } catch {
      /* not here */
    }
  }
  return null;
}
