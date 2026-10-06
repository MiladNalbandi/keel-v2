// Graph (Project): what uses what in the code, from the code graph index keel builds for each project (CodeGraph).
// Three levels on the Map's canvas: the packages and folders (folded to a depth) and the uses between them; one
// package's classes, functions and the packages they touch; one symbol with who uses it and what it uses.
//   #/graph                  packages        #/graph/in:<key>   one package       #/graph/<symbol id>   one symbol

import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorParts, type GraphFocus, type GraphHit, type GraphOverview } from "../api";
import { BoxDiagram, type GBox } from "../components/er/BoxDiagram";
import { IconDefs } from "../components/er/icons";
import { fileHref } from "../components/er/Structure";
import { FocusDiagram } from "../components/graph/FocusDiagram";
import {
  autoDepth, groupBoxes, groupLabel, iconOf, isTest, kindWord, maxDepth, overviewBoxes, usesText, type Depth,
} from "../components/graph/model";
import { EmptyState } from "../components/page";
import { Async, ErrorBox, PageHead } from "../components/ui";
import { agoText } from "../components/UsageStrip";
import { go, useApp, useLoad, useRoute } from "../state";

type Overview = Extract<GraphOverview, { available: true }>;
type Focus = Extract<GraphFocus, { focus: unknown }>;

const depthKey = (pid: string) => `keel2.graph.${pid}.depth`;
const testsKey = (pid: string) => `keel2.graph.${pid}.tests`;
const read = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const write = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* private window */ } };

function Glyph({ kind }: { kind: string }) {
  return <svg className={`gk gk-${iconOf(kind)}`} width="15" height="15" aria-hidden="true"><use href={`#erd-i-${iconOf(kind)}`} /></svg>;
}

