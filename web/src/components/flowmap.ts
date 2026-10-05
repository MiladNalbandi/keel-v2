// The flow map's model: which steps are the main path, which are side paths (folded under the step that leads to
// them), sections by phase, loops, included flows, the exits of every step (by target name) and each step's state in
// a running thread. Pure functions: StepsView renders what buildFlowMap returns.

import type { Step, ThreadStatus } from "../api";

// ------------------------------------------------------------------ words

/** Phase id → [plain name, what it is for]. Unknown phases show their id. */
export const PHASES: Record<string, [string, string]> = {
  none: ["Start", ""],
  setup: ["Setup", "configure the project, the run ladder and the knowledge base"],
  preflight: ["Preflight", "check the repo and move to the flow's own branch"],
  workspace: ["Workspace", "prepare the branch and the working copy"],
  triage: ["Triage", "size the change and list its criteria"],
  spec: ["Spec", "write and approve what to build"],
  contract: ["Contract", "write the API surface the criteria change"],
  red: ["Red", "write a failing test for the criterion"],
  green: ["Green", "make that test pass with the least code"],
  ac: ["Criterion", "tests and code for one criterion"],
  refactor: ["Refactor", "improve the code, the behaviour stays the same"],
  gate: ["Review", "a reviewer, then you, check the work"],
  "review-fix": ["Review fix", "fix what a review or a check found"],
  integration: ["Integration", "wire the real parts together"],
  e2e: ["End-to-end", "browser tests for the user journeys"],
  smoke: ["Smoke", "quick checks that also run after a deploy"],
  "lint-fix": ["Lint fix", "fix what the static checks found"],
  "coverage-fix": ["Coverage", "measure coverage and close the gaps"],
  trivial: ["Trivial change", "a change no test could notice"],
  "small-change": ["Small change", "prepare a small change"],
  "bug-report": ["Bug report", "take in the bug report"],
  "bug-repro": ["Reproduce", "prove the bug with a failing test"],
  "bug-investigate": ["Investigate", "find the root cause from evidence"],
  "gate-r": ["Gate R", "you confirm the bug is reproduced"],
  "gate-f": ["Gate F", "you approve the fix plan"],
  "bug-fix": ["Fix", "fix the bug while the tests stay frozen"],
  reset: ["Reset", "put back uncommitted work and try again"],
  security: ["Security", "audit the branch and its dependencies"],
  ship: ["Ship", "checks, reviews and the pull request"],
  "final-review": ["Final review", "you read every verdict before the PR"],
  memory: ["Knowledge", "update what keel knows about the project"],
  close: ["Close", "decision records and the closing note"],
  "hunt-scope": ["Hunt scope", "choose what to look at"],
  "hunt-sweep": ["Hunt sweep", "hunters propose candidate bugs"],
  "hunt-prove": ["Prove", "try to reproduce each candidate"],
  "hunt-report": ["Hunt report", "write the report"],
  "hunt-triage": ["Hunt triage", "decide what to fix first"],
  review: ["Review", "a read-only review"],
  verify: ["Verify", "run the checks"],
};

export const phaseTitle = (p: string) => PHASES[p]?.[0] ?? p;
export const phaseMeaning = (p: string) => PHASES[p]?.[1] ?? "";

/** What an included flow is, for its folded block. */
const INCLUDES: Record<string, string> = {
  ship: "checks, reviews and the pull request",
  cover: "measure coverage and close the gaps",
};
export const includeMeaning = (flow: string) => INCLUDES[flow] ?? `the steps of the ${flow} workflow`;

const pretty = (key: string) => key.replace(/^data\./, "").replace(/[_.-]+/g, " ").trim();

/** "coverage_groups" → "coverage group". */
export function singular(key: string): string {
  const p = pretty(key);
  return p.endsWith("ies") ? p.slice(0, -3) + "y" : p.endsWith("s") && !p.endsWith("ss") ? p.slice(0, -1) : p;
}

