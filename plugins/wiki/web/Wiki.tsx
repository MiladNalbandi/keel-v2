// Wiki (Project): the knowledge base the librarians write, a page for every workflow (its Steps map, read only, or
// the graph), the setup runbook and decisions. The page id is in the hash: #/wiki/kb:architecture.
// The Wiki plugin's page (plugins/wiki): it imports only react, @keel/web-sdk and its own files. Its pages come from
// the plugin's api part (/wiki, /wiki/page); "Refresh stale" is keel's (the Code page shows it too).
// 0.17.3 (docs/plugins/24-wiki-sections-spec-status.md): each menu section opens and closes (kept per project), and a
// spec shows which flow built it (a pill in the menu, a Build status box on its page with Resume or Start).
// The wiki's order (nav.ts): the menu is grouped by kind (Specs, Project knowledge, Decisions, Workflows by purpose,
// Overview) with counts, a stable order and readable titles; a page shows where it is (a breadcrumb) and, when long,
// what is on it.
// 0.18.55 the sidebar is easier to scan: sentence-case group titles on one line, the last pages opened (Recent), specs
// that wait for you first, the sections not written yet as one row with "Write them", the workflows by purpose (closed
// until opened), a search that opens what it finds and marks the match, and the open page scrolled into view.

import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import {
  Async, DocHistoryPanel, EmptyState, ErrorBox, Loading, Markdown, PageHead, Pill, ProjectNotesOff, RefreshStaleButton, ResumeRow,
  Skeleton, StartFlowDrawer, StepInfoDrawer,
  agoText, errorParts, get, go, hashForScreen, kfmt, plural, post, readStored, scrollToCenter, setNotes, tokensByStep, useApp, useLoad,
  useNarrow, useRoute, writeStored,
  type Estimate, type PillTone, type SpecState, type SpecStatus, type ThreadState, type WikiPage as WikiPageData, type WikiTree,
  type Workflow,
} from "@keel/web-sdk";
import {
  buildNav, filterNav, firstPage, groupCount, groupItems, overviewLinks, placeOf, pushRecent, recentItems, searchWords,
  type NavGroup, type NavItem, type NavPart,
} from "./nav";
import { WorkflowMap } from "./WorkflowMap";

const e = encodeURIComponent;

/** "?a=1&b=x" without the empty values, written as keel's api.ts writes a query. */
function query(params: Record<string, string | number | undefined | null>): string {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  });
  const str = s.toString();
  return str ? "?" + str : "";
}

/** The page's reads: the same urls as keel 0.15.1 (its api.ts). */
const wikiApi = {
  wiki: (pid: string) => get<WikiTree>(`/projects/${e(pid)}/wiki`),
  wikiPage: (pid: string, id: string) => get<WikiPageData>(`/projects/${e(pid)}/wiki/page${query({ id })}`),
  workflow: (wid: string) => get<Workflow>(`/workflows/${e(wid)}`),
  estimate: (pid: string, workflow_id: string, acs: number) =>
    get<Estimate>(`/projects/${e(pid)}/estimate${query({ workflow_id, acs })}`),
  exportUrl: (wid: string) => `/api/workflows/${e(wid)}/export`,
  /** 0.17.3 every spec with the flow that built it (the Wiki plugin's api) */
  specStatus: (pid: string) => get<SpecStatus[]>(`/projects/${e(pid)}/specs/status`),
  /** the knowledge-refresh flow for the named sections ("Refresh stale" and "Write them": the librarians write them) */
  refresh: (pid: string, sections: string[]) => post<ThreadState>(`/projects/${e(pid)}/wiki/refresh`, { sections }),
};

const STATUS: Record<string, ["ok" | "run" | "warn" | "idle", string]> = {
  written: ["ok", "written"], ok: ["ok", "written"], writing: ["run", "writing"], stale: ["warn", "stale"], missing: ["idle", "not written"],
  off: ["idle", "notes off"],
};

/** A spec's state in words and as a pill tone. */
const SPEC: Record<SpecState, [PillTone, string]> = {
  built: ["ok", "built"], in_progress: ["run", "in progress"], stopped: ["warn", "stopped"], not_built: ["idle", "not built"],
};

