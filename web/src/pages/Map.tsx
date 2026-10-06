// Map (Project): how the project is built, level by level, on one IDE-like canvas: the system, its modules and API
// endpoints, and the database diagram (tables, keys and foreign keys from the SQL migrations). Journeys and classes
// draw the boxes an older keel v1 map.json carried; the engine does not build them yet.

import { useEffect, useMemo, useState } from "react";
import { api, errorParts, type KeelMap, type MapLevel, type MapNode, type MapResponse } from "../api";
import { BoxDiagram, moduleBoxes, systemBoxes, type GBox } from "../components/er/BoxDiagram";
import { ErDiagram } from "../components/er/ErDiagram";
import { schemaOf } from "../components/er/model";
import { EmptyState } from "../components/page";
import { Async, PageHead, Panel, Tabs } from "../components/ui";
import { clock } from "../format";
import { useApp, useLoad } from "../state";

type LevelId = "system" | "flow" | "modules" | "classes" | "er";
const LEVELS: [LevelId, string][] = [["system", "System"], ["flow", "Journeys"], ["modules", "Modules"], ["classes", "Classes"], ["er", "Database (ER)"]];
const HEAD = 30, SUB = 16, ROW = 18;

const isMissing = (m: MapResponse): m is { missing: string } => typeof (m as { missing?: unknown }).missing === "string";

/** The api's reason without the "No map yet." the title already says; the usual one becomes what building does. */
export function missingWhy(missing: string): string {
  const rest = missing.replace(/^\s*no map yet[.!:]?\s*/i, "").trim();
  return !rest || /^build it/i.test(rest)
    ? "Build it to see the system, its modules, the user journeys and the database tables, drawn from this commit."
    : rest;
}

// ------------------------------------------------------------------ journeys and classes (keel v1 boxes, drawn as they come)

function NodeBox({ n }: { n: MapNode }) {
  const cls = n.kind === "data" ? "m-data" : n.kind === "ext" ? "m-ext" : n.kind === "actor" ? "m-actor" : "";
  const rows = n.rows ?? [];
  let y = n.y + HEAD - 10 + (n.sub ? SUB : 0);
  const h = n.h || HEAD + (n.sub ? SUB : 0) + rows.length * ROW + 8;
  return (
    <g className={`mbox ${cls}`}>
      <title>{n.title}{n.cite ? ` — ${n.cite.rel}:${n.cite.line}` : ""}</title>
      <rect x={n.x} y={n.y} width={n.w} height={h} rx={8} />
      <rect className="mhead" x={n.x} y={n.y} width={n.w} height={HEAD} rx={8} />
      <rect className="mhead" x={n.x} y={n.y + HEAD - 8} width={n.w} height={8} />
      <text className="mt" x={n.x + 10} y={n.y + 19}>{n.title}</text>
      {n.sub && <text className="ms" x={n.x + 10} y={n.y + HEAD + 8}>{n.sub}</text>}
      {rows.map((r, i) => {
        y += ROW;
        return <text key={i} className="mr" x={n.x + 10} y={y}>{r.flag && <tspan className={`mk k-${r.flag.toLowerCase()}`}>{r.flag.toUpperCase()} </tspan>}{r.t}</text>;
      })}
    </g>
  );
}

function LevelSvg({ level, label }: { level: MapLevel; label: string }) {
  if (!level.nodes?.length) return <EmptyState title="Nothing to draw here">This level has no boxes yet. Rebuild the map after the code changes.</EmptyState>;
  return (
    <div className="graph-wrap">
      <svg className="mapsvg real" viewBox={`0 0 ${level.width} ${level.height}`} role="img" aria-label={label}
        style={{ minWidth: Math.min(level.width, 1000), maxWidth: Math.max(level.width, 600) }}>
        <defs><marker id="mArr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" className="mah" /></marker></defs>
        {level.edges?.map((e, i) => <path key={i} className={`medge ${e.kind === "call" ? "dash" : ""}`} d={e.d} markerEnd="url(#mArr)" />)}
        {level.nodes.map((n) => <NodeBox key={n.id} n={n} />)}
      </svg>
    </div>
  );
}

// ------------------------------------------------------------------ the database level's empty state and legend

