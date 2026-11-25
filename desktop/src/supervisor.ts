// Starts and stops keel: one child, `bash keel-native-start` (keel-start's loop: the engine, the Kotlin api, the Python
// api in front; a restart on exit 75), in its own process group, its output straight into logs/keel.log. When the app
// dies, keel goes on; the next start of the app finds it again (adopt) by its pid file, as keel2 does.
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { keelEnv, type Ports } from "./keelenv";
import type { Bundle, Paths } from "./paths";
import { anyFree, firstFree, isFree } from "./ports";

export const FIRST_PORT = 8180;

export type Health = { ok: boolean; engine: boolean; version?: string; native?: boolean };

export type StartRequest = {
  folders: string[];
  port?: number;
  safe?: boolean;
  pathValue: string;
  env: NodeJS.ProcessEnv;
};

function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === "EPERM";
  }
}

function groupAlive(pid: number): boolean {
  try {
    process.kill(-pid, 0);
    return true;
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === "EPERM";
  }
}

function signalGroup(pid: number, sig: NodeJS.Signals): void {
  try {
    process.kill(-pid, sig);
  } catch {
    /* gone */
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** The command line of a process, "" when there is none (keel2's native_running test). */
export function commandOf(pid: number): string {
  try {
    if (process.platform === "linux") return fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").replace(/\0/g, " ");
    return execFileSync("ps", ["-p", String(pid), "-o", "command="], { encoding: "utf8", timeout: 3000 }).trim();
  } catch {
    return "";
  }
}

export function readPorts(text: string): Ports | null {
  const get = (k: string) => Number((text.match(new RegExp(`^${k}=(\\d+)$`, "m")) || [])[1]);
  const p = { api: get("api"), engine: get("engine"), back: get("back") };
  return p.api && p.engine ? p : null;
}

export class Keel extends EventEmitter {
  child: ChildProcess | null = null;
  pid = 0;
  ports: Ports | null = null;
  token = "";
  private stopping = false;
  private watch: NodeJS.Timeout | null = null;

  constructor(
    readonly paths: Paths,
    readonly bundle: Bundle,
    readonly version: string,
  ) {
    super();
  }

  get log(): string {
    return path.join(this.paths.logs, "keel.log");
  }

  get url(): string {
    return `http://localhost:${this.ports?.api}`;
  }

  private api(p: string): string {
    return `http://127.0.0.1:${this.ports?.api}/api${p}`;
  }

  running(): boolean {
    return this.pid > 0 && alive(this.pid);
  }

  /** keel's own folders: its database, keys and logs are this person's only (0700, as keel2's). */
  private prepare(): void {
    for (const d of [this.paths.root, this.paths.data, this.paths.run, this.paths.logs]) {
      fs.mkdirSync(d, { recursive: true, mode: 0o700 });
      try {
        fs.chmodSync(d, 0o700);
      } catch {
        /* not ours to change */
      }
    }
  }

  async start(r: StartRequest): Promise<void> {
    if (this.running()) throw new Error("keel runs already");
    this.prepare();
    const want = r.port && (await isFree(r.port)) ? r.port : await firstFree(FIRST_PORT);
    const engine = await anyFree([want]);
    const back = await anyFree([want, engine]);
    this.ports = { api: want, engine, back };
    this.token = randomBytes(24).toString("hex");
    // keel-start reads it from this file (0600, in the run folder agents' commands never see)
    const tokenFile = path.join(this.paths.run, "token");
    fs.rmSync(tokenFile, { force: true });
    fs.writeFileSync(tokenFile, this.token, { mode: 0o600 });
    if (fs.existsSync(this.log)) fs.renameSync(this.log, `${this.log}.1`);
    const fd = fs.openSync(this.log, "a", 0o600);
    const env = keelEnv(r.env, {
      bundle: this.bundle,
      paths: this.paths,
      ports: this.ports,
      folders: r.folders,
      tokenFile,
      pathValue: r.pathValue,
      safe: r.safe,
      version: this.version,
    });
    const script = path.join(this.bundle.keel, "keel-native-start");
    const child = spawn("/bin/bash", [script], {
      detached: true, // its own process group: a stop reaches every process keel started
      stdio: ["ignore", fd, fd],
      env,
      cwd: this.paths.data,
    });
    fs.closeSync(fd);
    this.child = child;
    this.pid = child.pid ?? 0;
    this.stopping = false;
    fs.writeFileSync(path.join(this.paths.run, "keel.pid"), `${this.pid}\n`, { mode: 0o600 });
    fs.writeFileSync(path.join(this.paths.run, "ports"), `api=${want}\nengine=${engine}\nback=${back}\n`, { mode: 0o600 });
    child.on("exit", (code, signal) => this.exited(code, signal));
  }

  private exited(code: number | null, signal: NodeJS.Signals | null): void {
    const pid = this.pid;
    this.child = null;
    this.pid = 0;
    if (this.watch) clearInterval(this.watch);
    this.watch = null;
    try {
      fs.rmSync(path.join(this.paths.run, "keel.pid"), { force: true });
    } catch {
      /* fine */
    }
    // whatever keel left behind in its group (a dev server an agent started)
    if (pid) signalGroup(pid, "SIGTERM");
    if (!this.stopping) this.emit("crashed", code ?? signal);
    this.emit("stopped");
  }

  /** A keel this app started before it died: same version, alive, answering. */
  async adopt(): Promise<boolean> {
    let pid = 0;
    try {
      pid = Number(fs.readFileSync(path.join(this.paths.run, "keel.pid"), "utf8").trim());
    } catch {
      return false;
    }
    if (!pid || !alive(pid) || !commandOf(pid).includes("keel-native-start")) return false;
    let ports: Ports | null = null;
    try {
      ports = readPorts(fs.readFileSync(path.join(this.paths.run, "ports"), "utf8"));
      this.token = fs.readFileSync(path.join(this.paths.data, "run", "internal-token"), "utf8").trim();
    } catch {
      return false;
    }
    if (!ports || !this.token) return false;
    this.pid = pid;
    this.ports = ports;
    const h = await this.health();
    if (!h?.ok || h.version !== this.version) {
      // another version (the app was updated) or not answering: start it fresh
      await this.stop();
      return false;
    }
    this.watch = setInterval(() => {
      if (!alive(pid)) this.exited(null, null);
    }, 2000);
    return true;
  }

  async health(): Promise<Health | null> {
    if (!this.ports) return null;
    try {
      const r = await fetch(this.api("/health"), { signal: AbortSignal.timeout(4000) });
      if (!r.ok) return null;
      return (await r.json()) as Health;
    } catch {
      return null;
    }
  }

  /** Waits until keel and its engine answer; false when keel stopped or `ms` passed. */
  async waitUp(ms = 180_000): Promise<boolean> {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      if (!this.running()) return false;
      const h = await this.health();
      if (h?.ok && h.engine) return true;
      await sleep(1000);
    }
    return false;
  }

  /** A call with the internal token (what keel2 sends: X-Keel-Token). */
  async call(method: string, p: string, body?: unknown): Promise<Response> {
    return fetch(this.api(p), {
      method,
      headers: { "X-Keel-Token": this.token, ...(body === undefined ? {} : { "content-type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(15_000),
    });
  }

  /** A one-time sign-in link (60 s) for the window or a browser: keel2 open's. */
  async signInUrl(): Promise<string> {
    const r = await this.call("POST", "/web/code");
    if (!r.ok) throw new Error(`keel did not give a sign-in code (${r.status})`);
    const j = (await r.json()) as { path?: string };
    if (!j.path || !j.path.startsWith("/")) throw new Error("keel gave no sign-in path");
    return `${this.url}${j.path}`;
  }

  /** Agents working right now; null when keel did not say (then nobody can tell: ask). */
  async busyAgents(): Promise<number | null> {
    try {
      const r = await this.call("GET", "/jobs?status=running&limit=50");
      if (!r.ok) return null;
      const jobs = (await r.json()) as unknown;
      if (!Array.isArray(jobs)) return null;
      return jobs.filter((j) => j && typeof j === "object" && (j as { agent?: unknown }).agent).length;
    } catch {
      return null;
    }
  }

  /** Every git repo in the folders becomes a keel project (keel2 register_added). */
  async register(folders: string[]): Promise<string[]> {
    const known = new Set<string>();
    try {
      const r = await this.call("GET", "/projects");
      for (const p of ((await r.json()) as { root?: string }[]) || []) if (p.root) known.add(p.root);
    } catch {
      /* keel answers below */
    }
    const isRepo = (d: string) => fs.existsSync(path.join(d, ".git"));
    const roots: string[] = [];
    for (const f of folders) {
      if (isRepo(f)) roots.push(f);
      else {
        try {
          for (const e of fs.readdirSync(f, { withFileTypes: true }))
            if (e.isDirectory() && !e.name.startsWith(".") && isRepo(path.join(f, e.name))) roots.push(path.join(f, e.name));
        } catch {
          /* not readable */
        }
      }
    }
    const added: string[] = [];
    for (const root of roots) {
      if (known.has(root)) continue;
      try {
        const r = await this.call("POST", "/projects", { root });
        if (r.ok) added.push(root);
      } catch {
        /* the next start tries again */
      }
    }
    return added;
  }

  /**
   * TERM to keel-start (it stops the apis and the engine, 20 s at most), then whatever is left in its group; KILL
   * after `ms`.
   */
  async stop(ms = 25_000): Promise<void> {
    const pid = this.pid;
    if (!pid) return;
    this.stopping = true;
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      /* gone already */
    }
    const end = Date.now() + ms;
    while (alive(pid) && Date.now() < end) await sleep(200);
    signalGroup(pid, "SIGTERM");
    const end2 = Date.now() + 3000;
    while (groupAlive(pid) && Date.now() < end2) await sleep(200);
    if (groupAlive(pid)) signalGroup(pid, "SIGKILL");
    if (alive(pid)) {
      try {
        process.kill(pid, "SIGKILL");
      } catch {
        /* gone */
      }
    }
    if (!this.child && this.pid) this.exited(null, null); // an adopted keel has no exit event
    const end3 = Date.now() + 3000;
    while (this.pid && Date.now() < end3) await sleep(100);
  }

  logTail(lines = 40): string {
    try {
      const t = fs.readFileSync(this.log, "utf8");
      return t.split("\n").slice(-lines - 1).join("\n").trim();
    } catch {
      return "";
    }
  }
}
