import { act, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { flowUse, toneOf } from "../components/BudgetBar";
import { db, FakeEventSource } from "./setup";

const bar = () => screen.findByRole("region", { name: "Budget at a glance" });
const nowCalls = () => db.calls.filter((c) => c.method === "GET" && c.path.endsWith("/budget/now")).length;

describe("Budget bar on top of every page", () => {
  afterEach(() => vi.useRealTimers());

  it("shows today, this month and the running flow against its cap, on any page", async () => {
    location.hash = "#/agents";
    render(<App />);
    const b = await bar();
    const today = await within(b).findByTestId("bb-today");
    expect(today).toHaveTextContent("Today120k$0.42");
    expect(today).toHaveAttribute("href", "#/settings/budget");
    expect(within(b).getByTestId("bb-this-month")).toHaveTextContent("This month1.90M$24.10");
    const flow = within(b).getByTestId("bb-flow");
    expect(flow).toHaveTextContent("waiting");
    expect(flow).toHaveTextContent("Scores for players");
    expect(flow).toHaveTextContent("184k/ 600k");
    expect(flow).toHaveTextContent("31%");
    expect(flow).toHaveAttribute("href", "#/flow");
    expect(within(b).queryByTestId("bb-providers")).toBeNull();           // #101 no provider plan windows
    expect(b).toHaveClass("bb-ok");
    // outside the page itself: a page's own queries do not see it
    expect(document.getElementById("main")!.contains(b)).toBe(false);
  });

  it("other projects' runs (keel Product's own) show as the total for all projects", async () => {
    db.budgetNow.all_today = { tokens: 540000, cost_usd: 2.99 };
    db.budgetNow.all_month = { tokens: 1900000, cost_usd: 24.1 };       // nothing else this month: no extra part
    location.hash = "#/projects";
    render(<App />);
    const b = await bar();
    const today = await within(b).findByTestId("bb-today");
    expect(within(today).getByTestId("bb-all-today")).toHaveTextContent("· all $2.99");
    expect(today).toHaveAttribute("title", expect.stringContaining("All projects (keel Product too): 540k tokens, $2.99."));
    expect(within(b).queryByTestId("bb-all-this-month")).toBeNull();
  });

  it("a day cap shows what is used of it; 95% turns the bar red", async () => {
    db.caps.push({ id: "c9", scope: "day", limit: 200000, unit: "tokens", action: "pause" });
    db.capUse.day.tokens = 190000;
    location.hash = "#/projects";
    render(<App />);
    const today = await within(await bar()).findByTestId("bb-today");
    await waitFor(() => expect(today).toHaveTextContent("cap 200k95%"));
    expect(today).toHaveAttribute("title", expect.stringContaining("Cap: 190k of 200k (pause when used up)."));
    expect(await bar()).toHaveClass("bb-bad");
  });

  it("this month's API cap is in dollars; a flow at 83% of its cap turns the bar amber", async () => {
    db.budgetNow.flows[0].tokens = 500000;
    location.hash = "#/projects";
    render(<App />);
    const b = await bar();
    const month = await within(b).findByTestId("bb-this-month");
    expect(month).toHaveTextContent("$100.00 API");                 // fx.caps c2: $3.50 of $100 this month
    expect(month).toHaveTextContent("4%");
    expect(within(b).getByTestId("bb-flow")).toHaveTextContent("83%");
    expect(b).toHaveClass("bb-warn");
  });

  it("no running flow: no flow part", async () => {
    db.budgetNow.flows = [];
    location.hash = "#/projects";
    render(<App />);
    const b = await bar();
    await within(b).findByTestId("bb-today");
    expect(within(b).queryByTestId("bb-flow")).toBeNull();
  });

  it("no project yet: the bar is still there (pages measure their top once) and says so", async () => {
    db.projects = [];
    location.hash = "#/projects";
    render(<App />);
    const b = await bar();
    expect(await within(b).findByRole("link", { name: "No project yet: the budget starts with the first one" })).toHaveAttribute("href", "#/projects");
    expect(within(b).queryByTestId("bb-today")).toBeNull();
  });

  it("live events reload it, at most once every 5 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    location.hash = "#/projects";
    render(<App />);
    await within(await bar()).findByTestId("bb-today");
    const first = nowCalls();
    for (let i = 0; i < 5; i++) {
      act(() => FakeEventSource.emit("agent.finished", { type: "agent.finished", thread_id: "th_7f3a", project_id: "ludus-engine", call_id: `j${i}`, at: new Date().toISOString(), data: {} }));
      await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    }
    expect(nowCalls()).toBe(first);                                   // a burst inside 5 s: not yet
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    await waitFor(() => expect(nowCalls()).toBe(first + 1));          // then once
  });

  it("the words and numbers", () => {
    expect(toneOf(null)).toBe("ok");
    expect(toneOf(79)).toBe("ok");
    expect(toneOf(80)).toBe("warn");
    expect(toneOf(95)).toBe("bad");
    const f = { thread_id: "t", title: "x", status: "running" as const, tokens: 100000, cost_usd: 4.5, cap_tokens: 600000, cap_usd: 5 };
    expect(flowUse(f)).toEqual({ used: "$4.50", cap: "$5.00", pct: 90 });               // the tighter cap wins
    expect(flowUse({ ...f, cap_usd: null })).toEqual({ used: "100k", cap: "600k", pct: 17 });
    expect(flowUse({ ...f, cap_usd: null, cap_tokens: null })).toEqual({ used: "100k", cap: null, pct: null });
  });
});
