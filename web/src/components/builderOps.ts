// Pure edit operations for the workflow builder (diagram + steps table). Each returns a new workflow.

import type { Step, StepKind, Workflow } from "../api";
import { newStep, nextStepId } from "./workflow";

export type Removed = { step: Step; index: number; links: { id: string; field: "back" | "no" }[] };

/** Insert a new step after `afterIndex` (-1 = at the start). Inside the per-AC loop it joins the loop. */
export function insertStep(w: Workflow, afterIndex: number, kind: StepKind): { w: Workflow; id: string } {
  const id = nextStepId(w.steps);
  const s = newStep(kind, id);
  const prev = w.steps[afterIndex];
  const next = w.steps[afterIndex + 1];
  if (prev?.per_ac && next?.per_ac) s.per_ac = true;
  const steps = [...w.steps];
  steps.splice(afterIndex + 1, 0, s);
  return { w: { ...w, steps }, id };
}

/** Remove a step. A locked step cannot go while keel rules are on: `{ locked: true }`. */
export function removeStep(w: Workflow, id: string): { w: Workflow; removed: Removed } | { locked: true } | null {
  const index = w.steps.findIndex((s) => s.id === id);
  if (index < 0) return null;
  const step = w.steps[index];
  if (step.lock && w.keel_rules) return { locked: true };
  const links: Removed["links"] = [];
  const steps = w.steps.filter((s) => s.id !== id).map((s) => {
    if (s.back === id || s.no === id) {
      const c = { ...s };
      if (c.back === id) { links.push({ id: s.id, field: "back" }); delete c.back; }
      if (c.no === id) { links.push({ id: s.id, field: "no" }); delete c.no; }
      return c;
    }
    return s;
  });
  return { w: { ...w, steps }, removed: { step, index, links } };
}

export function undoRemove(w: Workflow, r: Removed): Workflow {
  const steps = w.steps.map((s) => {
    const l = r.links.filter((x) => x.id === s.id);
    if (!l.length) return s;
    const c = { ...s };
    l.forEach((x) => { c[x.field] = r.step.id; });
    return c;
  });
  steps.splice(Math.min(r.index, steps.length), 0, r.step);
  return { ...w, steps };
}

export function moveStep(w: Workflow, id: string, d: -1 | 1): Workflow {
  const i = w.steps.findIndex((s) => s.id === id);
  const j = i + d;
  if (i < 0 || j < 0 || j >= w.steps.length) return w;
  const steps = [...w.steps];
  const [s] = steps.splice(i, 1);
  steps.splice(j, 0, s);
  return { ...w, steps };
}

export function updateStep(w: Workflow, id: string, patch: Partial<Step>): Workflow {
  return {
    ...w,
    steps: w.steps.map((s) => {
      if (s.id !== id) return s;
      const c: Step = { ...s, ...patch };
      (Object.keys(patch) as (keyof Step)[]).forEach((k) => {
        if (patch[k] === undefined || patch[k] === "") delete c[k];
      });
      return c;
    }),
  };
}

export const clone = (w: Workflow): Workflow => JSON.parse(JSON.stringify(w));
export const same = (a: Workflow | null, b: Workflow | null) => JSON.stringify(a) === JSON.stringify(b);
