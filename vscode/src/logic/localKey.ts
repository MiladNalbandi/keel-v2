// When this window may send keel2's own sign-in key (keel2 auth key). That key opens the person's keel, so it goes
// only to the keel keel2 runs: localhost, on the port keel2 reports, at an address the person set in their own user
// settings. A workspace's .vscode/settings.json comes with a cloned repo: its keel.url (another localhost port, where
// any program could listen) never gets the key.

/** What VS Code's configuration inspect() says about keel.url (only the fields used here). */
export type UrlSetting = {
  workspaceValue?: unknown;
  workspaceFolderValue?: unknown;
};

/** Whether keel2 on this computer can know this keel's key (it only knows the keel it runs). */
export function isLocal(base: string): boolean {
  try {
    const h = new URL(base).hostname;
    return h === "localhost" || h === "127.0.0.1" || h === "[::1]" || h === "::1";
  } catch {
    return false;
  }
}

/** The port of an http(s) address (the scheme's default when it names none); undefined for a broken address. */
export function portOf(base: string): number | undefined {
  try {
    const u = new URL(base);
    if (u.port) return Number(u.port);
    return u.protocol === "https:" ? 443 : u.protocol === "http:" ? 80 : undefined;
  } catch {
    return undefined;
  }
}

/** True when a workspace (or a folder of it) sets keel.url: then it is not the person's own choice. */
export function urlFromWorkspace(setting: UrlSetting | undefined): boolean {
  return setting?.workspaceValue !== undefined || setting?.workspaceFolderValue !== undefined;
}

/** keel's port from `keel2 status` ("  dashboard  http://localhost:8088"); undefined when keel does not run. */
export function statusPort(text: string): number | undefined {
  const plain = text.replace(/\u001b\[[0-9;]*[A-Za-z]/g, "");
  const m = /^\s*dashboard\s+https?:\/\/(?:localhost|127\.0\.0\.1|\[::1\]):(\d{1,5})\/?\s*$/m.exec(plain);
  if (!m) return undefined;
  const n = Number(m[1]);
  return n > 0 && n < 65536 ? n : undefined;
}

/** Whether keel2's key may go to [base]: this computer, not from a workspace setting, and the port keel2 reports. */
export function localKeyAllowed(base: string, setting: UrlSetting | undefined, reportedPort: number | undefined): boolean {
  if (!isLocal(base) || urlFromWorkspace(setting)) return false;
  return reportedPort !== undefined && portOf(base) === reportedPort;
}