// ---- which menu sections are open: per project, in localStorage (a private window or blocked storage: in memory only).
// A group is open until closed (keel.wiki.closed.<pid>, the list keel always kept); a workflow purpose is closed until
// opened (keel.wiki.opened.<pid>).

const closedKey = (pid: string) => `keel.wiki.closed.${pid}`;
const openedKey = (pid: string) => `keel.wiki.opened.${pid}`;
const recentKey = (pid: string) => `keel.wiki.recent.${pid}`;
const strings = (v: unknown) => Array.isArray(v) && v.every((x) => typeof x === "string");
const readList = (key: string) => readStored<string[]>(key, [], "local", strings);

function useFolds(pid: string) {
  const [closed, setClosed] = useState<string[]>(() => readList(closedKey(pid)));
  const [opened, setOpened] = useState<string[]>(() => readList(openedKey(pid)));
  useEffect(() => {
    setClosed(readList(closedKey(pid)));
    setOpened(readList(openedKey(pid)));
  }, [pid]);
  const flip = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  /** open by default (a group) or closed by default (a part that folds) */
  const isOpen = (id: string, byDefault: boolean) => (byDefault ? !closed.includes(id) : opened.includes(id));
  const toggle = (id: string, byDefault = true) => {
    if (byDefault) {
      setClosed((c) => {
        const next = flip(c, id);
        writeStored(closedKey(pid), next, "local");
        return next;
      });
    } else {
      setOpened((o) => {
        const next = flip(o, id);
        writeStored(openedKey(pid), next, "local");
        return next;
      });
    }
  };
  return { isOpen, toggle };
}

/** The last pages opened in this project, newest first: the open page joins the list when the menu has it. */
function useRecent(pid: string, groups: NavGroup[], cur: string) {
  const [ids, setIds] = useState<string[]>(() => readList(recentKey(pid)));
  useEffect(() => setIds(readList(recentKey(pid))), [pid]);
  const known = !!cur && !!placeOf(groups, cur);
  useEffect(() => {
    if (!known) return;
    setIds((list) => {
      if (list[0] === cur) return list;
      const next = pushRecent(list, cur);
      writeStored(recentKey(pid), next, "local");
      return next;
    });
  }, [pid, cur, known]);
  return ids;
}

type MenuProps = {
  pid: string;
  /** every group of the menu (the place of the open page is found here), and the ones the search keeps */
  groups: NavGroup[]; shown: NavGroup[]; cur: string; q: string; recent: string[];
  isOpen: (id: string, byDefault: boolean) => boolean; toggle: (id: string, byDefault?: boolean) => void;
  onClear: () => void; prefix: string; onPick?: () => void;
  /** 0.18.57 the project notes are off: no "not written" row with Write them (the banner offers Build them) */
  notesOff?: boolean;
};

/** The text with each search word marked. */
function marked(text: string, words: string[]): ReactNode {
  if (!words.length) return text;
  const low = text.toLowerCase();
  const hits: [number, number][] = [];
  for (const w of words) {
    for (let at = low.indexOf(w); at >= 0; at = low.indexOf(w, at + w.length)) hits.push([at, at + w.length]);
  }
  if (!hits.length) return text;
  hits.sort((a, b) => a[0] - b[0]);
  const out: ReactNode[] = [];
  let pos = 0;
  hits.forEach(([from, to], i) => {
    if (to <= pos) return;
    const start = Math.max(from, pos);
    if (start > pos) out.push(text.slice(pos, start));
    out.push(<mark key={i}>{text.slice(start, to)}</mark>);
    pos = to;
  });
  if (pos < text.length) out.push(text.slice(pos));
  return out;
}

/** A spec's pill in the menu: waiting for you, running, stopped, not built, built. */
function specPill(st: SpecStatus): [string, string] {
  if (st.state === "in_progress") return st.status === "waiting" ? ["waiting", "waiting"] : ["in_progress", "running"];
  return [st.state, SPEC[st.state][1]];
}

