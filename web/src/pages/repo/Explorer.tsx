// The Explorer: the project's files as a tree with git decorations (M/A/U/D colours and letters), keel-written and
// frozen marks, file-type icons, filter as you type, collapse all, reveal the active file, keyboard navigation and
// lazy loading (a folder's subtree is fetched when it opens). Long lists render only the rows on screen.

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { api, ApiError, errorParts, type Change, type TreeNode } from "../../api";
import { ErrorBox } from "../../components/ui";
import { Chevron, FileIcon, FolderIcon, Icon } from "./icons";
import { ancestors, changedFolders, decoOf, depthOf, indexTree, parentOf, visibleRows, type Row } from "./model";

/** Levels loaded at first, and per folder that opens later. */
const FIRST = 2;
const STEP = 2;
const ROW = 22;
const VIRTUAL_FROM = 400;

type Props = {
  pid: string;
  title: string;
  root?: string;
  changes: Change[];
  active: string | null;
  reveal: number;
  focusFilter: number;
  /** Bumped when the files changed under us (a merge): read the tree again. */
  version?: number;
  onOpen: (path: string, pin: boolean) => void;
};

function load(key: string): Set<string> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(key) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

export function Explorer({ pid, title, root, changes, active, reveal, focusFilter, version = 0, onOpen }: Props) {
  const [nodes, setNodes] = useState<TreeNode[] | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  /** Folder → how many levels below it are loaded ("" = the root). */
  const loaded = useRef(new Map<string, number>());
  const [pending, setPending] = useState<Set<string>>(new Set());
  const storeKey = `keel2.repo.open.${pid}`;
  const [expanded, setExpanded] = useState<Set<string>>(() => load(storeKey));
  const [filter, setFilter] = useState("");
  const [focus, setFocus] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const [win, setWin] = useState({ from: 0, to: 200 });
  /** Row height in px (22, taller on a phone), measured. */
  const rowH = useRef(ROW);

  const isLoaded = useCallback((dir: string) => {
    for (const [root, depth] of loaded.current) {
      if (root === "" ? depthOf(dir) + 1 < depth : (dir === root || dir.startsWith(root + "/")) && depthOf(dir) - depthOf(root) < depth) return true;
    }
    return false;
  }, []);

  const fetchDir = useCallback(async (dir: string) => {
    setPending((p) => new Set(p).add(dir));
    try {
      const more = await api.tree(pid, STEP, dir);
      loaded.current.set(dir, STEP);
      setNodes((have) => {
        const seen = new Set((have ?? []).map((n) => n.path));
        return [...(have ?? []), ...more.filter((n) => !seen.has(n.path))];
      });
    } catch (e) {
      // a folder that is gone (open last time, deleted since) just closes
      if (e instanceof ApiError && e.status === 404) {
        setExpanded((x) => {
          const n = new Set(x);
          n.delete(dir);
          return n;
        });
      } else setErr(errorParts(e));
    } finally {
      setPending((p) => {
        const n = new Set(p);
        n.delete(dir);
        return n;
      });
    }
  }, [pid]);

  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;

  /** The first levels, then every open folder again (open last time in this browser tab, or revealed meanwhile). */
  const refresh = useCallback(async () => {
    setErr(null);
    try {
      const first = await api.tree(pid, FIRST);
      loaded.current = new Map([["", first.length >= 5000 ? 1 : FIRST]]);
      setNodes(first);
      const open = [...expandedRef.current].sort((a, b) => depthOf(a) - depthOf(b));
      for (const d of open) if (!isLoaded(d)) void fetchDir(d);
    } catch (e) {
      setErr(errorParts(e));
    }
  }, [pid, isLoaded, fetchDir]);

  useEffect(() => {
    void refresh();
  }, [refresh, version]);

  useEffect(() => {
    try {
      sessionStorage.setItem(storeKey, JSON.stringify([...expanded]));
    } catch {
      /* private mode */
    }
  }, [expanded, storeKey]);

  const idx = useMemo(() => indexTree(nodes ?? []), [nodes]);
  const rows = useMemo(() => visibleRows(idx, expanded, filter), [idx, expanded, filter]);
  const byPath = useMemo(() => new Map(changes.map((c) => [c.path, c])), [changes]);
  const folders = useMemo(() => changedFolders(changes), [changes]);
  const keelPaths = useMemo(() => new Set((nodes ?? []).filter((n) => n.keel).map((n) => n.path)), [nodes]);

  const toggle = (dir: string, open?: boolean) => {
    setExpanded((s) => {
      const n = new Set(s);
      const want = open ?? !n.has(dir);
      if (want) n.add(dir);
      else n.delete(dir);
      return n;
    });
    if ((open ?? !expanded.has(dir)) && !isLoaded(dir)) void fetchDir(dir);
    else if (open ?? !expanded.has(dir)) {
      // prefetch the next level so the folders inside open at once
      const kids = idx.get(dir) ?? [];
      if (kids.some((k) => k.kind === "dir" && !isLoaded(k.path)) && !pending.has(dir)) void fetchDir(dir);
    }
  };

  // reveal the active file: open its folders, select it and scroll to it
  useEffect(() => {
    if (!reveal || !active) return;
    setFilter("");
    const dirs = ancestors(active);
    setExpanded((s) => new Set([...s, ...dirs]));
    for (const d of dirs) if (!isLoaded(d)) void fetchDir(d);
    setFocus(active);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reveal]);

  useEffect(() => {
    if (focusFilter) filterRef.current?.focus();
  }, [focusFilter]);

  // the open file is the selected row again when another tab becomes active
  useEffect(() => setFocus(null), [active]);

  const sel = focus ?? active;
  const selIndex = rows.findIndex((r) => r.node.path === sel);

  // keyboard moves: DOM focus follows the selected row while the tree has focus
  useEffect(() => {
    const el = list.current;
    if (!el || !focus || !el.contains(document.activeElement)) return;
    const row = Array.from(el.querySelectorAll<HTMLElement>("[data-path]")).find((x) => x.dataset.path === focus);
    if (row && row !== document.activeElement) row.focus();
  }, [focus, rows]);

  // keep the focused row on screen
  useEffect(() => {
    const el = list.current;
    if (!el || selIndex < 0) return;
    const h = rowH.current;
    const top = selIndex * h;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (el.clientHeight && top + h > el.scrollTop + el.clientHeight) el.scrollTop = top + h - el.clientHeight;
  }, [selIndex, focus]);

  const measure = () => {
    const el = list.current;
    if (!el) return;
    rowH.current = el.querySelector<HTMLElement>(".ex-row")?.offsetHeight || ROW;
    const from = Math.max(0, Math.floor(el.scrollTop / rowH.current) - 30);
    const to = Math.ceil((el.scrollTop + (el.clientHeight || 800)) / rowH.current) + 30;
    setWin((w) => (w.from === from && w.to === to ? w : { from, to }));
  };

  const onKey = (e: KeyboardEvent) => {
    if (!rows.length) return;
    const i = Math.max(0, selIndex);
    const r = rows[i];
    const move = (j: number) => {
      const k = Math.min(rows.length - 1, Math.max(0, j));
      setFocus(rows[k].node.path);
      e.preventDefault();
    };
    switch (e.key) {
      case "ArrowDown": move(selIndex < 0 ? 0 : i + 1); break;
      case "ArrowUp": move(i - 1); break;
      case "Home": move(0); break;
      case "End": move(rows.length - 1); break;
      case "PageDown": move(i + 15); break;
      case "PageUp": move(i - 15); break;
      case "ArrowRight":
        e.preventDefault();
        if (r.node.kind === "dir") {
          if (!r.open) toggle(r.node.path, true);
          else if (rows[i + 1] && parentOf(rows[i + 1].node.path) === r.node.path) setFocus(rows[i + 1].node.path);
        }
        break;
      case "ArrowLeft":
        e.preventDefault();
        if (r.node.kind === "dir" && r.open && !filter) toggle(r.node.path, false);
        else if (parentOf(r.node.path)) setFocus(parentOf(r.node.path));
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        if (r.node.kind === "dir") toggle(r.node.path);
        else onOpen(r.node.path, e.key === "Enter");
        break;
    }
  };

  const virtual = rows.length > VIRTUAL_FROM;
  const from = virtual ? Math.min(win.from, rows.length) : 0;
  const to = virtual ? Math.min(win.to, rows.length) : rows.length;

  const row = (r: Row, i: number) => {
    const n = r.node;
    const dir = n.kind === "dir";
    const c = byPath.get(n.path);
    const deco = dir ? undefined : decoOf(c);
    const tone = deco?.tone ?? (dir ? folders.get(n.path) : undefined);
    const keelMark = n.keel && !keelPaths.has(parentOf(n.path));
    const isSel = n.path === sel;
    const loading = dir && r.open && pending.has(n.path) && !(idx.get(n.path)?.length);
    return (
      <div key={n.path}>
        <div role="treeitem" aria-level={r.depth + 1} aria-selected={isSel} aria-expanded={dir ? r.open : undefined}
          aria-label={dir ? undefined : `Open ${n.path}`} data-path={n.path} tabIndex={isSel || (selIndex < 0 && i === 0) ? 0 : -1}
          className={`ex-row${isSel ? " sel" : ""}${n.path === active ? " ex-open" : ""}${tone ? ` t-${tone}` : ""}`}
          style={{ paddingLeft: 6 + r.depth * 12 }}
          title={deco ? `${n.path} · ${deco.title}` : n.path}
          onClick={() => {
            setFocus(n.path);
            if (dir) toggle(n.path);
            else onOpen(n.path, false);
          }}
          onDoubleClick={() => !dir && onOpen(n.path, true)}>
          <span className="ex-tw">{dir ? <Chevron open={r.open} /> : null}</span>
          {dir ? <FolderIcon open={r.open} keel={n.keel} /> : <FileIcon name={n.name} />}
          <span className="ex-name">{n.name}</span>
          {keelMark && <span className="ex-keel" title="Written by keel: every change is a commit you can review">keel</span>}
          {n.frozen && !dir && <span className="ex-lock" title="Frozen in this phase: agents cannot edit it"><Icon name="lock" size={12} /></span>}
          {deco ? <span className={`ex-deco t-${deco.tone}`} aria-label={deco.title}>{deco.letter}</span>
            : tone ? <span className={`ex-dot t-${tone}`} aria-label="Has changes">●</span> : null}
        </div>
        {loading && <div className="ex-row ex-loading" style={{ paddingLeft: 24 + r.depth * 12 }}>Loading…</div>}
      </div>
    );
  };

  return (
    <div className="sv">
      <div className="sv-head">
        <h2 className="sv-title" title={root}>Explorer<span className="sv-sub">{title}</span></h2>
        <div className="sv-actions">
          <button type="button" className="ib" aria-label="Reveal the open file" title="Reveal the open file" disabled={!active}
            onClick={() => {
              if (!active) return;
              setFilter("");
              const dirs = ancestors(active);
              setExpanded((s) => new Set([...s, ...dirs]));
              for (const d of dirs) if (!isLoaded(d)) void fetchDir(d);
              setFocus(active);
            }}><Icon name="reveal" size={16} /></button>
          <button type="button" className="ib" aria-label="Refresh the tree" title="Refresh the tree" onClick={() => void refresh()}><Icon name="refresh" size={16} /></button>
          <button type="button" className="ib" aria-label="Collapse all folders" title="Collapse all folders" onClick={() => setExpanded(new Set())}><Icon name="collapse" size={16} /></button>
        </div>
      </div>
      <div className="sv-filter">
        <input ref={filterRef} type="search" aria-label="Filter files" placeholder="Filter files by name" value={filter}
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" && rows.length) {
              e.preventDefault();
              setFocus(rows[0].node.path);
              window.setTimeout(() => list.current?.querySelector<HTMLElement>('[tabindex="0"]')?.focus(), 0);
            }
            if (e.key === "Escape") setFilter("");
          }} />
      </div>
      {err && <div className="sv-pad"><ErrorBox error={err} onRetry={() => void refresh()} /></div>}
      {!nodes && !err && <div className="sv-pad"><span className="pg-spin" role="status">Reading files…</span></div>}
      {nodes && !nodes.length && <div className="sv-pad sub">The repo is empty. Commit a first file, then come back.</div>}
      {nodes && filter && !rows.length && <div className="sv-pad sub">No open folder has a file named like that. Quick open (⌘P / Ctrl+P) searches every file.</div>}
      <div ref={list} className="ex-list" role="tree" aria-label="Files" onKeyDown={onKey} onScroll={virtual ? measure : undefined}
        onFocus={(e) => {
          const p = (e.target as HTMLElement).dataset.path;
          if (p && p !== focus) setFocus(p);
        }}>
        <div style={virtual ? { paddingTop: from * rowH.current, paddingBottom: (rows.length - to) * rowH.current } : undefined}>
          {rows.slice(from, to).map((r, k) => row(r, from + k))}
        </div>
      </div>
    </div>
  );
}
