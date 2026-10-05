// A workflow as Scratch-style blocks: a stack read top to bottom. Each kind has its colour and shape (agent, plain
// code, "wait for you" gate, if / else, loop, an included flow as a custom block). Loops are C-blocks that wrap their
// steps, a branch is an if / else with its arms inside, an included flow folds into one block, and every exit (a
// gate's choices, send back, then, after the rounds…) is a small chip inside the block that names its target —
// never a line across the page. Every block, loop and section shows its tokens. On the Flow page each block shows
// its state and its real tokens; in the builder blocks are added (palette, drag or +), moved and removed.

import {
  forwardRef, memo, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState,
  type DragEvent, type KeyboardEvent, type ReactNode,
} from "react";
import type { AcStatus, Step, StepKind, ThreadStatus } from "../api";
import { acLabel, kfmt } from "../format";
import {
  armsTaken, buildFlowMap, includeMeaning, loopTimes, phaseMeaning, phaseTitle, sideKey, STATE_LABEL, stepStates, sumOf, whenText,
  type Arm, type Exit, type FlowMap, type Node, type Row, type Section, type StepState,
} from "./flowmap";
import type { LoopCtx } from "./builderOps";

export type BlocksHandle = {
  /** Open the folds that hold a step, scroll it into view and (optionally) focus it. */
  reveal: (id: string, focus?: boolean) => void;
};

export type BlocksEdit = {
  /** File index after which the "add a block" picker is open (-1 = at the start), or null. */
  insertAt: number | null;
  insertLoop?: LoopCtx;
  onInsert: (afterIndex: number, loop?: LoopCtx) => void;
  onAdd: (kind: StepKind) => void;
  onCancelInsert: () => void;
  /** A block dropped from the palette. */
  onAddAt: (afterIndex: number, kind: StepKind, loop?: LoopCtx) => void;
  onRemove: (id: string) => void;
  onMove: (id: string, d: -1 | 1) => void;
  /** A block dragged to another place. */
  onMoveTo: (id: string, afterIndex: number, loop?: LoopCtx) => void;
  /** A question about one block (removing a keel rule), shown under it. */
  notice?: { id: string; node: ReactNode } | null;
};

export type BlocksProps = {
  steps: Step[];
  /** Estimated tokens per step id (a per-AC step: for all criteria, as the estimate gives it). */
  tokens?: Record<string, number>;
  /** Tokens a step really used in this thread (the Flow page). */
  actual?: Record<string, number>;
  /** How many criteria the per-AC loop runs for (the estimate's input, or the thread's criteria). */
  acCount?: number;
  customAgents?: Set<string>;
  /** keel rules on: locked steps show a padlock. */
  keel?: boolean;
  current?: string | null;
  status?: ThreadStatus | null;
  acs?: { id: string; status: AcStatus | string; title?: string; layer?: string }[];
  currentAc?: string | null;
  /** Step ids that ran in this thread (from its checkpoints): which arm an if / else took. */
  visited?: Set<string>;
  selected?: string | null;
  /** A click (or Enter) on a block: "what this step does" (Flow, Wiki) or its editor (builder). */
  onOpenStep?: (id: string) => void;
  edit?: BlocksEdit | null;
  /** Scroll the current block into view once, when the canvas opens. */
  autoScroll?: boolean;
  /** Something to show right under a block (the Flow page: the gate card under the block that waits). */
  inline?: (id: string) => ReactNode;
  label?: string;
};

export const ADD_KINDS: { kind: StepKind; label: string; hint: string }[] = [
  { kind: "agent", label: "Agent", hint: "an agent does a task" },
  { kind: "code", label: "Code", hint: "plain code, no tokens" },
  { kind: "gate", label: "Wait for you", hint: "you approve or send back" },
  { kind: "branch", label: "If / else", hint: "a yes or no question" },
  { kind: "parallel", label: "Parallel", hint: "agents at the same time" },
];

/** The kind of a step as a small shape (colour is never the only signal). */
export function KindIcon({ kind }: { kind: StepKind | "loop" }) {
  const c = { width: 14, height: 14, viewBox: "0 0 14 14", "aria-hidden": true, focusable: false } as const;
  switch (kind) {
    case "agent": return <svg {...c}><circle cx="7" cy="5" r="2.6" /><path d="M2.2 12.4c.7-2.6 2.6-3.9 4.8-3.9s4.1 1.3 4.8 3.9" /></svg>;
    case "code": return <svg {...c}><path d="M4.6 3.6 1.8 7l2.8 3.4M9.4 3.6 12.2 7l-2.8 3.4" /></svg>;
    case "gate": return <svg {...c}><path d="M5 2v10M5 2.4h6.2l-1.4 2.3 1.4 2.3H5" /></svg>;
    case "branch": return <svg {...c}><path d="M3 2v4.2c0 1.6 1.2 2.4 2.6 2.4H11M8.6 6.2 11 8.6 8.6 11" /></svg>;
    case "parallel": return <svg {...c}><path d="M3 3h8M3 7h8M3 11h8" /></svg>;
    case "include": return <svg {...c}><rect x="2" y="2.5" width="10" height="9" rx="2" /><path d="M5 7h4M7 5v4" /></svg>;
    case "loop": return <svg {...c}><path d="M11.2 5.4A4.4 4.4 0 0 0 3.1 5M2.8 8.6A4.4 4.4 0 0 0 10.9 9M3 2.4V5h2.6M11 11.6V9H8.4" /></svg>;
  }
}

const KIND_WORD: Record<StepKind, string> = {
  agent: "agent", code: "plain code", gate: "wait for you", branch: "if / else", parallel: "agents at the same time", include: "included flow",
};