/** Find a class, a method or a function anywhere in the project; Enter or a click opens it in the middle. */
function SymbolSearch({ pid }: { pid: string }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<GraphHit[]>([]);
  const [open, setOpen] = useState(false);
  const [pick, setPick] = useState(0);
  const closing = useRef<number | undefined>(undefined);
  useEffect(() => {
    const text = q.trim();
    if (!text) { setHits([]); return; }
    let live = true;
    const t = window.setTimeout(() => {
      api.graphSearch(pid, text).then((r) => { if (live) { setHits(r.results ?? []); setPick(0); } }, () => live && setHits([]));
    }, 150);
    return () => { live = false; window.clearTimeout(t); };
  }, [q, pid]);
  const choose = (h: GraphHit) => { setOpen(false); setQ(""); go("graph", h.id); };
  const show = open && q.trim().length > 0;
  return (
    <div className="gsearch" role="search">
      <input type="search" role="combobox" aria-label="Find a symbol" placeholder="Find a class, method or function…" value={q}
        aria-expanded={show} aria-controls="gsearch-list" aria-autocomplete="list"
        aria-activedescendant={show && hits.length ? `gsearch-${pick}` : undefined}
        onChange={(e) => { setQ(e.target.value); window.clearTimeout(closing.current); setOpen(true); }}
        onFocus={() => { window.clearTimeout(closing.current); setOpen(true); }}
        onBlur={() => { closing.current = window.setTimeout(() => setOpen(false), 150); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setPick((p) => Math.min(hits.length - 1, p + 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setPick((p) => Math.max(0, p - 1)); }
          else if (e.key === "Enter" && hits[pick]) { e.preventDefault(); choose(hits[pick]); }
          else if (e.key === "Escape") { setQ(""); setOpen(false); }
        }} />
      {show && (
        <ul className="gsearch-list" id="gsearch-list" role="listbox" aria-label="Matching symbols">
          {hits.length ? hits.map((h, i) => (
            <li key={h.id} id={`gsearch-${i}`} role="option" aria-selected={i === pick} className={i === pick ? "on" : ""}
              onMouseDown={(e) => { e.preventDefault(); choose(h); }}>
              <Glyph kind={h.kind} /><span className="mono">{h.name}</span>
              <span className="gsearch-sub">{kindWord(h.kind)} · {h.file.split("/").pop()}:{h.line}</span>
            </li>
          )) : <li className="gsearch-none" role="option" aria-selected={false}>No symbol matches “{q.trim()}”.</li>}
        </ul>
      )}
    </div>
  );
}

function Crumbs({ items }: { items: { label: string; href?: string }[] }) {
  return (
    <nav className="gcrumbs" aria-label="Where you are in the graph">
      {items.map((c, i) => (
        <span key={i}>
          {i > 0 && <span className="gcrumb-sep" aria-hidden="true">›</span>}
          {c.href ? <a href={c.href}>{c.label}</a> : <b aria-current="page">{c.label}</b>}
        </span>
      ))}
    </nav>
  );
}

/** The panel beside one symbol: what it is, where it is, how much depends on it, and the two lists. */
function FocusPanel({ data, o, depth, selected }: { data: Focus; o: Overview | null; depth: Depth; selected: string | null }) {
  const f = data.focus;
  const sel = selected && selected !== f.id ? data.nodes.find((n) => n.id === selected) : undefined;
  const near = (col: number) => data.nodes.filter((n) => n.col === col).map((n) => {
    const e = data.edges.find((x) => (col < 0 ? x.from === n.id && x.to === f.id : x.from === f.id && x.to === n.id));
    return { n, e };
  });
  const users = near(-1), used = near(1);
  const gl = (g: string) => (o ? groupLabel(o, g, depth) : g.replace(/^\w+:/, ""));
  const List = ({ title, rows, more }: { title: string; rows: typeof users; more: number }) => (
    <section className="gp-sec">
      <h3>{title} <span className="sub num">{rows.length + more}</span></h3>
      {rows.length ? (
        <ul className="gp-list">
          {rows.map(({ n, e }) => (
            <li key={n.id} className={`gp-use ${selected === n.id ? "on" : ""}`}>
              <a href={`#/graph/${encodeURIComponent(n.id)}`} title={`Put ${n.name} in the middle`}><Glyph kind={n.kind} />{n.name}</a>
              <span className="gp-n" title={e ? usesText(e.k) : ""}>{e?.n ?? ""}×</span>
              {e?.sites[0] && <a className="gp-site mono" href={fileHref({ rel: e.sites[0].file, line: e.sites[0].line })}
                title="Open this use in the Repo page">{e.sites[0].file.split("/").pop()}:{e.sites[0].line}</a>}
            </li>
          ))}
        </ul>
      ) : <p className="sub">Nothing.</p>}
      {more > 0 && <p className="hint">+{more} more not drawn (the busiest are shown).</p>}
    </section>
  );
  return (
    <aside className="gpanel" aria-label={`About ${f.name}`}>
      <div className="gp-head">
        <span className="gp-kind"><Glyph kind={f.kind} />{kindWord(f.kind)}</span>
        <h2 className="mono">{f.name}</h2>
        <span className="sub mono gp-q">{f.qualified}</span>
      </div>
      <dl className="gp-facts">
        <dt>In</dt><dd>{f.unit ? <a href={`#/graph/${encodeURIComponent(f.unit.id)}`}>{f.unit.name}</a> : null}{f.unit ? " · " : ""}{gl(f.group)}</dd>
        <dt>File</dt><dd><a className="mono" href={fileHref({ rel: f.file, line: f.line })} title="Open in the Repo page">{f.file}:{f.line}</a></dd>
        <dt>Impact</dt><dd><b>{data.impact}{data.impact_capped ? "+" : ""}</b> {data.level === "unit" ? (data.impact === 1 ? "unit depends" : "units depend") : (data.impact === 1 ? "symbol depends" : "symbols depend")} on it, directly or through others</dd>
      </dl>
      {f.signature && <pre className="gp-sig mono">{f.signature}</pre>}
      {f.docstring && <p className="gp-doc">{f.docstring}</p>}
      {sel && (
        <p className="gp-sel">Selected: <b>{sel.name}</b> ({kindWord(sel.kind)}, {gl(sel.group)}) · <a href={`#/graph/${encodeURIComponent(sel.id)}`}>put it in the middle</a> · <a href={fileHref({ rel: sel.file, line: sel.line })}>open the code</a></p>
      )}
      <List title="Used by" rows={users} more={data.more["-1"] ?? 0} />
      <List title="Uses" rows={used} more={data.more["1"] ?? 0} />
      {data.level === "unit" && data.focus.members.length > 0 && (
        <section className="gp-sec">
          <h3>Members <span className="sub num">{data.focus.members.length}</span></h3>
          <ul className="gp-list">
            {data.focus.members.map((m) => (
              <li key={m.id}>
                <a href={`#/graph/${encodeURIComponent(m.id)}`}><Glyph kind={m.kind} />{m.name}</a>
                <span className="gp-n" title="used from outside / uses outside">{m.in ? `←${m.in}` : ""}{m.in && m.out ? " " : ""}{m.out ? `${m.out}→` : ""}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}

function FocusView({ pid, id, o, depth, name }: { pid: string; id: string; o: Overview | null; depth: Depth; name: string }) {
  const [steps, setSteps] = useState<1 | 2>(() => (read(`keel2.graph.steps`) === "2" ? 2 : 1));
  const r = useLoad(`graph:${pid}:${id}:${steps}`, () => api.graphNode(pid, id, steps), { live: false });
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => setSelected(null), [id]);
  const setS = (s: 1 | 2) => { setSteps(s); write("keel2.graph.steps", String(s)); };
  return (
    <Async r={r} what="Reading the symbol">
      {(d) => {
        if (!d.available) return <div className="panel"><EmptyState title="No code graph">{d.reason}</EmptyState></div>;
        if (d.missing !== undefined || !("focus" in d)) return <div className="panel"><EmptyState title="Symbol not found" action={<a className="btn" href="#/graph">All packages</a>}>{d.missing}</EmptyState></div>;
        const f = d.focus;
        const key = o ? overviewBoxes(o, depth, true).keyOf.get(f.unit?.id ?? f.id) : undefined;
        return (
          <>
            <div className="gbar">
              <Crumbs items={[
                { label: "All packages", href: "#/graph" },
                ...(o && key ? [{ label: groupLabel(o, f.group, depth), href: `#/graph/${encodeURIComponent(`in:${key}`)}` }] : []),
                ...(f.unit ? [{ label: f.unit.name, href: `#/graph/${encodeURIComponent(f.unit.id)}` }] : []),
                { label: f.unit ? f.name.split(".").pop()! : f.name },
              ]} />
              <span className="gbar-gap" />
              <div className="gseg" role="radiogroup" aria-label="How far to look">
                {([1, 2] as const).map((s) => (
                  <button key={s} type="button" role="radio" aria-checked={steps === s} className={steps === s ? "on" : ""} onClick={() => setS(s)}>
                    {s === 1 ? "1 step" : "2 steps"}
                  </button>
                ))}
              </div>
            </div>
            <div className="gfocus-wrap">
              <FocusDiagram data={d} name={name} onOpen={(x) => go("graph", x)} selected={selected} onSelect={setSelected} />
              <FocusPanel data={d} o={o} depth={depth} selected={selected} />
            </div>
          </>
        );
      }}
    </Async>
  );
}

function Levels({ pid, o, arg, depth, setDepth, tests, setTests, name }: {
  pid: string; o: Overview; arg?: string; depth: Depth; setDepth: (d: Depth) => void; tests: boolean; setTests: (t: boolean) => void; name: string;
}) {
  const inGroup = arg?.startsWith("in:") ? arg.slice(3) : null;
  const over = useMemo(() => overviewBoxes(o, depth, tests), [o, depth, tests]);
  const grp = useMemo(() => (inGroup ? groupBoxes(o, inGroup, depth, tests) : null), [o, inGroup, depth, tests]);
  const deepest = maxDepth(o.groups);
  const hiddenTests = tests ? 0 : o.units.filter((u) => isTest(u.file)).length;
  const open = (b: GBox) => {
    if (inGroup) go("graph", b.id.startsWith("x:") ? `in:${b.id.slice(2)}` : b.id);
    else go("graph", `in:${b.id}`);
  };
  const controls = (
    <>
      <span className="gbar-gap" />
      <label className="gctl">Depth
        <select value={String(depth)} onChange={(e) => setDepth(e.target.value === "all" ? "all" : Number(e.target.value))} aria-label="Package depth">
          {Array.from({ length: deepest }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d}</option>)}
          <option value="all">all</option>
        </select>
      </label>
      <label className="gctl check"><input type="checkbox" checked={tests} onChange={(e) => setTests(e.target.checked)} /> Tests{hiddenTests ? ` (${hiddenTests} hidden)` : ""}</label>
    </>
  );
  if (grp) {
    if (!grp.units) {
      return (
        <div className="panel"><EmptyState title="Nothing in this package here" action={<a className="btn" href="#/graph">All packages</a>}>
          At this depth{tests ? "" : " and without tests"} no class or function of {name} is in it.
        </EmptyState></div>
      );
    }
    return (
      <>
        <div className="gbar"><Crumbs items={[{ label: "All packages", href: "#/graph" }, { label: grp.label }]} />{controls}</div>
        <BoxDiagram key={`grp:${inGroup}:${depth}:${tests}`} pid={pid} level={`graph.${inGroup}`} name={name} boxes={grp.boxes} edges={grp.edges}
          onDrill={open} label={`${grp.label} in ${name}: ${grp.units} classes and functions, and the packages they touch`} />
        <p className="hint">Lines point at what is used; a thick line is many uses. Grey boxes are other packages. Open a class or function to see who uses it and what it uses.</p>
      </>
    );
  }
  return (
    <>
      <div className="gbar"><Crumbs items={[{ label: "All packages" }]} />{controls}</div>
      {over.boxes.length ? (
        <BoxDiagram key={`all:${depth}:${tests}`} pid={pid} level={`graph.all.${depth}`} name={name} boxes={over.boxes} edges={over.edges}
          onDrill={open} label={`Packages of ${name}: ${over.boxes.length} boxes, ${over.edges.length} lines`} />
      ) : <div className="panel"><EmptyState title="Nothing to draw">The code graph of {name} has no classes or functions{tests ? "" : " outside tests"}.</EmptyState></div>}
      <p className="hint">Each box is a package (or a folder) with its most used classes; a line means code in one uses code in the other, and points at what is used. Code that is used by much sits on the right, entry points on the left.</p>
    </>
  );
}

export function GraphPage({ pid }: { pid: string }) {
  const { project, toast } = useApp();
  const { arg } = useRoute();
  const name = project?.name ?? pid;
  const r = useLoad(`graph:${pid}`, () => api.graph(pid), { live: false });
  const [busy, setBusy] = useState(false);
  const [depthState, setDepthState] = useState<Depth | null>(() => {
    const v = read(depthKey(pid));
    return v === "all" ? "all" : v ? Number(v) || null : null;
  });
  const [tests, setTestsState] = useState(() => read(testsKey(pid)) === "1");
  const o = r.data && r.data.available ? r.data : null;
  const depth: Depth = depthState ?? (o ? autoDepth(o.groups) : 1);
  const setDepth = (d: Depth) => { setDepthState(d); write(depthKey(pid), String(d)); };
  const setTests = (t: boolean) => { setTestsState(t); write(testsKey(pid), t ? "1" : "0"); };

  const rebuild = async () => {
    setBusy(true);
    try {
      await api.rebuildIndex(pid);
      toast("keel is indexing the code. The graph updates when it is done.");
      window.setTimeout(() => void r.reload(), 4000);
    } catch (e) {
      toast(`Not started: ${errorParts(e).message}`);
    } finally {
      setBusy(false);
    }
  };
  const focusId = arg && !arg.startsWith("in:") ? arg : null;
  const sub = o
    ? `What uses what in ${name}: ${o.counts.units} classes, functions and files in ${o.groups.length} packages and folders, ${o.counts.uses} uses. Read from the code graph keel keeps for agents${o.indexed_at ? `, indexed ${agoText(o.indexed_at)}` : ""}.`
    : `What uses what in ${name}, from the code graph keel keeps for agents.`;
  return (
    <>
      <PageHead title="Graph" sub={sub}
        actions={<button className="btn" type="button" onClick={rebuild} disabled={busy || (r.data && !r.data.available && r.data.status === "indexing") || false}>{busy ? "Starting…" : "Rebuild index"}</button>} />
      {r.data && !r.data.available ? (
        <div className="panel">
          <EmptyState title={r.data.status === "indexing" ? "Indexing the code…" : "No code graph yet"}
            action={r.data.status === "indexing" ? <button className="btn" type="button" onClick={() => void r.reload()}>Check again</button>
              : <button className="btn primary" type="button" onClick={rebuild} disabled={busy}>{busy ? "Starting…" : "Build the index"}</button>}>
            {r.data.reason}
          </EmptyState>
        </div>
      ) : (
        <>
          <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true"><IconDefs /></svg>
          <div className="gtop"><SymbolSearch pid={pid} /></div>
          {focusId ? <FocusView pid={pid} id={focusId} o={o} depth={depth} name={name} />
            : r.error ? <ErrorBox error={r.error} onRetry={() => void r.reload()} />
              : <Async r={r} what="Reading the code graph">
                {(d) => (d.available ? <Levels pid={pid} o={d} arg={arg} depth={depth} setDepth={setDepth} tests={tests} setTests={setTests} name={name} /> : null)}
              </Async>}
        </>
      )}
    </>
  );
}