/** A page in the menu: its number, its title (the search's words marked) and what state it is in (a spec's build
 *  status, a knowledge section that is stale, being written or not written yet, a workflow that is not keel's own, a
 *  file that is also in another folder). `current` is false in Recent: the page is marked once, in its group. */
function MenuLink({ item, cur, words, onPick, current = true }: {
  item: NavItem; cur: string; words: string[]; onPick?: () => void; current?: boolean;
}) {
  const st = item.spec;
  const pill = st ? specPill(st) : undefined;
  const tip = item.basedOn ? `Based on ${item.basedOn}` : item.path ?? undefined;
  return (
    <a href={`#/wiki/${encodeURIComponent(item.id)}`} aria-current={current && cur === item.id ? "page" : undefined} onClick={onPick}
      className={item.status === "missing" ? "wt-missing" : undefined} title={tip}>
      <span className="wt-t">{item.number && <span className="wt-num">{item.number}</span>}{marked(item.title, words)}</span>
      {item.tag && <span className={`wt-tag wt-tag-${item.tag.toLowerCase()}`}>{item.tag}</span>}
      {pill ? <span className={`wk-sp wk-sp-${pill[0]}`}>{pill[1]}</span>
        : item.status === "writing" ? <span className="adot" />
        : item.status === "stale" ? <span className="sub amber">stale</span> : null}
      {item.also.length > 0 && <span className="sub wk-miss" title={`Also in ${item.also.join(", ")}`}>×{item.also.length + 1}</span>}
    </a>
  );
}

/** 0.18.57 "Turn notes off": keel's agents stop getting the project notes and the flows stop updating them (the notes
 *  stay on disk; the banner then offers Turn on). */
function NotesOffButton({ pid, onDone }: { pid: string; onDone: () => void }) {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      await setNotes(pid, false);
      toast("Project notes are off: keel's agents read the code itself, and the flows no longer update the notes.");
      onDone();
    } catch (e) {
      setErr(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button type="button" className="btn" onClick={() => void run()} disabled={busy}
        title="keel's agents stop getting the project notes; ship stops updating them. The notes stay where they are.">
        {busy ? "Turning off…" : "Turn notes off"}
      </button>
      {err && <span className="wt-err" role="alert">{err}</span>}
    </>
  );
}

