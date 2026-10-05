// v0.4.1 "What this step does": a click (or Enter) on a block of the Flow page (its side panel) and the Wiki (the drawer),
// the Wiki's table, and the builder's "What it does" tab show Task / Rules / Next / Last run; a click on a graph node
// never pans.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { Graph } from "../components/Graph";
import { Zoom } from "../components/Zoom";
import { featureWorkflow } from "./fixtures";
import { db } from "./setup";

const explains = () => db.calls.filter((c) => c.method === "POST" && c.path === "/api/projects/ludus-engine/workflows/explain-step");

describe("What this step does", () => {
  it("opens from a Flow block with the thread: the real prompt, the rules, next and the last run", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^red, agent/ }));
    // wide screens: the side panel shows it (narrow ones: the drawer)
    const dlg = await screen.findByRole("region", { name: "What red does" });
    // The drawer explains the workflow the page draws, plus the thread: a thread started on an older template has fewer
    // steps, and asking with the thread alone said "No step 'review_fix' in workflow feature" for a step on screen.
    await waitFor(() => expect(explains().at(-1)?.body).toMatchObject({ step_id: "s3", thread_id: "th_7f3a", workflow: { id: "feature" } }));
    const task = await within(dlg).findByRole("region", { name: "Task" });
    expect(within(task).getByText("test-author")).toBeInTheDocument();
    expect(within(task).getByTestId("explain-prompt")).toHaveTextContent("Current criterion: AC-1 [API] main case");
    const rules = within(dlg).getByRole("region", { name: "Rules" });
    const row = (b: string) => rules.querySelector(`tr[data-bucket="${b}"]`) as HTMLElement;
    expect(within(row("api-test")).getByText("✓")).toBeInTheDocument();
    expect(within(row("api-main")).getByText("✗")).toBeInTheDocument();
    expect(within(row("protected-env")).getByText("✗ never")).toBeInTheDocument();
    expect(within(rules).getByText(/git commit: the engine/)).toBeInTheDocument();
    expect(within(dlg).getByRole("region", { name: "Next" })).toHaveTextContent("next → green");
    const last = within(dlg).getByRole("region", { name: "Last run" });
    expect(within(last).getByText(/test\(AC-1\): main case/)).toBeInTheDocument();
    expect(within(last).getByText("RED: tests/test_ac_1.py asserts AC-1.")).toBeInTheDocument();
    await user.click(within(dlg).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("region", { name: "What red does" })).not.toBeInTheDocument();
  });

  it("blocks are keyboard buttons: Enter opens a code step, in words", async () => {
    const user = userEvent.setup();
    render(<App />);
    const node = await screen.findByRole("button", { name: /^verify_red, plain code/ });
    node.focus();
    await user.keyboard("{Enter}");
    const dlg = await screen.findByRole("region", { name: "What commit contract does" });
    const task = await within(dlg).findByRole("region", { name: "Task" });
    expect(within(task).getByText("Looks for secrets in the staged diff.")).toBeInTheDocument();
    expect(within(task).getByText("In phase contract this is a contract commit.")).toBeInTheDocument();
  });

  it("a press on a node does not start a pan, a press on the empty graph does", () => {
    // jsdom has no PointerEvent: a MouseEvent carries the coordinates the pan reads.
    const had = window.PointerEvent;
    window.PointerEvent = class extends MouseEvent {} as unknown as typeof PointerEvent;
    render(<Zoom id="x"><Graph steps={featureWorkflow.steps} onSelect={() => {}} /></Zoom>);
    const box = screen.getByTestId("zoom-x");
    box.scrollLeft = 50;
    const node = screen.getByRole("button", { name: "What red does" });
    fireEvent.pointerDown(node, { button: 0, clientX: 100, clientY: 10 });
    fireEvent.pointerMove(box, { clientX: 40, clientY: 10 });
    expect(box.scrollLeft).toBe(50);
    fireEvent.pointerUp(box);
    fireEvent.pointerDown(screen.getByTestId("graph"), { button: 0, clientX: 100, clientY: 10 });
    fireEvent.pointerMove(box, { clientX: 40, clientY: 10 });
    expect(box.scrollLeft).toBe(110);
    window.PointerEvent = had;
  });

  it("opens from the Wiki's workflow page: the blocks and the table, with placeholders", async () => {
    const user = userEvent.setup();
    location.hash = "#/wiki/wf%3Afeature";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^red, agent/ }));
    const dlg = await screen.findByRole("dialog", { name: "What red does" });
    await waitFor(() => expect(explains().at(-1)?.body).toEqual({ step_id: "s3", workflow_id: "feature" }));
    expect(await within(dlg).findByText(/with «placeholders»/)).toBeInTheDocument();
    expect(within(dlg).queryByRole("region", { name: "Last run" })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Table" }));
    await user.click(screen.getByRole("button", { name: "verify_red" }));
    await waitFor(() => expect(explains().at(-1)?.body).toEqual({ step_id: "s4", workflow_id: "feature" }));
  });

  it("the builder's What it does explains the draft and editing still works", async () => {
    const user = userEvent.setup();
    location.hash = "#/workflows/feature";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^red, agent/ }));
    await user.type(await screen.findByLabelText("Name"), " tests");
    await user.click(screen.getByRole("tab", { name: "What it does" }));
    await screen.findByRole("region", { name: "Task" });
    await waitFor(() => expect(explains().at(-1)?.body).toMatchObject({ step_id: "s3", workflow: { id: "feature" } }));
    const sent = explains().at(-1)!.body as { workflow: { steps: { id: string; name: string }[] } };
    expect(sent.workflow.steps.find((s) => s.id === "s3")?.name).toBe("red tests");
    await user.click(screen.getByRole("tab", { name: "Block" }));
    expect(screen.getByText("unsaved changes")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("red tests");
  });
});
