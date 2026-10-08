// v0.4.1 blocks: the workflow as Scratch-style blocks on the Workflows page (edit) and the Flow page (live); the Wiki's
// read-only blocks: plugins/wiki/web/test.
// The model is checked on the real templates (content/workflows/feature, fix, ship, expanded by the engine).

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Node, Section } from "../components/flowmap";
import { App } from "../App";
import type { Workflow } from "../api";
import { Blocks } from "../components/Blocks";
import { moveStepTo } from "../components/builderOps";
import { armsTaken, buildFlowMap, exitsOf, stepStates } from "../components/flowmap";
import { linkSteps } from "../pages/Workflows";
import { fixWorkflow } from "./fixtures";
import featureJson from "./workflows/feature.json";
import fixJson from "./workflows/fix.json";
import shipJson from "./workflows/ship.json";
import { db } from "./setup";

const feature = featureJson as unknown as Workflow;
const fix = fixJson as unknown as Workflow;
const ship = shipJson as unknown as Workflow;
const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);

/** Every node of a section list, depth first (inside loops, arms and included flows too). */
function nodes(secs: Section[]): Node[] {
  const out: Node[] = [];
  const walk = (ns: Node[]) => ns.forEach((n) => {
    out.push(n);
    if (n.t === "loop") walk(n.body);
    if (n.t === "include") n.body.forEach((x) => walk(x.nodes));
    if (n.t === "if") [n.yes, n.no].forEach((a) => { if (a.t === "nodes") walk(a.nodes); });
  });
  secs.forEach((s) => walk(s.nodes));
  return out;
}
const ifOf = (secs: Section[], id: string) => nodes(secs).find((n) => n.t === "if" && n.row.step.id === id) as Extract<Node, { t: "if" }>;
const stepOf = (secs: Section[], id: string) => nodes(secs).find((n) => (n.t === "step" || n.t === "if") && n.row.step.id === id) as Extract<Node, { t: "step" }>;
const armIds = (a: Extract<Node, { t: "if" }>["yes"]) => (a.t === "nodes" ? a.nodes.map((n) => (n.t === "step" || n.t === "if" ? n.row.step.id : n.key)) : []);