/** The ACs as chips: id + status, the current one marked (Flow's AC strip and the loop's header). */
export function AcChips({ acs, current, label = "Acceptance criteria status" }: {
  acs: { id: string; status: AcStatus | string; title?: string; layer?: string }[]; current?: string | null; label?: string;
}) {
  if (!acs.length) return null;
  return (
    <div className="acstrip" aria-label={label}>
      {acs.map((a) => (
        <span key={a.id} className={`acchip s-${a.status} ${a.id === current ? "cur" : ""}`} data-ac={a.id} data-status={a.status}
          title={a.title ? `${a.id}${a.layer ? ` [${a.layer}]` : ""} ${a.title}` : a.id} aria-current={a.id === current ? "step" : undefined}>
          <b>{a.id}</b> {acLabel(a.status)}{a.id === current ? <span className="sr-only"> (working on it now)</span> : null}
        </span>
      ))}
    </div>
  );
}

const reducedMotion = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** A step whose next step is never the block below it: a new block right after it would never run. */
const endsHere = (s: Step) => !!s.then && s.then !== "continue";

/** What is being dragged (a palette kind or a block), for the drop targets. */
let dragging: { kind?: StepKind; id?: string } | null = null;
export function startPaletteDrag(e: DragEvent, kind: StepKind) {
  dragging = { kind };
  e.dataTransfer?.setData("text/plain", `kind:${kind}`);
  if (e.dataTransfer) e.dataTransfer.effectAllowed = "copyMove";
}
const readDrag = (e: DragEvent): { kind?: StepKind; id?: string } | null => {
  const t = e.dataTransfer?.getData?.("text/plain") ?? "";
  if (t.startsWith("kind:")) return { kind: t.slice(5) as StepKind };
  if (t.startsWith("step:")) return { id: t.slice(5) };
  return dragging;
};

type Ctx = {
  p: BlocksProps;
  map: FlowMap;
  states: Record<string, StepState> | null;
  taken: Record<string, "yes" | "no">;
  open: Set<string>;
  toggle: (key: string) => void;
  flash: string | null;
  reveal: (id: string, focus?: boolean) => void;
  nameOf: (id: string) => string;
  onKey: (e: KeyboardEvent<HTMLElement>, s: Step) => void;
  /** Per-AC tokens of one criterion (a block inside the loop shows one round). */
  per: (s: Step, m?: Record<string, number>) => number;
  total: number;
  dragOver: string | null;
  setDragOver: (k: string | null) => void;
  isDragging: boolean;
  setDragging: (b: boolean) => void;
};

