// A page's one-letter key, drawn on its button (keel 0.28 the redesign: "New task N"). It listens on the document, so it
// runs before keel's own N (New flow, on the window) and takes the key on this page. It does nothing while you type, with
// a modifier held, or while a dialog (a drawer) is open.

import { useEffect, useRef } from "react";

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

export function usePageKey(key: string, run: () => void, on = true) {
  const ref = useRef(run);
  ref.current = run;
  useEffect(() => {
    if (!on) return;
    const k = key.toLowerCase();
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== k || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || e.defaultPrevented) return;
      if (typing(e.target) || document.querySelector('[aria-modal="true"]')) return;
      e.preventDefault();
      ref.current();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [key, on]);
}