describe("flow map model (real templates)", () => {
  const map = buildFlowMap(feature.steps);

  it("groups the main path into sections by phase; the loop and the included ship flow are sections of their own", () => {
    expect(feature.steps.length).toBeGreaterThan(90);
    expect(map.sections.map((s) => (s.kind === "phase" ? s.phase : `${s.kind}:${s.phase}`))).toEqual([
      "preflight", "spec", "contract", "loop:ac", "integration", "security", "review-fix", "gate", "e2e", "smoke", "include:ship", "close",
    ]);
    // every step is placed exactly once (main path, a side path, an arm or a folded flow); none is lost
    expect(new Set(map.sections.flatMap((s) => s.ids)).size).toBe(feature.steps.length);
    expect(map.orphans).toEqual([]);
  });

  it("finds the side paths and hangs them under the step that leads to them", () => {
    expect(map.main).not.toContain("spec_edit");
    expect(stepOf(map.sections, "spec_gate").row.side.map((r) => r.step.id)).toEqual(["spec_edit", "spec_edit_sync", "spec_review", "spec_review_sync", "spec_restart"]);
    // the amendment path is reached first from the contract gate's "amend"
    expect(stepOf(map.sections, "contract_gate").row.side.map((r) => r.step.id)).toEqual(
      ["contract_undo", "amend", "amend_show", "amend_gate", "amend_commit", "amend_contract", "amend_to_contract", "rebuild"]);
    expect(map.parentOf.get("rebuild")).toBe("contract_gate");
    expect(map.containers("amend_gate")).toEqual(["side:contract_gate"]);
  });

  it("folds an included flow into one block, nested includes inside it", () => {
    const ship = nodes(map.sections).find((n) => n.t === "include" && n.flow === "ship") as Extract<Node, { t: "include" }>;
    expect(ship.count).toBe(30);
    expect(ship.ids).toContain("ship_verify");
    const cover = nodes(ship.body).find((n) => n.t === "include") as Extract<Node, { t: "include" }>;
    expect(cover.path).toBe("ship/cover");
    expect(cover.count).toBe(11);
    expect(map.containers("ship_cover_write")).toEqual(["inc:ship", "inc:ship/cover"]);
  });

  it("makes the per-AC steps one loop and a for_each list another", () => {
    const loops = nodes(map.sections).filter((n) => n.t === "loop") as Extract<Node, { t: "loop" }>[];
    expect(loops.map((l) => l.loop.title)).toEqual(["For each criterion", "For each coverage group"]);
    expect(loops[0].ids).toEqual(["red", "red_amend", "red_to_amend", "verify_red", "green", "green_amend", "green_to_amend", "verify_green", "ac_review", "ac_gate"]);
  });

  it("draws a branch as if / else: the steps a no skips sit in the yes arm, a side path in its own arm", () => {
    const e2e = ifOf(map.sections, "e2e_needed");
    expect(e2e.cond).toBe("e2e criteria + tool says E2E = yes");
    expect(armIds(e2e.yes)).toEqual(["e2e_tool", "e2e", "e2e_commit", "e2e_gate"]);
    expect(e2e.no.t).toBe("go");
    const amend = ifOf(map.sections, "red_amend");
    expect(amend.yes.t === "nodes" && amend.yes.side).toBe(true);
    expect(armIds(amend.yes)).toEqual(["red_to_amend"]);
    // fix: "reproduced" goes on when yes; the hand-off to diagnose is the else arm
    const fixMap = buildFlowMap(fix.steps);
    const rep = ifOf(fixMap.sections, "reproduced");
    expect(rep.yes.t).toBe("go");
    expect(armIds(rep.no)).toEqual(["to_diagnose"]);
    // a branch whose "no" loops back stays one block, its "no" a chip that goes back
    const covered = stepOf(buildFlowMap(ship.steps).sections, "cover_covered");
    expect(covered.t).toBe("step");
    expect(covered.row.exits.find((e) => e.kind === "no")).toMatchObject({ back: true, toName: "decide test / delete / accept" });
  });

  it("names each exit's target, and marks the ones that go back", () => {
    const byId = new Map(feature.steps.map((s, i) => [s.id, i]));
    const gate = exitsOf(feature.steps, byId.get("spec_gate")!, byId);
    expect(gate.map((e) => `${e.label}${e.back ? " ↩ " : " → "}${e.toName}`)).toEqual([
      "approve → freeze + commit the spec", "edit → edit the spec", "rewrite → edit the spec", "review → review the spec before approving",
      "order ↩ plan under the criteria", "reject → the interview starts again", "send back ↩ spec",
    ]);
    const close = exitsOf(feature.steps, byId.get("close")!, byId);
    expect(close).toEqual([{ kind: "then", label: "then", to: "end", toName: "end of the flow", back: false }]);
    const fixById = new Map(fix.steps.map((s, i) => [s.id, i]));
    expect(exitsOf(fix.steps, fixById.get("verify_fix")!, fixById).map((e) => `${e.label} ${e.toName}`)).toEqual(["after 1 round reset and reproduce again"]);
  });

  it("gives each step its state in a thread; an arm not taken is skipped", () => {
    const st = stepStates(feature.steps, map, "ac_gate", "waiting");
    expect(st.spec).toBe("done");
    expect(st.red).toBe("done");
    expect(st.ac_gate).toBe("wait");
    expect(st.integration).toBe("todo");
    expect(st.spec_edit).toBe("todo");          // a side path did not run
    expect(stepStates(feature.steps, map, "green", "running").green).toBe("run");
    expect(stepStates(feature.steps, map, "green", "failed").green).toBe("fail");
    expect(stepStates(feature.steps, map, null, "done").close).toBe("done");
    // smoke_scope is current: e2e_needed answered no (none of its arm ran), so those steps were skipped
    const at = stepStates(feature.steps, map, "smoke_scope", "running");
    const visited = new Set(["e2e_scope", "e2e_needed"]);
    const { taken, skipped } = armsTaken(map, at, visited);
    expect(taken.e2e_needed).toBe("no");
    expect([...skipped]).toEqual(["e2e_tool", "e2e", "e2e_commit", "e2e_gate"]);
  });

  it("moves a dragged step into a loop's mouth (it joins the loop) or out of it (it leaves)", () => {
    const w = { id: "w", name: "w", keel_rules: false, version: 1, yaml: "", steps: [
      { id: "a", kind: "agent", name: "a" }, { id: "b", kind: "agent", name: "b", per_ac: true }, { id: "c", kind: "agent", name: "c", per_ac: true },
      { id: "d", kind: "agent", name: "d" }] } as Workflow;
    const into = moveStepTo(w, "d", 1, "ac");
    expect(into.steps.map((s) => `${s.id}${s.per_ac ? "*" : ""}`)).toEqual(["a", "b*", "d*", "c*"]);
    const out = moveStepTo(w, "b", 3, null);
    expect(out.steps.map((s) => `${s.id}${s.per_ac ? "*" : ""}`)).toEqual(["a", "c*", "d", "b"]);
  });

  it("finds the steps a save error names", () => {
    expect(linkSteps("Step 'f3': an agent step needs an agent.", fixWorkflow.steps)).toEqual(["Step '", { id: "f3", text: "f3" }, "': an agent step needs an agent."]);
    expect(linkSteps("Locked steps cannot be removed while keel rules are on: gate R, verify + commit", fixWorkflow.steps)).toEqual(
      ["Locked steps cannot be removed while keel rules are on: ", { id: "f2", text: "gate R" }, ", ", { id: "f5", text: "verify + commit" }]);
  });
});