/** "Write them": the librarians write the knowledge sections that are not written yet (the knowledge-refresh flow). */
function WriteMissing({ pid, ids }: { pid: string; ids: string[] }) {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const [thread, setThread] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const sections = ids.map((id) => id.replace(/^kb:/, ""));
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      const t = await wikiApi.refresh(pid, sections);
      setThread(t?.thread_id ?? "");
      toast(`The librarians are writing ${sections.join(", ")}.`);
    } catch (e) {
      setErr(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  if (thread !== null) {
    return <a className="wt-act" href={thread ? hashForScreen("flow", thread) : "#/flow"} role="status">Writing · watch it</a>;
  }
  return (
    <>
      <button type="button" className="wt-act" onClick={run} disabled={busy} title={`The librarians write: ${sections.join(", ")}`}>
        {busy ? "Starting…" : "Write them"}
      </button>
      {err && <span className="wt-err" role="alert">{err}</span>}
    </>
  );
}

/** A part of a group: its pages, or, when it folds, a row that opens it (a workflow purpose; the sections not written
 *  yet, with "Write them"). It is open while searching and when it has the open page. */
function Part({ g, p, menu, words, current }: { g: NavGroup; p: NavPart; menu: MenuProps; words: string[]; current: boolean }) {
  const { cur, isOpen, toggle, onPick, prefix, pid } = menu;
  if (p.fold === "missing" && menu.notesOff) return null;
  const links = p.items.map((i) => <MenuLink key={i.id} item={i} cur={cur} words={words} onPick={onPick} current={current} />);
  if (!p.fold) {
    return (
      <div className="wt-part" role={p.title ? "group" : undefined} aria-label={p.title ? `${g.title}: ${p.title}` : undefined}>
        {p.title && <span className="wt-sub">{p.title} <span className="wt-n">{p.items.length}</span></span>}
        {links}
      </div>
    );
  }
  const pinned = p.items.some((i) => i.id === cur);
  const open = pinned || words.length > 0 || isOpen(p.id, false);
  const list = `${prefix}-${p.id}`;
  const title = p.fold === "missing" ? `${p.items.length} not written` : p.title ?? p.id;
  return (
    <div className={`wt-part wt-fold-${p.fold}`} role="group" aria-label={`${g.title}: ${title}`}>
      <div className="wt-fold-row">
        <button type="button" className="wt-fold" aria-expanded={open} aria-controls={list}
          aria-disabled={pinned ? true : undefined}
          title={pinned ? "The open page is here, so it stays open" : open ? `Close ${title}` : `Open ${title}`}
          onClick={() => { if (!pinned && !words.length) toggle(p.id, false); }}>
          <span className="wt-car" aria-hidden="true">{open ? "▾" : "▸"}</span>
          <span className="wt-ht">{title}</span>
          {p.fold === "sub" && <span className="wt-n" aria-label={plural(p.items.length, "page")}>{p.items.length}</span>}
        </button>
        {p.fold === "missing" && <WriteMissing pid={pid} ids={p.items.map((i) => i.id)} />}
      </div>
      <div id={list} className="wt-list" hidden={!open}>{open && links}</div>
    </div>
  );
}

/** A group: its title (opens and closes it, says how many pages) and its parts. The group of the open page stays open;
 *  while searching, every group with a match is open. */
function Group({ g, menu, words, here, current = true }: {
  g: NavGroup; menu: MenuProps; words: string[]; here?: string; current?: boolean;
}) {
  const { isOpen, toggle, prefix } = menu;
  const pinned = g.id === here;
  const searching = words.length > 0;
  const open = pinned || searching || isOpen(g.id, true);
  const list = `${prefix}-${g.id}`;
  const n = groupCount(g);
  return (
    <div className="wt-sec">
      <button type="button" className="wt-h" aria-expanded={open} aria-controls={list}
        aria-disabled={pinned ? true : undefined}
        title={pinned ? "The open page is in this group, so it stays open" : open ? `Close ${g.title}` : `Open ${g.title}`}
        onClick={() => { if (!pinned && !searching) toggle(g.id); }}>
        <span className="wt-car" aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className="wt-ht">{g.title}</span>
        <span className="wt-n" aria-label={plural(n, "page")}>{n}</span>
      </button>
      <div id={list} className="wt-list" hidden={!open}>
        {g.parts.map((p) => <Part key={p.id} g={g} p={p} menu={menu} words={words} current={current} />)}
      </div>
    </div>
  );
}

/** The menu (the side list, and the phone's sheet): the overview's links, Recent, then the groups in order of use.
 *  While searching only the matches show, their groups and parts open; nothing found says so with a way back. The open
 *  page is scrolled into view inside the list (never the window). */
function Sections(menu: MenuProps) {
  const { groups, shown, cur, q, recent, onClear, onPick } = menu;
  const words = searchWords(q);
  const searching = words.length > 0;
  const here = placeOf(groups, cur)?.group.id;
  const top = overviewLinks(groups) ? shown.find((g) => g.id === "overview") : undefined;
  const rest = shown.filter((g) => g !== top);
  const last = searching ? [] : recentItems(groups, recent, cur);
  const nav = useRef<HTMLElement>(null);
  useEffect(() => {
    const box = nav.current;
    const el = box?.querySelector<HTMLElement>('a[aria-current="page"]');
    if (!box || !el || box.scrollHeight <= box.clientHeight) return;
    const b = box.getBoundingClientRect(), r = el.getBoundingClientRect();
    if (r.top < b.top || r.bottom > b.bottom) scrollToCenter(box, el);
  }, [cur, groups.length]);
  return (
    <nav className="wtree" aria-label="Wiki pages" ref={nav}>
      {searching && !shown.length && (
        <div className="wt-none" role="status">
          <span>No page matches “{q.trim()}”.</span>
          <button type="button" className="btn sm" onClick={onClear}>Clear search</button>
        </div>
      )}
      {top && (
        <div className="wt-top">
          {groupItems(top).map((i) => (
            <MenuLink key={i.id} item={i.id === "kb:index" ? { ...i, title: "Overview" } : i} cur={cur} words={words} onPick={onPick} />
          ))}
        </div>
      )}
      {last.length > 0 && (
        <Group g={{ id: "recent", title: "Recent", parts: [{ id: "recent-pages", items: last }] }} words={words} menu={menu} current={false} />
      )}
      {rest.map((g) => <Group key={g.id} g={g} menu={menu} words={words} here={here} />)}
    </nav>
  );
}

/** "Wiki › Specs › In progress": where the open page is in the menu. */
function Crumbs({ groups, cur }: { groups: NavGroup[]; cur: string }) {
  const at = placeOf(groups, cur);
  if (!at) return null;
  return (
    <nav className="wk-crumbs" aria-label="Breadcrumb">
      <a href="#/wiki">Wiki</a>
      <span aria-hidden="true">›</span>
      <span>{at.group.title}</span>
      {at.part.title && <><span aria-hidden="true">›</span><span>{at.part.title}</span></>}
    </nav>
  );
}

/** "On this page": the headings of a long page (three or more), each one scrolls to its place. The links stay
 *  buttons: the hash holds the page id (#/wiki/kb:architecture), so an anchor would change the page. */
function Toc({ doc, text }: { doc: RefObject<HTMLDivElement | null>; text: string }) {
  const [heads, setHeads] = useState<{ level: number; text: string; el: HTMLElement }[]>([]);
  useEffect(() => {
    const els = [...(doc.current?.querySelectorAll<HTMLElement>("h1, h2, h3") ?? [])];
    // the page's own title (a "# " first line) is the page heading already
    const body = els.length && els[0].tagName === "H1" && els.filter((e) => e.tagName === "H1").length === 1 ? els.slice(1) : els;
    setHeads(body.map((el) => ({ level: Number(el.tagName[1]), text: el.textContent ?? "", el })).filter((h) => h.text.trim()));
  }, [doc, text]);
  if (heads.length < 3) return null;
  const top = Math.min(...heads.map((h) => h.level));
  return (
    <details className="wk-toc" open>
      <summary>On this page <span className="wt-n">{heads.length}</span></summary>
      <ul aria-label="On this page">
        {heads.map((h, i) => (
          <li key={i} style={{ paddingLeft: (h.level - top) * 12 }}>
            <button type="button" className="linkbtn" onClick={() => h.el.scrollIntoView({ behavior: "smooth", block: "start" })}>{h.text}</button>
          </li>
        ))}
      </ul>
    </details>
  );
}

function WorkflowPage({ pid, wid }: { pid: string; wid: string }) {
  const wf = useLoad(`wf:${wid}`, () => wikiApi.workflow(wid));
  const est = useLoad(`wfest:${pid}:${wid}`, () => wikiApi.estimate(pid, wid, 3), { live: false });
  const [explain, setExplain] = useState<string | null>(null);
  return (
    <>
    {explain && <StepInfoDrawer pid={pid} workflow={wid} stepId={explain} onClose={() => setExplain(null)} />}
    <Async r={wf} what="Loading the workflow">
      {(w) => {
        const gates = w.steps.filter((s) => s.kind === "gate").length;
        const agents = new Set(w.steps.filter((s) => s.agent).map((s) => s.agent)).size;
        const tokens = tokensByStep(w, est.data);
        return (
          <>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h2 className="wh">{w.name}</h2>
              <div className="row">
                <button className="btn sm" type="button" onClick={() => go("workflows", w.id)}>Edit in builder</button>
                <a className="btn sm" href={wikiApi.exportUrl(w.id)} download={`${w.id}.workflow.yaml`} style={{ textDecoration: "none" }}>Export</a>
              </div>
            </div>
            <p className="sub" style={{ marginTop: 0 }}>
              {w.steps.length} steps · {agents} agents · {gates} gates wait for you · keel rules {w.keel_rules ? "on" : "off"} · version {w.version}
              {est.data ? ` · about ${kfmt(est.data.tokens)} tokens for 3 ACs` : ""}
            </p>
            <div style={{ margin: "12px 0" }}>
              <WorkflowMap id="wiki" title="Steps" steps={w.steps} tokens={tokens} onOpenStep={setExplain}
                label={`Steps of ${w.name}: ${w.steps.length} steps. Select one to see what it does.`} />
            </div>
            <p className="hint">This page is generated from the workflow file, so it is always the version that runs.</p>
          </>
        );
      }}
    </Async>
    </>
  );
}

/** What the spec page knows of its build status: the list's entry, or that the list is loading or failed. */
type SpecInfo = { status: SpecStatus | null; loading: boolean; failed: boolean; reload: () => Promise<void> };

function Page({ pid, id, spec, item }: { pid: string; id: string; spec?: SpecInfo; item?: NavItem }) {
  const page = useLoad(`wikipage:${pid}:${id}`, () => wikiApi.wikiPage(pid, id));
  const doc = useRef<HTMLDivElement>(null);
  if (page.error) return <ErrorBox error={page.error} onRetry={() => void page.reload()} />;
  if (!page.data) return <Loading />;
  const p = page.data;
  const meta = p.meta ?? {};
  const status = typeof meta.status === "string" ? STATUS[meta.status] : undefined;
  const facts = [
    meta.what, meta.words !== undefined && `${meta.words} words`, meta.cites !== undefined && `${meta.cites} citations`,
    meta.by && `by ${meta.by}`, meta.sha && `checked against HEAD ${String(meta.sha).slice(0, 7)}`, meta.updated && `updated ${meta.updated}`,
  ].filter(Boolean) as string[];
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 className="wh">{p.title}</h2>
        {status && <Pill tone={status[0]}>{status[1]}</Pill>}
      </div>
      {facts.length > 0 && <p className="sub" style={{ marginTop: 0 }}>{facts.join(" · ")}</p>}
      {id.startsWith("spec:") && spec && (
        <SpecBuild pid={pid} path={id.slice(5)} title={p.title} acs={typeof meta.acs === "number" ? meta.acs : undefined} info={spec} />
      )}
      {item && item.also.length > 0 && (
        <p className="hint">The same file is also in {item.also.map((a, i) => <span key={a}>{i ? ", " : ""}<span className="mono">{a}</span></span>)}: the menu shows it once, from <span className="mono">{item.path ?? id.replace(/^\w+:/, "")}</span>.</p>
      )}
      {p.markdown && <Toc doc={doc} text={p.markdown} />}
      <div className="wdoc" ref={doc}>{p.markdown ? <Markdown text={p.markdown} /> : <p className="sub">This page is empty.</p>}</div>
      {typeof meta.path === "string" && /^(spec|kb|adr):/.test(id) && <DocHistoryPanel pid={pid} path={meta.path} onRestored={() => void page.reload()} />}
      {id.startsWith("kb:") && <p className="hint">Every claim cites <span className="mono">file:line</span>. <span className="mono">memory check</span> opens each citation; a broken one fails the check.</p>}
    </>
  );
}

