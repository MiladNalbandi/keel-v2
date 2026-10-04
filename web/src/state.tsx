// App-wide state: the hash route, the chosen project, the live event stream (SSE), notifications and toasts.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  api, ENGINE_EVENT_TYPES, errorParts,
  type EngineEvent, type Health, type JobStep, type Notification as Note, type NotificationSettings, type Project,
} from "./api";
import { desktopPop, NOTIFY_DEFAULTS, playSound, shouldAlert, unlockAudio, withDefaults } from "./notify";
import { hashFor, parseHash, routeFromLink, type Route, type ScreenId } from "./routes";

// ---------- route ----------

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(location.hash));
  useEffect(() => {
    const on = () => setRoute(parseHash(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
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
  notes: Note[];
  unread: number;
  markRead: (id: string) => void;
  markAllRead: () => void;
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
function storedMascot(): boolean {
  try {
    return localStorage.getItem(MASCOT_KEY) !== "0";
  } catch {
    return true;
  }
}
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

  const setProjectId = useCallback((id: string) => {
    setPidState(id);
    try {
      localStorage.setItem(PROJECT_KEY, id);
    } catch {
      /* private window */
    }
  }, []);

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
      if (n.project_id && n.project_id !== pidRef.current && projectsRef.current.some((p) => p.id === n.project_id)) {
        setProjectId(n.project_id);
      }
      const r = routeFromLink(n.link);
      go(r.page, r.arg);
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
    let closed = false;

    const onEngine = (e: MessageEvent) => {
      let ev: EngineEvent;
      try {
        ev = JSON.parse(e.data);
      } catch {
        return;
      }
      setRecent((r) => [...r.slice(-299), ev]);
      if (ev.type === "agent.step" && ev.call_id) {
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
    const onChanged = () => {
      void reloadProjects();
      bump();
    };

    const connect = () => {
      if (closed) return;
      // One stream per tab: this project's events plus the bell's events from every project.
      // Browsers allow only 6 connections per server, so every extra stream per tab can freeze other tabs.
      es = new EventSource(api.eventsUrl(pid, true));
      ENGINE_EVENT_TYPES.forEach((t) => es!.addEventListener(t, onEngine as EventListener));
      es.addEventListener("notification", onNote as EventListener);
      es.addEventListener("project.changed", onChanged);
      es.onmessage = onEngine;
      es.onopen = () => {
        attempt = 0;
        setLive("live");
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
      liveSteps, notes, unread, markRead, markAllRead, nset, saveNset, notifyLocal: arrive, openNote, popups, dismissPopup, alert, showMascot, setShowMascot, toast,
    }),
    [health, projects, projectsError, projectsLoaded, project, pid, setProjectId, reloadProjects, tick, live, recent,
      liveSteps, notes, unread, markRead, markAllRead, nset, saveNset, arrive, openNote, popups, dismissPopup, alert, showMascot, setShowMascot, toast],
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
  const liveTick = opts.live === false ? 0 : tick;

  const reload = useCallback(async () => {
    const k = keyRef.current;
    if (k === null) return;
    setLoading(true);
    try {
      const d = await fnRef.current();
      if (keyRef.current !== k) return;
      setData(d);
      setError(null);
    } catch (e) {
      if (keyRef.current !== k) return;
      setError(errorParts(e));
    } finally {
      if (keyRef.current === k) setLoading(false);
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