describe("Blocks (read only and live)", () => {
  it("renders 90+ steps with the ship flow folded; a click opens it in place", async () => {
    const user = userEvent.setup();
    const t0 = performance.now();
    render(<Blocks steps={feature.steps} onOpenStep={() => {}} />);
    expect(performance.now() - t0).toBeLessThan(1500);
    expect(screen.queryByText("verify fast + module", { selector: ".sb-name" })).not.toBeInTheDocument();
    const ship = screen.getByRole("button", { name: /^ship: 30 blocks from the ship workflow/ });
    expect(ship).toHaveAttribute("aria-expanded", "false");
    await user.click(ship);
    expect(screen.getByText("verify fast + module", { selector: ".sb-name" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^cover: 11 blocks/ })).toBeInTheDocument();
  });

  it("shows full names, gate exits as chips naming their targets, and a chip jumps to its target (opening its fold)", async () => {
    const user = userEvent.setup();
    render(<Blocks steps={feature.steps} onOpenStep={() => {}} />);
    expect(screen.getByText("fix the full-diff review's findings", { selector: ".sb-name" })).toBeInTheDocument();
    const gate = document.querySelector('[data-block="spec_gate"]') as HTMLElement;
    expect(within(gate).getByRole("button", { name: "approve: goes to freeze + commit the spec. Show that step." })).toBeInTheDocument();
    expect(within(gate).getByRole("button", { name: "send back: back to spec. Show that step." })).toBeInTheDocument();
    expect(screen.queryByText("edit the spec", { selector: ".sb-name" })).not.toBeInTheDocument();   // folded
    await user.click(within(gate).getByRole("button", { name: "edit: goes to edit the spec. Show that step." }));
    const target = document.querySelector('[data-block="spec_edit"] .sb-main') as HTMLElement;
    expect(target).toBeInTheDocument();
    expect(target).toHaveFocus();
  });

  it("a side path is folded under its block and opens", async () => {
    const user = userEvent.setup();
    render(<Blocks steps={feature.steps} onOpenStep={() => {}} />);
    const fold = within(document.querySelector('[data-block="spec_gate"]') as HTMLElement).getByRole("button", { name: /side path · 5 blocks/ });
    expect(fold).toHaveAttribute("aria-expanded", "false");
    await user.click(fold);
    expect(screen.getByText("review the spec before approving", { selector: ".sb-name" })).toBeInTheDocument();
  });

  it("the loop wraps its steps, says × N, and shows per round and total tokens; each block shows its estimate", () => {
    const tokens = { red: 84000, green: 156000, spec: 46000 };
    render(<Blocks steps={feature.steps} tokens={tokens} acCount={3} onOpenStep={() => {}} />);
    const loop = document.querySelector('[data-loop="ac"]') as HTMLElement;
    expect(within(loop).getByText("× 3")).toBeInTheDocument();
    expect(within(loop).getByText("≈ 80k each")).toBeInTheDocument();
    expect(within(loop).getByText("in all", { exact: false })).toHaveTextContent("≈ 240k in all");
    expect(within(loop).getByRole("button", { name: /^red, agent/ })).toHaveAccessibleName(/about 28k tokens per round/);
    expect(within(document.querySelector('[data-block="spec"]') as HTMLElement).getByText("≈ 46k")).toBeInTheDocument();
    expect(screen.getByLabelText("Tokens for the whole workflow")).toHaveTextContent("≈ 286k tokens");
  });

  it("live: each block has its state, the loop shows the criteria with the current one, real tokens next to the estimate", () => {
    render(<Blocks steps={feature.steps} current="ac_gate" status="waiting" tokens={{ green: 156000 }} actual={{ green: 111000 }} acCount={3}
      acs={[{ id: "AC-001", status: "done" }, { id: "AC-002", status: "green" }, { id: "AC-003", status: "todo" }]} currentAc="AC-002" onOpenStep={() => {}} />);
    const state = (id: string) => document.querySelector(`[data-block="${id}"]`)?.getAttribute("data-state");
    expect(state("spec")).toBe("done");
    expect(state("ac_gate")).toBe("wait");
    expect(state("integration")).toBe("todo");
    expect(within(document.querySelector('[data-block="ac_gate"]') as HTMLElement).getByText("waiting for you")).toBeInTheDocument();
    const chips = screen.getByLabelText("Criteria in this loop");
    expect(chips.querySelector('[data-ac="AC-002"]')).toHaveClass("cur");
    expect(screen.getByText("round 2 of 3")).toBeInTheDocument();
    expect(within(document.querySelector('[data-block="green"]') as HTMLElement).getByTitle(/111k tokens used so far/)).toHaveTextContent("111k / ≈52k");
  });
});