/** A step's `when` in words: "red says AMEND", "you chose stop at gate R", "there are criteria". */
export function whenText(w: Record<string, unknown> | undefined, name: (id: string) => string): string | null {
  if (!w || typeof w !== "object") return null;
  const src = w.step;
  const who = Array.isArray(src) ? src.map((x) => name(String(x))).join(" or ") : typeof src === "string" ? name(src) : "";
  const eq = w.equals !== undefined && w.equals !== null ? String(w.equals) : null;
  const among = Array.isArray(w.in) ? w.in.map(String).join(", ") : null;
  if (typeof w.marker === "string") {
    if (w.marker === "CHOICE") return eq ? `you chose ${eq}${who ? ` at ${who}` : ""}` : `you chose${who ? ` at ${who}` : ""}`;
    const said = `${who ? `${who}${w.any ? " (any of them)" : ""} says ` : ""}${w.marker}`;
    return eq ? `${said} = ${eq}` : among ? `${said} is ${among}` : said;
  }
  const path = typeof w.data === "string" ? w.data : typeof w.state === "string" ? w.state : null;
  if (!path) return null;
  if (path === "acs" && eq === null && !among) return "there are criteria";
  const p = pretty(path);
  return eq !== null ? `${p} = ${eq}` : among ? `${p} is ${among}` : `${p} is set`;
}

// ------------------------------------------------------------------ exits

export type ExitKind = "choice" | "list" | "back" | "yes" | "no" | "then" | "rounds" | "redo" | "fails" | "flow";
export type Exit = {
  kind: ExitKind;
  label: string;
  /** Step id, "end", or null (no single target: a list of choices, a started flow). */
  to: string | null;
  toName: string;
  /** The target is this step or an earlier one: a loop back, never drawn as a line. */
  back: boolean;
};

const END = "end";

/** Where a step can go besides "the next row": gate choices and send back, branch yes / no, then, after the rounds… */
export function exitsOf(steps: Step[], i: number, byId: Map<string, number>): Exit[] {
  const s = steps[i];
  const out: Exit[] = [];
  const add = (kind: ExitKind, label: string, to: string | null | undefined) => {
    if (to === undefined) return;
    if (to === null) return void out.push({ kind, label, to: null, toName: "", back: false });
    if (to === END) return void out.push({ kind, label, to: END, toName: "end of the flow", back: false });
    const j = byId.get(to);
    out.push({ kind, label, to, toName: j === undefined ? to : steps[j].name, back: j !== undefined && j <= i });
  };
  const next = steps[i + 1]?.id ?? END;
  if (s.kind === "gate") {
    if (s.choices && !Array.isArray(s.choices)) Object.entries(s.choices).forEach(([k, v]) => add("choice", k, v));
    else if (Array.isArray(s.choices) && s.choices.length) out.push({ kind: "list", label: s.per_item || s.per_ac ? "per item" : "choices", to: null, toName: s.choices.join(" / "), back: false });
    if (s.back) add("back", "send back", s.back);
  } else if (s.kind === "branch") {
    add("yes", "yes", next);
    add("no", "no", s.no ?? next);
  } else {
    if (s.back) add(s.kind === "code" ? "fails" : "back", s.kind === "code" ? "fails" : "blocking findings", s.back);
    if (s.after_rounds) add("rounds", s.rounds ? `after ${s.rounds} round${s.rounds > 1 ? "s" : ""}` : "after the rounds", s.after_rounds);
    if (s.redo) add("redo", "after a fix", s.redo);
    if (s.action === "start_flow" && s.flow) out.push({ kind: "flow", label: "starts", to: null, toName: `the ${s.flow} flow`, back: false });
    if (s.then && s.then !== "continue") add("then", "then", s.then);
  }
  return out;
}

/** The routes the main path may take from a step: forward ones count, back ones are loops. Default route first. */
function routes(steps: Step[], i: number, byId: Map<string, number>): number[] {
  const s = steps[i];
  const n = steps.length;
  const next = i + 1 < n ? i + 1 : n;            // n = past the last step: the flow ends
  const at = (id?: string) => (id === END ? n : id !== undefined && byId.has(id) ? byId.get(id)! : -1);
  if (s.kind === "gate") {
    if (s.choices && !Array.isArray(s.choices)) {
      const exits = Object.entries(s.choices);
      const first = exits.find(([k]) => k === "approve") ?? exits[0];
      const rest = exits.filter((e) => e !== first).map(([, v]) => at(v));
      // A gate with `when` that does not hold is approved unasked and goes on to the next step.
      return [at(first?.[1]), ...(s.when ? [next] : []), ...rest];
    }
    return [next];
  }
  if (s.kind === "branch") return [next, s.no ? at(s.no) : next];
  if (s.then === END) return [n];
  if (s.then && s.then !== "continue") return [at(s.then)];
  return [next];
}