/** 0.17.3 which flow built this spec and where it is: built (a link), in progress (Open the flow), stopped (Resume, or
 *  why not and Start), not built (Start a flow from this spec, with the feature workflow and this spec as its input). */
function SpecBuild({ pid, path, title, acs, info }: { pid: string; path: string; title: string; acs?: number; info: SpecInfo }) {
  const [start, setStart] = useState(false);
  if (info.failed) return null;     // a keel without the status endpoint: the page as before
  if (!info.status) return info.loading ? <div className="wk-build"><Skeleton lines={1} label="Loading the build status" /></div> : null;
  const st = info.status;
  const [tone, label] = SPEC[st.state];
  const flow = st.thread_id ? hashForScreen("flow", st.thread_id) : undefined;
  const name = st.flow_title || st.thread_id || "";
  const wf = st.workflow ? ` (${st.workflow})` : "";
  const others = st.flows.filter((f) => f.thread_id !== st.thread_id);
  const startButton = (primary: boolean) => (
    <button className={primary ? "btn primary sm" : "btn sm"} type="button" onClick={() => setStart(true)}>Start a flow from this spec</button>
  );
  return (
    <section className="wk-build" aria-label="Build status">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>Build status</b>
        <Pill tone={tone}>{label}</Pill>
      </div>
      {st.state === "not_built" && (
        <>
          <p className="sub">No flow has built this spec yet. Start the feature flow from it: the flow keeps its criteria.</p>
          <div className="row">{startButton(true)}</div>
        </>
      )}
      {st.state === "built" && (
        <p className="sub">Built by the flow <a href={flow}>{name}</a>{wf}{st.updated_at ? `, finished ${agoText(st.updated_at)}` : ""}.</p>
      )}
      {st.state === "in_progress" && (
        <>
          <p className="sub">
            The flow <b>{name}</b>{wf} {st.status === "waiting" ? "waits" : "runs"}{st.phase ? <>, phase <span className="mono">{st.phase}</span></> : null}.
            {st.status === "waiting" && st.waiting ? <> It waits for you: <b>{st.waiting}</b>{/[.?!]$/.test(st.waiting) ? "" : "."}</> : null}
          </p>
          <div className="row"><a className="btn primary sm" href={flow} style={{ textDecoration: "none" }}>Open the flow</a></div>
        </>
      )}
      {st.state === "stopped" && (
        <>
          <p className="sub">
            The flow <a href={flow}>{name}</a>{wf} {st.status === "failed" ? "failed" : "stopped"}
            {st.phase ? <>, in phase <span className="mono">{st.phase}</span></> : null}.
          </p>
          {st.resumable && st.thread_id ? (
            <ResumeRow thread={{ thread_id: st.thread_id, status: st.status ?? "stopped" }} onDone={async () => go("flow", st.thread_id!)} />
          ) : (
            <>
              <p className="hint">{st.why ?? "Only the last stopped flow can be resumed."}</p>
              <div className="row">{startButton(false)}</div>
            </>
          )}
        </>
      )}
      {others.length > 0 && (
        <div className="wk-flows">
          <span className="lab-s">Other flows on this spec</span>
          <ul>
            {others.map((f) => (
              <li key={f.thread_id}>
                <a href={hashForScreen("flow", f.thread_id)}>{f.title || f.thread_id}</a>
                <span className={`wk-sp wk-sp-${f.state}`}>{SPEC[f.state][1]}</span>
                {f.updated_at && <span className="sub">{agoText(f.updated_at)}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {start && (
        <StartFlowDrawer projectId={pid} workflowId="feature" title={title} spec={{ path, acs: acs ?? st.acs }}
          onClose={() => { setStart(false); void info.reload(); }} />
      )}
    </section>
  );
}

/** On a phone: the menu as one sheet ("Page: … ▾") whose panel has the same sections, open and close, and pills. It
 *  opens while searching, so the matches show; picking a page closes it and ends the search. */
function PagePicker(props: MenuProps) {
  const { groups, cur, q, onClear } = props;
  const [opened, setOpen] = useState(false);
  const open = opened || !!q.trim();
  const btn = useRef<HTMLButtonElement>(null);
  const title = placeOf(groups, cur)?.item.title;
  return (
    <div className="wk-pick" onKeyDown={(ev) => {
      if (ev.key === "Escape" && open) {
        setOpen(false);
        if (q.trim()) onClear();
        btn.current?.focus();
      }
    }}>
      <button ref={btn} type="button" className="wk-pick-btn" aria-expanded={open} aria-controls="wk-pick-list"
        onClick={() => { if (open && q.trim()) onClear(); setOpen(!open); }}>
        <span className="lab-s">Page</span>
        <b>{title ?? "Pick a page"}</b>
        <span aria-hidden="true">{open ? "▴" : "▾"}</span>
      </button>
      <div id="wk-pick-list" className="wk-pick-list" hidden={!open}>
        {open && <Sections {...props} onPick={() => { setOpen(false); if (q.trim()) onClear(); }} />}
      </div>
    </div>
  );
}

export function WikiPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const { arg } = useRoute();
  const tree = useLoad(`wiki:${pid}`, () => wikiApi.wiki(pid));
  const status = useLoad(`specstatus:${pid}`, () => wikiApi.specStatus(pid));
  const specs = useMemo(() => new Map((status.data ?? []).map((x) => [x.spec, x])), [status.data]);
  const groups = useMemo(() => (tree.data ? buildNav(tree.data, specs) : []), [tree.data, specs]);
  const { isOpen, toggle } = useFolds(pid);
  const [q, setQ] = useState("");
  const box = useRef<HTMLInputElement>(null);
  const shown = useMemo(() => filterNav(groups, q), [groups, q]);
  const narrow = useNarrow(900);
  const cur = arg ?? firstPage(groups) ?? "";
  const recent = useRecent(pid, groups, cur);
  const clear = () => {
    setQ("");
    box.current?.focus();
  };
  const notes = tree.data?.notes ?? null;
  const notesOff = !!notes && !notes.on;
  const stale = groups.flatMap(groupItems).filter((i) => i.status === "stale" && i.id.startsWith("kb:")).map((i) => i.id.slice(3));
  const place = placeOf(groups, cur);
  return (
    <div className="rdw rdw-wiki">
      <PageHead title="Wiki"
        sub={`What keel knows about ${project?.name ?? pid}: an overview, the knowledge base the librarians write, the specs by build status, the decisions and a page for every workflow.`}
        actions={notesOff ? undefined : <>
          <RefreshStaleButton pid={pid} sections={stale} className="btn" />
          {notes?.on && <NotesOffButton pid={pid} onDone={() => void tree.reload()} />}
        </>} />
      {tree.data?.notice && <div className="errbox" role="alert" style={{ marginBottom: 16 }}><b>keel cannot see this project's own files:</b> {tree.data.notice}</div>}
      {notesOff && <ProjectNotesOff pid={pid} notes={notes} onChange={() => void tree.reload()} />}
      {tree.data?.layout === "repo" && <p className="hint">keel keeps this project's files in <span className="mono">docs/</span> (layout repo), committed with your code. New projects use <span className="mono">.keel/</span>.</p>}
      {tree.error ? <ErrorBox error={tree.error} onRetry={() => void tree.reload()} /> : !tree.data ? <div className="panel"><Skeleton lines={6} label="Loading the wiki" /></div> : (() => {
        if (!groups.length) {
          return <div className="panel"><EmptyState title="The wiki is empty" action={<a className="btn" href="#/flow">Open Flow</a>}>The init flow writes the knowledge base, and every workflow gets a page here. Start init from the Flow page.</EmptyState></div>;
        }
        const menu = { pid, groups, shown, cur, q, recent, isOpen, toggle, onClear: clear, notesOff };
        return (
          <div className="wiki-grid">
            <div className="wk-side rise r1">
              <div className="wk-search">
                {/* keel's SearchBox, with Esc to clear (on the box only: Esc elsewhere keeps its own meaning) */}
                <input ref={box} type="search" className="inline-input pg-search" placeholder="Search the wiki" aria-label="Search the wiki"
                  value={q} onChange={(ev) => setQ(ev.target.value)}
                  onKeyDown={(ev) => {
                    if (ev.key === "Escape" && q) {
                      ev.preventDefault();
                      ev.stopPropagation();
                      setQ("");
                    }
                  }} />
              </div>
              {narrow ? <PagePicker {...menu} prefix="wkp" /> : <Sections {...menu} prefix="wks" />}
            </div>
            <article className="panel wk-article rise r2"><div className="panel-body">
              <Crumbs groups={groups} cur={cur} />
              {!cur ? <span className="sub">Pick a page.</span> : cur.startsWith("wf:") ? <WorkflowPage key={cur} pid={pid} wid={cur.slice(3)} /> : (
                <Page key={cur} pid={pid} id={cur} item={place?.item}
                  spec={{ status: specs.get(cur) ?? null, loading: !status.data && !status.error, failed: !!status.error, reload: status.reload }} />
              )}
            </div></article>
          </div>
        );
      })()}
    </div>
  );
}
