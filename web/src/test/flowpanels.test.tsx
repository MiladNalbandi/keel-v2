// v0.15.2 The Flow page's steps and side panels: a click on a step (Blocks, Table, Graph) shows what it does; the step
// that runs now (or waits for you) opens on Now, only what happens in it now, with Details one click away. The
// "<agent> works" and "Events" panels hide (the browser remembers it) and open big; Esc closes the big view only.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { EngineEvent, JobStep } from "../api";
import { db, FakeEventSource, server } from "./setup";

const side = () => screen.findByRole("complementary", { name: "Now, the selected step and checkpoints" });
const canvas = () => screen.findByRole("region", { name: "feature (keel): the steps" });
const explains = () => db.calls.filter((c) => c.method === "POST" && c.path === "/api/projects/ludus-engine/workflows/explain-step");
const ev = (type: EngineEvent["type"], step: string | undefined, data: Record<string, unknown> = {}): EngineEvent =>
  ({ type, thread_id: "th_7f3a", project_id: "ludus-engine", ...(step ? { step } : {}), at: new Date().toISOString(), data });
const push = (e: EngineEvent) => act(() => FakeEventSource.emit(e.type, e));

describe("Flow steps: a click shows the step", () => {
  it("a step that does not run now shows its details, from Blocks, Table and Graph", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^red, agent/ }));
    const panel = await within(await side()).findByRole("region", { name: "What red does" });
    expect(await within(panel).findByRole("region", { name: "Last run" })).toBeInTheDocument();
    expect(within(panel).queryByRole("group", { name: "Now or the details of this step" })).not.toBeInTheDocument();
    expect(within(await side()).getByRole("tab", { name: "Step: red" })).toHaveAttribute("aria-selected", "true");

    const c = await canvas();
    await user.click(within(c).getByRole("button", { name: "Table" }));
    await user.click(within(c).getByRole("button", { name: "verify_red" }));
    expect(await within(await side()).findByRole("region", { name: "What commit contract does" })).toBeInTheDocument();
    expect(within(c).getByRole("button", { name: "verify_red" }).closest("tr")).toHaveClass("rowsel");

    await user.click(within(c).getByRole("button", { name: "Graph" }));
    await user.click(within(c).getByRole("button", { name: "What green does" }));
    await waitFor(() => expect(explains().at(-1)?.body).toMatchObject({ step_id: "s5", thread_id: "th_7f3a" }));
    expect(within(c).getByRole("button", { name: "What green does" })).toHaveClass("gsel");
    expect(within(await side()).getByRole("tab", { name: "Step: green" })).toHaveAttribute("aria-selected", "true");
    expect(within(await side()).queryByRole("group", { name: "Now or the details of this step" })).not.toBeInTheDocument();
  });

  it("the step that waits for you opens on Now: the waiting card and only its events since it started; Details has the rest", async () => {
    const user = userEvent.setup();
    render(<App />);
    const block = await screen.findByRole("button", { name: /AC gate, wait for you, keel rule, waiting for you/ });
    await within(await side()).findAllByTestId("event-checkpoint");   // the stream is open
    push(ev("step.finished", "s8", { status: "ok" }));                 // the gate of the criterion before
    push(ev("agent.started", "s7", { agent: "ac-reviewer" }));
    push(ev("step.started", "s8"));
    push(ev("gate.waiting", "s8", { title: "AC gate" }));
    await user.click(block);
    const s = await side();
    const now = await within(s).findByRole("region", { name: "AC gate now" });
    expect(within(now).getByRole("button", { name: "Now" })).toHaveAttribute("aria-pressed", "true");
    expect(within(now).getByTestId("step-about")).toHaveTextContent("◆ Gate");
    const events = within(s).getByRole("region", { name: "Events of AC gate" });
    const lines = within(events).getByLabelText("What happened, newest first");
    expect(lines).toHaveTextContent("AC gate waits for you");
    expect(lines).toHaveTextContent("s8 started");
    expect(lines).not.toHaveTextContent("s8 finished");
    expect(lines).not.toHaveTextContent("ac-reviewer start");
    expect(within(events).queryAllByTestId("event-checkpoint")).toHaveLength(0);
    expect(within(s).queryByRole("region", { name: "Last run" })).not.toBeInTheDocument();
    expect(within(s).queryByRole("region", { name: "Rules" })).not.toBeInTheDocument();

    // Go to it: the focus goes into the answer card under the block
    await user.click(within(s).getByRole("button", { name: "Go to it" }));
    const card = screen.getByRole("region", { name: "Gate waits for you" });
    await waitFor(() => expect(card.contains(document.activeElement)).toBe(true));

    // Details: the whole explanation with its past runs (the fixture explains every agent step as red)
    await user.click(within(now).getByRole("button", { name: "Details" }));
    const details = await within(s).findByRole("region", { name: "What red does" });
    expect(await within(details).findByRole("region", { name: "Last run" })).toBeInTheDocument();
    expect(within(s).queryByRole("region", { name: "Events of AC gate" })).not.toBeInTheDocument();
    await user.click(within(details).getByRole("button", { name: "Now" }));
    expect(await within(s).findByRole("region", { name: "AC gate now" })).toBeInTheDocument();
  });

  it("the step a running agent works in opens on Now: the running card and the agent's newest steps", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^AC review, agent/ }));
    const s = await side();
    expect(await within(s).findByRole("region", { name: "AC review now" })).toBeInTheDocument();
    const card = s.querySelector<HTMLElement>(".running-card")!;
    expect(card).toHaveTextContent("running node AC review — AC-002");
    expect(within(card).getByRole("link", { name: "Watch live" })).toHaveAttribute("href", "#/live/j-482");
    const works = within(s).getByRole("region", { name: "ac-reviewer works" });
    expect(await within(works).findByText(/Looking at what the gate needs/)).toBeInTheDocument();
    expect(within(s).getByRole("region", { name: "Events of AC review" })).toHaveTextContent("Nothing happened in this step yet.");
  });

  it("Now opens from the Table and the Graph too", async () => {
    const user = userEvent.setup();
    render(<App />);
    const c = await canvas();
    await user.click(within(c).getByRole("button", { name: "Table" }));
    await user.click(within(c).getByRole("button", { name: "AC gate" }));
    expect(await within(await side()).findByRole("region", { name: "AC gate now" })).toBeInTheDocument();
    await user.click(within(c).getByRole("button", { name: "Graph" }));
    await user.click(within(c).getByRole("button", { name: "What AC review does" }));
    expect(await within(await side()).findByRole("region", { name: "AC review now" })).toBeInTheDocument();
    expect(within(c).getByRole("button", { name: "What AC review does" })).toHaveClass("gsel");
  });
});

