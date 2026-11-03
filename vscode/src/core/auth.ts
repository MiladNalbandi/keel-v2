import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";
import { KeelApi, KeelError, normalizeBase, say } from "../api/client";
import { deviceName } from "../logic/format";
import { isLocal, localKeyAllowed, portOf, statusPort, urlFromWorkspace } from "../logic/localKey";

const DEFAULT_URL = "http://localhost:8088";

/** How this window signs in to keel: a stored key or device token, or keel2's key read on this computer. */
export type AuthKind = "key" | "device" | "local";

/**
 * keel's address and sign-in. A pasted API key and a paired device's token live in VS Code's SecretStorage (the OS
 * keychain), one per keel address, never in settings; keel2's own key is read with `keel2 auth key` and kept in
 * memory only. No secret is ever written to a log or shown.
 */
export class Auth {
  /** keel2's key, and the address it was read for: it is never sent anywhere else. */
  private localKey: { base: string; key: string } | undefined;
  /** When keel2 auth key was last tried (0: never); a failed try is repeated after a minute (keel may start later). */
  private localTriedAt = 0;
  /** The port keel2 reports for the keel it runs (keel2 status), checked again after a minute. */
  private localPort: { port: number | undefined; at: number } | undefined;
  /** Signed out by hand: keel2's key is not read on its own until the next Connect. */
  private signedOut = false;
  private readonly changed = new vscode.EventEmitter<void>();
  /** The address or the sign-in changed: connect again. */
  readonly onDidChange = this.changed.event;

  constructor(private readonly ctx: vscode.ExtensionContext) {}

  /** keel's address from the setting keel.url (normalized; the default when it is not an address). */
  baseUrl(): string {
    const raw =
      vscode.workspace.getConfiguration("keel").get<string>("url") ||
      DEFAULT_URL;
    return normalizeBase(raw) ?? normalizeBase(DEFAULT_URL)!;
  }

  private secretKey(base = this.baseUrl()): string {
    return `keel.token:${base}`;
  }

  private kindKey(base = this.baseUrl()): string {
    return `keel.authKind:${base}`;
  }

  kind(): AuthKind | undefined {
    return this.ctx.globalState.get<AuthKind>(this.kindKey());
  }

  /** The sign-in for the current address: the stored secret, else keel2's key (when allowed), else undefined. */
  async token(): Promise<string | undefined> {
    // tests and demos (and CI): a sign-in from the environment, never stored
    const fromEnv = process.env.KEEL_VSCODE_TOKEN?.trim();
    if (fromEnv) return fromEnv;
    const stored = await this.ctx.secrets.get(this.secretKey());
    if (stored) return stored;
    // 0.21.3 keel2's key goes only to the keel keel2 runs (its port), at an address from the person's user settings
    const base = this.baseUrl();
    if (!(await this.mayUseLocal(base))) return undefined;
    if (this.localKey?.base === base) return this.localKey.key;
    // a workspace cannot turn this on: only the user setting counts
    const allowed = vscode.workspace.getConfiguration("keel").inspect<boolean>("useLocalKeel2")?.globalValue ?? true;
    if (allowed && !this.signedOut && Date.now() - this.localTriedAt > 60_000) {
      this.localTriedAt = Date.now();
      try {
        this.localKey = { base, key: await readLocalKey() };
      } catch {
        this.localKey = undefined; // keel2 missing or keel down: the Connect command says why
      }
    }
    return this.localKey?.base === base ? this.localKey.key : undefined;
  }

  /** Whether keel2's key may go to [base] now (keel2 status is asked at most once a minute). */
  private async mayUseLocal(base: string, fresh = false): Promise<boolean> {
    const setting = vscode.workspace.getConfiguration("keel").inspect<string>("url");
    if (!isLocal(base) || urlFromWorkspace(setting)) return false;
    if (fresh || !this.localPort || Date.now() - this.localPort.at > 60_000) {
      let port: number | undefined;
      try {
        port = await readLocalPort();
      } catch {
        port = undefined;
      }
      this.localPort = { port, at: Date.now() };
    }
    return localKeyAllowed(base, setting, this.localPort.port);
  }

