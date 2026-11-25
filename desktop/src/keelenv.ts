// The environment keel-native-start gets from the app: keel2 start --native's (keel2 native_start), with the app's own
// Python and Java, and the person's PATH.
import os from "node:os";
import path from "node:path";
import type { Bundle, Paths } from "./paths";

export type Ports = { api: number; engine: number; back: number };

export type StartOptions = {
  bundle: Bundle;
  paths: Paths;
  ports: Ports;
  folders: string[];
  /** the file that holds keel's internal token (0.41.2: a file, never a variable: other programs of this user can read
   * a process's environment, `ps eww`) */
  tokenFile: string;
  /** the person's PATH (shellenv.mergePath) */
  pathValue: string;
  /** keel2 restart --safe: only the plugins that came with keel */
  safe?: boolean;
  version: string;
};

/**
 * What the app itself runs in must not reach keel and its agents: an AppImage's mount (APPDIR, its LD_LIBRARY_PATH),
 * Electron's and Chromium's own settings.
 */
export function cleanEnv(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const out: NodeJS.ProcessEnv = {};
  const appdir = env.APPDIR;
  for (const [k, v] of Object.entries(env)) {
    if (v === undefined) continue;
    if (/^(ELECTRON_|CHROME_|ORIGINAL_XDG_)/.test(k)) continue;
    if (["APPDIR", "APPIMAGE", "ARGV0", "OWD", "KEEL_DESKTOP_SHELL_PROBE"].includes(k)) continue;
    if (appdir && v.includes(appdir)) continue;
    // keel's own settings come from the app only (a KEEL_* in the person's environment is for keel2)
    if (k.startsWith("KEEL_") && k !== "KEEL_DESKTOP_HOME") continue;
    out[k] = v;
  }
  return out;
}

export function keelEnv(base: NodeJS.ProcessEnv, o: StartOptions): NodeJS.ProcessEnv {
  const env = cleanEnv(base);
  const [workspace] = o.folders;
  env.PATH = o.pathValue;
  // macOS gives an app no LANG: Python would read and write files as ASCII
  if (!env.LANG && !env.LC_ALL && !env.LC_CTYPE) env.LANG = "en_US.UTF-8";
  Object.assign(env, {
    KEEL_APP: o.bundle.keel,
    KEEL_DATA: o.paths.data,
    KEEL_FOLDERS: o.folders.join(":"),
    KEEL_PORT: String(o.ports.api),
    KEEL_ENGINE_PORT: String(o.ports.engine),
    KEEL_BACK_PORT: String(o.ports.back),
    KEEL_INTERNAL_TOKEN_FILE: o.tokenFile,
    // what agents' commands never see besides keel's data (engine tools/agent_sandbox.py): the app's run and logs folders
    KEEL_HIDE: [o.paths.run, o.paths.logs].join(":"),
    KEEL_PLUGINS: o.safe ? "image" : "on",
    KEEL_BIND: "127.0.0.1",
    KEEL_NAME: "keel-desktop",
    KEEL_HOST_NAME: os.hostname().replace(/\.local$/, ""),
    KEEL_DESKTOP: "1",
    KEEL_DESKTOP_VERSION: o.version,
    // the app's own engine, Python api, Python and Java (docker/keel-native-start, docker/keel-start KEEL_JAVA): the
    // app's Python and Java are never on the agents' PATH
    KEEL_ENGINE_EXE: o.bundle.engine,
    KEEL_FRONT: "1",
    KEEL_FRONT_EXE: o.bundle.front,
    KEEL_PYTHON: o.bundle.python,
    // GitHub Copilot (your plan): the app's own Copilot runtime; the app's files are read-only, so no download
    COPILOT_CLI_PATH: o.bundle.copilot,
    COPILOT_SKIP_CLI_DOWNLOAD: "1",
    // 0.31 the code graph agents search before they read whole files (the Graph plugin): the app's own CodeGraph
    KEEL_CODEGRAPH_BIN: o.bundle.codegraph,
    KEEL_MCP_PYTHON: o.bundle.python,
    KEEL_API_JAR: path.join(o.bundle.keel, "api.jar"),
    KEEL_JAVA: o.bundle.java,
    // the app's files are read-only (an AppImage, a signed macOS app): they were compiled when the app was built
    PYTHONDONTWRITEBYTECODE: "1",
  });
  // no folder: keel makes its demo project (engine demo/, a KEEL_WORKSPACE that is empty); keel-start needs it set
  env.KEEL_WORKSPACE = workspace ?? "";
  if (workspace) {
    env.KEEL_HOST_WORKSPACE = workspace;
    env.KEEL_PROJECT_NAME = path.basename(workspace);
  }
  return env;
}