function ErEmpty({ m, name, onRebuild, busy }: { m: KeelMap; name: string; onRebuild: () => void; busy: boolean }) {
  const s = m.sources ?? {};
  const found = s.migrations?.length ?? 0;
  const looked = s.looked_in?.length ? s.looked_in : ["db/migration, migrations, prisma/migrations and the other usual folders"];
  return (
    <div className="panel dg-empty" role="region" aria-label="No database diagram">
      <h3>{found ? "The migrations create no tables" : "No SQL migrations found"}</h3>
      <p>
        {found
          ? `keel read ${found} SQL file${found === 1 ? "" : "s"} in ${name} but found no CREATE TABLE in them.`
          : `keel draws the database from the SQL migrations in ${name}, and found none.`}
        {s.configured ? " It looked only where the map config points:" : " It looked in:"}
      </p>
      <ul>{looked.map((l) => <li key={l} className="mono">{l}</li>)}</ul>
      <p>If yours live somewhere else, point keel at them in <span className="mono">.keel/config.yml</span>, then build the map again:</p>
      <pre>{"map:\n  migrations:\n    - path/to/your/migrations      # a folder or a glob, one or more"}</pre>
      <button className="btn primary" type="button" onClick={onRebuild} disabled={busy}>{busy ? "Building…" : "Build the map"}</button>
    </div>
  );
}

function ErLegend() {
  const icon = (id: string) => <svg width="13" height="13" aria-hidden="true" className={`erd-ic ${id}`}><use href={`#erd-i-${id}`} /></svg>;
  const line = (d: string, circle?: boolean) => (
    <svg width="28" height="12" aria-hidden="true">
      <path d={d} stroke="var(--dg-line)" fill="none" strokeWidth="1.3" />
      {circle && <circle cx="13" cy="6" r="3.3" fill="var(--panel)" stroke="var(--dg-line)" strokeWidth="1.3" />}
    </svg>
  );
  return (
    <div className="legend er-legend" aria-label="Legend">
      <span>{icon("key")} primary key</span>
      <span>{icon("fkey")} foreign key</span>
      <span>{icon("unique")} unique</span>
      <span>{icon("index")} indexed</span>
      <span><b className="mono" style={{ color: "var(--warn)" }}>?</b> nullable</span>
      <span>{line("M0 6H28M2 1 12 6 2 11")} many</span>
      <span>{line("M0 6H28M20 1v10M24 1v10")} exactly one</span>
      <span>{line("M0 6H28M24 1v10", true)} zero or one</span>
    </div>
  );
}

// ------------------------------------------------------------------ the page

const tabKey = (pid: string) => `keel2.map.${pid}.tab`;