  /** keel refused the sign-in: forget it (a removed device, or keel2 auth reset made a new key). */
  async removed(): Promise<void> {
    this.localKey = undefined;
    this.localTriedAt = 0;
    this.localPort = undefined;
    const k = this.kind();
    if (k === "key" || k === "device") {
      await this.ctx.secrets.delete(this.secretKey());
      await this.ctx.globalState.update(this.kindKey(), undefined);
    }
  }

  async signOut(): Promise<void> {
    await this.ctx.secrets.delete(this.secretKey());
    await this.ctx.globalState.update(this.kindKey(), undefined);
    this.localKey = undefined;
    this.signedOut = true; // not again on its own: Connect turns it back on
    this.changed.fire();
  }

  /** A client for the current address with the current sign-in. */
  api(token: string | undefined, onRemoved?: () => void): KeelApi {
    return new KeelApi({
      baseUrl: this.baseUrl(),
      token: () => token,
      onRemoved,
    });
  }

  /** keel: Connect — the address, then keel2 on this computer, an API key, or a pairing code. */
  async connect(): Promise<boolean> {
    const current = this.baseUrl();
    const url = await vscode.window.showInputBox({
      title: "keel: Connect (1/2)",
      prompt: "keel's address",
      value: current.replace(/\/$/, ""),
      ignoreFocusOut: true,
      validateInput: (v) =>
        normalizeBase(v)
          ? undefined
          : "An address like http://localhost:8088 or https://keel.example.ts.net",
    });
    if (url === undefined) return false;
    const base = normalizeBase(url)!;
    if (base !== current)
      await vscode.workspace
        .getConfiguration("keel")
        .update(
          "url",
          base.replace(/\/$/, ""),
          vscode.ConfigurationTarget.Global,
        );

    type Pick = vscode.QuickPickItem & { id: "local" | "key" | "pair" };
    const items: Pick[] = [
      {
        id: "local",
        label: "$(terminal) Use keel2 on this computer",
        detail: "Runs keel2 auth key; the key stays in memory.",
      },
      {
        id: "key",
        label: "$(key) Paste an API key",
        detail:
          "The key from keel2 auth key; kept in VS Code's secret storage.",
      },
      {
        id: "pair",
        label: "$(device-mobile) Pair with a code",
        detail: "keel › Settings › Devices › Pair shows a one-time code.",
      },
    ];
    const how = await vscode.window.showQuickPick(items, {
      title: "keel: Connect (2/2)",
      placeHolder: "How should VS Code sign in?",
      ignoreFocusOut: true,
    });
    if (!how) return false;
    try {
      if (how.id === "local") {
        // 0.21.3 only to the keel keel2 runs: localhost, keel2's port, and an address from the user settings
        if (!(await this.mayUseLocal(base, true))) {
          const port = this.localPort?.port;
          throw new KeelError(
            401,
            !isLocal(base)
              ? "keel2's key is only for a keel on this computer (localhost)."
              : urlFromWorkspace(vscode.workspace.getConfiguration("keel").inspect<string>("url"))
                ? "This workspace sets keel.url, so keel2's key is not sent there."
                : port === undefined
                  ? "keel2 status shows no running keel."
                  : `keel2 runs keel on port ${port}, not ${portOf(base) ?? "?"}.`,
            port === undefined
              ? "Start it (keel2 start), or connect with an API key or a pairing code."
              : `Use http://localhost:${port} as keel's address in your user settings.`,
          );
        }
        const key = await readLocalKey();
        await this.check(base, key);
        await this.ctx.secrets.delete(this.secretKey(base));
        await this.ctx.globalState.update(this.kindKey(base), "local");
        this.localKey = { base, key };
        this.signedOut = false;
      } else if (how.id === "key") {
        const key = await vscode.window.showInputBox({
          title: "keel: API key",
          prompt: "Paste keel's sign-in key (keel2 auth key prints it)",
          password: true,
          ignoreFocusOut: true,
          validateInput: (v) =>
            v.trim() && !/\s/.test(v.trim())
              ? undefined
              : "One piece, no spaces",
        });
        if (!key) return false;
        await this.check(base, key.trim());
        await this.store(base, key.trim(), "key");
      } else {
        const code = await vscode.window.showInputBox({
          title: "keel: Pair",
          prompt: "The code keel shows in Settings › Devices › Pair",
          ignoreFocusOut: true,
        });
        if (!code?.trim()) return false;
        const name = deviceName(os.hostname());
        const res = await new KeelApi({
          baseUrl: base,
          token: () => undefined,
        }).pair(code, name);
        await this.store(base, res.token, "device");
        vscode.window.showInformationMessage(
          `keel: paired as "${res.device.name ?? name}"${res.keel?.version ? ` with keel ${res.keel.version}` : ""}.`,
        );
      }
    } catch (e) {
      vscode.window.showErrorMessage(`keel: could not connect. ${say(e)}`);
      return false;
    }
    this.changed.fire();
    return true;
  }

