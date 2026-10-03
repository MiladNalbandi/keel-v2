// Workflow helpers shared by the builder, the wiki and the start drawer.

import type { Estimate, Step, StepKind, Workflow } from "../api";

export const KIND: Record<StepKind, string> = { agent: "Agent", code: "Plain code", gate: "◆ Gate", branch: "Branch", parallel: "Parallel" };

/** Estimated tokens per step id (the estimate names steps by id or by name). */
export function tokensByStep(w: Workflow, est: Estimate | null | undefined): Record<string, number> | undefined {
  if (!est?.per_step) return undefined;
  const m: Record<string, number> = {};
  est.per_step.forEach((p) => {
    const s = w.steps.find((x) => x.id === p.step || x.name === p.step);
    if (s) m[s.id] = (m[s.id] ?? 0) + p.tokens;
  });
  return m;
}

const scalar = (v: unknown): string => {
  if (typeof v === "string") return /^[\w./:@★ -]+$/.test(v) && !/^(true|false|null|yes|no|on|off|\d.*)$/i.test(v) && !/^[\s-]/.test(v) && !v.includes(": ") ? v : JSON.stringify(v);
  return String(v);
};

/** The workflow as YAML, in the shape of CONTRACT.md ("Workflow YAML"). Used for the YAML tab before saving. */
export function toYaml(w: Workflow, budget?: { max_tokens?: number; on_limit?: string }): string {
  const lines = [`name: ${scalar(w.name)}`];
  if (w.based_on) lines.push(`based_on: ${scalar(w.based_on)}`);
  lines.push(`keel_rules: ${w.keel_rules}`);
  if (budget?.max_tokens) lines.push(`budget: { max_tokens: ${budget.max_tokens}, on_limit: ${budget.on_limit ?? "pause"} }`);
  lines.push("steps:");
  const order: (keyof Step)[] = ["id", "kind", "name", "agent", "model", "phase", "action", "per_ac", "parallel", "back", "no", "lock", "max_tokens", "on_limit"];
  w.steps.forEach((s) => {
    const parts = order
      .filter((k) => s[k] !== undefined && s[k] !== null && s[k] !== false && s[k] !== "")
      .map((k) => `${k}: ${scalar(s[k])}`);
    if (s.tools?.length) parts.push(`tools: [${s.tools.map(scalar).join(", ")}]`);
    if (s.lanes?.length) parts.push(`lanes: [${s.lanes.map((l) => `{ name: ${scalar(l.name)}, kind: ${l.kind}${l.sub ? `, sub: ${scalar(l.sub)}` : ""} }`).join(", ")}]`);
    lines.push(`  - { ${parts.join(", ")} }`);
  });
  return lines.join("\n") + "\n";
}

export function newStep(kind: StepKind, id: string): Step {
  switch (kind) {
    case "agent": return { id, kind, name: "new agent step", agent: "explorer", model: "default" };
    case "parallel": return { id, kind, name: "parallel reviewers", agent: "reviewer", model: "default", parallel: 2 };
    case "gate": return { id, kind, name: "approval" };
    case "branch": return { id, kind, name: "tests pass?" };
    case "code": return { id, kind, name: "run command", action: "run:" };
  }
}

export const nextStepId = (steps: Step[]) => {
  let n = steps.length + 1;
  while (steps.some((s) => s.id === `s${n}`)) n++;
  return `s${n}`;
};
