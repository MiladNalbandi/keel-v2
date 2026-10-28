// keel 0.18.66 the first-run welcome's state, shared by the Shell (which draws it) and Help › Start here ("See the
// welcome again"). screen: 0 the welcome, 1–5 the set-up steps, 6 done. hidden: a step's link opened a page of keel
// (Connections, Add projects…); the welcome waits as a small "Back to the setup" button until the person comes back.

import { useSyncExternalStore } from "react";

export type WelcomeUi = { open: boolean; screen: number; hidden: boolean };

let ui: WelcomeUi = { open: false, screen: 0, hidden: false };
const listeners = new Set<() => void>();

function set(next: Partial<WelcomeUi>) {
  ui = { ...ui, ...next };
  listeners.forEach((l) => l());
}

export const welcomeUi = {
  get: () => ui,
  set,
  subscribe(l: () => void) {
    listeners.add(l);
    return () => void listeners.delete(l);
  },
};

/** Opens the welcome at its first screen (Help › Start here, or the first open of keel). */
export function openWelcome(screen = 0) {
  set({ open: true, screen, hidden: false });
}

export function closeWelcome() {
  set({ open: false, hidden: false, screen: 0 });
}

export function useWelcomeUi(): WelcomeUi {
  return useSyncExternalStore(welcomeUi.subscribe, welcomeUi.get, welcomeUi.get);
}