  private async store(
    base: string,
    token: string,
    kind: AuthKind,
  ): Promise<void> {
    await this.ctx.secrets.store(this.secretKey(base), token);
    await this.ctx.globalState.update(this.kindKey(base), kind);
    this.localKey = undefined;
    this.signedOut = false;
  }

  /** A read that needs the sign-in: a wrong key fails here, before it is kept. */
  private async check(base: string, token: string): Promise<void> {
    await new KeelApi({ baseUrl: base, token: () => token }).projects();
  }
}

export { isLocal } from "../logic/localKey";

/** Where keel2 usually is: the PATH, then the install script's places (an editor started from the Dock has a short PATH). */
export function keel2Candidates(
  home = os.homedir(),
  envPath = process.env.PATH ?? "",
): string[] {
  const dirs = [
    ...envPath.split(path.delimiter).filter(Boolean),
    path.join(home, ".local", "bin"),
    "/usr/local/bin",
    "/opt/homebrew/bin",
  ];
  return [...new Set(dirs)].map((d) =>
    path.join(d, process.platform === "win32" ? "keel2.exe" : "keel2"),
  );
}

/** keel2's environment: the install script's places on the PATH (an editor started from the Dock has a short PATH). */
function keel2Env(): NodeJS.ProcessEnv {
  const home = os.homedir();
  return {
    ...process.env,
    NO_COLOR: "1",
    PATH: [
      process.env.PATH ?? "",
      "/usr/local/bin",
      "/opt/homebrew/bin",
      path.join(home, ".local", "bin"),
      "/usr/bin",
      "/bin",
    ]
      .filter(Boolean)
      .join(path.delimiter),
  };
}

function keel2NotFound(): KeelError {
  return new KeelError(
    401,
    "keel2 was not found.",
    "Install keel (install.sh), or connect with an API key or a pairing code.",
  );
}

/** 0.21.3 the port of the keel keel2 runs (from `keel2 status`), or undefined when none runs. */
export function readLocalPort(timeoutMs = 30_000): Promise<number | undefined> {
  const exe = keel2Candidates().find((p) => existsSync(p));
  if (!exe) return Promise.reject(keel2NotFound());
  return new Promise((resolve) => {
    execFile(
      exe,
      ["status"],
      { env: keel2Env(), timeout: timeoutMs, maxBuffer: 1 << 20 },
      (err, stdout) => resolve(err ? undefined : statusPort(String(stdout))),
    );
  });
}

/** keel's sign-in key from `keel2 auth key`, or an error with keel2's own words (never the key itself). */
export function readLocalKey(timeoutMs = 30_000): Promise<string> {
  const exe = keel2Candidates().find((p) => existsSync(p));
  if (!exe) return Promise.reject(keel2NotFound());
  const env = keel2Env();
  return new Promise((resolve, reject) => {
    execFile(
      exe,
      ["auth", "key"],
      { env, timeout: timeoutMs, maxBuffer: 1 << 20 },
      (err, stdout, stderr) => {
        const key = String(stdout)
          .split(/\r?\n/)
          .map((l) => l.trim())
          .filter(Boolean)
          .pop();
        if (err || !key || /\s/.test(key)) {
          const why = String(stderr || "")
            .replace(/\u001b\[[0-9;]*[A-Za-z]/g, "")
            .split(/\r?\n/)
            .map((l) => l.trim())
            .filter(Boolean);
          reject(
            new KeelError(
              401,
              why[0] ?? "keel2 auth key printed no key.",
              why[1] ?? "Is keel running? keel2 status",
            ),
          );
          return;
        }
        resolve(key);
      },
    );
  });
}
