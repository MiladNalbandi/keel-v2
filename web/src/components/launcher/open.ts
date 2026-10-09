// Open the launcher from anywhere: the shell's search button, and ⇧⇧ in the Code page (a plugin, through
// @keel/web-sdk). The launcher (Launcher.tsx, mounted once by the shell) listens for this event.

import type { Scope } from "./model";

export const LAUNCHER_EVENT = "keel:launcher";

/** Open the launcher, in a scope or with text already typed. */
export function openLauncher(detail: { scope?: Scope; text?: string } = {}) {
  window.dispatchEvent(new CustomEvent(LAUNCHER_EVENT, { detail }));
}
