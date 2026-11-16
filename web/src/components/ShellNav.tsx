// keel 0.28 the redesigned menu and page head (docs: the redesign canvas). The menu has six main places for the
// view, the pages a person pinned, the project's recent flows, and at its foot All pages, Add features, Settings and
// Start here. Every other page is one ⌘K away, or in All pages, where a click pins it.
//
//   ┌ keel                ⌕  ◧ ┐      ┌ greeter › Review  ◆ Pinned      Today $0.42 · Month …   Search ⌘K   🔔 ┐
//   │ [project switcher]       │
//   │ [+ New flow          N]  │
//   │ Home · Inbox 2 · Flow 1 … │
//   │ PINNED  Review · Tasks …  │
//   │ RECENT FLOWS              │
//   │ All pages ⌘K · Add …      │

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { navGroups, useFeatures, useView } from "../addons";
import { api, type RunRow } from "../api";
import { mainIds, pageIndex, usePins } from "../nav";
import { hashFor, hashForScreen } from "../routes";
import { usePages, useSlot } from "../sdk/registry";
import { SLOTS, type NavBadgeItem } from "../sdk/slots";
import { useApp, useLoad, useRoute } from "../state";
import { keyLabel, menuPageKey } from "../keys";
import { isCoreOnly, NoPagesYet } from "./CoreOnly";
import { NavIcon } from "./NavIcons";
import { StartFlowDrawer } from "./StartFlow";

export const PAGES_EVENT = "keel:all-pages";
/** Opens All pages (the menu's foot link, ⌘K's action, Add features). */
export const openAllPages = () => window.dispatchEvent(new Event(PAGES_EVENT));

export const START_EVENT = "keel:start-flow";
type StartWith = { title?: string; request?: string; workflowId?: string };
/** Opens Start a flow (the menu's New flow, the N key, Home's box), with what to build already written. */
export const openStartFlow = (title?: string, more: Omit<StartWith, "title"> = {}) =>
  window.dispatchEvent(new CustomEvent<StartWith>(START_EVENT, { detail: { title, ...more } }));

/** The one Start a flow drawer the shell keeps; openStartFlow opens it. */
export function StartFlowHost() {
  const [open, setOpen] = useState<StartWith | null>(null);
  useEffect(() => {
    const on = (e: Event) => setOpen((e as CustomEvent<StartWith>).detail ?? {});
    window.addEventListener(START_EVENT, on);
    return () => window.removeEventListener(START_EVENT, on);
  }, []);
  if (!open) return null;
  return (
    <StartFlowDrawer
      title={open.title || undefined}
      request={open.request}
      workflowId={open.workflowId}
      onClose={() => setOpen(null)}
    />
  );
}

/** The page a route shows: an add-on's page names its screen. */
export function currentPage(route: { page: string; screen?: string }): string {
  return route.page === "addon" ? route.screen ?? "" : route.page;
}

/** The menu's pages and their facts, for the menu, the pins and the page head. */
export function useMenu() {
  const features = useFeatures();
  const view = useView();
  usePages();
  const groups = navGroups(features, view);
  const index = pageIndex(groups);
  const href = (id: string) => (index.get(id)?.addon ? hashForScreen(id) : hashFor(id));
  const label = (id: string) => (id === "home" ? "Home" : index.get(id)?.label ?? id);
  return { groups, index, view, href, label, main: mainIds(view, groups) };
}

/** v0.15.2 a part's count on a menu page (slot nav.badge: KeelBot's new answers on KeelBot), in the menu or the rail. */
function PartBadges({ page, kind }: { page: string; kind: "nav" | "rail" }) {
  const items = useSlot<NavBadgeItem>(SLOTS.navBadge).filter((b) => b.page === page);
  return (
    <>
      {items.map((b) => (
        <b.component key={b.id} kind={kind} />
      ))}
    </>
  );
}

function Count({ n, tone, label, testId }: { n: number; tone: "warn" | "run"; label: string; testId?: string }) {
  if (n <= 0) return null;
  return (
    <span className={`count${tone === "run" ? " run" : ""}`} aria-label={label} title={label} data-testid={testId}>
      {n}
    </span>
  );
}

/** The six main places, the pins, the recent flows and the foot. `onPick` runs after a link is used (the phone menu
 *  closes). */