describe("Flow side panels: hide and open big", () => {
  it("Hide folds the agent's works and Events; the browser remembers it", async () => {
    const user = userEvent.setup();
    const first = render(<App />);
    const s = await side();
    const works = await within(s).findByRole("region", { name: "ac-reviewer works" });
    await within(works).findByText(/Looking at what the gate needs/);
    await user.click(within(works).getByRole("button", { name: "Hide ac-reviewer works" }));
    expect(within(works).queryByText(/Looking at what the gate needs/)).not.toBeInTheDocument();
    expect(within(works).queryByRole("link", { name: "Watch live" })).not.toBeInTheDocument();
    expect(within(works).getByRole("button", { name: "Show ac-reviewer works" })).toHaveAttribute("aria-expanded", "false");
    expect(localStorage.getItem("keel2.fold.flow.works")).toBe("1");
    const events = within(s).getByRole("region", { name: "Events" });
    await within(events).findAllByTestId("event-checkpoint");
    await user.click(within(events).getByRole("button", { name: "Hide Events" }));
    expect(within(events).queryAllByTestId("event-checkpoint")).toHaveLength(0);
    expect(within(events).queryByRole("button", { name: "All checkpoints" })).not.toBeInTheDocument();
    expect(localStorage.getItem("keel2.fold.flow.events")).toBe("1");

    // the next visit: both are still hidden; Show opens them again
    first.unmount();
    render(<App />);
    const s2 = await side();
    const works2 = await within(s2).findByRole("region", { name: "ac-reviewer works" });
    expect(within(works2).getByRole("button", { name: "Show ac-reviewer works" })).toBeInTheDocument();
    const events2 = within(s2).getByRole("region", { name: "Events" });
    expect(within(events2).queryAllByTestId("event-checkpoint")).toHaveLength(0);
    await user.click(within(events2).getByRole("button", { name: "Show Events" }));
    expect(await within(events2).findAllByTestId("event-checkpoint")).toHaveLength(3);
    expect(localStorage.getItem("keel2.fold.flow.events")).toBeNull();
    expect(within(events2).getByRole("button", { name: "Hide Events" })).toHaveAttribute("aria-expanded", "true");
  });

  it("⤢ opens Events big with every event and saved step; Esc closes it and gives the focus back", async () => {
    const user = userEvent.setup();
    render(<App />);
    const s = await side();
    const events = await within(s).findByRole("region", { name: "Events" });
    await within(events).findAllByTestId("event-checkpoint");
    for (let i = 1; i <= 14; i++) push(ev("budget.warn", undefined, { text: `warn ${i}` }));
    await waitFor(() => expect(within(events).getByText("warn 14")).toBeInTheDocument());
    expect(within(events).queryByText("warn 2")).not.toBeInTheDocument();   // the panel: the newest 12
    const open = within(events).getByRole("button", { name: "Open Events big" });
    await user.click(open);
    const big = screen.getByRole("dialog", { name: "Events" });
    expect(within(big).getByText("warn 1")).toBeInTheDocument();
    expect(within(big).getByText("warn 14")).toBeInTheDocument();
    expect(within(big).getAllByTestId("event-checkpoint")).toHaveLength(3);
    expect(within(big).getByRole("button", { name: "Close" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Events" })).not.toBeInTheDocument();
    expect(open).toHaveFocus();
  });

  it("⤢ opens the agent's works big with every step so far; Close shuts it", async () => {
    const many: JobStep[] = Array.from({ length: 9 }, (_, i) => ({ n: i + 1, at: new Date().toISOString(), kind: "text", text: `step number ${i + 1}` }));
    server.use(http.get("/api/jobs/:id/steps", () => HttpResponse.json({ steps: many, running: false })));
    const user = userEvent.setup();
    render(<App />);
    const works = await within(await side()).findByRole("region", { name: "ac-reviewer works" });
    await within(works).findByText("step number 9");
    expect(within(works).queryByText("step number 3")).not.toBeInTheDocument();   // the panel: the newest 6
    await user.click(within(works).getByRole("button", { name: "Open ac-reviewer works big" }));
    const big = screen.getByRole("dialog", { name: "ac-reviewer works" });
    const feed = within(big).getByLabelText("Every step of ac-reviewer");
    expect(within(feed).getByText("step number 1")).toBeInTheDocument();
    expect(within(feed).getByText("step number 9")).toBeInTheDocument();
    expect(within(big).getByRole("link", { name: "Open job" })).toHaveAttribute("href", "#/jobs/j-482");
    await user.click(within(big).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("a narrow screen shows the step in a drawer; Esc in a big view over it closes only the big view", async () => {
    const had = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: q.includes("max-width: 1099px"), media: q, addEventListener() {}, removeEventListener() {}, onchange: null })) as unknown as typeof window.matchMedia;
    try {
      const user = userEvent.setup();
      render(<App />);
      await user.click(await screen.findByRole("button", { name: /^AC review, agent/ }));
      const drawer = await screen.findByRole("dialog", { name: "AC review now" });
      const works = await within(drawer).findByRole("region", { name: "ac-reviewer works" });
      await user.click(within(works).getByRole("button", { name: "Open ac-reviewer works big" }));
      expect(screen.getByRole("dialog", { name: "ac-reviewer works" })).toBeInTheDocument();
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("dialog", { name: "ac-reviewer works" })).not.toBeInTheDocument();
      expect(screen.getByRole("dialog", { name: "AC review now" })).toBeInTheDocument();
      await user.click(within(screen.getByRole("dialog", { name: "AC review now" })).getByRole("button", { name: "Details" }));
      expect(await screen.findByRole("dialog", { name: "What red does" })).toBeInTheDocument();
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    } finally {
      window.matchMedia = had;
    }
  });
});