// ------------------------------------------------------------------ the map

export type Row = { step: Step; index: number; exits: Exit[]; side: Row[] };
export type LoopInfo = { id: string; kind: "ac" | "each"; key: string; title: string };
/** An if/else arm: blocks (main steps it skips when the answer is no, or a side path), a jump, or nothing ("go on"). */
export type Arm = { t: "nodes"; nodes: Node[]; side: boolean; key: string } | { t: "jump"; exit: Exit } | { t: "go" };
export type Node =
  | { t: "step"; key: string; row: Row }
  | { t: "if"; key: string; row: Row; cond: string | null; yes: Arm; no: Arm }
  | { t: "loop"; key: string; loop: LoopInfo; body: Node[]; ids: string[] }
  | { t: "include"; key: string; flow: string; path: string; count: number; body: Section[]; ids: string[] };
/** Consecutive blocks of one phase (a loop or an included flow is a section of its own). */
export type Section = { key: string; kind: "phase" | "loop" | "include"; phase: string; nodes: Node[]; ids: string[] };

export type FlowMap = {
  sections: Section[];
  /** Steps no route reaches (a new step after one that ends the flow, say). */
  orphans: Row[];
  byId: Map<string, number>;
  /** Main-path step ids in order. */
  main: string[];
  /** Side step id → the main step it hangs under. */
  parentOf: Map<string, string>;
  /** Each step's phase (a step without one keeps the phase of the step before, as the engine does). */
  phaseOf: string[];
  /** Fold keys that hold a step (included flows, side paths), outermost first, to reveal it. */
  containers: (id: string) => string[];
  /** Every step id inside a node (its arms, side paths and inner blocks too). */
  idsOf: (n: Node) => string[];
};

const incPath = (s: Step) => (s.included_from ? s.included_from.split("/").filter(Boolean) : []);
export const includeKey = (segs: string[]) => `inc:${segs.join("/")}`;
export const sideKey = (id: string) => `side:${id}`;
export const armKey = (id: string, arm: "yes" | "no") => `arm:${id}:${arm}`;

