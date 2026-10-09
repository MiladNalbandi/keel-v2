// App shell. Wide screens: a sticky left rail (brand with the mascot and bell, the project picker, the grouped nav
// that scrolls on its own, then the plan usage, engine + live status and the theme switch). Phones (≤ 900px): one compact bar — menu,
// project picker, bell — over one horizontally scrollable row of every screen; Theme and the status live in the menu.

import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { navGroups, saveView, useFeatures, useView, type View } from "../addons";
import { FOCUS_EVENT, isMac, isTyping, KEYS, keyLabel, matchesAny, menuPageKey, menuPageOf } from "../keys";
import { hashFor, hashForScreen, type ScreenId } from "../routes";
import { usePages, useSlot } from "../sdk/registry";
import { SLOTS, type NavBadgeItem, type ShellWatchItem } from "../sdk/slots";
import { go, useApp, useRoute } from "../state";
import { Mascot } from "./Mascot";
import { BudgetBar } from "./BudgetBar";
import { Launcher, openLauncher } from "./launcher/Launcher";
import { KeySheet } from "./KeySheet";
import { NavIcon } from "./NavIcons";
import { NotificationDrawer, Popups } from "./Notifications";
import { isCoreOnly, StartWithSet } from "./StartWithSet";

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

/** v0.15.0 opens the launcher (also ⌘K on any page). */
function SearchButton({ className }: { className: string }) {
  const keys = keyLabel("meta+k");
  return (
    <button type="button" className={className} onClick={() => openLauncher()} aria-label={`Search and actions (${keys})`}
      title={`Search and actions (${keys})`}>
      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
        <circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.9" />
        <path d="M20 20l-4-4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
      </svg>
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

/** v0.15.4 a menu link's tooltip names its key: ⌃1–⌃9 open the first nine pages. */
const keyTip = (label: string, n: number) => (n <= 9 ? `${label} (${keyLabel(menuPageKey(n))})` : label);

/** v0.15.2 a part's count on a menu page (slot nav.badge: KeelBot's new answers on KeelBot), in the menu or the rail. */
function PartBadges({ page, kind }: { page: string; kind: "nav" | "rail" }) {
  const items = useSlot<NavBadgeItem>(SLOTS.navBadge).filter((b) => b.page === page);
  return <>{items.map((b) => <b.component key={b.id} kind={kind} />)}</>;
}

/** v0.15.2 what the parts watch on every page (slot shell.watch: KeelBot counts its new answers and plays its sound). */
function PartWatches() {
  const items = useSlot<ShellWatchItem>(SLOTS.shellWatch);
  return <>{items.map((w) => <w.component key={w.id} />)}</>;
}

/** The screens: All projects, then the four groups. `onPick` runs after a link is used (the phone menu closes). */
function NavLinks({ onPick, hints }: { onPick?: () => void; hints?: boolean }) {
  const route = useRoute();
  const { page } = route;
  const features = useFeatures();
  const view = useView();
  // the menu follows the page registry (a plugin's page shows when it registers)
  const pages = usePages();
  // v0.16.0 a keel with only its core has no Project pages: the group offers the sets of plugins instead
  const coreOnly = isCoreOnly(features, view, pages);
  const current = page === "addon" ? route.screen : page;
  const { project, projects } = useApp();
  const running = project?.running ?? 0;
  // flows that wait for a person in every project (the project list follows the event stream)
  const waitingAll = projects.reduce((a, p) => a + (p.waiting || 0), 0);
  const badge = (id: ScreenId) =>
    id === "inbox" ? (waitingAll > 0 ? <span className="count" data-testid={onPick ? undefined : "inbox-count"} title="waiting for you in all projects"
      aria-label={`${waitingAll} waiting`}>{waitingAll}</span> : null)
      : (id === "jobs" || id === "live") && running > 0 ? <span className="count run" title="running now" aria-label={`${running} running`}>{running}</span> : null;
  let n = 1;
  return (
    <>
      <a href={hashFor("projects")} className="nav-home" aria-current={page === "projects" ? "page" : undefined} onClick={onPick} title={keyTip("All projects", 1)}>
        <span className="nav-l">All projects</span>
        {waitingAll > 0 && <span className="count" title="waiting for you in all projects">◆ {waitingAll}</span>}
      </a>
      {navGroups(features, view).map((g) => (
        <Fragment key={g.id}>
          <div className={`nav-sec ${g.pages.some((p) => p.id === current) ? "cur" : ""}`}>
            <div className="nav-h" title={g.hint}><span>{g.label}</span>{hints && g.hint && <small>{g.hint}</small>}</div>
            {g.pages.map((p) => (
              <a key={p.id} href={p.addon ? hashForScreen(p.id) : hashFor(p.id)} aria-current={current === p.id ? "page" : undefined}
                onClick={onPick} title={keyTip(p.label, ++n)}>
                <span className="nav-l">{p.label}</span>
                {p.addon ? null : badge(p.id)}
                <PartBadges page={p.id} kind="nav" />
              </a>
            ))}
          </div>
          {coreOnly && g.id === "run" && <StartWithSet onPick={onPick} />}
        </Fragment>
      ))}
    </>
  );
}

/** The folded menu: one icon per screen, grouped like the menu, with the same counts; ☰ opens the full menu again. */
function RailLinks() {
  const route = useRoute();
  const { page } = route;
  const features = useFeatures();
  const view = useView();
  usePages();
  const current = page === "addon" ? route.screen : page;
  const { project, projects } = useApp();
  const running = project?.running ?? 0;
  const waitingAll = projects.reduce((a, p) => a + (p.waiting || 0), 0);
  const count = (id: ScreenId) =>
    id === "inbox" && waitingAll > 0 ? <span className="rail-count" aria-hidden="true">{waitingAll > 9 ? "9+" : waitingAll}</span>
      : (id === "jobs" || id === "live") && running > 0 ? <span className="rail-count run" aria-hidden="true">{running}</span> : null;
  let n = 0;
  const link = (id: string, label: string, addon?: string) => (
    <a key={id} href={addon ? hashForScreen(id) : hashFor(id)} className="rail-link" aria-current={current === id ? "page" : undefined}
      title={keyTip(label, ++n)} aria-label={id === "inbox" && waitingAll ? `${label}, ${waitingAll} waiting` : label}>
      <NavIcon id={addon ? "addon" : id} />
      {addon ? null : count(id)}
      <PartBadges page={id} kind="rail" />
    </a>
  );
  return (
    <nav className="rail-nav" aria-label="Screens (icons)">
      {link("projects", "All projects")}
      {navGroups(features, view).map((g) => (
        <div key={g.id} className="rail-group" title={g.label}>{g.pages.map((p) => link(p.id, p.label, p.addon))}</div>
      ))}
    </nav>
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
    <nav className="nav" id="nav" aria-label="Screens" ref={ref} aria-keyshortcuts={KEYS.menuJump[0].toUpperCase()}>
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

const NAV_KEY = "keel2.nav.hidden";

/** Product and Dev both on: each person shows All, Product or Dev in the menu (the data stays the same). */
function ViewSwitch() {
  const features = useFeatures();
  const view = useView();
  if (features.mode !== "both") return null;
  const opt = (v: View, label: string) => (
    <button key={v} type="button" className={view === v ? "on" : ""} aria-pressed={view === v} onClick={() => saveView(v)}>{label}</button>
  );
  return (
    <div className="view-switch" role="group" aria-label="View">
      {opt("all", "All")}{opt("product", "Product")}{opt("dev", "Dev")}
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { live } = useApp();
  const [notesOpen, setNotesOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const theme = useTheme();
  // a big screen can fold the menu into a thin strip (☰ opens it again, ⌘\ toggles); a phone keeps its own bar
  const [navHidden, setNavHidden] = useState<boolean>(() => {
    try { return localStorage.getItem(NAV_KEY) === "1"; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem(NAV_KEY, navHidden ? "1" : "0"); } catch { /* private window */ }
  }, [navHidden]);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      // KEYS.menuToggle (⌘\); ⌘ or Ctrl on every system, as before
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key === "\\") {
        e.preventDefault();
        setNavHidden((h) => !h);
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
  // v0.15.4 the menu by keyboard: F6 (or ⌃⌘M) jumps into it at the current page, ↑↓ Home End move, ↩ opens, Esc goes
  // back to the page; ⌃1–⌃9 open the first nine pages. The folded rail works the same way.
  const navHiddenRef = useRef(navHidden);
  navHiddenRef.current = navHidden;
  useEffect(() => {
    let back: HTMLElement | null = null;
    const shown = (el: Element | null): el is HTMLElement =>
      !!el && (typeof (el as HTMLElement).checkVisibility !== "function" || (el as HTMLElement).checkVisibility());
    const box = () => {
      const rail = document.querySelector(".side-rail .rail-nav");
      const nav = document.getElementById("nav");
      return [navHiddenRef.current ? rail : nav, rail, nav].find(shown) ?? null;
    };
    const links = (b: Element) => [...b.querySelectorAll<HTMLAnchorElement>("a[href]")];
    const jumpIn = () => {
      const b = box();
      (b?.querySelector<HTMLElement>('a[aria-current="page"]') ?? (b && links(b)[0]))?.focus();
    };
    const on = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      const at = document.activeElement as HTMLElement | null;
      if (matchesAny(e, KEYS.menuJump)) {
        e.preventDefault();
        if (!at?.closest("#nav, .rail-nav")) back = at;
        // Focus mode hides the menu: leave it first
        if (document.documentElement.dataset.focus) {
          window.dispatchEvent(new CustomEvent(FOCUS_EVENT, { detail: false }));
          window.setTimeout(jumpIn, 50);
        } else jumpIn();
        return;
      }
      const page = menuPageOf(e);
      if (page) {
        // off a Mac Ctrl+Alt is AltGr, which types { [ @ in a text field
        if (!isMac && isTyping(e.target)) return;
        const h = links(document.getElementById("nav") ?? document.body)[page - 1]?.getAttribute("href");
        if (!h) return;
        e.preventDefault();
        if (location.hash !== h) location.hash = h;
        return;
      }
      const b = at?.closest("#nav, .rail-nav, .ms-nav");
      if (!b || at?.tagName !== "A" || e.altKey || e.ctrlKey || e.metaKey) return;
      const list = links(b);
      const i = list.indexOf(at as HTMLAnchorElement);
      const move = (j: number) => {
        e.preventDefault();
        list[Math.max(0, Math.min(list.length - 1, j))]?.focus();
      };
      if (e.key === "ArrowDown" || e.key === "ArrowRight") move(i + 1);
      else if (e.key === "ArrowUp" || e.key === "ArrowLeft") move(i - 1);
      else if (e.key === "Home") move(0);
      else if (e.key === "End") move(list.length - 1);
      else if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        (at as HTMLAnchorElement).click();
      } else if (e.key === "Escape" && !b.matches(".ms-nav")) {
        // back to the page: where the focus was before the jump, else the page itself
        e.preventDefault();
        const main = document.getElementById("main");
        const to = back?.isConnected && main?.contains(back) ? back : main;
        back = null;
        to?.focus();
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
  const menuKeys = `${keyLabel(KEYS.menuToggle)}; ${keyLabel(KEYS.menuJump[0])} jumps into it`;
  return (
    <div className={`app${navHidden ? " nav-hidden" : ""}`}>
      {navHidden && (
        <div className="side-rail">
          <button className="rail-btn" type="button" onClick={() => setNavHidden(false)} aria-label="Show the menu" title={`Show the menu (${menuKeys})`}>
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" d="M4 7h16M4 12h16M4 17h16" />
            </svg>
          </button>
          <RailLinks />
          <div className="rail-foot">
            <SearchButton className="rail-btn" />
            <Bell onClick={() => setNotesOpen(true)} />
            <button className="rail-btn" type="button" onClick={theme.toggle}
              aria-label={theme.dark ? "Switch to the light theme" : "Switch to the dark theme"} title={theme.dark ? "Switch to the light theme" : "Switch to the dark theme"}>
              <ThemeIcon dark={theme.dark} />
            </button>
          </div>
        </div>
      )}
      <aside className="side">
        <div className="side-head">
          <button className="menu-btn" type="button" onClick={() => setMenuOpen(true)} aria-label="Menu" aria-expanded={menuOpen} aria-controls="menu-sheet"
            title={live === "live" ? "Menu" : `Menu (server events: ${live})`}>
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" d="M4 7h16M4 12h16M4 17h16" />
            </svg>
            {/* the phone bar has no Live pill: a dot says when the event stream is not live */}
            {live !== "live" && <span className={`menu-dot ${live === "off" ? "bad" : "warn"}`} aria-hidden="true" />}
          </button>
          <span className="brand"><Logo /><b>keel</b><span className="brand-v">v2 studio</span></span>
          <span className="side-tools"><Mascot /><SearchButton className="bell" /><Bell onClick={() => setNotesOpen(true)} />
            <button className="hide-nav" type="button" onClick={() => setNavHidden(true)} aria-label="Hide the menu" title={`Hide the menu (${menuKeys})`}>
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                <path fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" d="M15 6l-6 6 6 6" />
              </svg>
            </button>
          </span>
          <ProjectPicker />
          <ViewSwitch />
        </div>
        <Nav />
        <div className="side-foot">
          {/* v0.15.3 the version shows here: the head row is often too full for it */}
          <div className="row"><LiveDot /><EngineStatus version /></div>
          <button className="btn sm ghost theme-btn" id="theme" type="button" onClick={theme.toggle}
            aria-label={theme.dark ? "Switch to the light theme" : "Switch to the dark theme"} title={theme.dark ? "Switch to the light theme" : "Switch to the dark theme"}>
            <ThemeIcon dark={theme.dark} />
          </button>
        </div>
      </aside>
      {/* the budget bar stays on top while the page scrolls (a phone scrolls it away under its own header) */}
      <div className="app-col">
        <BudgetBar />
        <main id="main" tabIndex={-1}>{children}</main>
      </div>
      {menuOpen && <MenuSheet onClose={() => setMenuOpen(false)} theme={theme} />}
      {notesOpen && <NotificationDrawer onClose={() => setNotesOpen(false)} />}
      <Popups />
      <Launcher dark={theme.dark} toggleTheme={theme.toggle} toggleNav={() => setNavHidden((h) => !h)} openNotes={() => setNotesOpen(true)} />
      <KeySheet />
      {/* v0.15.2 the parts' watchers (KeelBot: a number on KeelBot and its own sound for an answer you did not see) */}
      <PartWatches />
    </div>
  );
}
