// App shell. Wide screens: a sticky left rail (brand with the mascot and bell, the project picker, the grouped nav
// that scrolls on its own, then the plan usage, engine + live status and the theme switch). Phones (≤ 900px): one compact bar — menu,
// project picker, bell — over one horizontally scrollable row of every screen; Theme and the status live in the menu.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { GROUPS, hashFor, type ScreenId } from "../routes";
import { go, useApp, useRoute } from "../state";
import { Mascot } from "./Mascot";
import { UsageStrip } from "./UsageStrip";
import { NotificationDrawer, Popups } from "./Notifications";

const THEME_KEY = "keel2.theme";

function applyTheme(t: string | null) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
}

function isDark(): boolean {
  const r = document.documentElement;
  if (r.dataset.theme) return r.dataset.theme === "dark";
  try {
    return !!window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

/** The saved theme, and a switch between light and dark. */
export function useTheme() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    try {
      applyTheme(localStorage.getItem(THEME_KEY));
    } catch {
      /* ignore */
    }
    setDark(isDark());
  }, []);
  const toggle = () => {
    const next = isDark() ? "light" : "dark";
    applyTheme(next);
    setDark(next === "dark");
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* ignore */
    }
  };
  return { dark, toggle };
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

const Logo = () => (
  <svg viewBox="0 0 120 68" aria-hidden="true">
    <path fill="var(--accent)" d="M4 0H116Q120 0 119.4 4C118 22 92 38 66.6 50Q64 51.5 64 54L62.2 64.5Q61.8 67 60 67Q58.2 67 57.8 64.5L56 54Q56 51.5 53.4 50C28 38 2 22 .6 4Q0 0 4 0Z" />
  </svg>
);

/** The project's phase (or "no flow") and branch on one line; `full` adds the flow's name. */
function projectLine(p: { branch: string; flow: string | null; phase: string }, full = false) {
  const now = p.flow ? (full ? `${p.flow} · ${p.phase}` : p.phase) : "no flow";
  return `${now} · ⎇ ${p.branch || "—"}`;
}

function ProjectPicker() {
  const { projects, project, setProjectId, toast } = useApp();
  const { page } = useRoute();
  return (
    <div className="projbox">
      <label htmlFor="projPick" className="sr-only">Project</label>
      <select
        id="projPick"
        value={project?.id ?? ""}
        disabled={!projects.length}
        title="The project every screen shows"
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
        <span className="pb-sub" title={projectLine(project, true)}>
          <span className="pb-line mono">{projectLine(project)}</span>
          {project.waiting > 0 && <b className="amber" title={`${project.waiting} waiting for you`}>◆ {project.waiting}</b>}
        </span>
      )}
    </div>
  );
}

/** Scroll `box` (only that box, never the page) so `el` is fully visible. */
function keepVisible(box: HTMLElement, el: HTMLElement) {
  const b = box.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  if (r.top < b.top) box.scrollTop -= b.top - r.top + 8;
  else if (r.bottom > b.bottom) box.scrollTop += r.bottom - b.bottom + 8;
  // sideways: clear the fade at the row's end too
  if (r.left < b.left + 16) box.scrollLeft -= b.left - r.left + 36;
  else if (r.right > b.right - 36) box.scrollLeft += r.right - b.right + 40;
}

/** The screens: All projects, then the four groups. `onPick` runs after a link is used (the phone menu closes). */
function NavLinks({ onPick, hints }: { onPick?: () => void; hints?: boolean }) {
  const { page } = useRoute();
  const { project, projects } = useApp();
  const running = project?.running ?? 0;
  // flows that wait for a person in every project (the project list follows the event stream)
  const waitingAll = projects.reduce((a, p) => a + (p.waiting || 0), 0);
  const badge = (id: ScreenId) =>
    id === "inbox" ? (waitingAll > 0 ? <span className="count" data-testid={onPick ? undefined : "inbox-count"} title="waiting for you in all projects"
      aria-label={`${waitingAll} waiting`}>{waitingAll}</span> : null)
      : (id === "jobs" || id === "live") && running > 0 ? <span className="count run" title="running now" aria-label={`${running} running`}>{running}</span> : null;
  return (
    <>
      <a href={hashFor("projects")} className="nav-home" aria-current={page === "projects" ? "page" : undefined} onClick={onPick}>
        <span className="nav-l">All projects</span>
        {waitingAll > 0 && <span className="count" title="waiting for you in all projects">◆ {waitingAll}</span>}
      </a>
      {GROUPS.map((g) => (
        <div key={g.id} className={`nav-sec ${g.pages.some(([p]) => p === page) ? "cur" : ""}`}>
          <div className="nav-h" title={g.hint}><span>{g.label}</span>{hints && <small>{g.hint}</small>}</div>
          {g.pages.map(([id, label]) => (
            <a key={id} href={hashFor(id)} aria-current={page === id ? "page" : undefined} onClick={onPick}>
              <span className="nav-l">{label}</span>
              {badge(id)}
            </a>
          ))}
        </div>
      ))}
    </>
  );
}

