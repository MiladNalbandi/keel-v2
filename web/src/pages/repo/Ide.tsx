// The Code page as a small, read-only VS Code: an activity bar (Explorer, Search, Source control, keel), a side
// bar you can resize, editor tabs (preview / pinned), breadcrumbs, and a status bar. Deep links #/repo/<path>:<line>
// open a file at a line, and the URL follows the active tab. On a phone the side bar and the editor are two screens.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as RPointerEvent } from "react";
import { api, type HelperSelection, type RepoInfo } from "../../api";
import { HelperPanel } from "../../components/helper/HelperPanel";
import { KeelBotCount } from "../../components/helper/unread";
import { useNarrow } from "../../components/page";
import { ErrorBox } from "../../components/ui";
import { WorkspaceDoctor } from "../../components/WorkspaceDoctor";
import { parseHash } from "../../routes";
import { useApp, useLoad, useRoute } from "../../state";
import {
  CodeView, DiffPane, ImagePane, MarkdownPane, Notice, TEXT_MAX, WRAP_MAX, canPreview, kindOf, useFileText,
  type Cmd, type Cursor, type DiffMode, type Target,
} from "./Editor";
import { Explorer } from "./Explorer";
import { FileIcon, Icon, extOf, languageName } from "./icons";
import { DocsView, KeelView, MemoryView, ruleText } from "./KeelView";
import {
  bytes, closeTab, decoOf, FOCUS_KEYS, nameOf, openTab, parseDeepLink, parseReviewLink, pinTab, repoHash, retargetTab, setView, stepTab, tabId, webUrl,
  type EditorTab, type OpenSpec, type Tabs, type View,
} from "./model";
import { BranchTab } from "./Branch";
import { logTitle, openLog, setLogBranch } from "./gitLog";
import { LogTab } from "./Log";
import { QuickOpen } from "./QuickOpen";
import { RecentFiles } from "./Recent";
import { ScmView } from "./Scm";
import { SearchView } from "./Search";
import { DbExplorer, DbTab, dbPath, dbTabTitle } from "../../components/plugins/DbTool";
import { openReview, ReviewSide } from "../../components/review/ReviewSide";
import { ReviewFileTab } from "../../components/review/ReviewFileTab";
import { ReviewLayer } from "../../components/review/ReviewLayer";
import { setOpener } from "../../components/review/store";
import { doubleShift, ideActionFor, IDE_KEYS, keyLabel, readKeymap } from "../../components/review/keymap";
import { isTyping, modalOpen } from "../../keys";
import { openLauncher } from "../../components/launcher/Launcher";

type Activity = "explorer" | "search" | "scm" | "review" | "db" | "keel";
const ACTIVITIES: [Activity, string, string, string, string][] = [
  ["explorer", "Explorer", "files", "⇧E", "Files"],
  ["search", "Search", "search", "⇧F", "Search"],
  ["scm", "Source control", "branch", "⇧G", "Git"],
  ["review", "Review", "review", "", "Review"],
  ["db", "Database", "database", "", "DB"],
  ["keel", "keel", "keel", "", "keel"],
];

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const MOD = isMac ? "⌘" : "Ctrl+";
/** v0.15.4 the key that closes the editor tab (⌥W: the browser keeps ⌘W), for the tab's tooltip */
const CLOSE_KEY = keyLabel(IDE_KEYS.find((a) => a.id === "closeTab")!.keys.intellij[0]);

function readJson<T>(store: Storage | undefined, key: string, fallback: T): T {
  try {
    const v = store?.getItem(key);
    return v ? (JSON.parse(v) as T) : fallback;
  } catch {
    return fallback;
  }
}
function writeJson(store: Storage | undefined, key: string, value: unknown) {
  try {
    store?.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode or full */
  }
}
const session = typeof sessionStorage !== "undefined" ? sessionStorage : undefined;
const local = typeof localStorage !== "undefined" ? localStorage : undefined;

function tabTitle(t: EditorTab): string {
  if (t.kind === "docs") return "Files keel wrote";
  if (t.kind === "memory") return "Memory";
  if (t.kind === "doctor") return "Workspace Doctor";
  if (t.kind === "db") return dbTabTitle(t.path);
  if (t.kind === "branch") return t.path;
  if (t.kind === "log") return logTitle(t.path);
  if (t.kind === "review") {
    const [key, file] = splitReview(t.path);
    return `${nameOf(file)} (${key.startsWith("pr:") ? key.slice(3) : key.slice(7)})`;
  }
  if (t.kind === "commit") return `${nameOf(t.path)} @ ${t.sha?.slice(0, 7)}`;
  return nameOf(t.path);
}

/** A review file tab's path: "<review key>|<file>" (pr:7|src/a.kt, branch:feat/x|src/a.kt). */
const splitReview = (p: string): [string, string] => {
  const i = p.indexOf("|");
  return i < 0 ? [p, ""] : [p.slice(0, i), p.slice(i + 1)];
};

