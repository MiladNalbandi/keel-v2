// App shell: the left rail (brand, project picker with bell, grouped nav, engine + live status, theme switch)
// and the working pane. On phones the rail becomes a top bar with a 4-group switcher.

import { useEffect, useState, type ReactNode } from "react";
import { GROUPS, groupOf, hashFor, type ScreenId } from "../routes";
import { go, useApp, useRoute } from "../state";
import { NotificationDrawer, Popups } from "./Notifications";

const THEME_KEY = "keel2.theme";

function applyTheme(t: string | null) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
}

export function useTheme() {
  useEffect(() => {
    try {
      applyTheme(localStorage.getItem(THEME_KEY));
    } catch {
      /* ignore */
    }
  }, []);
  return () => {
    const r = document.documentElement;
    const dark = r.dataset.theme ? r.dataset.theme === "dark" : window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    applyTheme(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* ignore */
    }
  };
}

export function Bell({ onClick }: { onClick: () => void }) {
  const { unread, nset } = useApp();
  return (
    <button type="button" className="bell" id="bell" onClick={onClick} aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`}>
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
        <path fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
          d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15L6 16zM10 20a2 2 0 0 0 4 0" />
      </svg>
      {nset.quiet && <span className="bell-q">zz</span>}
      {unread > 0 && <span className="bell-n" data-testid="unread">{unread}</span>}
    </button>
  );
}

function ProjectBox({ onBell }: { onBell: () => void }) {
  const { projects, project, setProjectId, toast } = useApp();
  const { page } = useRoute();
  const waitingAll = projects.reduce((a, p) => a + (p.waiting || 0), 0);
  return (
    <div className="projbox">
      <div className="pb-top">
        <label htmlFor="projPick" className="pb-lab">Project</label>
        <Bell onClick={onBell} />
      </div>
      <select
        id="projPick"
        value={project?.id ?? ""}
        disabled={!projects.length}
        onChange={(e) => {
          setProjectId(e.target.value);
          const name = projects.find((p) => p.id === e.target.value)?.name ?? e.target.value;
          if (page === "projects") go("flow");
          toast(`Now showing ${name}`);
        }}
      >
        {!projects.length && <option value="">no projects</option>}
        {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
      {project && (
        <span className="pb-sub">
          <span className="mono">⎇ {project.branch || "—"}</span> ·{" "}
          {project.flow ? <>{project.flow} · <span className="mono">{project.phase}</span></> : "no flow"}
          {project.waiting > 0 && <> · <b className="amber">◆ {project.waiting}</b></>}
        </span>
      )}
      <a href={hashFor("projects")} className={`pb-all ${page === "projects" ? "on" : ""}`}>
        ← All projects{waitingAll > 0 && <> · <b className="amber">◆ {waitingAll}</b></>}
      </a>
    </div>
  );
}

function Nav() {
  const { page } = useRoute();
  const { project } = useApp();
  const cur = groupOf(page);
  const running = project?.running ?? 0;
  const badge = (id: ScreenId) =>
    (id === "jobs" || id === "live") && running > 0 ? <span className="count run" title="running now">{running}</span> : null;
  return (
    <nav className="nav" id="nav" aria-label="Screens">
      <div className="nav-groups" role="tablist" aria-label="Sections" style={{ ["--gn" as string]: GROUPS.length }}>
        {GROUPS.map((g) => (
          <button key={g.id} type="button" role="tab" aria-selected={g === cur} onClick={() => go(g.pages[0][0])}>{g.label}</button>
        ))}
      </div>
      {GROUPS.map((g) => (
        <div key={g.id} className={`nav-sec ${g === cur ? "cur" : ""}`}>
          <div className="nav-h"><span>{g.label}</span><small>{g.hint}</small></div>
          {g.pages.map(([id, label]) => (
            <a key={id} href={hashFor(id)} aria-current={page === id ? "page" : undefined}>
              {label}
              {badge(id)}
            </a>
          ))}
        </div>
      ))}
    </nav>
  );
}

function LiveDot() {
  const { live } = useApp();
  const label = live === "live" ? "Live" : live === "reconnecting" ? "Reconnecting" : live === "connecting" ? "Connecting" : "Offline";
  const tone = live === "live" ? "p-ok" : live === "off" ? "p-bad" : "p-warn";
  return <span className={`pill ${tone}`} data-testid="live" title="Server events (SSE)">{label}</span>;
}

export function Shell({ children }: { children: ReactNode }) {
  const { health } = useApp();
  const [notesOpen, setNotesOpen] = useState(false);
  const toggleTheme = useTheme();
  return (
    <div className="app">
      <aside className="side">
        <div className="brand">
          <svg viewBox="0 0 120 68" aria-hidden="true">
            <path fill="var(--accent)" d="M4 0H116Q120 0 119.4 4C118 22 92 38 66.6 50Q64 51.5 64 54L62.2 64.5Q61.8 67 60 67Q58.2 67 57.8 64.5L56 54Q56 51.5 53.4 50C28 38 2 22 .6 4Q0 0 4 0Z" />
          </svg>
          <b>keel</b><span>v2 studio</span>
        </div>
        <ProjectBox onBell={() => setNotesOpen(true)} />
        <Nav />
        <div className="side-mini">
          <LiveDot />
          <button className="btn sm ghost" type="button" onClick={toggleTheme}>Theme</button>
        </div>
        <div className="side-foot">
          <div className="row"><LiveDot /><span className="sub">events</span></div>
          <div className="row">
            <span className={`pill ${health?.engine ? "p-ok" : health ? "p-bad" : "p-idle"}`}>engine</span>
            <span className="mono">{health ? (health.fake ? "fake model" : "langgraph") : "…"}</span>
          </div>
          {health?.keel?.version && <div className="row"><span className="pill p-ok">keel</span><span className="mono">{health.keel.version}</span></div>}
          <button className="btn sm ghost" id="theme" type="button" onClick={toggleTheme}>Switch theme</button>
        </div>
      </aside>
      <main id="main">{children}</main>
      {notesOpen && <NotificationDrawer onClose={() => setNotesOpen(false)} />}
      <Popups />
    </div>
  );
}