function Nav() {
  const { page } = useRoute();
  const ref = useRef<HTMLElement>(null);
  // keep the active screen in view: the rail scrolls up and down, the phone row sideways
  useEffect(() => {
    const show = () => {
      const box = ref.current;
      const el = box?.querySelector<HTMLElement>('[aria-current="page"]');
      if (box && el) keepVisible(box, el);
    };
    show();
    // the web font changes the widths once it arrives
    document.fonts?.ready.then(show, () => undefined);
  }, [page]);
  // a soft fade at the edge where more screens are hidden (the rail on a short screen, the phone row)
  useEffect(() => {
    const box = ref.current;
    if (!box) return;
    const mark = () => {
      const below = box.scrollHeight - box.clientHeight - box.scrollTop > 2;
      const right = box.scrollWidth - box.clientWidth - box.scrollLeft > 2;
      box.toggleAttribute("data-more-below", below);
      box.toggleAttribute("data-more-right", right);
      box.toggleAttribute("data-more-left", box.scrollLeft > 2);
    };
    mark();
    box.addEventListener("scroll", mark, { passive: true });
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(mark);
    ro?.observe(box);
    return () => {
      box.removeEventListener("scroll", mark);
      ro?.disconnect();
    };
  }, []);
  return (
    <nav className="nav" id="nav" aria-label="Screens" ref={ref}>
      <NavLinks />
    </nav>
  );
}

function LiveDot() {
  const { live } = useApp();
  const label = live === "live" ? "Live" : live === "reconnecting" ? "Reconnecting" : live === "connecting" ? "Connecting" : live === "paused" ? "Paused (tab hidden)" : "Offline";
  const tone = live === "live" ? "p-ok" : live === "off" ? "p-bad" : "p-warn";
  return <span className={`pill ${tone}`} data-testid="live" title="Server events (SSE)">{label}</span>;
}

function Version() {
  const { health } = useApp();
  return health?.version ? <span className="mono sub ver" title="keel v2 version">v{health.version.replace(/^v/, "")}</span> : null;
}

/** The engine in one pill: up (langgraph), the fake model, down, or still unknown; `version` adds keel's version. */
function EngineStatus({ version }: { version?: boolean }) {
  const { health } = useApp();
  const tone = !health ? "p-idle" : !health.engine ? "p-bad" : health.fake ? "p-warn" : "p-ok";
  const label = !health ? "engine …" : !health.engine ? "engine down" : health.fake ? "fake model" : "engine";
  const title = !health ? "Checking the engine" : !health.engine ? "The engine does not answer"
    : health.fake ? "The engine answers with the fake model: it writes example files, not real code" : "The LangGraph engine is up";
  return (
    <>
      <span className={`pill ${tone}`} title={title} data-testid="engine">{label}</span>
      {version && <Version />}
    </>
  );
}

function ThemeIcon({ dark }: { dark: boolean }) {
  return dark ? (
    <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><circle cx="12" cy="12" r="4.2" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" /></svg>
  ) : (
    <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" /></svg>
  );
}

/** Phones: everything the compact bar has no room for — the project, every screen, status and theme. */
function MenuSheet({ onClose, theme }: { onClose: () => void; theme: ReturnType<typeof useTheme> }) {
  const { project } = useApp();
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    (ref.current?.querySelector<HTMLElement>(".ms-nav [aria-current=page]") ?? ref.current?.querySelector<HTMLElement>(".ms-nav a"))?.focus();
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
      <div className="menu-sheet" role="dialog" aria-modal="true" aria-label="Menu" id="menu-sheet" ref={ref}>
        <header className="ms-head">
          <span className="brand"><Logo /><b>keel</b><span>v2 studio</span></span>
          <button className="btn sm ghost" type="button" onClick={onClose}>Close</button>
        </header>
        {project && (
          <div className="ms-proj">
            <b>{project.name}</b>
            <span className="sub mono">{projectLine(project, true)}</span>
            {project.waiting > 0 && <span className="amber">◆ {project.waiting} waiting for you</span>}
          </div>
        )}
        <nav className="ms-nav" aria-label="All screens"><NavLinks onPick={onClose} hints /></nav>
        <div className="ms-foot">
          <div className="row"><LiveDot /><EngineStatus version /></div>
          <button className="btn" type="button" onClick={theme.toggle}>{theme.dark ? "Switch to the light theme" : "Switch to the dark theme"}</button>
        </div>
      </div>
    </>,
    document.body,
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const [notesOpen, setNotesOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const theme = useTheme();
  return (
    <div className="app">
      <aside className="side">
        <div className="side-head">
          <button className="menu-btn" type="button" onClick={() => setMenuOpen(true)} aria-label="Menu" aria-expanded={menuOpen} aria-controls="menu-sheet">
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" d="M4 7h16M4 12h16M4 17h16" />
            </svg>
          </button>
          <span className="brand"><Logo /><b>keel</b><span className="brand-v">v2 studio</span><Version /></span>
          <span className="side-tools"><Mascot /><Bell onClick={() => setNotesOpen(true)} /></span>
          <ProjectPicker />
        </div>
        <Nav />
        {/* under the nav: when the cards arrive (or a provider is added) the screens above do not move */}
        <UsageStrip compact />
        <div className="side-foot">
          <div className="row"><LiveDot /><EngineStatus /></div>
          <button className="btn sm ghost theme-btn" id="theme" type="button" onClick={theme.toggle}
            aria-label={theme.dark ? "Switch to the light theme" : "Switch to the dark theme"} title={theme.dark ? "Switch to the light theme" : "Switch to the dark theme"}>
            <ThemeIcon dark={theme.dark} />
          </button>
        </div>
      </aside>
      <main id="main">{children}</main>
      {menuOpen && <MenuSheet onClose={() => setMenuOpen(false)} theme={theme} />}
      {notesOpen && <NotificationDrawer onClose={() => setNotesOpen(false)} />}
      <Popups />
    </div>
  );
}
