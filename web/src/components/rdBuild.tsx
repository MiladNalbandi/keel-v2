// keel 0.28 the redesign, the Build pages (Workflows, Agents, Skills, Stacks, Tools, Safety & History, Quality,
// Settings and Start here): the page frame their look hangs on (styles/rd-build.css) and the page's own keys.
//
// A button with `data-pagekey="N"` shows the key as a key cap (CSS) and pressing that letter clicks it, when you are not
// typing, no dialog is open and no modifier is held. The page's key wins over the shell's N (New flow): it runs first
// (on document, before the shell's window listener) and stops it with preventDefault.

import { useEffect, type ReactNode } from "react";
import { isTyping, modalOpen } from "../keys";

/** The visible, enabled element of the page that carries this key, or null. */
function keyTarget(key: string): HTMLElement | null {
  const all = document.querySelectorAll<HTMLElement>(".rdb [data-pagekey]");
  for (const el of all) {
    if ((el.dataset.pagekey ?? "").toLowerCase() !== key) continue;
    if ((el as HTMLButtonElement).disabled || el.getAttribute("aria-disabled") === "true") continue;
    if (typeof el.checkVisibility === "function" && !el.checkVisibility()) continue;
    return el;
  }
  return null;
}

/** The page keys: one letter clicks the page's button that shows it (see above). */
export function usePageKeys() {
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      if (e.key.length !== 1 || !/[a-z]/i.test(e.key)) return;
      if (isTyping(e.target) || modalOpen()) return;
      const el = keyTarget(e.key.toLowerCase());
      if (!el) return;
      e.preventDefault();
      el.click();
    };
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, []);
}

/** The frame of one Build page: its look (`rdb rdb-<name>`) and its keys. */
export function RdPage({ name, children }: { name: string; children: ReactNode }) {
  usePageKeys();
  return <div className={`rdb rdb-${name}`}>{children}</div>;
}

/** The props that give a button its page key: `<button {...pageKey("N")}>`. */
export const pageKey = (k: string) => ({ "data-pagekey": k.toUpperCase(), "aria-keyshortcuts": k.toUpperCase() });

/** A count beside a tab's or a filter's name. */
export const Count = ({ n }: { n: number | string }) => <span className="rdb-n">{n}</span>;
