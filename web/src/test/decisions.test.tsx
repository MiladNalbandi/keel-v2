// keel 0.18.54 "What did I decide in this run?": the Decisions tab lists every gate decision of the flow (step, decision,
// who: you / supervisor / the run mode, the note or the answers, the time, the round) and what the Supervisor said; a
// gate step's panel shows the answers given at that step (keel 0.18.66: its Decided tab, with a count), not only what the
// gate does.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { decisionsOf, logDecision } from "../components/eventRows";
import { db, server } from "./setup";
import { ANSWERS, openShopFlow, SHOP_TID, shopEvent, shopWorkflow } from "./shopFlow";

const nameOf = (id: string) => shopWorkflow.steps.find((s) => s.id === id)?.name;

const stored = () => [
  shopEvent("gate.decided", "interview_gate", { gate: "your answers", decision: "approve", clarify: true, why: ANSWERS }, 10),
  shopEvent("gate.decided", "spec_gate", { gate: "spec approval", decision: "reject", why: "AC-2 misses the empty cart", by: "supervisor" }, 20),
  shopEvent("supervisor.decided", "spec_gate", { decision: "reject", why: "AC-2 misses the empty cart", confidence: "high" }, 21),
  shopEvent("gate.decided", "spec_gate", { gate: "spec approval", decision: "approve", why: "auto-approved (mode auto)" }, 30),
];

describe("decisions of a flow", () => {
  it("are in order with who decided, the round and the answers", () => {
    const items = decisionsOf(stored().reverse(), nameOf);
    expect(items.map((d) => [d.stepName, d.decision.label, d.by, d.round, d.rounds])).toEqual([
      ["your answers", "answered", "you", 1, 1],
      ["spec approval", "sent back", "supervisor", 1, 2],
      ["spec approval", "sent back", "supervisor", 0, 0],
      ["spec approval", "approved", "auto", 2, 2],
    ]);
    expect(items[0].answers?.map((a) => a.a)).toEqual(["Only open carts", "ana and bo (the shop owners)"]);
    expect(items[2].source).toBe("supervisor");
    expect(items[2].note).toBe("AC-2 misses the empty cart");
    expect(items[3].note).toBeUndefined();          // the run mode's line is the "by", not a note
  });

  it("a gate log line reads as a decision when no event is there", () => {
    expect(logDecision("gate spec_gate reject: AC-2 misses the empty cart", nameOf))
      .toMatchObject({ stepName: "spec approval", decision: { label: "sent back" }, note: "AC-2 misses the empty cart", by: "you" });
    expect(logDecision("ac AC-2 approve: auto-approved (mode auto)")).toMatchObject({ gate: "criterion AC-2", by: "auto" });
    expect(logDecision("gate interview_gate answered the explorer's 2 question(s)", nameOf))
      .toMatchObject({ decision: { label: "answered" }, note: "answered the explorer's 2 question(s)" });
    expect(logDecision("something else")).toBeNull();
  });
});

describe("Decisions tab", () => {
  it("lists every decision of the flow, oldest first, with who and when", async () => {
    const user = userEvent.setup();
    openShopFlow();
    db.decisions[SHOP_TID] = stored();
    render(<App />);
    await user.click(await screen.findByRole("tab", { name: "Decisions" }));
    const list = await screen.findByRole("list", { name: "Decisions of this flow, oldest first" });
    const rows = within(list).getAllByTestId("decision");
    expect(rows).toHaveLength(4);
    expect(screen.getByTestId("decisions-sum")).toHaveTextContent("4 decisions: 1 by you, 2 by supervisor, 1 by auto (run mode)");
    expect(within(rows[0]).getByTestId("decision-badge")).toHaveTextContent("answered");
    expect(within(rows[0]).getByText("Which carts keep the discount?")).toBeInTheDocument();
    expect(within(rows[0]).getByText("you")).toBeInTheDocument();
    expect(within(rows[1]).getByTestId("decision-badge")).toHaveClass("p-warn");
    expect(rows[1]).toHaveTextContent("spec approval");
    expect(rows[1]).toHaveTextContent("round 1 of 2");
    expect(rows[1]).toHaveTextContent("by supervisor");
    expect(rows[2]).toHaveTextContent("supervisor · spec approval");
    expect(within(rows[3]).getByTestId("decision-badge")).toHaveClass("p-ok");
    expect(rows[3]).toHaveTextContent("by auto (run mode)");
    expect(rows[3].querySelector("time")).toHaveAttribute("dateTime", "2026-10-17T10:00:30.000Z");
  });

  it("says so when nothing was decided yet", async () => {
    const user = userEvent.setup();
    openShopFlow();
    render(<App />);
    await user.click(await screen.findByRole("tab", { name: "Decisions" }));
    expect(await screen.findByText(/No decision yet/)).toBeInTheDocument();
  });

  it("falls back to the gate log when the decisions cannot be read", async () => {
    const user = userEvent.setup();
    openShopFlow({ gate_log: ["gate spec_gate reject: AC-2 misses the empty cart", "gate spec_gate approve"] });
    server.use(http.get("/api/projects/:pid/flows/:tid/decisions", () => HttpResponse.json({ error: "not here" }, { status: 404 })));
    render(<App />);
    await user.click(await screen.findByRole("tab", { name: "Decisions" }));
    const list = await screen.findByRole("list", { name: "Decisions of this flow, oldest first" });
    expect(within(list).getAllByTestId("decision").map((r) => within(r).getByTestId("decision-badge").textContent)).toEqual(["sent back", "approved"]);
    expect(screen.getByText(/From the flow's gate log/)).toBeInTheDocument();
  });
});

describe("A gate step's panel", () => {
  it("shows the answers given at that step, each round, who and when", async () => {
    const user = userEvent.setup();
    openShopFlow();
    db.decisions[SHOP_TID] = stored();
    render(<App />);
    const block = await screen.findByRole("button", { name: /^wait for you, spec approval, / });
    await user.click(block);
    await user.click(await screen.findByRole("tab", { name: "Decided 3" }));
    const here = await screen.findByTestId("step-decisions");
    expect(here).toHaveTextContent("Decided 2 times · oldest first");
    const rows = within(here).getAllByTestId("decision");
    expect(rows.map((r) => within(r).getByTestId("decision-badge").textContent)).toEqual(["sent back", "sent back", "approved"]);
    expect(rows[0]).toHaveTextContent("by supervisor");
    expect(rows[0]).toHaveTextContent("AC-2 misses the empty cart");
    expect(rows[2]).toHaveTextContent("by auto (run mode)");
    expect(rows[2]).toHaveTextContent("round 2 of 2");
  });

  it("shows the Q → A answers of a clarify gate, or that it was not answered yet", async () => {
    const user = userEvent.setup();
    openShopFlow();
    db.decisions[SHOP_TID] = stored().slice(0, 1);
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^wait for you, your answers, / }));
    await user.click(await screen.findByRole("tab", { name: "Decided 1" }));
    const here = await screen.findByTestId("step-decisions");
    const qa = within(here).getByRole("list", { name: "Your answers" });
    expect(within(qa).getAllByRole("listitem")).toHaveLength(2);
    expect(here).toHaveTextContent("alex reviews the copy.");

    // the Decided tab stays picked for the next step
    await user.click(screen.getByRole("button", { name: /^wait for you, spec approval, / }));
    expect(await screen.findByText("Not answered in this run yet.")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Decided" })).toHaveAttribute("aria-selected", "true");
  });
});
