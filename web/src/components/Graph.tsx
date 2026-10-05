// One graph style everywhere (flow, wiki, builder): the mockup's `autoGraph`, as a React SVG component.
// Steps are laid out in rows that snake left→right then right→left. Gates are diamonds, plain code is a
// square box, agents are round boxes. "for each AC" draws a loop above, send back / if-no draw below.

import { useId, type KeyboardEvent, type ReactNode } from "react";
import type { AcStatus, Step, ThreadStatus } from "../api";
import { acLabel, kfmt } from "../format";

export type GraphProps = {
  steps: Step[];
  /** Step id the thread is at. Steps before it are done. */
  current?: string | null;
  /** Thread status: "waiting" marks the current step as waiting for you, "done" marks every step done. */
  status?: ThreadStatus | null;
  edit?: boolean;
  per?: number;
  selected?: string | null;
  insertAt?: number | null;
  /** keel rules on: locked steps show a padlock instead of × (edit mode). */
  keel?: boolean;
  /** Estimated tokens per step id, shown under each step. */
  tokens?: Record<string, number>;
  /** Agent ids that are custom (★, dashed amber). */
  customAgents?: Set<string>;
  onSelect?: (id: string) => void;
  onInsert?: (afterIndex: number) => void;
  onRemove?: (id: string) => void;
  label?: string;
  /** The thread's acceptance criteria: one dot each under the "next AC" loop, coloured by status. */
  acs?: { id: string; status: AcStatus | string }[];
  /** The AC the thread works on now (its dot gets a ring). */
  currentAc?: string | null;
};

type NodeState = "todo" | "done" | "run" | "wait" | "fail";

/** "verify_green + commit" → ["verify_green", "+ commit"]: a long plain-code step name on two lines. */
export function splitName(name: string): [string, string] | null {
  const at = name.indexOf(" + ") >= 0 ? name.indexOf(" + ") : name.lastIndexOf(" ", 15);
  return at > 0 ? [name.slice(0, at), name.slice(at + 1)] : null;
}

const short = (t: string) => (t.length > 15 ? t.slice(0, 14) + "…" : t);

function keyActivate(fn?: () => void) {
  return (e: KeyboardEvent) => {
    if (fn && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      fn();
    }
  };
}