export const Blocks = memo(forwardRef<BlocksHandle, BlocksProps>(function Blocks(p, ref) {
  const { steps, edit } = p;
  const map = useMemo(() => buildFlowMap(steps), [steps]);
  const states = useMemo(() => {
    if (!p.current && !p.status) return null;
    const st = stepStates(steps, map, p.current, p.status);
    const { skipped } = armsTaken(map, st, p.visited);
    skipped.forEach((id) => { st[id] = "skip"; });
    return st;
  }, [steps, map, p.current, p.status, p.visited]);
  const taken = useMemo(() => (states ? armsTaken(map, states, p.visited).taken : {}), [map, states, p.visited]);
  const root = useRef<HTMLDivElement>(null);
  const pending = useRef<{ id: string; focus: boolean; block: ScrollLogicalPosition } | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<string | null>(null);
  const [isDragging, setDragging] = useState(false);
  const [open, setOpen] = useState<Set<string>>(() => {
    const at = p.current?.replace(/__fix$/, "") ?? p.selected ?? null;
    return new Set(at ? map.containers(at) : []);
  });
  const [, bump] = useState(0);

  const reveal = (id: string, focus = false, block: ScrollLogicalPosition = "center") => {
    const keys = map.containers(id);
    pending.current = { id, focus, block };
    if (keys.some((k) => !open.has(k))) setOpen((o) => new Set([...o, ...keys]));
    else bump((n) => n + 1);   // render anyway, so the layout effect below scrolls
  };
  useImperativeHandle(ref, () => ({ reveal: (id, focus) => reveal(id, focus) }));

  useLayoutEffect(() => {
    const want = pending.current;
    if (!want || !root.current) return;
    pending.current = null;
    const el = root.current.querySelector<HTMLElement>(`[data-block="${want.id.replace(/["\\]/g, "\\$&")}"]`);
    if (!el) return;
    const main = el.querySelector<HTMLElement>(".sb-main") ?? el;
    const box = el.closest<HTMLElement>(".sx-scroll");
    if (box) {
      const r = main.getBoundingClientRect(), b = box.getBoundingClientRect();
      const top = box.scrollTop + r.top - b.top - (want.block === "center" ? (box.clientHeight - r.height) / 2 : 12);
      box.scrollTo?.({ top: Math.max(0, top), behavior: reducedMotion() ? "auto" : "smooth" });
    } else {
      main.scrollIntoView?.({ block: want.block, behavior: reducedMotion() ? "auto" : "smooth" });
    }
    if (want.focus) main.focus({ preventScroll: true });
    if (want.focus || want.block === "center") {
      setFlash(want.id);
      window.setTimeout(() => setFlash((f) => (f === want.id ? null : f)), 1600);
    }
  });

  // The current block, once, when the canvas opens; a block selected from outside (an error link), when it changes.
  useEffect(() => {
    const at = p.current?.replace(/__fix$/, "");
    if (p.autoScroll && at && map.byId.has(at)) reveal(at, false);
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const lastSel = useRef(p.selected);
  useEffect(() => {
    if (p.selected && p.selected !== lastSel.current && map.byId.has(p.selected)) reveal(p.selected, false, "nearest");
    lastSel.current = p.selected;
  }, [p.selected]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const end = () => { dragging = null; setDragging(false); setDragOver(null); };
    window.addEventListener("dragend", end);
    window.addEventListener("drop", end);
    return () => { window.removeEventListener("dragend", end); window.removeEventListener("drop", end); };
  }, []);

  const toggle = (key: string) => setOpen((o) => {
    const nx = new Set(o);
    if (nx.has(key)) nx.delete(key); else nx.add(key);
    return nx;
  });
  const nameOf = (id: string) => steps[map.byId.get(id) ?? -1]?.name ?? id;

  const onKey = (e: KeyboardEvent<HTMLElement>, s: Step) => {
    if (edit && e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      e.preventDefault();
      edit.onMove(s.id, e.key === "ArrowUp" ? -1 : 1);
      pending.current = { id: s.id, focus: true, block: "nearest" };
      return;
    }
    if (edit && (e.key === "Delete" || e.key === "Backspace")) {
      e.preventDefault();
      edit.onRemove(s.id);
      return;
    }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      const all = [...(root.current?.querySelectorAll<HTMLElement>(".sb-main") ?? [])];
      const at = all.indexOf(e.currentTarget);
      const to = e.key === "Home" ? 0 : e.key === "End" ? all.length - 1 : at + (e.key === "ArrowDown" ? 1 : -1);
      if (all[to]) {
        e.preventDefault();
        all[to].focus();
      }
    }
  };

  const n = Math.max(p.acCount ?? (p.acs?.length || 3), 1);
  const per = (s: Step, m?: Record<string, number>) => {
    const v = m?.[s.id] ?? 0;
    if (m === p.actual) return v;   // real tokens are what they are
    return s.per_ac ? v / n : s.per_item ? v / 3 : v;
  };
  const total = sumOf(steps.map((s) => s.id), p.tokens);
  const ctx: Ctx = {
    p: { ...p, acCount: n }, map, states, taken, open, toggle, flash, reveal, nameOf, onKey, per, total,
    dragOver, setDragOver, isDragging, setDragging,
  };
  const label = p.label ?? `Blocks of the workflow: ${steps.length} steps`;
  const used = p.actual ? sumOf(steps.map((s) => s.id), p.actual) : 0;
  return (
    <div className={`sx ${edit ? "sx-edit" : ""} ${states ? "sx-live" : ""} ${isDragging ? "is-dragging" : ""}`} ref={root} aria-label={label} role="region">
      {p.tokens && total > 0 && (
        <div className="sx-total" aria-label="Tokens for the whole workflow">
          <span className="sx-total-n">{used ? <><b>{kfmt(used)}</b> used of </> : null}≈ <b>{kfmt(total)}</b> tokens</span>
          <span className="sx-total-s">for the whole workflow{steps.some((s) => s.per_ac) ? ` with ${n} ${n === 1 ? "criterion" : "criteria"}` : ""}</span>
          {used ? <span className="sx-bar is-used" aria-hidden="true"><i style={{ width: `${Math.min(100, (used / total) * 100)}%` }} /></span> : null}
        </div>
      )}
      {!steps.length ? (
        <div className="sx-empty">
          <b>No blocks yet.</b>
          {edit && (edit.insertAt === -1 ? <AddPicker ctx={ctx} after={null} /> : (
            <span className="sub">Drag a block from the palette here, or <button className="linkbtn steplink" type="button" onClick={() => edit.onInsert(-1)}>add the first block</button>.</span>
          ))}
          {edit && <Gap ctx={ctx} after={-1} gk="gap:start" loop={null} always />}
        </div>
      ) : (
        <>
          {edit && <ol className="sx-stack sx-top"><Gap ctx={ctx} after={-1} gk="gap:start" loop={null} /></ol>}
          {map.sections.map((sec, k) => <SectionView key={sec.key} ctx={ctx} sec={sec} last={k === map.sections.length - 1} />)}
          {map.orphans.length > 0 && (
            <section className="sx-sec sx-orphans" aria-label="Blocks no route reaches">
              <h3 className="sx-h"><span className="sx-h-t">Not reached</span><span className="sx-h-m">no block leads here: the block before it ends the flow or jumps away</span></h3>
              <ol className="sx-stack">{map.orphans.map((r) => <StepBlock key={r.step.id} ctx={ctx} row={r} loop={null} />)}</ol>
            </section>
          )}
        </>
      )}
    </div>
  );
}));

function TokenBar({ value, total, used }: { value: number; total: number; used?: number }) {
  if (!total || (!value && !used)) return null;
  const pct = Math.round((value / total) * 100);
  return (
    <span className="sx-cost" title={`${pct}% of the estimate`}>
      <span className="sx-bar" aria-hidden="true"><i style={{ width: `${Math.max(2, Math.min(100, (value / total) * 100))}%` }} /></span>
      <span className="sx-cost-n">{used ? <><b>{kfmt(used)}</b> / </> : null}≈ {kfmt(value)}<span className="sx-pct"> · {pct}%</span></span>
    </span>
  );
}

function SectionView({ ctx, sec, last }: { ctx: Ctx; sec: Section; last?: boolean }) {
  const { p, total } = ctx;
  const est = sumOf(sec.ids, p.tokens);
  const used = p.actual ? sumOf(sec.ids, p.actual) : 0;
  if (sec.kind !== "phase") {
    return (
      <section className="sx-sec" aria-label={sec.kind === "loop" ? "A loop" : `The ${sec.phase} flow`}>
        <Stack ctx={ctx} nodes={sec.nodes} loop={null} endGap={last} />
      </section>
    );
  }
  const m = phaseMeaning(sec.phase);
  return (
    <section className="sx-sec" data-phase={sec.phase} aria-label={`${phaseTitle(sec.phase)}${m ? `: ${m}` : ""}`}>
      <h3 className="sx-h">
        <span className="sx-h-t">{phaseTitle(sec.phase)}</span>
        {m && <span className="sx-h-m">{m}</span>}
        {p.tokens ? <TokenBar value={est} total={total} used={used} /> : null}
      </h3>
      <Stack ctx={ctx} nodes={sec.nodes} loop={null} endGap={last} />
    </section>
  );
}

