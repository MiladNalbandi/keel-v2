// Helpers for agent step feeds (Live agents, Jobs). Each step is drawn by StepView.

import type { JobStep } from "../api";

export { StepView } from "./StepView";

export const kindClass = (k: string) => `k-${k}`;

export function mergeSteps(a: JobStep[], b: JobStep[]): JobStep[] {
  const m = new Map<number, JobStep>();
  [...a, ...b].forEach((s) => m.set(s.n, s));
  return [...m.values()].sort((x, y) => x.n - y.n);
}

export const filesTouched = (steps: JobStep[]) =>
  [...new Set(steps.filter((s) => (s.kind === "write" || s.kind === "edit") && s.path).map((s) => s.path!))];
