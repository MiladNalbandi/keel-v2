// keel 0.28 the Flow page's redesign: the steps as a rail you can fold, the work as a feed (agent turns, tool calls one
// line each that open the full step), the gate card in the feed with its keys (A, R, O), the line after an answer with
// the way back (the checkpoints), and one Details drawer that D folds; G jumps to the current step, J / K follow a step.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { App } from "../App";
import * as fx from "./fixtures";
import { db, server } from "./setup";

const feed = () => screen.findByRole("region", { name: "What happens" });

describe("Flow page: the feed (keel 0.28)", () => {
  it("shows one turn per agent call: who, the step, and its work as one line per tool call that opens the full step", async () => {
    const user = userEvent.setup();
    render(<App />);
    const f = await feed();
    const turns = within(f).getByRole("list", { name: "What the steps did" });
    await waitFor(() => expect(turns.querySelectorAll(".rdfl-turn")).toHaveLength(2));
    const items = within(turns).getAllByRole("listitem");
    expect(items.map((i) => i.querySelector(".rdfl-turn-h b")?.textContent)).toEqual(["implementer", "criterion-reviewer"]);
    expect(items[0]).toHaveTextContent("green (AC-002)");
    // the running agent shows its newest steps as they come: its words as text, every tool call and edit as one line
    const live = items[1];
    expect(await within(live).findByText(/Looking at what the gate needs/)).toBeInTheDocument();
    const tool = within(live).getByRole("button", { name: /keel · keel_timeline/ });
    expect(tool).toHaveAttribute("aria-expanded", "false");
    expect(within(live).getByRole("button", { name: /Edited api\/src\/main\/kotlin\/scores\/ScoreController\.kt/ })).toHaveTextContent("+1−1");
    await user.click(tool);
    expect(tool).toHaveAttribute("aria-expanded", "true");
    expect(within(live).getByText(/GREEN a81c3f0/, { selector: "pre, code, span, div" })).toBeInTheDocument();
    expect(within(live).getByRole("link", { name: "Watch live" })).toHaveAttribute("href", "#/live/j-482");
    // a finished call's work opens on a click
    await user.click(within(items[0]).getByRole("button", { name: "Show what it did" }));
    expect(await within(items[0]).findByRole("button", { name: /keel · keel_timeline/ })).toBeInTheDocument();
  });

  it("the waiting gate sits at the end of the feed with its keys; after an answer the feed says it, with the way back", async () => {
    const user = userEvent.setup();
    render(<App />);
    const f = await feed();
    const card = await within(f).findByRole("region", { name: "Gate waits for you" });
    const approve = within(card).getByRole("button", { name: "Approve AC-002" });
    expect(approve).toHaveAttribute("aria-keyshortcuts", "A");
    expect(approve.querySelector("kbd")).toHaveTextContent("A");
    expect(within(card).getByRole("button", { name: /^Send back/ }).querySelector("kbd")).toHaveTextContent("R");
    // O reads the criterion's change: in Code (or Review) when the page is there, else the code section of the card
    const read = await within(card).findByText("Read the change");
    expect(read.closest("a, button")).toHaveAttribute("aria-keyshortcuts", "O");
    await user.click(approve);
    await waitFor(() => expect(db.calls.find((c) => c.path.endsWith("/resume"))?.body).toEqual({ decision: "approve" }));
    const line = await within(f).findByText("You answered “Approve AC-002”");
    expect(screen.queryByRole("region", { name: "Gate waits for you" })).not.toBeInTheDocument();
    // keel has no undo for an answer: the way back is a checkpoint (Rewind runs the step again, and the gate asks again)
    await user.click(within(line.closest(".rdfl-said") as HTMLElement).getByRole("button", { name: "Go back to a checkpoint…" }));
    expect(screen.getByRole("tab", { name: "Checkpoints" })).toHaveAttribute("aria-selected", "true");
  });

  it("the answer's line sits where it happened: before the turns that started after it", async () => {
    const user = userEvent.setup();
    const later = { ...fx.jobs[1], id: "j-490", agent: "test-author", step: "s9", started_at: new Date(Date.now() + 60_000).toISOString(), ended_at: null };
    server.use(http.get("/api/projects/:pid/flows/:tid/step-runs", () => HttpResponse.json(fx.stepRunsOf([fx.jobs[1], later]))));
    render(<App />);
    const f = await feed();
    const card = await within(f).findByRole("region", { name: "Gate waits for you" });
    await user.click(within(card).getByRole("button", { name: "Approve AC-002" }));
    const said = (await within(f).findByText("You answered “Approve AC-002”")).closest("li") as HTMLElement;
    const turns = [...f.querySelectorAll(".rdfl-turn")];
    // the running agent's turn (no row yet) comes last
    expect(turns.map((t) => t.querySelector(".rdfl-turn-h b")?.textContent)).toEqual(["implementer", "test-author", "criterion-reviewer"]);
    expect(turns[0].compareDocumentPosition(said) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(said.compareDocumentPosition(turns[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("O in the gate's card reads the change", async () => {
    render(<App />);
    const card = await screen.findByRole("region", { name: "Gate waits for you" });
    const read = (await within(card).findByText("Read the change")).closest("a, button") as HTMLElement;
    const seen: Element[] = [];
    const had = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) { seen.push(this); };
    try {
      within(card).getByRole("button", { name: "Approve AC-002" }).focus();
      fireEvent.keyDown(card, { key: "o" });
      if (read.tagName === "A") await waitFor(() => expect(location.hash).toBe(read.getAttribute("href")));
      else await waitFor(() => expect(seen).toContain(within(card).getByRole("region", { name: "The code of this criterion" })));
      expect(db.calls.some((c) => c.path.endsWith("/resume"))).toBe(false);
    } finally {
      Element.prototype.scrollIntoView = had;
    }
  });
});

describe("Flow page: rail, Details and keys (keel 0.28)", () => {
  it("D folds the Details drawer to its bar and opens it again; the header button says which", async () => {
    render(<App />);
    await feed();
    const btn = screen.getByRole("button", { name: "Details panel" });
    expect(btn).toHaveAttribute("aria-pressed", "true");
    fireEvent.keyDown(document.body, { key: "d" });
    expect(await screen.findByRole("complementary", { name: "Side panel (closed)" })).toBeInTheDocument();
    expect(btn).toHaveAttribute("aria-pressed", "false");
    fireEvent.keyDown(document.body, { key: "d" });
    expect(await screen.findByRole("complementary", { name: "Now, the selected step and checkpoints" })).toBeInTheDocument();
    // not while typing
    const box = await screen.findByLabelText("Why (needed to send back)");
    fireEvent.keyDown(box, { key: "d" });
    expect(screen.getByRole("complementary", { name: "Now, the selected step and checkpoints" })).toBeInTheDocument();
  });

  it("J and K follow the next and the previous step: the feed shows that step, Details its tab", async () => {
    const user = userEvent.setup();
    render(<App />);
    const f = await feed();
    fireEvent.keyDown(document.body, { key: "j" });
    expect(await within(f).findByText("Showing: spec")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Step: spec" })).toHaveAttribute("aria-selected", "true");
    expect(within(f).getByText("Not run in this flow yet")).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: "j" });
    fireEvent.keyDown(document.body, { key: "k" });
    expect(await within(f).findByText("Showing: spec")).toBeInTheDocument();
    await user.click(within(f).getByRole("button", { name: /Show the whole flow/ }));
    expect(within(f).queryByText(/^Showing:/)).toBeNull();
    expect(within(f).getByRole("list", { name: "What the steps did" })).toBeInTheDocument();
  });

  it("G jumps to the current step: the gate's card gets the focus", async () => {
    const had = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function () {};
    try {
      render(<App />);
      await screen.findByRole("region", { name: "Gate waits for you" });
      expect(screen.getByRole("button", { name: "Jump to current" })).toHaveAttribute("aria-keyshortcuts", "G");
      fireEvent.keyDown(document.body, { key: "g" });
      // the card may be drawn again while the flow refreshes: look it up again on every check
      await waitFor(() => {
        const now = screen.getByRole("region", { name: "Gate waits for you" });
        expect(document.activeElement && now.contains(document.activeElement)).toBe(true);
      }, { timeout: 3000 });
    } finally {
      Element.prototype.scrollIntoView = had;
    }
  });

  it("the steps rail folds to a thin bar and opens again; the browser remembers it", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("region", { name: /: the steps$/ });
    await user.click(screen.getByRole("button", { name: "Fold the steps" }));
    const bar = screen.getByRole("complementary", { name: "Steps (folded)" });
    expect(bar).toHaveTextContent(/Steps · \d+ of \d+/);
    expect(screen.queryByRole("region", { name: /: the steps$/ })).toBeNull();
    expect(localStorage.getItem("keel2.flow.rail")).toBe("0");
    await user.click(within(bar).getByRole("button", { name: "Open the steps" }));
    expect(await screen.findByRole("region", { name: /: the steps$/ })).toBeInTheDocument();
    expect(localStorage.getItem("keel2.flow.rail")).toBeNull();
  });

  it("Details holds the budget, the criteria and Before ship as tabs", async () => {
    const user = userEvent.setup();
    render(<App />);
    await feed();
    await user.click(screen.getByRole("tab", { name: "Budget" }));
    expect(screen.getByRole("meter", { name: "Tokens used of the cap" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Criteria" }));
    expect(screen.getByRole("heading", { name: "Acceptance criteria" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Before ship" }));
    expect(screen.getByRole("heading", { name: "Before ship" })).toBeInTheDocument();
  });
});
