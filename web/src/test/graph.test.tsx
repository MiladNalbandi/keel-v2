import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Workflow } from "../api";
import { Builder, useBuilder } from "../components/Builder";
import { Graph } from "../components/Graph";
import { featureWorkflow, fixWorkflow } from "./fixtures";

const stateOf = (id: string) => document.querySelector(`[data-step="${id}"]`)?.getAttribute("data-state");

describe("Graph", () => {
  it("marks steps done, waiting and todo from the thread", () => {
    render(<Graph steps={featureWorkflow.steps} current="s8" status="waiting" />);
    expect(stateOf("s1")).toBe("done");
    expect(stateOf("s7")).toBe("done");
    expect(stateOf("s8")).toBe("wait");
    expect(stateOf("s9")).toBe("todo");
    expect(screen.getByRole("img", { name: /now at AC gate/ })).toBeInTheDocument();
    expect(screen.getByText("next AC")).toBeInTheDocument(); // per-AC loop
    expect(screen.getAllByText("send back").length).toBe(2); // gate connections
  });

  it("shows a running agent step and a finished flow", () => {
    const { rerender } = render(<Graph steps={fixWorkflow.steps} current="f3" status="running" />);
    expect(stateOf("f3")).toBe("run");
    rerender(<Graph steps={fixWorkflow.steps} current="f5" status="done" />);
    expect(stateOf("f1")).toBe("done");
    expect(stateOf("f5")).toBe("done");
  });

  it("draws lanes and parallel copies", () => {
    render(<Graph steps={[{ id: "a", kind: "parallel", name: "both", parallel: 3, lanes: [{ name: "ladder", kind: "code" }, { name: "knowledge", kind: "agent" }] }]} />);
    expect(screen.getByText("at the same time")).toBeInTheDocument();
    expect(screen.getByText("ladder")).toBeInTheDocument();
  });
});

function Harness({ start }: { start: Workflow }) {
  const [w, setW] = useState(start);
  const [sel, setSel] = useState<string | null>(null);
  const b = useBuilder(w, setW, setSel);
  return (
    <>
      <Builder w={w} sel={sel} onSelect={setSel} b={b} />
      <ol data-testid="order">{w.steps.map((s) => <li key={s.id}>{s.name}</li>)}</ol>
      <span data-testid="rules">{String(w.keel_rules)}</span>
    </>
  );
}
const order = () => [...screen.getByTestId("order").querySelectorAll("li")].map((l) => l.textContent);

describe("Builder", () => {
  it("inserts a step with + on an arrow", async () => {
    const user = userEvent.setup();
    render(<Harness start={fixWorkflow} />);
    await user.click(screen.getByRole("button", { name: "Insert a step after bug-repro" }));
    expect(screen.getByText(/Insert after/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Gate" }));
    expect(order()).toEqual(["bug-repro", "approval", "gate R", "bug-investigate", "bug-fix", "verify + commit"]);
  });

  it("removes a step with × and undoes it", async () => {
    const user = userEvent.setup();
    render(<Harness start={fixWorkflow} />);
    await user.click(screen.getByRole("button", { name: "Remove bug-repro" }));
    expect(order()).not.toContain("bug-repro");
    expect(screen.getByText(/Arrows were reconnected/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(order()[0]).toBe("bug-repro");
    // the gate's "send back" link came back too
    expect(screen.getAllByText("send back").length).toBe(1);
  });

  it("asks before removing a keel rule, and can turn rules off", async () => {
    const user = userEvent.setup();
    render(<Harness start={fixWorkflow} />);
    await user.click(screen.getByRole("button", { name: "gate R is a keel rule" }));
    expect(screen.getByRole("alert")).toHaveTextContent("gate R is a keel rule: a person must approve here");
    expect(order()).toContain("gate R");
    await user.click(screen.getByRole("button", { name: "Turn rules off and remove" }));
    expect(order()).not.toContain("gate R");
    expect(screen.getByTestId("rules")).toHaveTextContent("false");
  });
});

describe("Zoom", () => {
  it("zooms with the buttons and Ctrl + wheel, remembers it, and Fit resets", async () => {
    const { Zoom } = await import("../components/Zoom");
    localStorage.removeItem("keel2.zoom.t");
    const { unmount } = render(<Zoom id="t"><Graph steps={featureWorkflow.steps} /></Zoom>);
    expect(screen.getByText("100%")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByText("125%")).toBeInTheDocument();
    expect(screen.getByTestId("zoom-t").style.getPropertyValue("--zoom")).toBe("1.25");
    expect(localStorage.getItem("keel2.zoom.t")).toBe("1.25");
    screen.getByTestId("zoom-t").dispatchEvent(new WheelEvent("wheel", { deltaY: 200, ctrlKey: true, bubbles: true, cancelable: true }));
    expect(await screen.findByText(/^\d+%$/)).not.toHaveTextContent("125%");
    unmount();
    render(<Zoom id="t"><Graph steps={featureWorkflow.steps} /></Zoom>);   // remembered per place
    expect(screen.queryByText("100%")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Fit to width" }));
    expect(screen.getByText("100%")).toBeInTheDocument();
    for (let i = 0; i < 8; i++) await userEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(screen.getByText("40%")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zoom out" })).toBeDisabled();
  });
});

describe("toYaml", () => {
  it("keeps every key a step has, not only the ones the builder edits", async () => {
    // Real bug: saving any edit of a v0.4 template dropped then/flow/seed/when/choices and the engine refused it (422).
    const { toYaml } = await import("../components/workflow");
    const steps = [
      { id: "keep", kind: "code", name: "stop here", phase: "gate-r", then: "end" },
      { id: "hand", kind: "code", name: "hand to diagnose", action: "start_flow", flow: "diagnose", then: "end",
        seed: { title: "$title", symptoms: ["$request"] } },
      { id: "g", kind: "gate", name: "gate R", choices: { investigate: "hyp", stop: "keep" }, when: { data: "x" } },
      { id: "a", kind: "agent", name: "fix", agent: "implementer", instructions: "Fix it: one line, no new deps.", for_each: "groups" },
    ] as unknown as Workflow["steps"];
    const y = toYaml({ id: "w", name: "w", keel_rules: true, version: 1, steps, yaml: "" });
    for (const want of ["then: end", "flow: diagnose", 'seed: {"title":"$title","symptoms":["$request"]}',
      'choices: {"investigate":"hyp","stop":"keep"}', 'when: {"data":"x"}', 'instructions: "Fix it: one line, no new deps."', "for_each: groups"]) {
      expect(y).toContain(want);
    }
  });
});
