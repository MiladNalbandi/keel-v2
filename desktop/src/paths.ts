// Where keel desktop keeps keel's data and logs, and where the app's own keel and runtimes are.
import path from "node:path";

export type Paths = {
  /** keel desktop's folder: data/, run/, logs/ (Linux), desktop.json */
  root: string;
  /** KEEL_DATA: keel.db, checkpoints.db, master.key, run/web-key, plugins/, demo/ */
  data: string;
  /** keel.pid and ports of the keel this app started */
  run: string;
  logs: string;
  settings: string;
};

/**
 * One folder per computer user, never shared with keel2's native keel (~/.keel2/<name>): two keels must not open one
 * keel.db. KEEL_DESKTOP_HOME moves all of it (tests, the smoke test in CI).
 *   macOS  ~/Library/Application Support/keel (logs in ~/Library/Logs/keel)
 *   Linux  ${XDG_DATA_HOME:-~/.local/share}/keel
 */
export function pathsFor(platform: NodeJS.Platform, home: string, env: NodeJS.ProcessEnv): Paths {
  let root: string;
  let logs: string;
  if (env.KEEL_DESKTOP_HOME) {
    root = path.resolve(env.KEEL_DESKTOP_HOME);
    logs = path.join(root, "logs");
  } else if (platform === "darwin") {
    root = path.join(home, "Library", "Application Support", "keel");
    logs = path.join(home, "Library", "Logs", "keel");
  } else if (platform === "win32") {
    root = path.join(env.LOCALAPPDATA || path.join(home, "AppData", "Local"), "keel");
    logs = path.join(root, "logs");
  } else {
    const xdg = env.XDG_DATA_HOME && path.isAbsolute(env.XDG_DATA_HOME) ? env.XDG_DATA_HOME : path.join(home, ".local", "share");
    root = path.join(xdg, "keel");
    logs = path.join(root, "logs");
  }
  return { root, data: path.join(root, "data"), run: path.join(root, "run"), logs, settings: path.join(root, "desktop.json") };
}

export type Bundle = {
  /** KEEL_APP: the unpacked native release */
  keel: string;
  python: string;
  java: string;
  bin: string;
  engine: string;
  front: string;
  /** the Copilot SDK's runtime (GitHub Copilot, your plan) */
  copilot: string;
  /** CodeGraph (the Graph plugin's code graph), with its own Node */
  codegraph: string;
};

/** The keel and the runtimes inside the app (resources/), or KEEL_DESKTOP_RESOURCES (a dev build: desktop/build/stage). */
export function bundleIn(resources: string): Bundle {
  const rt = path.join(resources, "runtime");
  const bin = path.join(rt, "bin");
  return {
    keel: path.join(resources, "keel"),
    python: path.join(rt, "python", "bin", "python3"),
    java: path.join(rt, "jre", "bin", "java"),
    bin,
    engine: path.join(bin, "keel-engine"),
    front: path.join(bin, "keel-api"),
    copilot: path.join(rt, "copilot", "copilot-runtime"),
    codegraph: path.join(rt, "codegraph", "bin", "codegraph"),
  };
}