export function buildFlowMap(steps: Step[]): FlowMap {
  const n = steps.length;
  const byId = new Map(steps.map((s, i) => [s.id, i]));
  const exits = steps.map((_, i) => exitsOf(steps, i, byId));

  const phaseOf: string[] = [];
  steps.forEach((s, i) => { phaseOf[i] = s.phase || (i ? phaseOf[i - 1] : "none"); });

  // Loops: the per-AC steps, and each run of per_item steps that starts at a for_each step.
  const loopOf: (LoopInfo | null)[] = [];
  let each: LoopInfo | null = null;
  steps.forEach((s, i) => {
    if (s.for_each) each = { id: s.id, kind: "each", key: s.for_each, title: `For each ${singular(s.for_each)}` };
    else if (!s.per_item) each = null;
    loopOf[i] = s.per_ac ? { id: "ac", kind: "ac", key: "acs", title: "For each criterion" } : s.per_item ? each : null;
  });

  // Main path: the longest run of forward routes from the first step to an end. Back routes are loops; a side path
  // (a gate's other choice, a jump to a hand-off) either loops back or ends early, so the longest run skips it.
  const best = new Array<number>(n + 1).fill(-Infinity);
  const pick = new Array<number>(n).fill(-1);
  best[n] = 0;
  for (let i = n - 1; i >= 0; i--) {
    for (const j of routes(steps, i, byId)) {
      if (j <= i || j > n) continue;
      if (best[j] + 1 > best[i]) { best[i] = best[j] + 1; pick[i] = j; }
    }
  }
  const mainIdx: number[] = [];
  if (n && best[0] > -Infinity) {
    for (let i = 0; i < n; i = pick[i]) mainIdx.push(i);
  } else if (n) {
    // Nothing reaches an end (every route loops): follow the default routes until a step repeats.
    const seen = new Set<number>();
    for (let i = 0; i >= 0 && i < n && !seen.has(i);) {
      seen.add(i);
      mainIdx.push(i);
      const r = routes(steps, i, byId)[0];
      i = r === undefined ? -1 : r;
    }
    mainIdx.sort((x, y) => x - y);
  }
  const onMain = new Set(mainIdx);

  // Side paths: every step reached from a main step through steps that are not on the main path, hung under the
  // first main step (in order) that reaches it.
  const owner = new Map<number, number>();
  const targets = (i: number) => {
    const out: number[] = [];
    routes(steps, i, byId).forEach((j) => { if (j >= 0 && j < n) out.push(j); });
    exits[i].forEach((e) => { const j = e.to ? byId.get(e.to) : undefined; if (j !== undefined) out.push(j); });
    return out;
  };
  for (const m of mainIdx) {
    const queue = targets(m).filter((j) => !onMain.has(j) && !owner.has(j));
    queue.forEach((j) => owner.set(j, m));
    while (queue.length) {
      const k = queue.shift()!;
      for (const j of targets(k)) {
        if (onMain.has(j) || owner.has(j)) continue;
        owner.set(j, m);
        queue.push(j);
      }
    }
  }
  const sideOf = new Map<number, number[]>();
  [...owner.entries()].sort((x, y) => x[0] - y[0]).forEach(([j, m]) => sideOf.set(m, [...(sideOf.get(m) ?? []), j]));
  const plain = (i: number): Row => ({ step: steps[i], index: i, exits: exits[i], side: [] });
  /** The side steps of main step `m` reached from `entry` (through side steps of m only), in file order. */
  const reach = (m: number, entry: number) => {
    const got = new Set<number>([entry]);
    const queue = [entry];
    while (queue.length) {
      const k = queue.shift()!;
      for (const j of targets(k)) if (owner.get(j) === m && !got.has(j)) { got.add(j); queue.push(j); }
    }
    return [...got].sort((x, y) => x - y);
  };

  const here = (j: number, depth: number) => incPath(steps[j]).length <= depth;

  /** A branch as if / else: the arms, or null when both arms are plain routes (then it is one block with chips). */
  const ifNode = (i: number, idx: number[], k: number, depth: number): { node: Node; next: number } | null => {
    const s = steps[i];
    const yesJ = i + 1;
    const noJ = s.no ? byId.get(s.no) : undefined;
    const used = new Set<number>();
    let yes: Arm = { t: "go" };
    let no: Arm = { t: "go" };
    let next = k + 1;
    const sideArm = (entry: number, arm: "yes" | "no"): Arm => {
      const got = reach(i, entry).filter((j) => !used.has(j));
      got.forEach((j) => used.add(j));
      return { t: "nodes", side: true, key: armKey(s.id, arm), nodes: got.map((j) => ({ t: "step", key: `step:${steps[j].id}`, row: plain(j) })) };
    };
    if (yesJ < n && owner.get(yesJ) === i) yes = sideArm(yesJ, "yes");
    else if (noJ !== undefined && noJ > i && onMain.has(noJ)) {
      const p = idx.indexOf(noJ, k + 1);
      const range = p > k + 1 ? idx.slice(k + 1, p) : [];
      // A loop may not be cut in two by the arm.
      const own = loopOf[i]?.id;
      const cut = range.some((j) => { const L = loopOf[j]?.id; return !!L && L !== own && idx.some((x) => loopOf[x]?.id === L && !range.includes(x)); });
      if (range.length && !cut && range.every((j) => incPath(steps[j]).length >= depth)) {
        yes = { t: "nodes", side: false, key: armKey(s.id, "yes"), nodes: seq(range, depth, loopOf[i]?.id ?? null) };
        next = p;
      }
    }
    if (noJ !== undefined && owner.get(noJ) === i && !used.has(noJ)) no = sideArm(noJ, "no");
    else if (yes.t === "nodes" && !yes.side) no = { t: "go" };
    else {
      const e = exits[i].find((x) => x.kind === "no");
      if (e && e.to && !(noJ !== undefined && noJ === idx[k + 1])) no = { t: "jump", exit: e };
    }
    if (yes.t !== "nodes" && no.t !== "nodes") return null;
    const rest = (sideOf.get(i) ?? []).filter((j) => !used.has(j));
    const row: Row = { step: s, index: i, exits: exits[i].filter((e) => e.kind !== "yes" && e.kind !== "no"), side: rest.map(plain) };
    return { node: { t: "if", key: `if:${s.id}`, row, cond: whenText(s.when, (id) => steps[byId.get(id) ?? -1]?.name ?? id), yes, no }, next };
  };

  /** Blocks for consecutive main steps: included flows, loops, if / else, plain blocks. */
  function seq(idx: number[], depth: number, loopId: string | null): Node[] {
    const out: Node[] = [];
    let k = 0;
    while (k < idx.length) {
      const i = idx[k];
      const segs = incPath(steps[i]);
      if (segs.length > depth) {
        const path = segs.slice(0, depth + 1);
        const same = (j: number) => incPath(steps[j]).slice(0, depth + 1).join("/") === path.join("/");
        let e = k;
        while (e < idx.length && same(idx[e])) e++;
        const inner = idx.slice(k, e);
        const all = steps.map((_, j) => j).filter((j) => same(j));
        out.push({ t: "include", key: includeKey(path), flow: segs[depth], path: path.join("/"), count: all.length,
          body: sections(inner, depth + 1), ids: all.map((j) => steps[j].id) });
        k = e;
        continue;
      }
      const lp = loopOf[i];
      if (lp && lp.id !== loopId) {
        let e = k;
        while (e < idx.length && here(idx[e], depth) && loopOf[idx[e]]?.id === lp.id) e++;
        const inner = idx.slice(k, e);
        const body = seq(inner, depth, lp.id);
        out.push({ t: "loop", key: `loop:${lp.id}:${steps[i].id}`, loop: lp, body, ids: body.flatMap(idsOf) });
        k = e;
        continue;
      }
      if (steps[i].kind === "branch") {
        const r = ifNode(i, idx, k, depth);
        if (r) { out.push(r.node); k = r.next; continue; }
      }
      out.push({ t: "step", key: `step:${steps[i].id}`, row: { ...plain(i), side: (sideOf.get(i) ?? []).map(plain) } });
      k++;
    }
    return out;
  }

  function sections(idx: number[], depth: number): Section[] {
    const out: Section[] = [];
    for (const node of seq(idx, depth, null)) {
      if (node.t === "loop" || node.t === "include") {
        out.push({ key: `sec:${node.key}`, kind: node.t, phase: node.t === "include" ? node.flow : node.loop.id, nodes: [node], ids: idsOf(node) });
        continue;
      }
      const phase = phaseOf[node.row.index];
      const last = out[out.length - 1];
      if (last && last.kind === "phase" && last.phase === phase) { last.nodes.push(node); last.ids.push(...idsOf(node)); }
      else out.push({ key: `sec:${node.key}`, kind: "phase", phase, nodes: [node], ids: idsOf(node) });
    }
    return out;
  }

  function idsOf(nd: Node): string[] {
    if (nd.t === "step") return [nd.row.step.id, ...nd.row.side.map((r) => r.step.id)];
    if (nd.t === "if") {
      const arm = (a: Arm) => (a.t === "nodes" ? a.nodes.flatMap(idsOf) : []);
      return [nd.row.step.id, ...nd.row.side.map((r) => r.step.id), ...arm(nd.yes), ...arm(nd.no)];
    }
    return nd.ids;
  }

  const secs = sections(mainIdx, 0);
  const placed = new Set(secs.flatMap((x) => x.ids));
  const orphans = steps.map((_, i) => i).filter((i) => !placed.has(steps[i].id)).map(plain);

  // Where each step sits, for reveal(): the folds around it.
  const holder = new Map<string, string[]>();
  const walk = (nodes: Node[], keys: string[]) => nodes.forEach((nd) => {
    if (nd.t === "step") {
      holder.set(nd.row.step.id, keys);
      nd.row.side.forEach((r) => holder.set(r.step.id, [...keys, sideKey(nd.row.step.id)]));
    } else if (nd.t === "if") {
      holder.set(nd.row.step.id, keys);
      nd.row.side.forEach((r) => holder.set(r.step.id, [...keys, sideKey(nd.row.step.id)]));
      ([["yes", nd.yes], ["no", nd.no]] as const).forEach(([w, a]) => { if (a.t === "nodes") walk(a.nodes, a.side ? [...keys, armKey(nd.row.step.id, w)] : keys); });
    } else if (nd.t === "loop") walk(nd.body, keys);
    else nd.body.forEach((x) => walk(x.nodes, [...keys, nd.key]));
  });
  secs.forEach((x) => walk(x.nodes, []));

  const parentOf = new Map([...owner.entries()].map(([j, m]) => [steps[j].id, steps[m].id]));
  return { sections: secs, orphans, byId, main: mainIdx.map((i) => steps[i].id), parentOf, phaseOf, containers: (id) => holder.get(id) ?? [], idsOf };
}