export function MainNav({ onPick, rail }: { onPick?: () => void; rail?: boolean }) {
  const route = useRoute();
  const current = currentPage(route);
  const { project, projects } = useApp();
  const m = useMenu();
  const { pins } = usePins();
  const features = useFeatures();
  const pagesList = usePages();
  const waitingAll = projects.reduce((a, p) => a + (p.waiting || 0), 0);
  const running = project?.running ?? 0;
  const count = (id: string) =>
    id === "inbox" ? (
      <Count n={waitingAll} tone="warn" label={`${waitingAll} waiting for you`} testId={onPick || rail ? undefined : "inbox-count"} />
    ) : id === "flow" || id === "live" ? (
      <Count n={running} tone="run" label={`${running} running now`} />
    ) : null;
  let n = 0;
  const tip = (label: string) => (++n <= 9 ? `${label} (${keyLabel(menuPageKey(n))})` : label);
  const link = (id: string, cls: string) => (
    <a
      key={id}
      href={m.href(id)}
      className={cls}
      aria-current={current === id || (id === "home" && !current) ? "page" : undefined}
      onClick={onPick}
      title={tip(m.label(id))}
      aria-label={rail ? m.label(id) : undefined}
    >
      <NavIcon id={id} size={rail ? 20 : 18} />
      {!rail && <span className="nav-l">{m.label(id)}</span>}
      {count(id)}
      <PartBadges page={id} kind={rail ? "rail" : "nav"} />
    </a>
  );
  const pinned = pins.filter((id) => m.index.has(id) && !m.main.includes(id));
  // v0.16.0 a keel with only its core has no Project pages: the pins say so, with + Add pages
  const coreOnly = isCoreOnly(features, m.view, pagesList);
  if (rail) return <>{m.main.map((id) => link(id, "rail-link"))}{pinned.map((id) => link(id, "rail-link rail-pin"))}</>;
  return (
    <>
      <div className="nav-main">{m.main.map((id) => link(id, "nav-i"))}</div>
      <div className="nav-sec nav-pins">
        <div className="nav-h2">
          <span>Pinned</span>
          <button type="button" className="nav-edit" onClick={openAllPages}>
            Edit
          </button>
        </div>
        {pinned.map((id) => (
          <a key={id} href={m.href(id)} className="nav-s" aria-current={current === id ? "page" : undefined} onClick={onPick} title={tip(m.label(id))}>
            <span className="nav-ini" aria-hidden="true">{initials(m.label(id))}</span>
            <span className="nav-l">{m.label(id)}</span>
          </a>
        ))}
        {pinned.length === 0 && !coreOnly && <span className="nav-empty">Pin a page from All pages or its ◇ Pin button.</span>}
      </div>
      {coreOnly && <NoPagesYet onPick={onPick} />}
      <RecentFlows onPick={onPick} />
    </>
  );
}

/** The menu's foot: All pages (⌘K finds them too), Add features, Settings, Start here. */
export function NavFoot({ onPick }: { onPick?: () => void }) {
  const route = useRoute();
  const current = currentPage(route);
  return (
    <div className="nav-foot2">
      <button type="button" className="nav-s" onClick={() => { onPick?.(); openAllPages(); }}>
        <NavIcon id="projects" size={16} />
        <span className="nav-l">All pages</span>
      </button>
      <a href={hashFor("features")} className="nav-s" aria-current={current === "features" ? "page" : undefined} onClick={onPick}>
        <NavIcon id="plugins" size={16} />
        <span className="nav-l">Add features</span>
      </a>
      <a href={hashFor("settings")} className="nav-s" aria-current={current === "settings" ? "page" : undefined} onClick={onPick}>
        <NavIcon id="settings" size={16} />
        <span className="nav-l">Settings</span>
      </a>
      <a href={hashFor("help")} className="nav-s" aria-current={current === "help" ? "page" : undefined} onClick={onPick}>
        <NavIcon id="help" size={16} />
        <span className="nav-l">Start here</span>
      </a>
    </div>
  );
}

const initials = (label: string) =>
  label.replace(/[^A-Za-z ]/g, "").split(" ").filter(Boolean).map((w) => w[0]).join("").slice(0, 2).toUpperCase() || label.slice(0, 2);

/** What a run is now, as a dot: waits for you, runs, done, or stopped. */
export function runTone(r: Pick<RunRow, "status" | "waiting">): "wait" | "run" | "ok" | "stop" | "bad" {
  if (r.waiting) return "wait";
  if (r.status === "running") return "run";
  if (r.status === "done" || r.status === "completed") return "ok";
  if (r.status === "failed" || r.status === "error") return "bad";
  return "stop";
}

/** The project's newest flows (five), each one click away. */
function RecentFlows({ onPick }: { onPick?: () => void }) {
  const { pid, tick } = useApp();
  const runs = useLoad(pid ? `nav-runs:${pid}` : null, () => api.runs(pid!, undefined, 5), { live: false });
  const reload = runs.reload;
  // new events: look again, at most every few seconds
  const last = useRef(0);
  useEffect(() => {
    if (!tick || Date.now() - last.current < 5000) return;
    last.current = Date.now();
    void reload();
  }, [tick, reload]);
  const list = runs.data ?? [];
  if (!pid) return null;
  return (
    <div className="nav-sec nav-recent">
      <div className="nav-h2">
        <span>Recent flows</span>
      </div>
      {list.map((r) => (
        <a key={r.thread_id} href={hashFor("flow", r.thread_id)} className="nav-s" onClick={onPick} title={r.title}>
          <span className={`nav-dot t-${runTone(r)}`} aria-hidden="true" />
          <span className="nav-l">{r.title || "Untitled flow"}</span>
          <span className="sr-only">{runTone(r) === "wait" ? "waits for you" : r.status}</span>
        </a>
      ))}
      {runs.data && list.length === 0 && <span className="nav-empty">No flow yet in this project.</span>}
    </div>
  );
}