/** The last file index a node covers (a new block "after" it goes there). */
const lastIndex = (nd: Node): number => {
  if (nd.t === "step") return nd.row.index;
  if (nd.t === "if") return nd.yes.t === "nodes" && !nd.yes.side && nd.yes.nodes.length ? lastIndex(nd.yes.nodes[nd.yes.nodes.length - 1]) : nd.row.index;
  if (nd.t === "loop") return nd.body.length ? lastIndex(nd.body[nd.body.length - 1]) : -1;
  const secs = nd.body;
  const lastSec = secs[secs.length - 1];
  return lastSec?.nodes.length ? lastIndex(lastSec.nodes[lastSec.nodes.length - 1]) : -1;
};
const endsNode = (nd: Node) => nd.t === "step" && endsHere(nd.row.step);

function Stack({ ctx, nodes, loop, endGap }: { ctx: Ctx; nodes: Node[]; loop: LoopCtx; endGap?: boolean }) {
  const edit = ctx.p.edit;
  return (
    <ol className="sx-stack">
      {nodes.map((nd, k) => (
        <NodeView key={nd.key} ctx={ctx} nd={nd} loop={loop}
          gap={edit && !endsNode(nd) ? <Gap ctx={ctx} after={lastIndex(nd)} gk={`gap:${nd.key}`} loop={loop} always={endGap && k === nodes.length - 1} /> : null} />
      ))}
    </ol>
  );
}

function NodeView({ ctx, nd, loop, gap }: { ctx: Ctx; nd: Node; loop: LoopCtx; gap: ReactNode }) {
  return (
    <>
      {nd.t === "step" ? <StepBlock ctx={ctx} row={nd.row} loop={loop} />
        : nd.t === "if" ? <IfBlock ctx={ctx} nd={nd} loop={loop} />
          : nd.t === "loop" ? <LoopBlock ctx={ctx} nd={nd} />
            : <IncludeBlock ctx={ctx} nd={nd} />}
      {gap}
    </>
  );
}

function StateBadge({ state }: { state: StepState }) {
  if (state === "todo") return null;
  if (state === "done") return <span className="sb-st st-done" title="done"><span aria-hidden="true">✓</span><span className="sr-only">done</span></span>;
  return <span className={`sb-st st-${state}`}>{STATE_LABEL[state]}</span>;
}

function ExitChip({ ctx, e }: { ctx: Ctx; e: Exit }) {
  const arrow = e.back ? "↩" : "→";
  const words = `${e.label}: ${e.back ? "back to" : "goes to"}`;
  const inner = (
    <>
      <b>{e.back && e.kind !== "choice" ? `${arrow} ${e.label}` : e.label}</b>
      {e.to || e.kind === "flow" ? (e.back && e.kind !== "choice" ? null : <span className="sx-x-a" aria-hidden="true">{e.kind === "flow" ? "" : arrow}</span>) : <span aria-hidden="true">:</span>}
      <span className="sx-x-t">{e.toName}</span>
    </>
  );
  const cls = `sx-x x-${e.kind} ${e.back ? "is-back" : ""}`;
  if (!e.to || e.to === "end") return <span className={cls}>{inner}</span>;
  return (
    <button type="button" className={cls} onClick={(ev) => { ev.stopPropagation(); ctx.reveal(e.to!, true); }}
      aria-label={`${words} ${e.toName}. Show that step.`}>
      {inner}
    </button>
  );
}

function Tokens({ ctx, s }: { ctx: Ctx; s: Step }) {
  const { p } = ctx;
  const est = ctx.per(s, p.tokens);
  const used = p.actual?.[s.id] ?? 0;
  if (!est && !used) return null;
  const inLoop = s.per_ac || s.per_item;
  return (
    <span className="sb-tok" title={`${used ? `${kfmt(used)} tokens used so far, ` : ""}about ${kfmt(est)} tokens estimated${inLoop ? " per round" : ""}`}>
      {used ? <><b>{kfmt(used)}</b><span className="sb-tok-e"> / ≈{kfmt(est)}</span></> : <>≈ {kfmt(est)}</>}
    </span>
  );
}

function Tools({ ctx, s, row }: { ctx: Ctx; s: Step; row: Row }) {
  const edit = ctx.p.edit!;
  const locked = !!s.lock && ctx.p.keel !== false;
  return (
    <span className="sb-tools" role="group" aria-label={`Move or remove ${s.name}`}>
      <button type="button" className="sb-tool" aria-label={`Move ${s.name} earlier`} disabled={row.index === 0} onClick={() => edit.onMove(s.id, -1)}>↑</button>
      <button type="button" className="sb-tool" aria-label={`Move ${s.name} later`} disabled={row.index === ctx.p.steps.length - 1} onClick={() => edit.onMove(s.id, 1)}>↓</button>
      {locked ? (
        <button type="button" className="sb-tool is-lock" aria-label={`${s.name} is a keel rule`} onClick={() => edit.onRemove(s.id)}>
          <span aria-hidden="true">🔒</span>
        </button>
      ) : (
        <button type="button" className="sb-tool is-del" aria-label={`Remove ${s.name}`} onClick={() => edit.onRemove(s.id)}>×</button>
      )}
    </span>
  );
}

