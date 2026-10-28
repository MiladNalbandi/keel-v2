// App-wide state: the hash route, the chosen project, the live event stream (SSE), notifications and toasts.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  api, ENGINE_CHANNEL, ENGINE_EVENT_TYPES, errorParts,
  type EngineEvent, type Health, type JobStep, type Notification as Note, type NotificationSettings, type Project,
} from "./api";
import { inboxApi } from "./inboxApi";
import { loadProjectLanguages } from "./components/stackLanguages";
import { desktopPop, NOTIFY_DEFAULTS, playSound, shouldAlert, unlockAudio, withDefaults } from "./notify";
import { hashFor, hashForScreen, parseHash, routeFromLink, type Route, type ScreenId } from "./routes";
import { usePagesVersion } from "./sdk/registry";

// ---------- route ----------

export function useRoute(): Route {
  const [hash, setHash] = useState(() => location.hash);
  // a plugin's page registers after keel started (its web part loads at start): the same link (#/map) may name it
  // now, so the link is read again when a page comes or goes
  const pages = usePagesVersion();
  useEffect(() => {
    const on = () => setHash(location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return useMemo(() => parseHash(hash), [hash, pages]);
}

export function go(page: ScreenId, arg?: string) {
  const h = hashFor(page, arg);
  if (location.hash !== h) location.hash = h;
  try {
    window.scrollTo(0, 0);
  } catch {
    /* jsdom */
  }
}

// ---------- context ----------

export type LiveStatus = "connecting" | "live" | "reconnecting" | "paused" | "off";
export type Popup = { key: number; note: Note };

interface Ctx {
  health: Health | null;
  projects: Project[];
  projectsError: string;
  projectsLoaded: boolean;
  project: Project | null;
  pid: string | null;
  setProjectId: (id: string) => void;
  reloadProjects: () => Promise<void>;
  /** Bumps (at most every 300 ms) when the server says something changed. Pages refetch on it. */
  tick: number;
  live: LiveStatus;
  /** The last engine events for this project, newest last. */
  recent: EngineEvent[];
  /** Live agent steps from SSE, by call_id (= job id). */
  liveSteps: Record<string, JobStep[]>;
  /** keel 0.18.2: the text an agent (or KeelBot) is writing right now, by call_id; gone when the step with the
   *  whole message arrives (helper.delta, agent.delta). */
  liveText: Record<string, string>;
  /** 0.18.23 what KeelBot thinks right now, by call_id (helper.thinking.delta: the turn's thinking so far); apart from
   *  liveText, never the answer */
  liveThinking: Record<string, string>;
  notes: Note[];
  unread: number;
  markRead: (id: string) => void;
  markAllRead: () => void;
  /** v0.4.1: delete one notification, or clear the whole list. */
  deleteNote: (id: string) => void;
  clearNotes: () => void;
  nset: NotificationSettings;
  saveNset: (s: NotificationSettings) => void;
  /** Push a notification as if it came from the server (used by "Send a test notification"). */
  notifyLocal: (n: Note) => void;
  openNote: (n: Note) => void;
  popups: Popup[];
  dismissPopup: (key: number) => void;
  /** The last notification that alerted (sound / pop-up rules passed); the mascot reacts to it. */
  alert: { key: number; note: Note } | null;
  /** "Show keel" (the mascot by the bell). Kept in this browser: the api's notification settings are typed. */
  showMascot: boolean;
  setShowMascot: (on: boolean) => void;
  /** keel 0.18.63 "Follow other keel windows" (Settings › This browser): on, this browser goes along with the shared
   *  focus (followsFocus); off, it keeps its own project and KeelBot chat. Kept in this browser, on at first. */
  followFocus: boolean;
  setFollowFocus: (on: boolean) => void;
  toast: (msg: string) => void;
}

const AppCtx = createContext<Ctx | null>(null);

export function useApp(): Ctx {
  const c = useContext(AppCtx);
  if (!c) throw new Error("useApp outside AppProvider");
  return c;
}

const PROJECT_KEY = "keel2.project";
const MASCOT_KEY = "keel2.mascot";
// v0.15.0 off unless the person turned it on (Settings › This browser, or the notification settings)
function storedMascot(): boolean {
  try {
    return localStorage.getItem(MASCOT_KEY) === "1";
  } catch {
    return false;
  }
}
const FOLLOW_KEY = "keel2.followFocus";
/** keel 0.18.63 does this browser go along with the shared focus below ("Follow other keel windows" in Settings › This
 *  browser)? On (the default, and when storage is blocked): it follows the project and KeelBot chat the other keel
 *  windows pick, and its own picks move them. Off: neither way; it keeps its own project and chat. Read at each use, so
 *  the switch counts at once in every tab of this browser. */
export function followsFocus(): boolean {
  try {
    return localStorage.getItem(FOLLOW_KEY) !== "0";
  } catch {
    return true;
  }
}
/** keel 0.18.6 this tab's id in the shared focus (api ui/UiFocus.kt): the project and KeelBot chat every keel front end
 *  (the dashboard's tabs, keel TUI) shows; a tab never follows its own change. */
export const FOCUS_CLIENT = `web-${Math.random().toString(36).slice(2, 10)}`;
export type UiFocus = { project: string | null; chats: Record<string, string>; by?: string | null; client?: string | null };
/** Tell keel what this tab shows now (the project, or the KeelBot chat on a project); an older keel without it: nothing.
 *  keel 0.18.63 nothing either while this browser keeps its own project and chat (followsFocus). */
export function shareFocus(patch: { project?: string; chat?: string; chat_project?: string; close?: boolean }) {
  if (!followsFocus()) return;
  void api.setFocus({ ...patch, by: "web", client: FOCUS_CLIENT }).catch(() => undefined);
}
/** The window event a focus change from another front end comes as, for the parts that follow it (KeelBot's chat). */
export const UI_FOCUS_EVENT = "keel:ui-focus";

function storedProject(): string | null {
  try {
    return localStorage.getItem(PROJECT_KEY);
  } catch {
    return null;
  }
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsError, setProjectsError] = useState("");
  const [projectsLoaded, setProjectsLoaded] = useState(false);
  const [pidState, setPidState] = useState<string | null>(storedProject);
  const [tick, setTick] = useState(0);
  const [live, setLive] = useState<LiveStatus>("connecting");
  const [recent, setRecent] = useState<EngineEvent[]>([]);
  const [liveSteps, setLiveSteps] = useState<Record<string, JobStep[]>>({});
  const [liveText, setLiveText] = useState<Record<string, string>>({});
  const [liveThinking, setLiveThinking] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Note[]>([]);
  const [nset, setNset] = useState<NotificationSettings>(NOTIFY_DEFAULTS);
  const [popups, setPopups] = useState<Popup[]>([]);
  const [alert, setAlert] = useState<{ key: number; note: Note } | null>(null);
  const [showMascot, setShowMascotState] = useState(storedMascot);
  const setShowMascot = useCallback((on: boolean) => {
    setShowMascotState(on);
    try {
      localStorage.setItem(MASCOT_KEY, on ? "1" : "0");
    } catch {
      /* private window */
    }
  }, []);
  const [followFocus, setFollowFocusState] = useState(followsFocus);
  // keel 0.18.63 nothing moves when the switch changes: the next pick here, or in another window, is shared again
  const setFollowFocus = useCallback((on: boolean) => {
    setFollowFocusState(on);
    try {
      localStorage.setItem(FOLLOW_KEY, on ? "1" : "0");
    } catch {
      /* private window */
    }
  }, []);
  const [toastMsg, setToastMsg] = useState<{ text: string; n: number } | null>(null);

  const toast = useCallback((text: string) => setToastMsg({ text, n: Date.now() + Math.random() }), []);

  const reloadProjects = useCallback(async () => {
    try {
      setProjects(await api.projects());
      setProjectsError("");
    } catch (e) {
      const p = errorParts(e);
      setProjectsError(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setProjectsLoaded(true);
    }
  }, []);

  useEffect(() => {
    void reloadProjects();
    api.health().then(setHealth, () => setHealth(null));
    api.notifications().then(setNotes, () => undefined);
    api.notificationSettings().then((s) => setNset(withDefaults(s)), () => undefined);
    unlockAudio();
  }, [reloadProjects]);

  const project = projects.find((p) => p.id === pidState) ?? projects[0] ?? null;
  const pid = project?.id ?? null;
  // keel 0.18.28 the project's stack languages, loaded when the project opens (highlight.ts)
  useEffect(() => {
    void loadProjectLanguages(pid);
  }, [pid]);

  const keepProject = useCallback((id: string) => {
    setPidState(id);
    try {
      localStorage.setItem(PROJECT_KEY, id);
    } catch {
      /* private window */
    }
  }, []);
  // the person picks a project: this tab shows it, and keel TUI and the other tabs follow (0.18.6; 0.18.63 not while
  // this browser keeps its own project: shareFocus says nothing then)
  const setProjectId = useCallback((id: string) => {
    keepProject(id);
    shareFocus({ project: id });
  }, [keepProject]);

  // 0.18.6 at the start: the project keel TUI or another tab is on, when keel knows one (0.18.63 not while this browser
  // keeps its own: it opens on its own last project)
  useEffect(() => {
    if (!projectsLoaded || !followsFocus()) return;
    api.focus().then((f) => {
      if (f?.project && f.project !== pidState && projects.some((p) => p.id === f.project)) keepProject(f.project);
    }, () => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectsLoaded]);

  // v0.15.8 #/projects/<id>/<page> (a keel_dashboard link) picks that project, then shows the plain #/<page> link
  useEffect(() => {
    const pick = () => {
      const r = parseHash(location.hash);
      if (!r.project || !projects.some((p) => p.id === r.project)) return;
      setProjectId(r.project);
      const h = location.hash.replace(/^#\/?projects\/[^/]+/, "#");
      history.replaceState(null, "", h);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    };
    pick();
    window.addEventListener("hashchange", pick);
    return () => window.removeEventListener("hashchange", pick);
  }, [projects, setProjectId]);

  // ---- notifications ----
  const nsetRef = useRef(nset);
  nsetRef.current = nset;
  const pidRef = useRef(pid);
  pidRef.current = pid;
  const projectsRef = useRef(projects);
  projectsRef.current = projects;
  const popKey = useRef(0);
  const notesRef = useRef(notes);
  notesRef.current = notes;

  const openNote = useCallback(
    (n: Note) => {
      if (!n.read) {
        setNotes((ns) => ns.map((x) => (x.id === n.id ? { ...x, read: true } : x)));
        api.readOne(n.id).catch(() => undefined);
      }
      const r = routeFromLink(n.link);
      const project = r.project ?? n.project_id;
      if (project && project !== pidRef.current && projectsRef.current.some((p) => p.id === project)) {
        setProjectId(project);
      }
      // a page that is neither keel's own nor registered (an add-on's, or a plugin's this keel does not have: the Graph's
      // "Index ready" names the Code page): its own link, and the router shows the add-on's page, or Flow (step 6)
      if (r.page === "addon" && r.screen) {
        const h = hashForScreen(r.screen, r.arg);
        if (location.hash !== h) location.hash = h;
      }
      // v0.15.3 a flow's notification opens that flow (its thread), not just the project's newest one
      else go(r.page, r.arg ?? (r.page === "flow" && n.thread_id ? n.thread_id : undefined));
    },
    [setProjectId],
  );

  const arrive = useCallback(
    (n: Note) => {
      if (notesRef.current.some((x) => x.id === n.id)) return;
      notesRef.current = [n, ...notesRef.current];
      setNotes((ns) => (ns.some((x) => x.id === n.id) ? ns : [n, ...ns].slice(0, 200)));
      const s = nsetRef.current;
      if (!shouldAlert(n, s, pidRef.current)) return;
      setAlert({ key: ++popKey.current, note: n });
      playSound(n.type, s);
      if (s.popup) {
        const key = ++popKey.current;
        setPopups((p) => [...p.slice(-3), { key, note: n }]);
        window.setTimeout(() => setPopups((p) => p.filter((x) => x.key !== key)), 9000);
      }
      if (s.desktop) {
        const name = projectsRef.current.find((p) => p.id === n.project_id)?.name ?? n.project_id;
        desktopPop(n, name, () => openNote(n));
      }
    },
    [openNote],
  );

  const markRead = useCallback((id: string) => {
    setNotes((ns) => ns.map((x) => (x.id === id ? { ...x, read: true } : x)));
    api.readOne(id).catch(() => undefined);
  }, []);
  const markAllRead = useCallback(() => {
    setNotes((ns) => ns.map((x) => ({ ...x, read: true })));
    api.readAll().catch(() => undefined);
  }, []);
  const deleteNote = useCallback((id: string) => {
    setNotes((ns) => ns.filter((x) => x.id !== id));
    notesRef.current = notesRef.current.filter((x) => x.id !== id);
    inboxApi.deleteNote(id).catch(() => undefined);
  }, []);
  const clearNotes = useCallback(() => {
    setNotes([]);
    notesRef.current = [];
    inboxApi.clearNotes().catch((e) => toast(`Not cleared: ${errorParts(e).message}`));
  }, [toast]);
  const saveNset = useCallback(
    (s: NotificationSettings) => {
      setNset(s);
      api.saveNotificationSettings(s).catch((e) => toast(`Not saved: ${errorParts(e).message}`));
    },
    [toast],
  );
  const dismissPopup = useCallback((key: number) => setPopups((p) => p.filter((x) => x.key !== key)), []);

  // ---- SSE: one stream for the chosen project (live data), reconnect with backoff ----
  const pending = useRef<number | null>(null);
  const bump = useCallback(() => {
    if (pending.current !== null) return;
    pending.current = window.setTimeout(() => {
      pending.current = null;
      setTick((t) => t + 1);
    }, 300);
  }, []);

  useEffect(() => {
    if (typeof EventSource === "undefined") {
      setLive("off");
      return;
    }
    let es: EventSource | null = null;
    let timer: number | null = null;
    let attempt = 0;
    let opened = false;
    let closed = false;

    const onEngine = (e: MessageEvent) => {
      let ev: EngineEvent;
      try {
        ev = JSON.parse(e.data);
      } catch {
        return;
      }
      // 0.18.23 KeelBot's thinking as it is written: live like the text, kept apart from it
      if (ev.type === "helper.thinking.delta" && ev.call_id) {
        const text = String((ev.data as { text?: unknown }).text ?? "");
        setLiveThinking((m) => ({ ...m, [ev.call_id!]: text }));
        return;
      }
      if (ev.type === "helper.finished" && ev.call_id) {
        setLiveThinking((m) => {
          if (!(ev.call_id! in m)) return m;
          const { [ev.call_id!]: _done, ...rest } = m;
          return rest;
        });
      }
      // live text: many per answer, so it is neither a recent event nor a reason to refetch
      if ((ev.type === "helper.delta" || ev.type === "agent.delta") && ev.call_id) {
        const text = String((ev.data as { text?: unknown }).text ?? "");
        setLiveText((m) => ({ ...m, [ev.call_id!]: text }));
        return;
      }
      setRecent((r) => [...r.slice(-299), ev]);
      if ((ev.type === "agent.step" || ev.type === "code.step" || ev.type === "helper.step") && ev.call_id) {
        // a whole message arrived: the live text of that block is in it now
        if (["text", "answer"].includes(String((ev.data as { kind?: unknown }).kind))) {
          setLiveText((m) => {
            if (!(ev.call_id! in m)) return m;
            const { [ev.call_id!]: _done, ...rest } = m;
            return rest;
          });
        }
        const d = ev.data as Partial<JobStep>;
        const step: JobStep = { n: Number(d.n) || 0, at: ev.at, kind: String(d.kind ?? "text"), text: String(d.text ?? ""),
          tool: d.tool, server: d.server, path: d.path, diff: d.diff, ms: d.ms, ok: d.ok };
        setLiveSteps((m) => {
          const prev = m[ev.call_id!] ?? [];
          if (prev.some((s) => s.n === step.n)) return m;
          return { ...m, [ev.call_id!]: [...prev, step].slice(-500) };
        });
        return; // steps are frequent: the Live page reads them directly, no refetch needed
      }
      bump();
    };
    const onNote = (e: MessageEvent) => {
      try {
        arrive(JSON.parse(e.data) as Note);
      } catch {
        /* ignore */
      }
    };
    const onChanged = (e?: MessageEvent) => {
      void reloadProjects();
      bump();
      // keel 0.18.28 a stack changed (saved, installed, turned off): its languages, without a reload
      try {
        const d = e?.data ? (JSON.parse(e.data) as { id?: string; stacks?: boolean }) : null;
        if (d?.stacks && (!d.id || d.id === pid)) void loadProjectLanguages(pid);
      } catch {
        /* not ours to read */
      }
    };
    // v0.4.1: a gate was decided somewhere (Flow page, Inbox, MCP, the run mode): its notifications are done.
    const onDone = (e: MessageEvent) => {
      try {
        const d = JSON.parse(e.data) as { ids?: (string | number)[]; read?: boolean };
        const ids = new Set((d.ids ?? []).map(String));
        if (ids.size) setNotes((ns) => ns.map((x) => (ids.has(String(x.id)) ? { ...x, done: true, read: d.read === false ? x.read : true } : x)));
      } catch {
        /* ignore */
      }
    };

    const connect = () => {
      if (closed) return;
      // One stream per tab: this project's events plus the bell's events from every project.
      // Browsers allow only 6 connections per server, so every extra stream per tab can freeze other tabs.
      es = new EventSource(api.eventsUrl(pid, true));
      ENGINE_EVENT_TYPES.forEach((t) => es!.addEventListener(t, onEngine as EventListener));
      es.addEventListener(ENGINE_CHANNEL, onEngine as EventListener);
      es.addEventListener("notification", onNote as EventListener);
      es.addEventListener("project.changed", onChanged as EventListener);
      es.addEventListener("notification.done", onDone as EventListener);
      // 0.18.6 another front end (keel TUI, another tab) moved to a project or a KeelBot chat: follow it (0.18.63 unless
      // this browser keeps its own project and chat; KeelBot's chat then hears nothing either)
      es.addEventListener("ui.focus", ((e: MessageEvent) => {
        try {
          const f = JSON.parse(e.data) as UiFocus;
          if (!f || f.client === FOCUS_CLIENT || !followsFocus()) return;
          if (f.project) keepProject(f.project);
          window.dispatchEvent(new CustomEvent(UI_FOCUS_EVENT, { detail: f }));
        } catch {
          /* not ours to read */
        }
      }) as EventListener);
      es.onmessage = onEngine;
      es.onopen = () => {
        attempt = 0;
        setLive("live");
        // a RE-open (after a drop or a keel restart) missed events: catch up like a tab shown again
        if (opened) {
          void reloadProjects();
          bump();
        }
        opened = true;
      };
      es.onerror = () => {
        es?.close();
        es = null;
        if (closed) return;
        setLive("reconnecting");
        const wait = Math.min(30000, 1000 * 2 ** attempt++);
        timer = window.setTimeout(connect, wait);
      };
    };
    // A tab in the background gives its connection back after 30 s and catches up when it is shown again.
    let hiddenTimer: number | null = null;
    const onVisibility = () => {
      if (document.hidden) {
        hiddenTimer = window.setTimeout(() => {
          if (timer) window.clearTimeout(timer);
          es?.close();
          es = null;
          setLive("paused");
        }, 30000);
      } else {
        if (hiddenTimer) window.clearTimeout(hiddenTimer);
        hiddenTimer = null;
        if (!es && !closed) {
          attempt = 0;
          connect();
          void reloadProjects();
          bump();
        }
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    setLive("connecting");
    setRecent([]);
    connect();
    return () => {
      closed = true;
      document.removeEventListener("visibilitychange", onVisibility);
      if (hiddenTimer) window.clearTimeout(hiddenTimer);
      if (timer) window.clearTimeout(timer);
      es?.close();
    };
  }, [pid, arrive, bump, reloadProjects]);

  // waiting / running counts in the project list change with every event
  useEffect(() => {
    if (tick) void reloadProjects();
  }, [tick, reloadProjects]);

  useEffect(() => {
    if (!toastMsg) return;
    const t = window.setTimeout(() => setToastMsg(null), 2600);
    return () => window.clearTimeout(t);
  }, [toastMsg]);

  const unread = notes.filter((n) => !n.read).length;

  const value = useMemo<Ctx>(
    () => ({
      health, projects, projectsError, projectsLoaded, project, pid, setProjectId, reloadProjects, tick, live, recent,
      liveSteps, liveText, liveThinking, notes, unread, markRead, markAllRead, deleteNote, clearNotes, nset, saveNset, notifyLocal: arrive, openNote, popups, dismissPopup, alert, showMascot, setShowMascot, followFocus, setFollowFocus, toast,
    }),
    [health, projects, projectsError, projectsLoaded, project, pid, setProjectId, reloadProjects, tick, live, recent,
      liveSteps, liveText, liveThinking, notes, unread, markRead, markAllRead, deleteNote, clearNotes, nset, saveNset, arrive, openNote, popups, dismissPopup, alert, showMascot, setShowMascot, followFocus, setFollowFocus, toast],
  );

  return (
    <AppCtx.Provider value={value}>
      {children}
      {toastMsg && (
        <div className="toast" role="status" key={toastMsg.n}>
          {toastMsg.text}
        </div>
      )}
    </AppCtx.Provider>
  );
}

// ---------- data loading ----------

export type Loaded<T> = {
  data: T | null;
  error: { message: string; hint?: string } | null;
  loading: boolean;
  reload: () => Promise<void>;
  setData: (d: T | null | ((old: T | null) => T | null)) => void;
};

/**
 * Run `fn` now, when `key` changes, and on every live tick (unless `live: false`).
 * `key` null skips loading (for example when no project is chosen).
 */
export function useLoad<T>(key: string | null, fn: () => Promise<T>, opts: { live?: boolean } = {}): Loaded<T> {
  const { tick } = useApp();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);
  const [loading, setLoading] = useState(key !== null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const keyRef = useRef(key);
  keyRef.current = key;
  // each load takes a number; only the newest one may write (an older, slower answer is dropped)
  const seq = useRef(0);
  const liveTick = opts.live === false ? 0 : tick;

  const reload = useCallback(async () => {
    const k = keyRef.current;
    if (k === null) return;
    const mine = ++seq.current;
    setLoading(true);
    try {
      const d = await fnRef.current();
      if (keyRef.current !== k || seq.current !== mine) return;
      setData(d);
      setError(null);
    } catch (e) {
      if (keyRef.current !== k || seq.current !== mine) return;
      setError(errorParts(e));
    } finally {
      if (keyRef.current === k && seq.current === mine) setLoading(false);
    }
  }, []);

  // a different key is a different thing: do not show the old one while the new one loads
  useEffect(() => {
    setData(null);
    setError(null);
    setLoading(key !== null);
  }, [key]);

  useEffect(() => {
    void reload();
  }, [key, liveTick, reload]);

  return { data, error, loading, reload, setData };
}
