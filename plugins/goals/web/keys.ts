// A page's one-letter keys, as the buttons show them (<kbd>N</kbd>). The page listens on the document, so it runs before
// keel's own N (New flow, on the window) and stops it: on this page N means this page's N. It does nothing while you
// type, with a modifier held, or while a dialog is open. It imports only react.

import { useEffect, useRef } from "react";

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");

export function usePageKey(key: string, run: () => void, on = true) {
  const ref = useRef(run);
  ref.current = run;
  useEffect(() => {
    if (!on) return;
    const k = key.toLowerCase();
    const handle = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== k || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || e.defaultPrevented) return;
      if (typing(e.target) || document.querySelector('[aria-modal="true"]')) return;
      e.preventDefault();
      ref.current();
    };
    document.addEventListener("keydown", handle);
    return () => document.removeEventListener("keydown", handle);
  }, [key, on]);
}