/** The face of one block: its name, who does it, its tokens and state, its exits as chips. */
function Face({ ctx, s, row, head, cls = "", children }: {
  ctx: Ctx; s: Step; row: Row; head?: ReactNode; cls?: string; children?: ReactNode;
}) {
  const { p, states, flash } = ctx;
  const edit = p.edit;
  const st: StepState | null = states ? states[s.id] ?? "todo" : null;
  const locked = !!s.lock && p.keel !== false;
  const custom = !!s.agent && (s.agent.startsWith("★") || !!p.customAgents?.has(s.agent));
  const nameOf = ctx.nameOf;
  const cond = whenText(s.when, nameOf);
  const count = s.kind === "parallel" ? (s.parallel ? `× ${s.parallel} at once` : s.from ? "one per item" : "") : "";
  const tags: string[] = [];
  if (s.kind === "gate" && cond) tags.push(`asks only if ${cond}`);
  else if (s.kind !== "branch" && cond) tags.push(`only if ${cond}`);
  if (s.retry_only) tags.push("only after a send-back");
  if (s.skip_menu) tags.push("you pick which steps run");
  if (s.report === "verdicts") tags.push("shows every verdict");
  if (s.soft) tags.push("soft check");
  if (s.skippable === "optional") tags.push("optional");
  if (s.skippable === "deferred") tags.push("can be deferred");
  if (s.kind === "include") tags.push(`runs the ${s.flow ?? "?"} flow`);
  const live = st === "run" || st === "wait" || st === "fail" || st === "stop";
  const est = ctx.per(s, p.tokens);
  const aria = [
    head ? String(head) : null, s.name, KIND_WORD[s.kind] ?? s.kind,
    s.agent && `agent ${s.agent}${custom && !s.agent.startsWith("★") ? " (custom)" : ""}`, s.model && `model ${s.model}`,
    count, locked && "keel rule", st && st !== "todo" && STATE_LABEL[st],
    p.actual?.[s.id] ? `${kfmt(p.actual[s.id])} tokens used` : "", est ? `about ${kfmt(est)} tokens${s.per_ac || s.per_item ? " per round" : ""}` : "",
  ].filter(Boolean).join(", ");
  const exits = row.exits;
  const draggable = !!edit;
  return (
    <div className={`sb-face ${cls} ${p.selected === s.id ? "is-sel" : ""} ${flash === s.id ? "is-flash" : ""}`}
      draggable={draggable || undefined}
      onDragStart={draggable ? (e) => { e.stopPropagation(); dragging = { id: s.id }; e.dataTransfer?.setData("text/plain", `step:${s.id}`); ctx.setDragging(true); } : undefined}>
      {p.onOpenStep ? (
        <button type="button" className="sb-main" aria-label={aria} aria-current={live ? "step" : undefined}
          aria-pressed={edit ? p.selected === s.id : undefined}
          onClick={() => p.onOpenStep!(s.id)} onKeyDown={(e) => ctx.onKey(e, s)}>
          <FaceText s={s} head={head} custom={custom} count={count} locked={locked} tags={tags} />
        </button>
      ) : (
        <div className="sb-main" aria-label={aria} role="group"><FaceText s={s} head={head} custom={custom} count={count} locked={locked} tags={tags} /></div>
      )}
      <span className="sb-side">
        {st && <StateBadge state={st} />}
        <Tokens ctx={ctx} s={s} />
      </span>
      {(exits.length > 0 || children) && (
        <div className="sb-exits" role="group" aria-label={`Where ${s.name} goes`}>
          {exits.map((e, k) => <ExitChip key={`${e.kind}${e.label}${k}`} ctx={ctx} e={e} />)}
          {children}
        </div>
      )}
      {edit && <Tools ctx={ctx} s={s} row={row} />}
    </div>
  );
}

function FaceText({ s, head, custom, count, locked, tags }: { s: Step; head?: ReactNode; custom: boolean; count: string; locked: boolean; tags: string[] }) {
  return (
    <>
      <span className="sb-ico" aria-hidden="true"><KindIcon kind={s.kind} /></span>
      <span className="sb-text">
        <span className="sb-name">{head && <span className="sb-head">{head}</span>}{s.name}{locked && <span className="sb-lock" title="keel rule: cannot be removed while keel rules are on"><span aria-hidden="true"> 🔒</span><span className="sr-only">keel rule</span></span>}</span>
        <span className="sb-meta">
          {s.agent && <span className={`sb-agent ${custom ? "is-custom" : ""}`}>{custom && !s.agent.startsWith("★") ? "★ " : ""}{s.agent}{s.model && <span className="sb-model">{s.model}</span>}</span>}
          {count && <span className="sb-count">{count}</span>}
          {tags.map((t) => <span key={t} className="sb-tag">{t}</span>)}
        </span>
      </span>
    </>
  );
}

const STATE_CLS = (st: StepState | null) => (st ? `s-${st}` : "");

function StepBlock({ ctx, row }: { ctx: Ctx; row: Row; loop?: LoopCtx }) {
  const { p, states, open, toggle } = ctx;
  const s = row.step;
  const st = states ? states[s.id] ?? "todo" : null;
  const fk = sideKey(s.id);
  const foldOpen = open.has(fk);
  return (
    <li className={`sb k-${s.kind} ${STATE_CLS(st)} ${p.edit?.notice?.id === s.id ? "has-notice" : ""}`} data-block={s.id} data-step={s.id} data-state={st ?? undefined}>
      <Face ctx={ctx} s={s} row={row} head={s.kind === "gate" ? "wait for you" : s.kind === "branch" ? "if" : undefined} />
      {p.edit?.notice?.id === s.id && <div className="sb-notice">{p.edit.notice.node}</div>}
      {row.side.length > 0 && (
        <div className={`sb-fold ${foldOpen ? "is-open" : ""}`}>
          <button type="button" className="sb-fold-t" aria-expanded={foldOpen} aria-controls={`sx-side-${s.id}`} onClick={() => toggle(fk)}>
            <span className="sx-chev" aria-hidden="true">▸</span>
            side path · {row.side.length} {row.side.length === 1 ? "block" : "blocks"}
            <span className="sr-only"> off the main path, from {s.name}</span>
            <SideCost ctx={ctx} rows={row.side} />
          </button>
          {foldOpen && (
            <ol className="sx-stack sx-side" id={`sx-side-${s.id}`} aria-label={`Side path from ${s.name}`}>
              {row.side.map((r) => <StepBlock key={r.step.id} ctx={ctx} row={r} loop={null} />)}
            </ol>
          )}
        </div>
      )}
      {p.inline?.(s.id)}
    </li>
  );
}