/** The page head's left side: project › page, and Pin for a page that is not a main place. */
export function Crumb() {
  const route = useRoute();
  const current = currentPage(route) || "home";
  const { project } = useApp();
  const m = useMenu();
  const { pinned, toggle } = usePins();
  const [said, setSaid] = useState<string | null>(null);
  const canPin = m.index.has(current) && !m.main.includes(current);
  const on = pinned(current);
  return (
    <div className="tb-crumb">
      {project && <span className="tb-proj">{project.name}</span>}
      {project && <span aria-hidden="true" className="tb-sep">›</span>}
      <span className="tb-page">{current === "features" ? "Add features" : m.label(current)}</span>
      {canPin && (
        <button
          type="button"
          className={`tb-pin${on ? " on" : ""}`}
          aria-pressed={on}
          onClick={() => setSaid(toggle(current) ? "Pinned to the menu" : "Not pinned any more")}
        >
          {on ? "◆ Pinned" : "◇ Pin"}
        </button>
      )}
      {said && <Said text={said} onDone={() => setSaid(null)} />}
    </div>
  );
}

function Said({ text, onDone }: { text: string; onDone: () => void }) {
  useEffect(() => {
    const t = window.setTimeout(onDone, 2200);
    return () => window.clearTimeout(t);
  }, [text, onDone]);
  return (
    <span className="tb-said" role="status">
      {text}
    </span>
  );
}

/** All pages: every page of this keel by group, a search, and a pin on each. Opens from the menu's foot and ⌘K. */
export function AllPages() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const on = () => setOpen(true);
    window.addEventListener(PAGES_EVENT, on);
    return () => window.removeEventListener(PAGES_EVENT, on);
  }, []);
  if (!open) return null;
  return <AllPagesSheet onClose={() => setOpen(false)} />;
}

function AllPagesSheet({ onClose }: { onClose: () => void }) {
  const m = useMenu();
  const { pinned, toggle } = usePins();
  const [q, setQ] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    input.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeRef.current();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      before?.focus?.();
    };
  }, []);
  const groups = useMemo(() => {
    const t = q.trim().toLowerCase();
    return m.groups
      .map((g) => ({ ...g, pages: g.pages.filter((p) => !t || `${p.label} ${g.label}`.toLowerCase().includes(t)) }))
      .filter((g) => g.pages.length > 0);
  }, [m.groups, q]);
  const first = groups[0]?.pages[0];
  return createPortal(
    <>
      <div className="scrim" onClick={onClose} />
      <div className="all-pages" role="dialog" aria-modal="true" aria-label="All pages">
        <div className="ap-head">
          <label htmlFor="ap-q" className="sr-only">
            Find a page
          </label>
          <input
            id="ap-q"
            ref={input}
            className="ap-q"
            placeholder="Find a page… (↩ opens the first)"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && first) {
                location.hash = m.href(first.id);
                onClose();
              }
            }}
          />
          <button type="button" className="btn sm ghost" onClick={onClose}>
            Close <kbd>esc</kbd>
          </button>
        </div>
        <p className="ap-sub">Every page of this keel. ◇ pins one to the menu. Turn features on or off in <a href={hashFor("features")} onClick={onClose}>Add features</a>.</p>
        <nav className="ap-grid" aria-label="All pages">
          {groups.map((g) => (
            <section key={g.id} className="ap-group">
              <h3>{g.label}</h3>
              {g.pages.map((p) => {
                const on = pinned(p.id);
                return (
                  <div key={p.id} className="ap-row">
                    <a href={m.href(p.id)} onClick={onClose}>
                      {p.label}
                    </a>
                    {!m.main.includes(p.id) && (
                      <button
                        type="button"
                        className={`ap-pin${on ? " on" : ""}`}
                        aria-pressed={on}
                        aria-label={`${on ? "Unpin" : "Pin"} ${p.label}`}
                        onClick={() => toggle(p.id)}
                      >
                        {on ? "◆" : "◇"}
                      </button>
                    )}
                  </div>
                );
              })}
            </section>
          ))}
          {groups.length === 0 && <p className="sub">No page matches “{q}”.</p>}
        </nav>
      </div>
    </>,
    document.body,
  );
}
