// Map (Project): keel v1's .keel/map.json drawn level by level — system, journeys, modules, classes, database.
// Every node already has x / y / w / h and every edge its SVG path, so this page only draws.

import { useEffect, useState } from "react";
import { api, errorParts, type KeelMap, type MapLevel, type MapNode, type MapResponse } from "../api";
import { OpenKeelV1Button } from "../components/OpenKeelV1";
import { Async, PageHead, Panel, Tabs } from "../components/ui";
import { clock } from "../format";
import { useApp, useLoad } from "../state";

type LevelId = "system" | "flow" | "modules" | "classes" | "er";
const LEVELS: [LevelId, string][] = [["system", "System"], ["flow", "Journeys"], ["modules", "Modules"], ["classes", "Classes"], ["er", "Database (ER)"]];
const HEAD = 30, SUB = 16, ROW = 18;

const isMissing = (m: MapResponse): m is { missing: string } => typeof (m as { missing?: unknown }).missing === "string";

function NodeBox({ n, onDrill }: { n: MapNode; onDrill?: (n: MapNode) => void }) {
  const cls = n.kind === "data" ? "m-data" : n.kind === "ext" ? "m-ext" : n.kind === "actor" ? "m-actor" : "";
  const rows = n.rows ?? [];
  let y = n.y + HEAD - 10 + (n.sub ? SUB : 0);
  const drill = n.drill && onDrill;
  const h = n.h || HEAD + (n.sub ? SUB : 0) + rows.length * ROW + 8;
  return (
    <g className={`mbox ${cls}`} style={drill ? { cursor: "pointer" } : undefined}
      onClick={drill ? () => onDrill!(n) : undefined} role={drill ? "button" : undefined} tabIndex={drill ? 0 : undefined}
      onKeyDown={drill ? (e) => e.key === "Enter" && onDrill!(n) : undefined} aria-label={drill ? `Open ${n.title} (${n.drill})` : undefined}>
      <title>{n.title}{n.cite ? ` — ${n.cite.rel}:${n.cite.line}` : ""}</title>
      <rect x={n.x} y={n.y} width={n.w} height={h} rx={8} />
      <rect className="mhead" x={n.x} y={n.y} width={n.w} height={HEAD} rx={8} />
      <rect className="mhead" x={n.x} y={n.y + HEAD - 8} width={n.w} height={8} />
      <text className="mt" x={n.x + 10} y={n.y + 19}>{n.title}</text>
      {n.drill && <text className="mtag" x={n.x + n.w - 10} y={n.y + 19} textAnchor="end">{n.drill} ›</text>}
      {n.sub && <text className="ms" x={n.x + 10} y={n.y + HEAD + 8}>{n.sub}</text>}
      {rows.map((r, i) => {
        y += ROW;
        return (
          <text key={i} className="mr" x={n.x + 10} y={y}>
            {r.flag && <tspan className={`mk k-${r.flag.toLowerCase()}`}>{r.flag.toUpperCase()} </tspan>}
            {r.t}
          </text>
        );
      })}
    </g>
  );
}

function LevelSvg({ level, label, onDrill }: { level: MapLevel; label: string; onDrill?: (n: MapNode) => void }) {
  if (!level.nodes?.length) return <div className="empty">Nothing to draw at this level.</div>;
  return (
    <div className="graph-wrap">
      <svg className="mapsvg real" viewBox={`0 0 ${level.width} ${level.height}`} role="img" aria-label={label}
        style={{ minWidth: Math.min(level.width, 1000), maxWidth: Math.max(level.width, 600) }}>
        <defs>
          <marker id="mArr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" className="mah" /></marker>
          <marker id="mMany" viewBox="0 0 14 14" refX="13" refY="7" markerWidth="12" markerHeight="12" orient="auto-start-reverse"><path d="M13 7 L1 1 M13 7 L1 13 M13 7 L1 7 M1 2 L1 12" className="mcrow" /></marker>
          <marker id="mOne" viewBox="0 0 14 14" refX="13" refY="7" markerWidth="12" markerHeight="12" orient="auto-start-reverse"><path d="M9 1 L9 13 M5 1 L5 13" className="mcrow" /></marker>
        </defs>
        {level.bands?.map((b) => (
          <g key={b.id}>
            <rect className="mband" x={b.x} y={b.y} width={b.w} height={b.h} rx={10} />
            <text className="mbandt" x={b.x + b.w - 10} y={b.y + 18} textAnchor="end">{b.label}</text>
          </g>
        ))}
        {level.edges?.map((e, i) => (
          <g key={i}>
            <path className={`medge ${e.kind === "call" ? "dash" : ""}`} d={e.d}
              markerStart={e.kind === "fk" ? "url(#mOne)" : undefined} markerEnd={e.kind === "fk" ? "url(#mMany)" : "url(#mArr)"} />
            {e.label && e.lx !== undefined && <text className="mlbl" x={e.lx} y={(e.ly ?? 0) - 6} textAnchor="middle">{e.label}</text>}
          </g>
        ))}
        {level.nodes.map((n) => <NodeBox key={n.id} n={n} onDrill={onDrill} />)}
      </svg>
    </div>
  );
}