function SideCost({ ctx, rows }: { ctx: Ctx; rows: Row[] }) {
  const v = sumOf(rows.map((r) => r.step.id), ctx.p.tokens);
  return v ? <span className="sb-fold-n">≈ {kfmt(v)}</span> : null;
}

function ArmView({ ctx, arm, word, loop, takenHere, notTaken }: { ctx: Ctx; arm: Arm; word: string; loop: LoopCtx; takenHere: boolean; notTaken: boolean }) {
  const { open, toggle } = ctx;
  const cls = `sc-mouth ${takenHere ? "is-taken" : ""} ${notTaken ? "not-taken" : ""}`;
  if (arm.t === "go") return <div className={cls}><span className="sc-go">{word === "then" ? "go on" : "go on with the next block"}</span></div>;
  if (arm.t === "jump") return <div className={cls}><span className="sc-go"><ExitChip ctx={ctx} e={arm.exit} /></span></div>;
  const small = arm.nodes.length <= 2;
  const isOpen = !arm.side || small || open.has(arm.key);
  return (
    <div className={cls}>
      {arm.side && !small && (
        <button type="button" className="sb-fold-t sc-fold" aria-expanded={isOpen} onClick={() => toggle(arm.key)}>
          <span className="sx-chev" aria-hidden="true">▸</span> side path · {arm.nodes.length} blocks
          <SideCost ctx={ctx} rows={arm.nodes.flatMap((x) => (x.t === "step" ? [x.row] : []))} />
        </button>
      )}
      {isOpen && <Stack ctx={ctx} nodes={arm.nodes} loop={arm.side ? null : loop} />}
    </div>
  );
}

function IfBlock({ ctx, nd, loop }: { ctx: Ctx; nd: Extract<Node, { t: "if" }>; loop: LoopCtx }) {
  const { states, taken } = ctx;
  const s = nd.row.step;
  const st = states ? states[s.id] ?? "todo" : null;
  const t = taken[s.id];
  const yesWord = nd.yes.t === "nodes" && nd.yes.side ? "then (side path)" : "then";
  return (
    <li className={`sc sc-if ${STATE_CLS(st)}`} data-block={s.id} data-step={s.id} data-state={st ?? undefined}>
      <Face ctx={ctx} s={s} row={nd.row} head="if" cls="sc-head">
        {nd.cond && <span className="sx-x x-cond"><b>asks</b><span className="sx-x-t">{nd.cond}</span></span>}
        {t && <span className="sx-x x-taken"><b>took</b><span className="sx-x-t">{t === "yes" ? "yes" : "no (else)"}</span></span>}
      </Face>
      <ArmView ctx={ctx} arm={nd.yes} word={yesWord} loop={loop} takenHere={t === "yes"} notTaken={t === "no"} />
      {nd.no.t !== "go" ? (
        <>
          <div className="sc-mid"><span className="sc-word">else</span>{t === "no" && <span className="sr-only">(taken)</span>}</div>
          <ArmView ctx={ctx} arm={nd.no} word="else" loop={loop} takenHere={t === "no"} notTaken={t === "yes"} />
        </>
      ) : null}
      <div className="sc-foot">{nd.no.t === "go" && nd.yes.t === "nodes" && !nd.yes.side ? <span className="sc-word">otherwise skip these</span> : null}</div>
      {ctx.p.inline?.(s.id)}
    </li>
  );
}

function LoopBlock({ ctx, nd }: { ctx: Ctx; nd: Extract<Node, { t: "loop" }> }) {
  const { p, states, total } = ctx;
  const times = loopTimes(nd.loop, p.acCount ?? 3);
  const est = sumOf(nd.ids, p.tokens);
  const used = p.actual ? sumOf(nd.ids, p.actual) : 0;
  const what = nd.loop.kind === "ac" ? "criterion" : nd.loop.title.replace(/^For each /, "");
  const live = states ? nd.ids.some((id) => ["run", "wait", "fail"].includes(states[id])) : false;
  const allDone = states ? nd.ids.every((id) => states[id] === "done") : false;
  const doneAcs = p.acs?.filter((a) => a.status === "done" || a.status === "already-met").length ?? 0;
  const curAc = p.acs?.findIndex((a) => a.id === p.currentAc) ?? -1;
  const loopCtx: LoopCtx = nd.loop.kind === "ac" ? "ac" : "each";
  return (
    <li className={`sc sc-loop ${live ? "is-live" : ""} ${allDone ? "s-done" : ""}`} data-loop={nd.loop.id}>
      <div className="sc-head sc-loop-h">
        <span className="sb-ico" aria-hidden="true"><KindIcon kind="loop" /></span>
        <span className="sc-title">
          <b>{nd.loop.title.toLowerCase().replace(/^for each/, "for each")}</b>
          <span className="sc-times" title={nd.loop.kind === "ac" ? "criteria in the spec" : "the engine assumes 3 items"}>× {times}</span>
          {p.acs?.length && nd.loop.kind === "ac" && states ? (
            <span className="sc-iter">{curAc >= 0 ? `round ${curAc + 1} of ${p.acs.length}` : `${doneAcs} of ${p.acs.length} done`}</span>
          ) : null}
        </span>
        {est > 0 && (
          <span className="sc-cost">
            <span>≈ {kfmt(est / times)} each</span>
            <span className="sc-cost-t">{used ? <><b>{kfmt(used)}</b> / </> : null}≈ <b>{kfmt(est)}</b> in all</span>
            <TokenBarMini value={est} total={total} />
          </span>
        )}
        {nd.loop.kind === "ac" && p.acs?.length ? <AcChips acs={p.acs} current={p.currentAc} label="Criteria in this loop" /> : null}
      </div>
      <div className="sc-mouth">
        {p.edit && <ol className="sx-stack"><Gap ctx={ctx} after={nd.body.length ? firstIndex(nd.body[0]) - 1 : -1} gk={`gap:in:${nd.key}`} loop={loopCtx} /></ol>}
        <Stack ctx={ctx} nodes={nd.body} loop={loopCtx} />
      </div>
      <div className="sc-foot"><span className="sc-word">↻ again for the next {what}</span></div>
    </li>
  );
}