/** The ids of the blocks on the canvas, in order. */
const blockIds = () => [...document.querySelectorAll(".wf-canvas [data-block]")].map((e) => e.getAttribute("data-block"));

describe("Workflows page (builder)", () => {
  it("lists the workflows, shows the blocks with a palette, and Table / Graph / YAML views", async () => {
    const user = userEvent.setup();
    location.hash = "#/workflows/fix";
    render(<App />);
    const rail = await screen.findByRole("navigation", { name: "Workflows" });
    expect(within(rail).getByRole("button", { name: /feature \(keel\)/ })).toBeInTheDocument();
    expect(await screen.findByRole("group", { name: "Blocks to add" })).toBeInTheDocument();
    await waitFor(() => expect(blockIds()).toEqual(["f1", "f2", "f3", "f4", "f5"]));
    await user.click(screen.getByRole("tab", { name: "Table" }));
    expect(screen.getByRole("table", { name: "Steps as a table" })).toHaveTextContent("bug-investigate");
    await user.click(screen.getByRole("tab", { name: "Graph" }));
    expect(screen.getByTestId("graph")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Blocks" }));
    await user.click(within(rail).getByRole("button", { name: /feature \(keel\)/ }));
    expect(location.hash).toBe("#/workflows/feature");
  });

  it("+ between blocks adds one; the new block is selected and its editor opens next to the blocks", async () => {
    const user = userEvent.setup();
    location.hash = "#/workflows/fix";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Add a block after bug-repro" }));
    await user.click(within(screen.getByRole("group", { name: "Add a block after bug-repro" })).getByRole("button", { name: /Wait for you/ }));
    expect(blockIds()).toEqual(["f1", "s6", "f2", "f3", "f4", "f5"]);
    const editor = screen.getByRole("region", { name: "Step" });
    expect(within(editor).getByLabelText("Name")).toHaveValue("approval");
    expect(screen.getByText("unsaved changes")).toBeInTheDocument();
  });

  it("removes a block and undoes it; a keel rule asks first", async () => {
    const user = userEvent.setup();
    location.hash = "#/workflows/fix";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Remove bug-repro" }));
    expect(blockIds()).toEqual(["f2", "f3", "f4", "f5"]);
    expect(screen.getByRole("status")).toHaveTextContent("Removed bug-repro. Arrows were reconnected.");
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(blockIds()).toEqual(["f1", "f2", "f3", "f4", "f5"]);
    expect(within(document.querySelector('[data-block="f2"]') as HTMLElement).getByRole("button", { name: /send back: back to bug-repro/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "gate R is a keel rule" }));
    expect(screen.getByRole("alert")).toHaveTextContent("gate R is a keel rule: a person must approve here");
    await user.click(screen.getByRole("button", { name: "Turn rules off and remove" }));
    expect(blockIds()).toEqual(["f1", "f3", "f4", "f5"]);
    expect(screen.getByRole("checkbox", { name: /keel rules off/ })).not.toBeChecked();
  });

  it("moves blocks with the buttons and with Alt + arrow keys; Delete removes", async () => {
    const user = userEvent.setup();
    location.hash = "#/workflows/fix";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Move bug-investigate later" }));
    expect(blockIds()).toEqual(["f1", "f2", "f4", "f3", "f5"]);
    const main = screen.getByRole("button", { name: /^bug-investigate, agent/ });
    main.focus();
    await user.keyboard("{Alt>}{ArrowUp}{/Alt}");
    expect(blockIds()).toEqual(["f1", "f2", "f3", "f4", "f5"]);
    await waitFor(() => expect(screen.getByRole("button", { name: /^bug-investigate, agent/ })).toHaveFocus());
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("button", { name: /^bug-fix, agent/ })).toHaveFocus();
    await user.keyboard("{Delete}");
    expect(blockIds()).toEqual(["f1", "f2", "f3", "f5"]);
  });

  it("the palette adds a block after the selected one, and a block dragged from it lands where it is dropped", async () => {
    const user = userEvent.setup();
    location.hash = "#/workflows/fix";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^bug-investigate, agent/ }));
    await user.click(screen.getByRole("button", { name: "Add Code block after bug-investigate" }));
    expect(blockIds()).toEqual(["f1", "f2", "f3", "s6", "f4", "f5"]);
    // drag "If / else" from the palette onto the gap after bug-repro
    const data = new Map<string, string>();
    const dataTransfer = { setData: (k: string, v: string) => data.set(k, v), getData: (k: string) => data.get(k) ?? "", effectAllowed: "", dropEffect: "" };
    fireEvent.dragStart(screen.getByRole("button", { name: /^Add If \/ else block/ }), { dataTransfer });
    const gap = document.querySelector('.wf-canvas [data-gap="0"]') as HTMLElement;
    fireEvent.dragOver(gap, { dataTransfer });
    fireEvent.drop(gap, { dataTransfer });
    expect(blockIds()[1]).toBe("s7");
    expect(screen.getByRole("region", { name: "Step" })).toHaveTextContent("Branch");
  });

  it("selecting a block opens its editor; edits show in the block; What it does explains the draft", async () => {
    const user = userEvent.setup();
    location.hash = "#/workflows/feature";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^red, agent/ }));
    const editor = screen.getByRole("region", { name: "Step" });
    await user.type(within(editor).getByLabelText("Name"), " tests");
    expect(screen.getByRole("button", { name: /^red tests, agent/ })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "What it does" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/workflows/explain-step").at(-1)?.body).toMatchObject({ step_id: "s3", workflow: { id: "feature" } }));
    expect(await screen.findByRole("region", { name: "Task" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Estimate" }));
    expect(await screen.findByLabelText("Tokens per section", undefined, { timeout: 3000 })).toBeInTheDocument();
  });

  it("a save error names the step as a link that selects it", async () => {
    const user = userEvent.setup();
    location.hash = "#/workflows/fix";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Move bug-fix later" }));
    db.yamlErrors = ["Step 'f3': an agent step needs an agent."];
    await user.click(screen.getByRole("button", { name: "Save" }));
    const err = await screen.findByRole("alert");
    expect(err).toHaveTextContent("The workflow YAML is not valid.");
    await user.click(within(err).getByRole("button", { name: "Show step bug-investigate" }));
    expect(within(screen.getByRole("region", { name: "Step" })).getByLabelText("Name")).toHaveValue("bug-investigate");
    expect(screen.getByRole("button", { name: /^bug-investigate, agent/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("on a narrow screen the block editor opens in a drawer", async () => {
    const user = userEvent.setup();
    const had = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: q.includes("max-width: 1099px"), media: q, addEventListener() {}, removeEventListener() {}, onchange: null })) as unknown as typeof window.matchMedia;
    try {
      location.hash = "#/workflows/fix";
      render(<App />);
      await user.click(await screen.findByRole("button", { name: /^bug-fix, agent/ }));
      const dlg = await screen.findByRole("dialog", { name: "Block: bug-fix" });
      expect(within(dlg).getByLabelText("Name")).toHaveValue("bug-fix");
    } finally {
      window.matchMedia = had;
    }
  });
});

describe("Flow page (live blocks)", () => {
  it("the waiting gate's card sits under its block; the bar shows tokens, cost and Jump to current", async () => {
    render(<App />);
    await screen.findByRole("region", { name: "Gate waits for you" });
    const block = document.querySelector('[data-block="s8"]') as HTMLElement;
    expect(block).toHaveAttribute("data-state", "wait");
    expect(within(block).getByRole("region", { name: "Gate waits for you" })).toBeInTheDocument();
    expect(within(block).getByRole("button", { name: "Approve AC-002" })).toBeInTheDocument();
    const bar = screen.getByRole("region", { name: "This flow" });
    expect(bar).toHaveTextContent("182k");
    expect(bar).toHaveTextContent("$2.86");
    expect(within(bar).getByRole("button", { name: "Jump to current" })).toBeInTheDocument();
    // real tokens per step from the thread's jobs: green used 37k
    await waitFor(() => expect(within(document.querySelector('[data-block="s5"]') as HTMLElement).getByTitle(/37k tokens used so far/)).toBeInTheDocument());
  });

  it("Go to it shows the answer card under the waiting block and focuses its first button", async () => {
    // Real bug: in a narrow window the side panel sits under the canvas; "Go to it" scrolled only inside the
    // canvas box (off screen), so nothing seemed to happen.
    const user = userEvent.setup();
    const seen: Element[] = [];
    const had = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) { seen.push(this); };
    try {
      render(<App />);
      await screen.findByRole("region", { name: "Gate waits for you" });
      await user.click(await screen.findByRole("button", { name: "Go to it" }));
      const card = screen.getByRole("region", { name: "Gate waits for you" });
      await waitFor(() => expect(seen).toContain(card));
      expect(document.activeElement && card.contains(document.activeElement)).toBe(true);
    } finally {
      Element.prototype.scrollIntoView = had;
    }
  });

  it("a click on a block shows what it does in the side panel (with the workflow and the thread)", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^red, agent/ }));
    const panel = await screen.findByRole("region", { name: "What red does" });
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/workflows/explain-step").at(-1)?.body)
      .toMatchObject({ step_id: "s3", thread_id: "th_7f3a", workflow: { id: "feature" } }));
    expect(await within(panel).findByRole("region", { name: "Task" })).toBeInTheDocument();
  });
});