function MapView({ m, onRebuild, busy, pid }: { m: KeelMap; onRebuild: () => void; busy: boolean; pid: string }) {
  const { project } = useApp();
  const schema = useMemo(() => schemaOf(m), [m]);
  const [tab, setTabState] = useState<LevelId>(() => {
    try {
      const v = localStorage.getItem(tabKey(pid)) as LevelId | null;
      if (v && LEVELS.some(([k]) => k === v)) return v;
    } catch { /* private window */ }
    return "system";
  });
  const [focus, setFocus] = useState<{ table: string; n: number } | null>(null);
  const setTab = (t: LevelId) => {
    setTabState(t);
    try { localStorage.setItem(tabKey(pid), t); } catch { /* private window */ }
  };
  const journeys = Object.keys(m.levels.flow?.byJourney ?? {});
  const modules = Object.keys(m.levels.classes?.byModule ?? {});
  const [journey, setJourney] = useState(journeys[0] ?? "");
  const [mod, setMod] = useState(modules[0] ?? "");
  useEffect(() => setFocus(null), [m]);

  const name = project?.name ?? pid;
  const drill = (b: GBox) => {
    if (b.drill === "modules") setTab("modules");
    if (b.drill === "er") {
      if (b.table) setFocus((f) => ({ table: b.table!, n: (f?.n ?? 0) + 1 }));
      setTab("er");
    }
  };

  const system = useMemo(() => (m.levels.system ? systemBoxes(m.levels.system) : null), [m]);
  const mods = useMemo(() => (m.levels.modules ? moduleBoxes(m, m.levels.modules) : null), [m]);
  const nothing = <Panel><EmptyState title="Nothing to draw here">{`keel found no source for this level in ${name}.`}</EmptyState></Panel>;

  let body: React.ReactNode;
  if (tab === "er") {
    body = schema ? (
      <>
        <ErDiagram key={`${pid}:${m.at}:${focus?.n ?? 0}`} schema={schema} pid={pid} name={name} initial={focus?.table ?? null} />
        <ErLegend />
        {!m.schema && <p className="hint">This map was built by an older keel: rebuild it for column types, nullability, indexes, views and the migration line of every column.</p>}
      </>
    ) : <ErEmpty m={m} name={name} onRebuild={onRebuild} busy={busy} />;
  } else if (tab === "system") {
    body = system?.boxes.length
      ? <BoxDiagram key={`sys:${m.at}`} pid={pid} level="system" name={name} boxes={system.boxes} edges={system.edges} onDrill={drill} />
      : nothing;
  } else if (tab === "modules") {
    body = mods?.boxes.length
      ? <BoxDiagram key={`mod:${m.at}`} pid={pid} level="modules" name={name} boxes={mods.boxes} bands={mods.bands} onDrill={drill} />
      : nothing;
  } else {
    const level = tab === "flow"
      ? (journey && m.levels.flow?.byJourney?.[journey]) || m.levels.flow
      : m.levels.classes?.byModule?.[mod];
    body = (
      <Panel>
        {level ? <LevelSvg level={level} label={`${LEVELS.find(([k]) => k === tab)?.[1]} map of ${name}`} /> : (
          <EmptyState title="Nothing to draw here" action={tab === "flow" ? <a className="btn" href="#/wiki">Open the wiki</a> : undefined}>
            {tab === "flow" ? `The knowledge base of ${name} has no journeys section yet. The init flow writes it.` : `keel found no source for this level in ${name}.`}
          </EmptyState>
        )}
      </Panel>
    );
  }

  const picker = tab === "flow" && journeys.length > 1 ? (
    <select aria-label="Journey" value={journey} onChange={(e) => setJourney(e.target.value)}>{journeys.map((j) => <option key={j} value={j}>{j}</option>)}</select>
  ) : tab === "classes" && modules.length ? (
    <select aria-label="Module" value={mod} onChange={(e) => setMod(e.target.value)}>{modules.map((x) => <option key={x} value={x}>{x}</option>)}</select>
  ) : null;

  return (
    <>
      <div className="mapbar">
        <span className="sub">built from <span className="mono">{m.sha?.slice(0, 7)}</span>{m.at ? ` · ${clock(m.at, false)}` : ""}</span>
        {Object.entries(m.counts ?? {}).filter(([, v]) => v).map(([k, v]) => <span key={k} className="tag">{v} {v === 1 ? k.replace(/s$/, "") : k}</span>)}
        {m.demo && <span className="mapwarn">⚠ demo map</span>}
        <span style={{ marginLeft: "auto" }}><button className="btn sm" type="button" onClick={onRebuild} disabled={busy}>{busy ? "Rebuilding…" : "Rebuild"}</button></span>
      </div>
      <div className="row" style={{ margin: "12px 0", justifyContent: "space-between" }}>
        <Tabs value={tab} onChange={setTab} label="Map level" options={LEVELS.map(([k, l]) => [k, l] as [LevelId, string])} />
        {picker}
      </div>
      {body}
      {!!m.limits?.length && <p className="hint">What the map cannot see: {m.limits.join(" · ")}.</p>}
    </>
  );
}

export function MapPage({ pid }: { pid: string }) {
  const { project, toast } = useApp();
  const map = useLoad(`map:${pid}`, () => api.map(pid), { live: false });
  const [busy, setBusy] = useState(false);
  const rebuild = async () => {
    setBusy(true);
    try {
      map.setData(await api.rebuildMap(pid));
      toast("keel map rebuilt for HEAD.");
    } catch (e) {
      const p = errorParts(e);
      toast(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHead title="Map" sub={`How ${project?.name ?? pid} is built — read from the code, migrations and API contract. Nothing connects to a running system.`} />
      <Async r={map} what="Reading the map">
        {(m) => isMissing(m) ? (
          <div className="panel">
            <EmptyState title="No map yet" action={<button className="btn primary" type="button" onClick={rebuild} disabled={busy}>{busy ? "Building…" : "Build the map"}</button>}>
              {missingWhy(m.missing)}
            </EmptyState>
          </div>
        ) : <MapView m={m} onRebuild={rebuild} busy={busy} pid={pid} />}
      </Async>
      {map.data && !isMissing(map.data) && <p className="hint">Tables come from the migration files, endpoints from the API contract, modules from the folders. The map is pinned to a commit, so agents and you look at the same picture.</p>}
    </>
  );
}