function TokenBarMini({ value, total }: { value: number; total: number }) {
  if (!total || !value) return null;
  return <span className="sx-bar is-mini" aria-hidden="true"><i style={{ width: `${Math.max(2, Math.min(100, (value / total) * 100))}%` }} /></span>;
}

const firstIndex = (nd: Node): number =>
  nd.t === "step" || nd.t === "if" ? nd.row.index : nd.t === "loop" ? (nd.body[0] ? firstIndex(nd.body[0]) : -1) : nd.body[0]?.nodes[0] ? firstIndex(nd.body[0].nodes[0]) : -1;

function IncludeBlock({ ctx, nd }: { ctx: Ctx; nd: Extract<Node, { t: "include" }> }) {
  const { open, toggle, states, p, total } = ctx;
  const isOpen = open.has(nd.key);
  const est = sumOf(nd.ids, p.tokens);
  const used = p.actual ? sumOf(nd.ids, p.actual) : 0;
  const done = states ? nd.ids.filter((id) => states[id] === "done").length : 0;
  const now = states ? nd.ids.find((id) => ["run", "wait", "fail", "stop"].includes(states[id])) : undefined;
  const bodyId = `sx-${nd.key.replace(/[^\w-]/g, "-")}`;
  return (
    <li className={`sc sc-inc ${isOpen ? "is-open" : ""} ${now ? `s-${states![now]}` : states && done && done >= nd.ids.length - 2 ? "s-done" : ""}`} data-include={nd.path}>
      <button type="button" className="sc-head sc-inc-h" aria-expanded={isOpen} aria-controls={bodyId} onClick={() => toggle(nd.key)}
        aria-label={`${nd.flow}: ${nd.count} blocks from the ${nd.flow} workflow, ${includeMeaning(nd.flow)}${est ? `, about ${kfmt(est)} tokens` : ""}. ${isOpen ? "Fold" : "Show"} them.`}>
        <span className="sb-ico" aria-hidden="true"><KindIcon kind="include" /></span>
        <span className="sc-title"><span className="sx-chev" aria-hidden="true">▸</span> <b>{nd.flow}</b> <span className="sc-times">{nd.count} blocks</span></span>
        <span className="sc-inc-m">{includeMeaning(nd.flow)}</span>
        {states && (
          <span className="sc-inc-s">
            {now ? <><StateBadge state={states[now]} /> <span className="sc-inc-now">{ctx.nameOf(now)}</span></> : done ? `${done} of ${nd.ids.length} done` : null}
          </span>
        )}
        {est > 0 && <span className="sc-cost"><span className="sc-cost-t">{used ? <><b>{kfmt(used)}</b> / </> : null}≈ <b>{kfmt(est)}</b></span><TokenBarMini value={est} total={total} /></span>}
      </button>
      {isOpen && (
        <div className="sc-mouth" id={bodyId}>
          {nd.body.map((sec) => <SectionView key={sec.key} ctx={ctx} sec={sec} last={false} />)}
        </div>
      )}
      <div className="sc-foot" aria-hidden="true" />
    </li>
  );
}

function Gap({ ctx, after, gk, loop, always }: { ctx: Ctx; after: number; gk: string; loop: LoopCtx; always?: boolean }) {
  const edit = ctx.p.edit!;
  const name = after >= 0 ? ctx.p.steps[after]?.name ?? "" : "";
  if (edit.insertAt === after && (edit.insertLoop ?? null) === (loop ?? null)) {
    return <li className="sx-gap is-open"><AddPicker ctx={ctx} after={after >= 0 ? ctx.p.steps[after] : null} /></li>;
  }
  const over = ctx.dragOver === gk;
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const d = readDrag(e);
    dragging = null;
    ctx.setDragOver(null);
    ctx.setDragging(false);
    if (d?.kind) edit.onAddAt(after, d.kind, loop);
    else if (d?.id) edit.onMoveTo(d.id, after, loop);
  };
  return (
    <li className={`sx-gap ${always ? "is-always" : ""} ${over ? "is-over" : ""}`} data-gap={after}
      onDragOver={(e) => { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = dragging?.kind ? "copy" : "move"; if (!over) ctx.setDragOver(gk); }}
      onDragEnter={(e) => { e.preventDefault(); ctx.setDragging(true); }}
      onDragLeave={() => { if (over) ctx.setDragOver(null); }}
      onDrop={onDrop}>
      <button type="button" className="sx-add" aria-label={after >= 0 ? `Add a block after ${name}` : "Add a block at the start"}
        onClick={() => edit.onInsert(after, loop)}>
        <span aria-hidden="true">+</span>{always ? " add a block" : ""}
      </button>
    </li>
  );
}

function AddPicker({ ctx, after }: { ctx: Ctx; after: Step | null }) {
  const edit = ctx.p.edit!;
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => { first.current?.focus(); }, []);
  return (
    <div className="sx-picker" role="group" aria-label={after ? `Add a block after ${after.name}` : "Add a block at the start"}
      onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); edit.onCancelInsert(); } }}>
      <span className="sx-picker-t">{after ? <>Add after <b>{after.name}</b>:</> : "Add a block:"}</span>
      {ADD_KINDS.map((k, i) => (
        <button key={k.kind} ref={i ? undefined : first} className={`pal k-${k.kind}`} type="button" onClick={() => edit.onAdd(k.kind)}>
          <KindIcon kind={k.kind} /> {k.label}
        </button>
      ))}
      <button className="btn sm ghost" type="button" onClick={edit.onCancelInsert}>Cancel</button>
    </div>
  );
}