function MapView({ m, onRebuild, busy }: { m: KeelMap; onRebuild: () => void; busy: boolean }) {
  const { project } = useApp();
  const present = LEVELS.filter(([k]) => m.levels[k] !== undefined);
  const [tab, setTab] = useState<LevelId>(present[0]?.[0] ?? "system");
  const journeys = Object.keys(m.levels.flow?.byJourney ?? {});
  const modules = Object.keys(m.levels.classes?.byModule ?? {});
  const [journey, setJourney] = useState(journeys[0] ?? "");
  const [mod, setMod] = useState(modules[0] ?? "");
  useEffect(() => {
    if (!present.some(([k]) => k === tab) && present[0]) setTab(present[0][0]);
  }, [m]); // only when a new map arrives

  const drill = (n: MapNode) => {
    if (n.drill === "modules") setTab("modules");
    if (n.drill === "classes") {
      const key = (n as MapNode & { module?: string }).module ?? n.title;
      if (modules.includes(key)) setMod(key);
      setTab("classes");
    }
  };

  let level: MapLevel | undefined;
  let picker = null;
  if (tab === "flow") {
    const f = m.levels.flow;
    level = journey && f?.byJourney?.[journey] ? f.byJourney[journey] : f;
    if (journeys.length > 1) {
      picker = (
        <select aria-label="Journey" value={journey} onChange={(e) => setJourney(e.target.value)}>
          {journeys.map((j) => <option key={j} value={j}>{j}</option>)}
        </select>
      );
    }
  } else if (tab === "classes") {
    level = m.levels.classes?.byModule?.[mod];
    picker = modules.length ? (
      <select aria-label="Module" value={mod} onChange={(e) => setMod(e.target.value)}>
        {modules.map((x) => <option key={x} value={x}>{x}</option>)}
      </select>
    ) : null;
  } else {
    level = m.levels[tab] as MapLevel | undefined;
  }

  const name = project?.name ?? "";
  return (
    <>
      <div className="mapbar">
        <span className="sub">built from <span className="mono">{m.sha?.slice(0, 7)}</span>{m.at ? ` · ${clock(m.at, false)}` : ""}</span>
        {Object.entries(m.counts ?? {}).filter(([, v]) => v).map(([k, v]) => <span key={k} className="tag">{v} {k}</span>)}
        {m.demo && <span className="mapwarn">⚠ demo map</span>}
        <span style={{ marginLeft: "auto" }}><button className="btn sm" type="button" onClick={onRebuild} disabled={busy}>{busy ? "Rebuilding…" : "Rebuild"}</button></span>
      </div>
      <div className="row" style={{ margin: "12px 0", justifyContent: "space-between" }}>
        <Tabs value={tab} onChange={setTab} label="Map level" options={LEVELS.map(([k, l]) => [k, l] as [LevelId, string])} />
        <div className="row">
          {picker}
          <div className="legend">
            <span><i style={{ borderColor: "var(--ok)", background: "var(--ok-soft)" }} />data</span>
            <span><i style={{ borderColor: "var(--faint)", borderStyle: "dotted" }} />external</span>
            <span><i style={{ borderColor: "var(--rail)" }} />code</span>
          </div>
        </div>
      </div>
      <Panel>
        {level ? <LevelSvg level={level} label={`${LEVELS.find(([k]) => k === tab)?.[1]} map of ${name}`} onDrill={drill} /> : (
          <div className="empty">keel could not build this level for {name}.<br /><span className="sub">{tab === "flow" ? "No journeys section in the knowledge base yet." : tab === "er" ? "No SQL migrations found." : "No source found."}</span></div>
        )}
        {level?.overflow ? <p className="hint">{level.overflow} more not drawn.</p> : null}
      </Panel>
      {!!m.limits?.length && (
        <p className="hint">What the map cannot see: {m.limits.join(" · ")}.</p>
      )}
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
      <PageHead title="Map" sub={`How ${project?.name ?? pid} is built — read from the code, migrations and API contract. Nothing connects to a running system.`} actions={<OpenKeelV1Button />} />
      <Async r={map} what="Reading the map">
        {(m) => isMissing(m) ? (
          <div className="panel"><div className="panel-body empty grid" style={{ gap: 10, justifyItems: "center" }}>
            <b>No map yet</b>
            <span className="sub">{m.missing}</span>
            <button className="btn primary" type="button" onClick={rebuild} disabled={busy}>{busy ? "Building…" : "Build the map"}</button>
          </div></div>
        ) : <MapView m={m} onRebuild={rebuild} busy={busy} />}
      </Async>
      <p className="hint">Tables come from the migration files, endpoints from the API contract, modules and classes from the source. The map is pinned to a commit, so agents and you look at the same picture.</p>
    </>
  );
}
