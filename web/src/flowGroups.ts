// 0.34 the flows in groups, for every picker (the task card's "keel suggests", Start flow): each workflow's `group:`
// (build, fix, check, plan, ship, product, support), in keel's own order inside a group; a flow with no group keel knows
// is one of "Your flows". A flow with `start: own-page` starts only from its own page (the Org's runner, Goals, Desk,
// Answers, CI): pickers leave it out.
import type { Workflow } from "./api";

export type FlowGroup = { id: string; title: string; flows: Workflow[] };

/** The groups in the order pickers show them. */
export const FLOW_GROUPS: { id: string; title: string }[] = [
  { id: "build", title: "Build" },
  { id: "fix", title: "Fix" },
  { id: "check", title: "Check" },
  { id: "plan", title: "Plan" },
  { id: "ship", title: "Ship & set up" },
  { id: "product", title: "keel Product" },
  { id: "support", title: "Support" },
];
export const YOURS = { id: "yours", title: "Your flows" };

/** Flows a picker offers: not the ones only their own page starts (the one [keep] names stays, as it is chosen already). */
export function pickable(wfs: Workflow[] | null | undefined, keep?: string): Workflow[] {
  return (wfs ?? []).filter((w) => w.start !== "own-page" || w.id === keep);
}

function groupOf(w: Workflow): string {
  return w.group && FLOW_GROUPS.some((g) => g.id === w.group) ? w.group : YOURS.id;
}

/**
 * The flows by group, in [FLOW_GROUPS] order then "Your flows"; empty groups are left out. [pick] (keel's pick) and
 * [near] (up to [n] more of the pick's group, in keel's order) are what a card shows before "More flows".
 */
export function groupFlows(wfs: Workflow[] | null | undefined, pick?: string, n = 3): {
  pick: Workflow | null;
  near: Workflow[];
  groups: FlowGroup[];
} {
  const list = pickable(wfs, pick);
  const chosen = list.find((w) => w.id === pick) ?? null;
  const near = chosen ? list.filter((w) => w.id !== chosen.id && groupOf(w) === groupOf(chosen)).slice(0, n) : [];
  const groups = [...FLOW_GROUPS, YOURS]
    .map((g) => ({ ...g, flows: list.filter((w) => groupOf(w) === g.id) }))
    .filter((g) => g.flows.length > 0);
  return { pick: chosen, near, groups };
}