/** The block palette of the builder: drag a block into the stack, or click to add it after the selected block. */
export function Palette({ onAdd, where }: { onAdd: (kind: StepKind | "loop") => void; where: string }) {
  return (
    <div className="sx-palette" role="group" aria-label="Blocks to add">
      <span className="sx-palette-t">Blocks</span>
      {ADD_KINDS.map((k) => (
        <button key={k.kind} type="button" className={`pal k-${k.kind}`} draggable onDragStart={(e) => startPaletteDrag(e, k.kind)}
          onClick={() => onAdd(k.kind)} title={`${k.hint}. Drag it into the stack, or click to add it ${where}.`}
          aria-label={`Add ${k.label} block ${where}`}>
          <KindIcon kind={k.kind} /> {k.label}
        </button>
      ))}
      <button type="button" className="pal k-loop" onClick={() => onAdd("loop")}
        title={`a step that runs once per criterion. Click to add it ${where}.`} aria-label={`Add a loop (for each criterion) ${where}`}>
        <KindIcon kind="loop" /> Loop
      </button>
      <span className="sx-palette-h">drag into the stack · or click to add {where}</span>
    </div>
  );
}

// ------------------------------------------------------------------ Blocks | Table | Graph

export type MapView = "blocks" | "table" | "graph";

/** Which view a place shows (per browser, `keel2.view.<id>`); Blocks by default. */
export function useMapView(id: string, allowed: MapView[] = ["blocks", "table", "graph"]): [MapView, (v: MapView) => void] {
  const [v, setV] = useState<MapView>(() => {
    try {
      const got = localStorage.getItem(`keel2.view.${id}`) as MapView | null;
      return got && allowed.includes(got) ? got : "blocks";
    } catch {
      return "blocks";
    }
  });
  return [v, (nv: MapView) => {
    setV(nv);
    try { localStorage.setItem(`keel2.view.${id}`, nv); } catch { /* private window: not remembered */ }
  }];
}

const VIEW_LABEL: Record<MapView, string> = { blocks: "Blocks", table: "Table", graph: "Graph" };

export function ViewToggle({ value, onChange, views = ["blocks", "table", "graph"] }: { value: MapView; onChange: (v: MapView) => void; views?: MapView[] }) {
  return (
    <div className="tabs fm-toggle" role="group" aria-label="How to show the workflow">
      {views.map((v) => (
        <button key={v} type="button" aria-pressed={value === v} onClick={() => onChange(v)}>{VIEW_LABEL[v]}</button>
      ))}
    </div>
  );
}

/** A compact table of every step (a quick scan), the selected one marked. */
export function StepTable({ steps, tokens, actual, selected, onOpenStep, acCount = 3 }: {
  steps: Step[]; tokens?: Record<string, number>; actual?: Record<string, number>; selected?: string | null; onOpenStep?: (id: string) => void; acCount?: number;
}) {
  const map = useMemo(() => buildFlowMap(steps), [steps]);
  const side = new Set(map.parentOf.keys());
  return (
    <div className="table-wrap sx-table"><table aria-label="Steps as a table">
      <thead><tr><th>#</th><th>Step</th><th>Kind</th><th>Who</th><th className="num">Tokens</th></tr></thead>
      <tbody>{steps.map((s, i) => (
        <tr key={s.id} className={s.id === selected ? "rowsel" : ""}>
          <td className="num sub">{i + 1}</td>
          <td>
            {onOpenStep ? <button type="button" className="linkbtn" onClick={() => onOpenStep(s.id)}>{s.name}</button> : <b>{s.name}</b>}
            {(s.per_ac || s.per_item) && <span className="sb-tag"> ↻ {s.per_ac ? `each criterion (× ${acCount})` : "each item"}</span>}
            {side.has(s.id) && <span className="sb-tag"> side path</span>}
            {s.included_from && <span className="sb-tag"> from {s.included_from.split("/")[0]}</span>}
          </td>
          <td><span className={`sx-kind k-${s.kind}`}><KindIcon kind={s.kind} /> {KIND_WORD[s.kind] ?? s.kind}</span></td>
          <td className="sub">{s.agent ? <>{s.agent}{s.model ? <span className="mono"> · {s.model}</span> : null}</> : s.kind === "gate" ? "you" : "—"}</td>
          <td className="num mono">{actual?.[s.id] ? <><b>{kfmt(actual[s.id])}</b> / </> : null}{tokens?.[s.id] ? kfmt(tokens[s.id]) : "—"}</td>
        </tr>
      ))}</tbody>
    </table></div>
  );
}

/** What the colours and shapes mean. */
export function BlocksLegend({ live }: { live?: boolean }) {
  const items: [StepKind | "loop", string][] = [["agent", "agent"], ["code", "plain code"], ["gate", "wait for you"], ["branch", "if / else"], ["loop", "loop"], ["include", "included flow"]];
  return (
    <div className="legend sx-legend" aria-label="Legend">
      {items.map(([k, l]) => <span key={k}><span className={`sx-kind k-${k}`}><KindIcon kind={k} /></span>{l}</span>)}
      <span><span className="sx-x is-back sm"><b>↩</b></span>goes back</span>
      {live && <>
        <span><span className="sb-st st-done">✓</span>done</span>
        <span><span className="sb-st st-run">running</span></span>
        <span><span className="sb-st st-wait">waiting for you</span></span>
      </>}
    </div>
  );
}
