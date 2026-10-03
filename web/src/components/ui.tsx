// Small shared pieces: page head, panels, pills, drawer, tabs, loading / error / empty states.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { Provider } from "../api";
import { dur, PROV } from "../format";
import { groupOf, type ScreenId } from "../routes";
import { useApp, useRoute, type Loaded } from "../state";

export function PageHead({ title, sub, actions }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode }) {
  const { project } = useApp();
  const { page } = useRoute();
  const g = groupOf(page);
  return (
    <div className="top">
      <div>
        <div className="crumb">{page === "projects" ? "All projects" : `${project?.name ?? "no project"} › ${g ? g.label : ""}`}</div>
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function Panel({ title, extra, children, body = true, className = "", style }: {
  title?: ReactNode; extra?: ReactNode; children?: ReactNode; body?: boolean | string; className?: string; style?: React.CSSProperties;
}) {
  return (
    <section className={`panel ${className}`} style={style}>
      {(title || extra) && (
        <div className="panel-head">
          {typeof title === "string" ? <h2>{title}</h2> : title}
          {extra}
        </div>
      )}
      {body ? <div className={`panel-body ${typeof body === "string" ? body : ""}`}>{children}</div> : children}
    </section>
  );
}

export type PillTone = "ok" | "run" | "warn" | "bad" | "idle";
export const Pill = ({ tone, children, title }: { tone: PillTone; children: ReactNode; title?: string }) => (
  <span className={`pill p-${tone}`} title={title}>{children}</span>
);

export function Prov({ p, m }: { p?: Provider | string; m?: string }) {
  if (!p) return <span className="sub">—</span>;
  return (
    <span className="prov">
      <i className={`c-${p}`} style={p === "fake" ? { background: "var(--faint)" } : undefined} />
      {PROV[p] ?? p}
      {m ? <> <span className="sub mono">{m}</span></> : null}
    </span>
  );
}

export function StatusPill({ status }: { status: string }) {
  const tone: PillTone =
    status === "done" || status === "ok" || status === "green" ? "ok"
      : status === "running" ? "run"
        : status === "waiting" ? "warn"
          : status === "failed" || status === "guard" || status === "error" ? "bad" : "idle";
  return <Pill tone={tone} title={status === "guard" ? "the diff guard reverted an edit" : undefined}>{status}</Pill>;
}

export function Tabs<T extends string>({ value, options, onChange, label }: {
  value: T; options: [T, ReactNode][]; onChange: (v: T) => void; label?: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {options.map(([k, l]) => (
        <button key={k} role="tab" type="button" aria-selected={value === k} onClick={() => onChange(k)}>{l}</button>
      ))}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: { message: string; hint?: string; details?: string[] }; onRetry?: () => void }) {
  return (
    <div className="errbox" role="alert">
      <b>{error.message}</b>
      {error.hint && <span className="sub">{error.hint}</span>}
      {!!error.details?.length && <ul className="errlist">{error.details.map((d, i) => <li key={i}>{d}</li>)}</ul>}
      {onRetry && <div><button className="btn sm" type="button" onClick={onRetry}>Try again</button></div>}
    </div>
  );
}

export const Loading = ({ what = "Loading" }: { what?: string }) => (
  <div className="empty loading" role="status" aria-live="polite">{what}…</div>
);

export const Empty = ({ children }: { children: ReactNode }) => <div className="empty">{children}</div>;

/** Loading → error → content, for one `useLoad` result. */
export function Async<T>({ r, children, what }: { r: Loaded<T>; children: (d: T) => ReactNode; what?: string }) {
  if (r.data !== null && r.data !== undefined) return <>{children(r.data)}</>;
  if (r.error) return <ErrorBox error={r.error} onRetry={() => void r.reload()} />;
  return <Loading what={what} />;
}

/** A duration that counts up every second. */
export function Since({ from, to }: { from: string; to?: string | null }) {
  const [, setN] = useState(0);
  useEffect(() => {
    if (to) return;
    const t = window.setInterval(() => setN((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, [to]);
  const a = Date.parse(from);
  if (Number.isNaN(a)) return <>—</>;
  return <>{dur((to ? Date.parse(to) : Date.now()) - a)}</>;
}

/** Side drawer: Escape or the scrim closes it, focus goes to the first field and back on close. */
export function Drawer({ title, onClose, children, footer, id }: {
  title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; id?: string;
}) {
  const ref = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>(".body input, .body select, .body textarea, .body button");
    (first ?? ref.current?.querySelector<HTMLElement>("header button"))?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      before?.focus?.();
    };
  }, []);
  return createPortal(
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={title} ref={ref} id={id}>
        <header>
          <h2>{title}</h2>
          <button className="btn sm ghost" type="button" onClick={onClose} aria-label="Close">Close</button>
        </header>
        <div className="body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </aside>
    </>,
    document.body,
  );
}

/** Go-to link used for in-app navigation from buttons. */
export function GoButton({ to, children, className = "btn sm", arg }: { to: ScreenId; children: ReactNode; className?: string; arg?: string }) {
  return (
    <a className={className} href={`#/${to}${arg ? "/" + encodeURIComponent(arg) : ""}`} style={{ textDecoration: "none", display: "inline-block" }}>
      {children}
    </a>
  );
}

export function NoProject() {
  return (
    <div className="panel">
      <div className="panel-body empty grid" style={{ gap: 10, justifyItems: "center" }}>
        <b>No project yet</b>
        <span className="sub">Register a repo on the All projects page first.</span>
        <GoButton to="projects" className="btn primary">All projects</GoButton>
      </div>
    </div>
  );
}

export function Legend({ items }: { items: [React.CSSProperties, ReactNode][] }) {
  return (
    <div className="legend">
      {items.map(([st, l], i) => <span key={i}><i style={st} />{l}</span>)}
    </div>
  );
}

/** Two-step confirm for actions that change something important: first click asks, second click does it. */
export function Confirm({ text, yes, onYes, onNo, busy }: { text: ReactNode; yes: string; onYes: () => void; onNo: () => void; busy?: boolean }) {
  return (
    <div className="confirm" role="group" aria-label="Confirm">
      <span>{text}</span>
      <div className="row">
        <button className="btn sm warn" type="button" onClick={onYes} disabled={busy}>{busy ? "Working…" : yes}</button>
        <button className="btn sm" type="button" onClick={onNo} disabled={busy}>Cancel</button>
      </div>
    </div>
  );
}