// ------------------------------------------------------------------ tokens

/** How many times a loop runs: N criteria for the per-AC loop, the engine's assumed 3 items for a for_each list. */
export const loopTimes = (loop: LoopInfo, acs: number) => (loop.kind === "ac" ? Math.max(acs, 1) : 3);

export const sumOf = (ids: string[], m?: Record<string, number>) => (m ? ids.reduce((a, id) => a + (m[id] ?? 0), 0) : 0);

// ------------------------------------------------------------------ a thread's state per step

export type StepState = "done" | "run" | "wait" | "fail" | "stop" | "skip" | "todo";

/** Done / running / waiting for you / failed / to do, per step id, from the thread's current step and status. */
export function stepStates(steps: Step[], map: FlowMap, current?: string | null, status?: ThreadStatus | null): Record<string, StepState> {
  const out: Record<string, StepState> = {};
  steps.forEach((s) => { out[s.id] = "todo"; });
  if (status === "done") {
    map.main.forEach((id) => { out[id] = "done"; });
    return out;
  }
  // A review step's fix node (`<id>__fix`, added by the engine) shows on its review step.
  const cur = current ? current.replace(/__fix$/, "") : null;
  const ci = cur ? map.byId.get(cur) : undefined;
  if (!cur || ci === undefined) return out;
  const anchor = map.parentOf.get(cur) ?? cur;
  const pos = map.main.indexOf(anchor);
  map.main.forEach((id, k) => { if (k < pos || (k === pos && anchor !== cur)) out[id] = "done"; });
  if (anchor !== cur) {
    // On a side path: the side steps before it in the same fold ran too.
    steps.forEach((s, j) => { if (j < ci && map.parentOf.get(s.id) === anchor) out[s.id] = "done"; });
  }
  const s = steps[ci];
  out[cur] = status === "failed" ? "fail" : status === "stopped" ? "stop" : s.kind === "gate" || status === "waiting" ? "wait" : "run";
  return out;
}

