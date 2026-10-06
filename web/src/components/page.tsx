// Shared pieces for the project, build and control pages: a section with a one-line explanation, an empty state
// (title, one line of what to do, a button), a toolbar for tabs / filters / search, and loading that only covers
// the part that is still loading (a spinner with words, skeleton lines that keep the space).

import { useEffect, useId, useState, type ReactNode } from "react";
import "../styles/pages.css";

/** A part of a page: a heading, one line that says what it is, its own actions, then the content. */
export function Section({ title, sub, actions, children, className = "" }: {
  title: ReactNode; sub?: ReactNode; actions?: ReactNode; children?: ReactNode; className?: string;
}) {
  const id = useId();
  return (
    <section className={`pg-sec ${className}`} aria-labelledby={id}>
      <header className="pg-sec-h">
        <div className="pg-sec-t">
          <h2 id={id}>{title}</h2>
          {sub && <p>{sub}</p>}
        </div>
        {actions && <div className="pg-sec-a">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

/** Nothing here yet: what it is, what to do, and the button that does it. */
export function EmptyState({ title, children, action, compact }: { title: ReactNode; children?: ReactNode; action?: ReactNode; compact?: boolean }) {
  return (
    <div className={`pg-empty${compact ? " compact" : ""}`}>
      <b className="pg-empty-t">{title}</b>
      {children && <p>{children}</p>}
      {action && <div className="pg-empty-a">{action}</div>}
    </div>
  );
}

/** Tabs, filters and search under the page head. */
export const Toolbar = ({ children, label }: { children: ReactNode; label?: string }) => (
  <div className="pg-bar" role={label ? "toolbar" : undefined} aria-label={label}>{children}</div>
);

/** A small spinner with words, for one part of a page. */
export const Spinner = ({ children = "Loading" }: { children?: ReactNode }) => (
  <span className="pg-spin" role="status" aria-live="polite">{children}…</span>
);

/** Grey lines that hold the space of what is loading, so nothing jumps when it arrives. */
export function Skeleton({ lines = 3, label = "Loading" }: { lines?: number; label?: string }) {
  return (
    <div className="pg-skel-box">
      <Spinner>{label}</Spinner>
      {Array.from({ length: lines }, (_, i) => <span key={i} className="pg-skel" style={{ width: `${92 - ((i * 17) % 40)}%` }} aria-hidden="true" />)}
    </div>
  );
}

/** A search box for the toolbar: full width on a phone. */
export function SearchBox({ value, onChange, label, id }: { value: string; onChange: (v: string) => void; label: string; id?: string }) {
  return (
    <input type="search" id={id} className="inline-input pg-search" placeholder={label} aria-label={label} value={value}
      onChange={(e) => onChange(e.target.value)} />
  );
}

/** True below `px` wide (a phone or a narrow window). */
export function useNarrow(px = 720) {
  const q = `(max-width: ${px}px)`;
  const [narrow, setNarrow] = useState(() => !!window.matchMedia?.(q).matches);
  useEffect(() => {
    const m = window.matchMedia?.(q);
    if (!m) return;
    const on = () => setNarrow(m.matches);
    on();
    m.addEventListener?.("change", on);
    return () => m.removeEventListener?.("change", on);
  }, [q]);
  return narrow;
}
