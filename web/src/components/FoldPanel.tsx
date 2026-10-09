// v0.15.2 A side panel you can hide and open big (the Flow page's "<agent> works" and "Events"). Hide keeps only its
// head; the choice is kept per browser (`keel2.fold.<id>`). ⤢ opens it in a large view over the page to read it at
// ease; Esc, Close or a click outside closes it.

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** Is the panel hidden? Remembered per browser; a private window does not remember it. */
export function useFolded(id: string): [boolean, (v: boolean) => void] {
  const key = `keel2.fold.${id}`;
  const [v, setV] = useState(() => {
    try {
      return localStorage.getItem(key) === "1";
    } catch {
      return false;
    }
  });
  return [v, (nv: boolean) => {
    setV(nv);
    try {
      if (nv) localStorage.setItem(key, "1");
      else localStorage.removeItem(key);
    } catch { /* private window: not remembered */ }
  }];
}

/** A panel with Hide / Show and ⤢ (open big). `big` draws what the big view shows (often more than the panel). */
export function FoldPanel({ id, title, extra, big, bodyClass = "", live, children }: {
  id: string; title: string; extra?: ReactNode; big: () => ReactNode; bodyClass?: string; live?: boolean; children: ReactNode;
}) {
  const [folded, setFolded] = useFolded(id);
  const [open, setOpen] = useState(false);
  const body = `fold-${useId().replace(/:/g, "")}`;
  return (
    <section className={`panel fold${folded ? " is-folded" : ""}`} aria-label={title}>
      <div className="panel-head">
        <h2>{title}</h2>
        <div className="fold-tools">
          {!folded && extra}
          <button className="btn sm ghost" type="button" aria-expanded={!folded} aria-controls={folded ? undefined : body}
            aria-label={`${folded ? "Show" : "Hide"} ${title}`} title={folded ? "Show it again" : "Hide it (this browser remembers it)"}
            onClick={() => setFolded(!folded)}>{folded ? "Show" : "Hide"}</button>
          <button className="btn sm ghost fold-big" type="button" aria-label={`Open ${title} big`} title="Open big to read it (Esc closes it)"
            onClick={() => setOpen(true)}><span aria-hidden="true">⤢</span></button>
        </div>
      </div>
      {!folded && <div className={`panel-body ${bodyClass}`} id={body} aria-live={live ? "polite" : undefined}>{children}</div>}
      {open && <BigView title={title} onClose={() => setOpen(false)}>{big()}</BigView>}
    </section>
  );
}

/** The big view: a large box over the page. Esc closes this view only (not a drawer under it); the focus goes to Close
 * and back to the button that opened it. */
export function BigView({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("header button")?.focus();
    // capture, and stop it there: a drawer under this view (a narrow screen's step drawer) stays open
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopImmediatePropagation();
      closeRef.current();
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      before?.focus?.();
    };
  }, []);
  return createPortal(
    <>
      <div className="scrim big-scrim" onClick={onClose} />
      <div className="bigview" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <header>
          <h2>{title}</h2>
          <button className="btn sm ghost" type="button" onClick={onClose}>Close</button>
        </header>
        <div className="body">{children}</div>
      </div>
    </>,
    document.body,
  );
}