export const STATE_LABEL: Record<StepState, string> = {
  done: "done", run: "running", wait: "waiting for you", fail: "failed", stop: "stopped here", skip: "skipped", todo: "to do",
};

/** Which arm each if / else took in a thread (by the steps that ran), and the skipped steps of an arm not taken. */
export function armsTaken(map: FlowMap, states: Record<string, StepState>, visited?: Set<string>): { taken: Record<string, "yes" | "no">; skipped: Set<string> } {
  const taken: Record<string, "yes" | "no"> = {};
  const skipped = new Set<string>();
  const live = (id: string) => ["run", "wait", "fail", "stop"].includes(states[id]);
  const walk = (nodes: Node[]) => nodes.forEach((nd) => {
    if (nd.t === "loop") return walk(nd.body);
    if (nd.t === "include") return nd.body.forEach((x) => walk(x.nodes));
    if (nd.t !== "if") return;
    const arm = (a: Arm) => (a.t === "nodes" ? a.nodes.flatMap(map.idsOf) : []);
    const yes = arm(nd.yes), no = arm(nd.no);
    const id = nd.row.step.id;
    if (yes.some(live)) taken[id] = "yes";
    else if (no.some(live)) taken[id] = "no";
    else if (states[id] === "done" && visited?.size) {
      if (yes.some((x) => visited.has(x))) taken[id] = "yes";
      else if (no.some((x) => visited.has(x))) taken[id] = "no";
      else if (nd.yes.t === "nodes" && !nd.yes.side) taken[id] = "no";
    }
    if (taken[id] === "no" && nd.yes.t === "nodes" && !nd.yes.side) yes.forEach((x) => { if (states[x] === "done") skipped.add(x); });
    if (nd.yes.t === "nodes") walk(nd.yes.nodes);
    if (nd.no.t === "nodes") walk(nd.no.nodes);
  });
  map.sections.forEach((x) => walk(x.nodes));
  return { taken, skipped };
}