export function Graph(o: GraphProps) {
  const uid = useId().replace(/:/g, "");
  const arr = `arr-${uid}`;
  const steps = o.steps;
  const PER = o.per || 8;
  const W = 1000, NW = 96, NH = 36, X0 = 60, X1 = 940;
  const ROWH = o.edit ? 160 : o.tokens ? 150 : 140;
  const rowsN = Math.max(1, Math.ceil(steps.length / PER));
  const H = (o.edit ? 70 : 40) + rowsN * ROWH - (o.edit ? 20 : o.tokens ? 30 : 40);
  const stepX = (X1 - X0) / Math.max(1, PER - 1);
  const top = o.edit ? 80 : 60;
  const pos = steps.map((_, i) => {
    const r = Math.floor(i / PER), c = i % PER;
    return { x: r % 2 ? X1 - c * stepX : X0 + c * stepX, y: top + r * ROWH, r };
  });
  // A review step's fix node (`<id>__fix`, added by the engine) shows on its review step.
  const idx = (id?: string | null) => (id ? steps.findIndex((s) => s.id === id.replace(/__fix$/, "")) : -1);
  const cur = idx(o.current);
  const state = (i: number): NodeState => {
    if (o.status === "done") return "done";
    if (cur < 0) return "todo";
    if (i < cur) return "done";
    if (i > cur) return "todo";
    if (o.status === "failed") return "fail";
    if (o.status === "stopped") return "todo";
    return steps[i].kind === "gate" || o.status === "waiting" ? "wait" : "run";
  };
  const half = (s: Step) => (s.kind === "gate" || s.kind === "branch" ? 36 : NW / 2);
  const isCustom = (s: Step) => !!s.agent && (s.agent.startsWith("★") || !!o.customAgents?.has(s.agent));

  const edges: ReactNode[] = [];
  const plus: ReactNode[] = [];
  for (let i = 0; i < steps.length - 1; i++) {
    const a = pos[i], b = pos[i + 1];
    const done = state(i + 1) !== "todo" ? " done" : "";
    let mx: number, my: number;
    if (a.r === b.r) {
      const d = b.x > a.x ? 1 : -1;
      edges.push(<path key={`e${i}`} className={`edge${done}`} d={`M${a.x + d * half(steps[i])} ${a.y} L${b.x - d * half(steps[i + 1])} ${b.y}`} markerEnd={`url(#${arr})`} />);
      mx = (a.x + b.x) / 2;
      my = a.y;
    } else {
      const y1 = a.y + (steps[i].lanes?.length ? 44 : NH / 2 + (steps[i].kind === "gate" ? 4 : 0));
      const y2 = b.y - NH / 2 - (steps[i + 1].kind === "gate" ? 4 : 0);
      edges.push(<path key={`e${i}`} className={`edge${done}`} d={`M${a.x} ${y1} L${b.x} ${y2}`} markerEnd={`url(#${arr})`} />);
      mx = a.x;
      my = (a.y + b.y) / 2;
    }
    if (steps[i].kind === "branch") edges.push(<text key={`y${i}`} className="looplbl" x={mx} y={my - 8} textAnchor="middle">yes</text>);
    if (o.edit) {
      const at = i;
      plus.push(
        <g key={`p${i}`} className={`gplus ${o.insertAt === i ? "on" : ""}`} role="button" tabIndex={0} data-testid={`insert-${i}`}
          aria-label={`Insert a step after ${steps[i].name}`} onClick={() => o.onInsert?.(at)} onKeyDown={keyActivate(() => o.onInsert?.(at))}>
          <circle cx={mx} cy={my} r={9} />
          <text x={mx} y={my + 4} textAnchor="middle">+</text>
        </g>,
      );
    }
  }
  if (o.edit) {
    const last = steps.length - 1;
    const l = last >= 0 ? pos[last] : { x: X0 - 70, y: top, r: 0 };
    const cx = l.x + (l.r % 2 ? -70 : 70);
    plus.push(
      <g key="pend" className={`gplus ${o.insertAt === last ? "on" : ""}`} role="button" tabIndex={0} data-testid="insert-end"
        aria-label={last >= 0 ? "Add a step at the end" : "Add the first step"} onClick={() => o.onInsert?.(last)} onKeyDown={keyActivate(() => o.onInsert?.(last))}>
        <circle cx={cx} cy={l.y} r={9} />
        <text x={cx} y={l.y + 4} textAnchor="middle">+</text>
      </g>,
    );
  }

  // loop back for "each AC" (above the row)
  const fi = steps.findIndex((s) => s.per_ac);
  const li = steps.map((s) => !!s.per_ac).lastIndexOf(true);
  if (fi >= 0 && li > fi) {
    const a = pos[li], b = pos[fi];
    const ya = a.y - NH / 2 - 2, yb = b.y - NH / 2 - 2, cy = Math.min(ya, yb) - 46;
    edges.push(
      <g key="loop">
        <path className="edge loop" d={`M${a.x} ${ya} C${a.x} ${cy}, ${b.x} ${cy}, ${b.x} ${yb}`} markerEnd={`url(#${arr})`} />
        <text className="looplbl" x={(a.x + b.x) / 2} y={cy + 10} textAnchor="middle">next AC</text>
        {o.acs?.length ? (
          <g className="acdots" data-testid="graph-acs">
            {o.acs.map((ac, k) => {
              const x = (a.x + b.x) / 2 + (k - (o.acs!.length - 1) / 2) * 13;
              return (
                <g key={ac.id} className={`acdot s-${ac.status} ${ac.id === o.currentAc ? "cur" : ""}`} data-ac={ac.id} data-status={ac.status}>
                  <circle cx={x} cy={cy + 22} r={4.5} />
                  <title>{`${ac.id} · ${acLabel(ac.status)}`}</title>
                </g>
              );
            })}
          </g>
        ) : null}
      </g>,
    );
  }

  // custom connections: gate "send back", branch "no" (below the row)
  steps.forEach((s, i) => {
    ([[s.back, "send back"], [s.no, "no"]] as const).forEach(([t, lbl]) => {
      const j = idx(t);
      if (j < 0) return;
      const a = pos[i], b = pos[j];
      const ya = a.y + NH / 2 + 4, yb = b.y + NH / 2 + 2, cy = Math.max(ya, yb) + 44;
      edges.push(
        <g key={`c${i}${lbl}`}>
          <path className={`edge back ${lbl === "no" ? "no" : ""}`} d={`M${a.x} ${ya} C${a.x} ${cy}, ${b.x} ${cy}, ${b.x} ${yb}`} markerEnd={`url(#${arr})`} />
          <text className="looplbl backlbl" x={(a.x + b.x) / 2} y={cy - 4} textAnchor="middle">{lbl}</text>
        </g>,
      );
    });
  });

  const nodes: ReactNode[] = steps.map((s, i) => {
    const { x, y } = pos[i];
    const st = state(i);
    const sel = o.selected === s.id ? " gsel" : "";
    const interactive = o.edit
      ? { role: "button", tabIndex: 0, "aria-label": `Step ${s.name}`, onClick: () => o.onSelect?.(s.id), onKeyDown: keyActivate(() => o.onSelect?.(s.id)) }
      : o.onSelect
        // Read-only graphs (Flow, Wiki): a click or Enter opens "what this step does". The Zoom wrapper never pans from a .node.
        ? { role: "button", tabIndex: 0, "aria-label": `What ${s.name} does`, onClick: () => o.onSelect?.(s.id),
            onKeyDown: keyActivate(() => o.onSelect?.(s.id)), style: { cursor: "pointer" } }
        : {};
    const title = <title>{s.name + (s.agent ? " — " + s.agent : "")}</title>;
    const tokN = o.tokens?.[s.id];
    const tok = tokN ? <text className="lbl2 tok" x={x} y={y + (s.lanes?.length ? 62 : 34)} textAnchor="middle">≈ {kfmt(tokN)}</text> : null;
    const locked = !!s.lock && o.keel !== false;
    const lift = s.lanes?.length ? 22 : 0;
    const badge = !o.edit ? null : locked ? (
      <g className="glock" role="button" tabIndex={0} aria-label={`${s.name} is a keel rule`} data-testid={`lock-${s.id}`}
        onClick={() => o.onRemove?.(s.id)} onKeyDown={keyActivate(() => o.onRemove?.(s.id))}>
        <rect x={x + 36} y={y - 31 - lift} width={16} height={13} rx={3} />
        <path d={`M${x + 40} ${y - 31 - lift} v-3 a4 4 0 0 1 8 0 v3`} />
      </g>
    ) : (
      <g className="gdel" role="button" tabIndex={0} aria-label={`Remove ${s.name}`} data-testid={`remove-${s.id}`}
        onClick={() => o.onRemove?.(s.id)} onKeyDown={keyActivate(() => o.onRemove?.(s.id))}>
        <circle cx={x + 44} cy={y - 22 - lift} r={9} />
        <text x={x + 44} y={y - 18 - lift} textAnchor="middle">×</text>
      </g>
    );

    if (s.lanes?.length) {
      return (
        <g key={s.id} data-step={s.id} data-state={st}>
          {s.lanes.slice(0, 2).map((ln, j) => {
            const yy = y + (j ? 22 : -22);
            return (
              <g key={j} className={`node n-${st}${sel}`} {...interactive}>
                {title}
                <rect x={x - NW / 2} y={yy - 17} width={NW} height={34} rx={ln.kind === "code" ? 3 : 8} />
                <text x={x} y={yy - 1} textAnchor="middle">{short(ln.name)}</text>
                {ln.sub && <text className="lbl2" x={x} y={yy + 11} textAnchor="middle">{short(ln.sub)}</text>}
              </g>
            );
          })}
          <text className="looplbl" x={x} y={y - 46} textAnchor="middle">at the same time</text>
          {tok}
          {badge}
        </g>
      );
    }
    if (s.kind === "gate" || s.kind === "branch") {
      const r = 22;
      const gl = s.kind === "branch" ? "?" : "◆";
      const name = s.name.length <= 10 ? s.name : s.name.split(" ")[0];
      return (
        <g key={s.id} data-step={s.id} data-state={st}>
          <g className={`node n-${st}${s.kind === "branch" ? " n-branch" : ""}${sel}`} {...interactive}>
            {title}
            <path d={`M${x} ${y - r} L${x + r + 14} ${y} L${x} ${y + r} L${x - r - 14} ${y} Z`} />
            <text x={x} y={y + 4} textAnchor="middle">{gl} {name}</text>
          </g>
          {badge}
        </g>
      );
    }
    const count = s.kind === "parallel" ? s.parallel || 2 : 0;
    const ghosts = count ? Math.min(count, 3) - 1 : 0;
    const custom = isCustom(s);
    const cls = custom && st === "todo" ? "custom" : st;
    const agentLabel = s.agent ? (custom && !s.agent.startsWith("★") ? "★ " + s.agent : s.agent) : "";
    return (
      <g key={s.id} data-step={s.id} data-state={st}>
        {Array.from({ length: ghosts }, (_, k) => ghosts - k).map((k) => (
          <rect key={k} className="ghost" x={x - NW / 2 + k * 4} y={y - NH / 2 - k * 4} width={NW} height={NH} rx={8} />
        ))}
        <g className={`node n-${cls}${sel}`} {...interactive}>
          {title}
          <rect x={x - NW / 2} y={y - NH / 2} width={NW} height={NH} rx={s.kind === "code" ? 3 : 8} />
          {(() => {
            const name = s.name.replace(/ × \d+.*$/, "");
            const two = !s.agent && name.length > 15 ? splitName(name) : null;
            return two ? (
              <text x={x} y={y - 3} textAnchor="middle">
                <tspan x={x}>{short(two[0])}</tspan>
                <tspan x={x} dy={13}>{short(two[1])}</tspan>
              </text>
            ) : (
              <text x={x} y={y + (s.agent ? -1 : 4)} textAnchor="middle">
                {short(name)}{count ? ` ×${count}` : ""}
              </text>
            );
          })()}
          {s.agent && <text className="lbl2" x={x} y={y + 11} textAnchor="middle">{short(agentLabel)}</text>}
        </g>
        {tok}
        {badge}
      </g>
    );
  });

  const label = o.label ?? `Graph of ${steps.length} steps${cur >= 0 ? ", now at " + steps[cur].name : ""}`;
  return (
    <svg className={`graph ${o.edit ? "gedit" : ""}`} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} data-testid="graph">
      <defs>
        <marker id={arr} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0 0 L10 5 L0 10 z" fill="var(--faint)" />
        </marker>
      </defs>
      {edges}
      {nodes}
      {plus}
    </svg>
  );
}

export function GraphLegend() {
  return (
    <div className="legend">
      <span><i style={{ background: "var(--ok-soft)", borderColor: "var(--ok)" }} />done</span>
      <span><i style={{ background: "var(--met-soft)", borderColor: "var(--met)", borderRadius: "50%" }} />AC already met</span>
      <span><i style={{ background: "var(--run-soft)", borderColor: "var(--run)" }} />running</span>
      <span><i style={{ background: "var(--warn-soft)", borderColor: "var(--warn)" }} />◆ waits for you</span>
      <span><i style={{ borderColor: "var(--warn)", borderStyle: "dashed" }} />★ custom agent</span>
      <span><i style={{ borderRadius: 0, borderColor: "var(--rail)" }} />square = plain code, no LLM</span>
    </div>
  );
}