export function RepoIde({ pid, repo, version = 0, focus = false, onFocus }: {
  pid: string; repo: RepoInfo | null; version?: number;
  /** the page head is hidden (Focus); the status bar can bring it back */
  focus?: boolean; onFocus?: (on: boolean) => void;
}) {
  const { project } = useApp();
  const route = useRoute();
  const narrow = useNarrow(720);
  const root = useRef<HTMLDivElement>(null);

  const [activity, setActivity] = useState<Activity>("explorer");
  const [sideOpen, setSideOpen] = useState(true);
  const [width, setWidth] = useState(() => readJson(local, "keel2.repo.side", 280));
  const tabsKey = `keel2.repo.tabs.${pid}`;
  const [tabs, setTabs] = useState<Tabs>(() => readJson(session, tabsKey, { tabs: [], active: null }));
  const [targets, setTargets] = useState<Record<string, Target>>({});
  const [links, setLinks] = useState<Record<string, number>>({});
  const [cursor, setCursor] = useState<Cursor | null>(null);
  const [cmd, setCmd] = useState<Cmd>(null);
  const [qo, setQo] = useState(false);
  // v0.15.4 Recent files (⌘E): the tabs you looked at, newest first, also after they were closed
  const [recentOpen, setRecentOpen] = useState(false);
  const recentKey = `keel2.repo.recent.${pid}`;
  const [recent, setRecent] = useState<EditorTab[]>(() => readJson(session, recentKey, []));
  const [screen, setScreen] = useState<"side" | "editor">(() => (parseDeepLink(route.arg) ? "editor" : "side"));
  const [reveal, setReveal] = useState(0);
  const [searchFocus, setSearchFocus] = useState(0);
  const [filterFocus, setFilterFocus] = useState(0);
  const [against, setAgainst] = useState<"head" | "base">("head");
  const [mode, setMode] = useState<DiffMode>(() => readJson(local, "keel2.repo.diff", "inline"));
  const [wrap, setWrap] = useState<boolean>(() => readJson(local, "keel2.repo.wrap", false));
  const [dims, setDims] = useState("");
  const [copied, setCopied] = useState(false);
  // KeelBot: a chat panel on the right (⌘I), remembered per browser; it can take the lines selected in the code
  const [helperOpen, setHelperOpen] = useState<boolean>(() => readJson(local, "keel2.repo.helper", false));
  const [helperFocus, setHelperFocus] = useState(0);
  // KeelBot's column: drag its left edge (300 px up to all but 360 px of the IDE), remembered per browser
  const [helperW, setHelperW] = useState<number>(() => readJson(local, "keel2.repo.helper.w", 380));
  const [helperSel, setHelperSel] = useState<HelperSelection | null>(null);

  const changes = useLoad(`changes:${pid}`, () => api.changes(pid));
  const byPath = useMemo(() => new Map((changes.data ?? []).map((c) => [c.path, c])), [changes.data]);
  const active = tabs.tabs.find((t) => t.id === tabs.active) ?? null;
  const activeFile = active?.kind === "file" ? active.path : null;
  const meta = useLoad(activeFile ? `file:${pid}:${activeFile}` : null, () => api.file(pid, activeFile!));
  const metaData = meta.data && meta.data.path === activeFile ? meta.data : null;
  const kind = metaData ? kindOf(metaData) : null;
  const view: View = active?.view ?? "code";
  const showText = !!metaData && (kind === "text" || (kind === "image" && extOf(metaData.path) === "svg" && view === "code"));
  const text = useFileText(pid, metaData, showText && (view === "code" || view === "preview"));
  const phone = narrow;
  const diffMode: DiffMode = phone ? "inline" : mode;

  useEffect(() => writeJson(session, tabsKey, { tabs: tabs.tabs.filter((t) => t.kind !== "doctor"), active: tabs.active }), [tabs, tabsKey]);
  useEffect(() => writeJson(local, "keel2.repo.side", width), [width]);
  useEffect(() => writeJson(local, "keel2.repo.diff", mode), [mode]);
  useEffect(() => writeJson(local, "keel2.repo.wrap", wrap), [wrap]);
  useEffect(() => writeJson(local, "keel2.repo.helper", helperOpen), [helperOpen]);
  useEffect(() => writeJson(local, "keel2.repo.helper.w", helperW), [helperW]);
  useEffect(() => {
    setCursor(null);
    setDims("");
    if (active) setRecent((r) => [active, ...r.filter((x) => x.id !== active.id)].slice(0, 30));
  }, [active?.id]);
  useEffect(() => writeJson(session, recentKey, recent.filter((t) => t.kind !== "doctor")), [recent, recentKey]);

  const open = useCallback((spec: OpenSpec, o: { pin?: boolean; line?: number; col?: number; len?: number } = {}) => {
    setTabs((t) => openTab(t, spec, o.pin));
    const id = tabId(spec);
    if (o.line) {
      setTargets((x) => ({ ...x, [id]: { line: o.line!, col: o.col, len: o.len, n: Date.now() } }));
      setLinks((x) => ({ ...x, [id]: o.line! }));
    }
    setScreen("editor");
  }, []);

  // v0.15.3 a Markdown file opens rendered (Preview); Code and Changes are one click away
  const openFile = useCallback((path: string, pin = false, view?: View) => open({ path, view: view ?? (canPreview(path) ? "preview" : undefined) }, { pin }), [open]);
  // v0.15.2 the Git log: one tab, on a branch ("" = the current one)
  const showLog = useCallback((branch = "") => {
    setTabs((t) => openLog(t, branch));
    setScreen("editor");
  }, []);

  // a deep link (#/repo/<path>:<line>) opens that file at that line — on load and on every hash change, also when
  // it names the same file again after the URL followed other tabs; #/repo/@review/pr:7 opens that review
  useEffect(() => {
    const follow = () => {
      const r = parseHash(location.hash);
      const review = r.page === "repo" ? parseReviewLink(r.arg) : null;
      if (review) {
        openReview(pid, review);
        setActivity("review");
        setSideOpen(true);
        setScreen("side");
        return;
      }
      const link = r.page === "repo" ? parseDeepLink(r.arg) : null;
      if (link) {
        open({ path: link.path, view: "code" }, { line: link.line });
        setReveal((n) => n + 1);
      }
    };
    follow();
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
  }, [open, pid]);

  // the URL follows the active tab (replaceState: no history entry per click)
  useEffect(() => {
    if (route.page !== "repo") return;
    const want = active?.kind === "file" ? repoHash(active.path, links[active.id]) : "#/repo";
    if (location.hash !== want) {
      try {
        history.replaceState(history.state, "", want);
      } catch {
        /* sandboxed */
      }
    }
  }, [active, links, route.page]);

  const showSide = useCallback((a: Activity) => {
    setActivity(a);
    setSideOpen(true);
    setScreen("side");
  }, []);
  // v0.15.4 an activity's button and its key (⌘1, ⌘9, ⇧⌘9) show its panel, or hide it when it is shown already
  const toggleSide = useCallback((a: Activity) => {
    if (!phone && activity === a && sideOpen) setSideOpen(false);
    else showSide(a);
  }, [phone, activity, sideOpen, showSide]);
  // v0.15.4 ⇧Esc hides the active tool window, like IntelliJ: KeelBot when the focus is in it, else the side bar
  const hideActive = useCallback(() => {
    const at = document.activeElement;
    const inHelp = !!at && !!root.current?.querySelector(".ide-help")?.contains(at);
    const inSide = !!at && !!root.current?.querySelector(".ide-side")?.contains(at);
    if (inHelp) setHelperOpen(false);
    else if (phone) setScreen("editor");
    else setSideOpen(false);
    // the focus was in what closed: it goes to the editor's tab
    if (inHelp || inSide) window.setTimeout(() => root.current?.querySelector<HTMLElement>('.ed-tab[aria-selected="true"]')?.focus(), 0);
  }, [phone]);

  const codeActive = !!active && active.kind === "file" && view === "code" && showText;

  /** The lines selected in the code view, as KeelBot takes them; null when nothing (or not code) is selected. */
  const codeSelection = useCallback((): HelperSelection | null => {
    const sel = typeof window !== "undefined" ? window.getSelection() : null;
    if (!sel || sel.isCollapsed || !activeFile || !codeActive) return null;
    const rowOf = (n: Node | null) => (n instanceof Element ? n : n?.parentElement)?.closest<HTMLElement>(".cv-row");
    const a = rowOf(sel.anchorNode), b = rowOf(sel.focusNode);
    if (!a || !b || !root.current?.contains(a)) return null;
    const lines = [Number(a.dataset.line), Number(b.dataset.line)].sort((x, y) => x - y);
    const text = sel.toString().trim();
    return text ? { path: activeFile, from: lines[0], to: lines[1], text: text.slice(0, 8000) } : null;
  }, [activeFile, codeActive]);

  // a part of the page hands text to KeelBot (Code › Git: address the comments, draft the PR): open it with the text
  const [prefill, setPrefill] = useState<{ text: string; n: number } | null>(null);
  useEffect(() => {
    const on = (e: Event) => {
      const text = String((e as CustomEvent).detail ?? "");
      if (!text) return;
      setHelperOpen(true);
      setPrefill((p) => ({ text, n: (p?.n ?? 0) + 1 }));
    };
    window.addEventListener("keel:ask-keelbot", on);
    return () => window.removeEventListener("keel:ask-keelbot", on);
  }, []);

  const plugins = useLoad(`plugins:${pid}`, () => api.plugins(pid), { live: false });
  const dbOn = !!plugins.data?.find((x) => x.name === "db")?.enabled;
  const reviewOn = !!plugins.data?.find((x) => x.name === "review")?.enabled;
  // v0.14.0 a review opens its files as editor tabs (kind "review", path "<key>|<file>", view diff or code)
  useEffect(() => {
    setOpener((key, path, view, pin) => open({ kind: "review", path: `${key}|${path}`, view }, { pin }));
    return () => setOpener(null);
  }, [open]);

  const askHelper = useCallback(() => {
    const picked = codeSelection();
    if (picked) setHelperSel(picked);
    setHelperOpen(true);
    setHelperFocus((n) => n + 1);
  }, [codeSelection]);

  // keyboard: ⌘/Ctrl+P quick open, +Shift+F search, +Shift+E explorer, +Shift+G source control, +F find, +G go to line, Alt+Z wrap
  useEffect(() => {
    const twice = doubleShift();
    const on = (e: KeyboardEvent) => {
      // v0.14.0 IntelliJ's keys (keymap.ts): ⌘1 files, ⌘9 Git, ⇧⌘9 Review, ⇧⌘O go to file, ⌘L go to line;
      // v0.15.0 ⇧⇧ searches everywhere (the launcher, like IntelliJ's Search Everywhere)
      if (twice(e)) {
        openLauncher();
        return;
      }
      const ij = ideActionFor(e);
      if (ij) {
        if (ij === "review" && !reviewOn) return;
        // v0.15.4 a key with ⌥ types a letter on a Mac (⌥W is ∑): not while typing; and none under another popup
        if ((e.altKey && isTyping(e.target)) || modalOpen()) return;
        e.preventDefault();
        if (ij === "quickOpen") setQo(true);
        else if (ij === "gotoLine") { if (codeActive) setCmd({ kind: "goto", n: Date.now() }); }
        else if (ij === "toggleSide") setSideOpen((o) => !o);
        else if (ij === "hideSide") hideActive();
        else if (ij === "recentFiles") setRecentOpen(true);
        else if (ij === "closeTab") setTabs((t) => (t.active ? closeTab(t, t.active) : t));
        else if (ij === "nextTab" || ij === "prevTab") setTabs((t) => stepTab(t, ij === "nextTab" ? 1 : -1));
        else toggleSide(ij);
        return;
      }
      const mod = e.metaKey || e.ctrlKey;
      const k = e.key.toLowerCase();
      if (e.altKey && !mod && e.code === "KeyZ") {
        e.preventDefault();
        setWrap((w) => !w);
        return;
      }
      if (!mod || e.altKey) return;
      if (!e.shiftKey && k === "i") {
        e.preventDefault();
        // ⌘I: open KeelBot (with the selected lines); again with nothing selected closes it
        if (helperOpen && !codeSelection()) setHelperOpen(false);
        else askHelper();
        return;
      }
      if (!e.shiftKey && k === "p") {
        e.preventDefault();
        setQo(true);
      } else if (e.shiftKey && k === "f") {
        e.preventDefault();
        showSide("search");
        setSearchFocus((n) => n + 1);
      } else if (e.shiftKey && k === "e") {
        e.preventDefault();
        showSide("explorer");
        setFilterFocus((n) => n + 1);
      } else if (e.shiftKey && k === "g") {
        e.preventDefault();
        showSide("scm");
      } else if (!e.shiftKey && (k === "f" || k === "g") && codeActive) {
        e.preventDefault();
        setCmd({ kind: k === "f" ? "find" : "goto", n: Date.now() });
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [showSide, toggleSide, hideActive, codeActive, helperOpen, codeSelection, askHelper, reviewOn]);

  // the IDE fills the window below the page head (measured again when the head grows, e.g. a merge result)
  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    const set = () => el.style.setProperty("--ide-top", `${Math.max(0, el.getBoundingClientRect().top + window.scrollY)}px`);
    set();
    window.addEventListener("resize", set);
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(set) : null;
    if (el.parentElement) ro?.observe(el.parentElement);
    const head = el.parentElement?.firstElementChild;
    if (head && head !== el) ro?.observe(head);
    return () => {
      window.removeEventListener("resize", set);
      ro?.disconnect();
    };
  }, []);

  // the breadcrumbs keep the file name in view, the tab strip the active tab
  const crumbsRef = useRef<HTMLElement>(null);
  const tabsRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const c = crumbsRef.current;
    if (!c) return;
    c.scrollLeft = c.scrollWidth;
    c.dataset.cut = c.scrollLeft > 0 ? "1" : "";
  });
  useEffect(() => {
    const strip = tabsRef.current;
    const on = strip?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!strip || !on) return;
    if (on.offsetLeft < strip.scrollLeft) strip.scrollLeft = on.offsetLeft;
    else if (on.offsetLeft + on.offsetWidth > strip.scrollLeft + strip.clientWidth) strip.scrollLeft = on.offsetLeft + on.offsetWidth - strip.clientWidth;
  }, [tabs.active, tabs.tabs.length, screen]);

  const close = (id: string) => setTabs((t) => closeTab(t, id));

  // the splitter: drag, or arrows when focused
  const drag = (e: RPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const startX = e.clientX;
    const start = width;
    const move = (ev: PointerEvent) => setWidth(Math.min(640, Math.max(180, start + ev.clientX - startX)));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.classList.remove("ide-dragging");
    };
    document.body.classList.add("ide-dragging");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const maxHelper = () => Math.max(300, (root.current?.getBoundingClientRect().width || window.innerWidth || 1200) - 360);
  const dragHelper = (e: RPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const startX = e.clientX;
    const start = helperW;
    const move = (ev: PointerEvent) => setHelperW(Math.round(Math.min(maxHelper(), Math.max(300, start - (ev.clientX - startX)))));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.classList.remove("ide-dragging");
    };
    document.body.classList.add("ide-dragging");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const change = activeFile ? byPath.get(activeFile) : undefined;
  const deco = decoOf(change);
  const remoteUrl = activeFile ? webUrl(repo?.remote, repo?.branch, activeFile, links[active!.id]) : null;
  const names = useMemo(() => {
    const count = new Map<string, number>();
    for (const t of tabs.tabs) count.set(tabTitle(t), (count.get(tabTitle(t)) ?? 0) + 1);
    return count;
  }, [tabs.tabs]);

  const sideView = (
    <>
      <div hidden={activity !== "explorer"} className="sv-host">
        <Explorer pid={pid} title={project?.name ?? pid} root={project?.root} changes={changes.data ?? []} active={activeFile} reveal={reveal} focusFilter={filterFocus} version={version}
          onOpen={(p, pin) => openFile(p, pin)} />
      </div>
      <div hidden={activity !== "search"} className="sv-host">
        <SearchView pid={pid} focus={searchFocus} onOpen={(p, line, col, len, pin) => open({ path: p, view: "code" }, { pin, line, col, len })} />
      </div>
      <div hidden={activity !== "scm"} className="sv-host">
        <ScmView pid={pid} repo={repo} changes={changes.data} changesError={changes.error}
          onOpenChange={(p, pin) => openFile(p, pin, "diff")}
          onOpenCommitFile={(sha, p, pin) => open({ kind: "commit", path: p, sha, view: "diff" }, { pin })}
          onDoctor={() => open({ kind: "doctor", path: "doctor" }, { pin: true })}
          onOpenBranch={(name, pin) => open({ kind: "branch", path: name }, { pin })} onOpenLog={showLog} />
      </div>
      {reviewOn && (
        <div hidden={activity !== "review"} className="sv-host">
          <ReviewSide pid={pid} activeFile={active?.kind === "review" ? (([key, path]) => ({ key, path }))(splitReview(active.path)) : null} />
        </div>
      )}
      {dbOn && (
        <div hidden={activity !== "db"} className="sv-host">
          <DbExplorer pid={pid} onConsole={(c) => open({ kind: "db", path: dbPath(c) }, { pin: true })}
            onTable={(c, t, pin) => open({ kind: "db", path: dbPath(c, t) }, { pin })} />
        </div>
      )}
      <div hidden={activity !== "keel"} className="sv-host">
        <KeelView pid={pid} file={metaData} fileError={activeFile ? meta.error : null}
          onOpenDocs={() => open({ kind: "docs", path: "docs" }, { pin: true })}
          onOpenMemory={() => open({ kind: "memory", path: "memory" }, { pin: true })} />
      </div>
    </>
  );

  // ---------- the editor body for the active tab ----------
  let body: JSX.Element;
  if (!active) {
    body = (
      <div className="ed-empty">
        <svg viewBox="0 0 120 68" width="88" aria-hidden="true"><path fill="var(--accent)" opacity=".18" d="M4 0H116Q120 0 119.4 4C118 22 92 38 66.6 50Q64 51.5 64 54L62.2 64.5Q61.8 67 60 67Q58.2 67 57.8 64.5L56 54Q56 51.5 53.4 50C28 38 2 22 .6 4Q0 0 4 0Z" /></svg>
        <p>Pick a file in the Explorer, or find one by name.</p>
        <dl className="ed-keys">
          <dt>Find a file</dt><dd><kbd>{MOD}P</kbd></dd>
          <dt>Search every file</dt><dd><kbd>{MOD}{isMac ? "⇧" : "Shift+"}F</kbd></dd>
          <dt>Changed files</dt><dd><kbd>{MOD}{isMac ? "⇧" : "Shift+"}G</kbd></dd>
          <dt>Find in the open file</dt><dd><kbd>{MOD}F</kbd></dd>
          <dt>Go to a line</dt><dd><kbd>{MOD}G</kbd></dd>
        </dl>
      </div>
    );
  } else if (active.kind === "docs") {
    body = <DocsView pid={pid} onOpen={(p) => openFile(p, true)} />;
  } else if (active.kind === "memory") {
    body = <MemoryView pid={pid} />;
  } else if (active.kind === "db") {
    const id = active.id;
    body = <DbTab key={id} pid={pid} path={active.path}
      onConn={(c) => setTabs((t) => retargetTab(t, id, { kind: "db", path: dbPath(c) }))} />;
  } else if (active.kind === "doctor") {
    body = <div className="ed-doc"><WorkspaceDoctor pid={pid} onClean={() => void changes.reload()} /></div>;
  } else if (active.kind === "review") {
    const [rkey, rfile] = splitReview(active.path);
    body = <ReviewFileTab key={active.id} pid={pid} reviewKey={rkey} path={rfile} view={active.view === "code" ? "code" : "diff"} mode={diffMode} />;
  } else if (active.kind === "branch") {
    body = <BranchTab key={active.id} pid={pid} name={active.path} mode={diffMode} onOpenLog={showLog}
      onOpenCommitFile={(sha, p, pin) => open({ kind: "commit", path: p, sha, view: "diff" }, { pin })} />;
  } else if (active.kind === "log") {
    body = <LogTab pid={pid} branch={active.path} changes={changes.data} mode={diffMode} onBranch={(b) => setTabs((t) => setLogBranch(t, b))}
      onOpenCommitFile={(sha, p, pin) => open({ kind: "commit", path: p, sha, view: "diff" }, { pin })} onOpenChange={(p, pin) => openFile(p, pin, "diff")} />;
  } else if (active.kind === "commit") {
    body = <DiffPane pid={pid} path={active.path} against="head" sha={active.sha} mode={diffMode} />;
  } else if (view === "diff") {
    body = <DiffPane pid={pid} path={active.path} against={against} mode={diffMode} />;
  } else if (meta.error && !metaData) {
    body = <div className="ed-note"><ErrorBox error={meta.error} onRetry={() => void meta.reload()} /></div>;
  } else if (!metaData) {
    body = <div className="ed-note"><span className="pg-spin" role="status">Opening {nameOf(active.path)}…</span></div>;
  } else if (kind === "image" && !(extOf(metaData.path) === "svg" && view === "code")) {
    body = <ImagePane pid={pid} path={metaData.path} size={metaData.size} onDims={setDims} />;
  } else if (kind === "binary") {
    body = <Notice title={`${nameOf(metaData.path)} is a binary file (${bytes(metaData.size)}).`}>keel shows text files and images only.</Notice>;
  } else if (kind === "big") {
    body = (
      <div className="ed-big">
        <Notice title={`Too big to show: ${bytes(metaData.size)}.`}>keel shows text files up to {bytes(TEXT_MAX)}. Here are its first lines; search finds text anywhere in it.</Notice>
        <CodeView key={`${active.id}:head`} text={metaData.head} path={metaData.path} wrap={false} target={null} cmd={null} onCursor={setCursor} onLink={() => undefined} scrollKey={`${active.id}:head`} />
      </div>
    );
  } else if (!text) {
    body = <div className="ed-note"><span className="pg-spin" role="status">Opening {nameOf(active.path)}…</span></div>;
  } else if ("error" in text) {
    body = <div className="ed-note"><ErrorBox error={text.error} /></div>;
  } else if (view === "preview" && canPreview(metaData.path)) {
    body = <MarkdownPane text={text.text} />;
  } else {
    body = (
      <CodeView key={active.id} text={text.text} path={metaData.path} wrap={wrap} target={targets[active.id] ?? null} cmd={cmd}
        onCursor={setCursor} onLink={(n) => setLinks((x) => ({ ...x, [active.id]: n }))} scrollKey={active.id} />
    );
  }

  const fileTab = active?.kind === "file";
  const lines = text && "text" in text ? text.text : "";
  const eol = lines.includes("\r\n") ? "CRLF" : "LF";
  const lineCount = useMemo(() => (lines ? lines.split("\n").length : 0), [lines]);
  const segs = active && (active.kind === "file" || active.kind === "commit") ? active.path.split("/") : [];

  const toolbar = fileTab && active && (
    <div className="ed-tools" role="toolbar" aria-label="Editor">
      {canPreview(active.path) && (
        <button type="button" className={`tb${view === "preview" ? " on" : ""}`} aria-pressed={view === "preview"} aria-label="Preview"
          title={view === "preview" ? "Show the code" : "Show it rendered (copying it gives the Markdown)"}
          onClick={() => setTabs((t) => setView(t, active.id, view === "preview" ? "code" : "preview"))}>
          <Icon name="preview" size={15} /><span>Preview</span>
        </button>
      )}
      {extOf(active.path) === "svg" && view !== "diff" && (
        <button type="button" className={`tb${view === "code" ? " on" : ""}`} aria-pressed={view === "code"} aria-label="Source" title="Show the source"
          onClick={() => setTabs((t) => setView(t, active.id, view === "code" ? "preview" : "code"))}>
          <Icon name="files" size={15} /><span>Source</span>
        </button>
      )}
      <button type="button" className={`tb${view === "diff" ? " on" : ""}`} aria-pressed={view === "diff"} aria-label="Changes"
        title={change ? "Show what changed" : "Compare with HEAD or the base branch"}
        onClick={() => setTabs((t) => setView(t, active.id, view === "diff" ? "code" : "diff"))}>
        <Icon name="diff" size={15} /><span>Changes</span>
      </button>
      {view === "diff" && (
        <span className="seg" role="group" aria-label="Compare with">
          <button type="button" aria-pressed={against === "head"} onClick={() => setAgainst("head")} title="Uncommitted changes (work tree against HEAD)">HEAD</button>
          <button type="button" aria-pressed={against === "base"} onClick={() => setAgainst("base")} title={`Everything this branch changed since ${repo?.base ?? "the base"}`}>{repo?.base ?? "base"}</button>
        </span>
      )}
      {view === "diff" && !phone && (
        <span className="seg" role="group" aria-label="Diff layout">
          <button type="button" aria-pressed={mode === "inline"} onClick={() => setMode("inline")} title="Inline" aria-label="Inline"><Icon name="inline" size={14} /><span>Inline</span></button>
          <button type="button" aria-pressed={mode === "split"} onClick={() => setMode("split")} title="Side by side" aria-label="Side by side"><Icon name="split" size={14} /><span>Side by side</span></button>
        </span>
      )}
      {view === "code" && showText && (
        <button type="button" className={`tb${wrap ? " on" : ""}`} aria-pressed={wrap} aria-label="Wrap" onClick={() => setWrap(!wrap)}
          disabled={lineCount > WRAP_MAX} title={lineCount > WRAP_MAX ? `Wrap is off for files over ${WRAP_MAX.toLocaleString()} lines` : "Word wrap (Alt+Z)"}>
          <Icon name="wrap" size={15} /><span>Wrap</span>
        </button>
      )}
      {view === "code" && showText && (
        <button type="button" className="tb" aria-label="Ask KeelBot" title={`Ask KeelBot about the selected lines, or this file (${MOD}I)`}
          onMouseDown={(e) => e.preventDefault()} onClick={askHelper}>
          <Icon name="helper" size={15} /><span>Ask</span>
        </button>
      )}
      <button type="button" className="tb" aria-label="Copy the path" title="Copy the path"
        onClick={() => {
          void navigator.clipboard?.writeText(active.path).then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1400);
          }, () => undefined);
        }}>
        <Icon name="copy" size={15} /><span>{copied ? "Copied" : "Path"}</span>
      </button>
      {remoteUrl && <a className="tb" href={remoteUrl} target="_blank" rel="noreferrer" title="Open this file on the remote" aria-label="Open on the remote"><Icon name="link" size={15} /><span>Remote</span></a>}
    </div>
  );

  const ide = {
    "--side-w": `${width}px`,
    "--help-w": `${helperW}px`,
  } as CSSProperties;

  return (
    <div ref={root} className={`ide${phone ? ` phone s-${screen}` : ""}${sideOpen ? "" : " side-closed"}${helperOpen ? " help-open" : ""}`} style={ide}>
      <nav className="ide-act" aria-label="Repo views">
        {ACTIVITIES.filter(([id]) => (id !== "db" || dbOn) && (id !== "review" || reviewOn)).map(([id, label, icon, key, short]) => {
          const n = id === "scm" ? changes.data?.length ?? 0 : 0;
          // v0.15.4 the tooltip names IntelliJ's key too (⌘1, ⌘9, ⇧⌘9)
          const ij = IDE_KEYS.find((a) => a.id === id)?.keys[readKeymap()][0];
          const keys = [key && `${MOD}${key}`, ij && keyLabel(ij)].filter(Boolean);
          return (
            <button key={id} type="button" className={`act${activity === id && sideOpen ? " on" : ""}`} aria-pressed={activity === id && sideOpen}
              aria-label={label} title={keys.length ? `${label} (${keys.join(", ")})` : label}
              onClick={() => toggleSide(id)}>
              <Icon name={icon} size={22} />
              {phone && <span className="act-l" aria-hidden="true">{short}</span>}
              {n > 0 && <span className="act-n" aria-label={`${n} changed`}>{n > 99 ? "99+" : n}</span>}
            </button>
          );
        })}
        <button type="button" className={`act act-help${helperOpen ? " on" : ""}`} aria-pressed={helperOpen} aria-label="KeelBot"
          title={`KeelBot: ask about this project (${MOD}I)`} onClick={() => (helperOpen ? setHelperOpen(false) : askHelper())}>
          <Icon name="helper" size={22} />
          {phone && <span className="act-l" aria-hidden="true">KeelBot</span>}
          <KeelBotCount kind="act" />
        </button>
      </nav>
      <aside className="ide-side" aria-label={ACTIVITIES.find((a) => a[0] === activity)?.[1]}>{sideView}</aside>
      {reviewOn && (
        <ReviewLayer pid={pid} active={active?.kind === "review"
          ? (([key, path]) => ({ key, path, view: active.view === "code" ? "code" as const : "diff" as const }))(splitReview(active.path)) : null} />
      )}
      {!phone && sideOpen && (
        <div className="ide-split" role="separator" aria-orientation="vertical" aria-label="Resize the side bar" tabIndex={0}
          aria-valuenow={width} aria-valuemin={180} aria-valuemax={640} onPointerDown={drag}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") setWidth((w) => Math.max(180, w - 16));
            if (e.key === "ArrowRight") setWidth((w) => Math.min(640, w + 16));
          }} />
      )}
      <section className="ide-main" aria-label="Editor">
        <div className="ed-head">
          {phone && (
            <button type="button" className="ed-back" onClick={() => setScreen("side")} aria-label="Back to the files">
              <Icon name="back" size={18} /><span>{ACTIVITIES.find((a) => a[0] === activity)?.[1]}</span>
            </button>
          )}
          <div ref={tabsRef} className="ed-tabs" role="tablist" aria-label="Open files">
            {tabs.tabs.map((t) => {
              const title = tabTitle(t);
              const dup = (names.get(title) ?? 0) > 1 && t.kind === "file";
              const tdeco = t.kind === "file" ? decoOf(byPath.get(t.path)) : undefined;
              const on = t.id === tabs.active;
              return (
                <div key={t.id} role="tab" aria-selected={on} tabIndex={on ? 0 : -1} title={t.kind === "file" || t.kind === "commit" ? t.path : title}
                  className={`ed-tab${on ? " on" : ""}${t.preview ? " preview" : ""}${tdeco ? ` t-${tdeco.tone}` : ""}`}
                  onClick={() => setTabs((x) => ({ ...x, active: t.id }))}
                  onDoubleClick={() => setTabs((x) => pinTab(x, t.id))}
                  onMouseDown={(e) => e.button === 1 && e.preventDefault()}
                  onAuxClick={(e) => {
                    if (e.button === 1) {
                      e.preventDefault();
                      close(t.id);
                    }
                  }}
                  onKeyDown={(e) => {
                    const i = tabs.tabs.findIndex((x) => x.id === t.id);
                    const go = (j: number) => {
                      const n = tabs.tabs[(j + tabs.tabs.length) % tabs.tabs.length];
                      setTabs((x) => ({ ...x, active: n.id }));
                      window.setTimeout(() => (e.currentTarget.parentElement?.querySelector('[aria-selected="true"]') as HTMLElement | null)?.focus(), 0);
                    };
                    if (e.key === "ArrowRight") go(i + 1);
                    if (e.key === "ArrowLeft") go(i - 1);
                    if (e.key === "Delete") close(t.id);
                    if (e.key === "Enter") setTabs((x) => pinTab(x, t.id));
                  }}>
                  {t.kind === "file" || t.kind === "commit" ? <FileIcon name={nameOf(t.path)} /> : <Icon name={t.kind === "review" ? "review" : t.kind === "branch" ? "branch" : t.kind === "log" ? "commit" : t.kind === "doctor" ? "refresh" : t.kind === "memory" ? "history" : "keel"} size={15} />}
                  <span className="ed-tab-n">{title}</span>
                  {dup && <span className="ed-tab-d">{t.path.split("/").slice(-2, -1)[0]}</span>}
                  {t.kind === "file" && t.view === "diff" && <span className="ed-tab-v">diff</span>}
                  {tdeco && <span className={`ed-tab-m t-${tdeco.tone}`} aria-label={tdeco.title}>{tdeco.letter}</span>}
                  <button type="button" className="ed-tab-x" aria-label={`Close ${title}`} title={`Close (${CLOSE_KEY}, or middle-click)`}
                    onClick={(e) => {
                      e.stopPropagation();
                      close(t.id);
                    }}><Icon name="close" size={14} /></button>
                </div>
              );
            })}
          </div>
        </div>
        {active && (segs.length > 0 || toolbar) && (
          <div className="ed-bar">
            {segs.length > 0 && (
              <nav ref={crumbsRef} className="crumbs" aria-label="Path">
                {segs.map((s, i) => (
                  <span key={i} className="crumb-i">
                    {i > 0 && <span className="crumb-sep" aria-hidden="true">›</span>}
                    {i < segs.length - 1
                      ? <button type="button" className="crumb-b" onClick={() => { showSide("explorer"); setReveal((n) => n + 1); }} title="Reveal in the Explorer">{s}</button>
                      : <b className="crumb-f">{active.kind === "commit" ? `${s} @ ${active.sha?.slice(0, 7)}` : s}</b>}
                  </span>
                ))}
              </nav>
            )}
            {toolbar}
          </div>
        )}
        <div className="ed-body">{body}</div>
      </section>
      {helperOpen && (
        <div className="ide-help">
          {!phone && (
            <div className="ide-help-split" role="separator" aria-orientation="vertical" aria-label="Resize KeelBot" tabIndex={0}
              aria-valuenow={helperW} aria-valuemin={300} onPointerDown={dragHelper} onDoubleClick={() => setHelperW(380)}
              title="Drag to make KeelBot wider (double-click: back to normal)"
              onKeyDown={(e) => {
                if (e.key === "ArrowLeft") setHelperW((w) => Math.min(maxHelper(), w + 32));
                if (e.key === "ArrowRight") setHelperW((w) => Math.max(300, w - 32));
              }} />
          )}
          <HelperPanel pid={pid} openFile={activeFile} selection={helperSel} onClearSelection={() => setHelperSel(null)} focusKey={helperFocus} prefill={prefill}
            onOpenFile={(p, line) => open({ path: p, view: "code" }, { pin: true, line })} onOpenDiff={(p) => openFile(p, true, "diff")}
            onClose={() => setHelperOpen(false)} />
        </div>
      )}
      <footer className="ide-sb" aria-label="Status bar">
        <button type="button" className="sb-i sb-branch" onClick={() => showSide("scm")} title={repo ? `${repo.branch}: ${repo.ahead} ahead of ${repo.base}, ${repo.behind} behind` : "Branch"}>
          <Icon name="branch" size={14} /><span>{repo?.branch ?? "…"}</span>
        </button>
        {repo && repo.base !== repo.branch && <span className="sb-i" title={`${repo.ahead} commits ahead of ${repo.base}, ${repo.behind} behind`}>↑{repo.ahead} ↓{repo.behind}</span>}
        {(changes.data?.length ?? 0) > 0 && (
          <button type="button" className="sb-i" onClick={() => showSide("scm")} title="Uncommitted files">● {changes.data!.length} uncommitted</button>
        )}
        <span className="sb-sp" />
        {fileTab && metaData && (
          <>
            {showText && cursor && view === "code" && (
              <button type="button" className="sb-i" onClick={() => setCmd({ kind: "goto", n: Date.now() })} title={`Go to line (${MOD}G)`}>Ln {cursor.line}, Col {cursor.col}</button>
            )}
            {dims && kind === "image" && <span className="sb-i">{dims}</span>}
            {showText && <span className="sb-i sb-wide">UTF-8</span>}
            {showText && <span className="sb-i sb-wide">{eol}</span>}
            <span className="sb-i sb-wide">{languageName(metaData.path)}</span>
            {deco && <span className={`sb-i sb-file t-${deco.tone}`} title={deco.title}>{deco.letter} {deco.title.split(" (")[0]}</span>}
            {metaData.ac && <span className="sb-i" title={`Changed on this branch for ${metaData.ac}`}>{metaData.ac}</span>}
            <button type="button" className={`sb-i sb-rule${metaData.frozen ? " frozen" : ""}`} onClick={() => showSide("keel")} title={ruleText(metaData)}>
              {metaData.frozen && <Icon name="lock" size={12} />}
              <span>{metaData.phase && metaData.phase !== "none" ? `${metaData.phase}: ${metaData.frozen ? "frozen" : metaData.verdict === "allow" ? "editable" : metaData.verdict}` : "no flow"}</span>
            </button>
          </>
        )}
        {project && !fileTab && <span className="sb-i sb-wide">{project.name}</span>}
        {onFocus && (
          <button type="button" className="sb-i sb-focus" onClick={() => onFocus(!focus)}
            title={focus ? `Leave Focus mode (${keyLabel(FOCUS_KEYS)}, or Esc twice)` : `Focus mode (${keyLabel(FOCUS_KEYS)}): only the code, like an IDE`}>
            {focus ? "Exit focus" : "Focus"} <kbd className="sb-k">{keyLabel(FOCUS_KEYS)}</kbd>
          </button>
        )}
      </footer>
      {qo && (
        <QuickOpen pid={pid} hasFile={codeActive} onClose={() => setQo(false)}
          onOpen={(p, pin) => openFile(p, pin)}
          onGoto={(line) => active && setTargets((x) => ({ ...x, [active.id]: { line, n: Date.now() } }))} />
      )}
      {recentOpen && (
        <RecentFiles onClose={() => setRecentOpen(false)}
          items={recent.filter((x) => x.id !== tabs.active).map((x) => ({ tab: x, title: tabTitle(x), open: tabs.tabs.some((t) => t.id === x.id) }))}
          onPick={(x) => open({ kind: x.kind, path: x.path, sha: x.sha, view: x.view }, { pin: !tabs.tabs.some((t) => t.id === x.id) })} />
      )}
    </div>
  );
}
