// keel 0.18.11 the Flow page for long flows: phases fold to one line (a phase that is over or not started yet starts
// closed, the one at work open, the person's choice remembered), one row per criterion with its tries and why it was
// sent back, "Needs you" pinned on top of the steps, and the canvas follows the live step until the person scrolls.

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { sendBacksOf, workTime } from "../components/stepFacts";
import { db, FakeEventSource, server } from "./setup";

const block = (id: string) => document.querySelector<HTMLElement>(`[data-block="${id}"]`);

describe("Flow page: long flows (0.18.11)", () => {
  it("phases fold: one that is over starts closed with its summary, the one at work is open, and a choice is remembered", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("region", { name: "Gate waits for you" });
    const spec = document.querySelector<HTMLElement>('section[data-phase="spec"]')!;
    const head = spec.querySelector<HTMLElement>(".sx-h-btn")!;
    expect(spec).toHaveClass("is-folded");
    expect(head).toHaveAttribute("aria-expanded", "false");
    expect(head).toHaveTextContent("done · 1 step ran");
    expect(block("s1")).toBeNull();
    expect(block("s8")).not.toBeNull();                                   // the gate that waits: its phase is open

    await user.click(head);
    expect(head).toHaveAttribute("aria-expanded", "true");
    expect(block("s1")).not.toBeNull();
    expect(Object.values(JSON.parse(localStorage.getItem("keel2.flow.fold.th_7f3a")!))).toContain(true);
  });

  it("a criterion row says its state, tokens and why it was sent back", async () => {
    db.flows["ludus-engine"].thread!.gate_log = ["gate s2 approve", "ac AC-002 reject: the test checks nothing", "ac AC-002 reject", "ac AC-001 approve"];
    render(<App />);
    const rows = await screen.findByLabelText("Each criterion");
    const ac2 = rows.querySelector<HTMLElement>('[data-ac-row="AC-002"]')!;
    expect(ac2).toHaveClass("is-cur", "s-wait");
    expect(ac2).toHaveTextContent("Refuse a negative score with 400");
    const why = within(ac2).getByRole("list", { name: "Why AC-002 was sent back" });
    expect(why).toHaveTextContent("try 1 sent back: “the test checks nothing”");
    expect(why).toHaveTextContent("try 2 sent back: “no reason given”");
    await waitFor(() => expect(ac2.querySelector(".sc-ac-n")).toHaveTextContent("57k"));   // green 37k + the review at work 20k
    expect(rows.querySelector('[data-ac-row="AC-001"]')).toHaveClass("s-done");
    expect(rows.querySelector('[data-ac-row="AC-003"]')).toHaveTextContent("to do");
  });

  it("says a step's working time in short words", () => {
    expect(workTime(300)).toBe("<1s");
    expect(workTime(0)).toBe("0s");
    expect(workTime(40_000)).toBe("40s");
    expect(workTime(130_000)).toBe("2m 10s");
    expect(workTime(120_000)).toBe("2m");
    expect(workTime(3_900_000)).toBe("1h 05m");
  });

  it("reads why a criterion was sent back from the gate log, oldest first", () => {
    expect(sendBacksOf([
      "ac AC-1 reject: too weak", "gate s2 reject: not this", "ac AC-1 already-met reject: no", "ac AC-2 reject (x) [fix]: later", "ac AC-1 reject",
    ])).toEqual({ "AC-1": ["too weak", "no reason given"], "AC-2": ["later"] });
    expect(sendBacksOf(undefined)).toEqual({});
  });

  it("the canvas follows the live step until the person scrolls; then Jump to live brings it back", async () => {
    const user = userEvent.setup();
    const f = db.flows["ludus-engine"];
    f.thread = { ...f.thread!, status: "running", waiting: undefined, current: "s3" };
    const seen: string[] = [];
    const had = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) {
      const b = this.closest("[data-block]")?.getAttribute("data-block");
      if (b) seen.push(b);
    };
    try {
      render(<App />);
      await waitFor(() => expect(block("s3")).not.toBeNull());
      const step = (to: string) => {
        f.thread = { ...f.thread!, current: to };
        act(() => FakeEventSource.emit("step.started", { type: "step.started", thread_id: "th_7f3a", project_id: "ludus-engine", step: to, at: new Date().toISOString(), data: {} }));
      };
      step("s5");
      await waitFor(() => expect(seen).toContain("s5"));
      expect(screen.queryByRole("button", { name: "Jump to live" })).toBeNull();

      fireEvent.wheel(screen.getByRole("region", { name: /: the steps$/ }));
      const back = await screen.findByRole("button", { name: "Jump to live" });
      step("s7");
      await waitFor(() => expect(block("s7")).toHaveAttribute("data-state", "run"));
      expect(seen).not.toContain("s7");                                    // it does not pull the page away

      await user.click(back);
      await waitFor(() => expect(seen).toContain("s7"));
      expect(screen.queryByRole("button", { name: "Jump to live" })).toBeNull();
    } finally {
      Element.prototype.scrollIntoView = had;
    }
  });

  it("a failed step is pinned on top of the steps with Go to it", async () => {
    const user = userEvent.setup();
    const f = db.flows["ludus-engine"];
    f.thread = { ...f.thread!, status: "failed", waiting: undefined, current: "s5" };
    render(<App />);
    const pin = await waitFor(() => {
      const el = document.querySelector<HTMLElement>(".fl-needs");
      expect(el).not.toBeNull();
      return el!;
    });
    expect(pin).toHaveTextContent("green failed. See why and what to do.");
    expect(pin).toHaveClass("is-bad");
    await user.click(within(pin).getByRole("button", { name: "Go to it" }));
    await waitFor(() => expect(document.activeElement?.closest("[data-block]")?.getAttribute("data-block")).toBe("s5"));
  });
});

describe("Flow page: the gate card (0.18.11)", () => {
  it("a criterion's `code` shows as code in the gate card, the criteria list and its row (0.18.57)", async () => {
    const t = db.flows["ludus-engine"].thread!;
    t.acs = t.acs.map((a) => (a.id === "AC-002" ? { ...a, title: "Refuse a `score` below 0 with 400" } : a));
    render(<App />);
    const card = await screen.findByRole("region", { name: "Gate waits for you" });
    const codes = (el: Element | null) => [...(el?.querySelectorAll("code") ?? [])].map((c) => c.textContent);
    expect(codes(card.querySelector("h3"))).toEqual(["score"]);
    expect(card.querySelector("h3")).not.toHaveTextContent("`");
    expect(card.querySelector("dl.gc-lines")).toHaveTextContent("You approveAC-002 “Refuse a score below 0 with 400”");
    expect(codes(card.querySelector("dl.gc-lines dd"))).toEqual(["score"]);
    // keel 0.28 the criteria are a tab of the Details drawer
    fireEvent.click(screen.getByRole("tab", { name: "Criteria" }));
    const list = (await screen.findByText("Acceptance criteria")).closest(".panel");
    expect(codes(list)).toContain("score");
    expect(list).not.toHaveTextContent("`score`");
    const row = (await screen.findByLabelText("Each criterion")).querySelector('[data-ac-row="AC-002"]');
    expect(codes(row)).toEqual(["score"]);
  });

  it("says what you approve, what it cost so far, and what each button does; A approves, R goes to the reason", async () => {
    const user = userEvent.setup();
    render(<App />);
    const card = await screen.findByRole("region", { name: "Gate waits for you" });
    const lines = card.querySelector<HTMLElement>("dl.gc-lines")!;
    expect(lines).toHaveTextContent("You approveAC-002 “Refuse a negative score with 400”");
    expect(lines).toHaveTextContent("So far182k tokens · $2.86 for this flow");
    expect(lines).toHaveTextContent("After approvekeel starts the next criterion: AC-003 “Show the top 10 scores”");
    expect(lines).toHaveTextContent("After send backthe test-author does “red” again for AC-002, with your reason");
    expect(card).toHaveTextContent("Proof");
    expect(card).not.toHaveTextContent("Command(resume");

    within(card).getByRole("button", { name: /^Approve AC-002/ }).focus();
    await user.keyboard("r");
    expect(document.activeElement).toBe(within(card).getByLabelText("Why (needed to send back)"));
    await user.keyboard("r");                                              // typing in the box is only typing
    expect(within(card).getByLabelText("Why (needed to send back)")).toHaveValue("r");

    within(card).getByRole("button", { name: /^Approve AC-002/ }).focus();
    await user.keyboard("a");
    await waitFor(() => expect(db.calls.find((c) => c.method === "POST" && c.path === "/api/threads/th_7f3a/resume")?.body).toMatchObject({ decision: "approve" }));
  });

  it("an answer that fails offers Ask KeelBot to fix it, with keel's error", async () => {
    const user = userEvent.setup();
    server.use(http.post("/api/threads/:tid/resume", () => HttpResponse.json({ error: "The engine is busy", hint: "Try again in a minute." }, { status: 409 })));
    const asked: string[] = [];
    const on = (e: Event) => asked.push((e as CustomEvent<{ text: string }>).detail.text);
    window.addEventListener("keel:ask-assistant", on);
    try {
      render(<App />);
      const card = await screen.findByRole("region", { name: "Gate waits for you" });
      await user.click(within(card).getByRole("button", { name: /^Approve AC-002/ }));
      await user.click(await within(card).findByRole("button", { name: "Ask KeelBot to fix it" }));
      expect(asked[0]).toContain("This failed: answering the gate “AC gate” of the flow “Scores for players”");
      expect(asked[0]).toContain("keel said: The engine is busy");
      await waitFor(() => expect(location.hash).toBe("#/helper"));
    } finally {
      window.removeEventListener("keel:ask-assistant", on);
    }
  });
});

describe("Flow page: the top and the live line (0.18.11)", () => {
  it("the running block shows what its agent writes now, else that it works", async () => {
    const f = db.flows["ludus-engine"];
    f.thread = { ...f.thread!, status: "running", waiting: undefined, current: "s7" };   // j-482, the AC review, runs
    render(<App />);
    await waitFor(() => expect(block("s7")?.querySelector(".sb-live")).toHaveTextContent("working…"));
    act(() => FakeEventSource.emit("agent.delta", { type: "agent.delta", thread_id: "th_7f3a", project_id: "ludus-engine", call_id: "j-482", step: "s7",
      at: new Date().toISOString(), data: { text: "Looking at the gate.\nThe test checks the 400 body now" } }));
    await waitFor(() => expect(block("s7")!.querySelector(".sb-live")).toHaveTextContent("✎ The test checks the 400 body now"));
    expect(block("s5")!.querySelector(".sb-live")).toBeNull();               // only the running block
    expect(document.querySelector(".sx-total")).toBeNull();                  // the flow bar has the flow's tokens
  });

  it("once the flow bar scrolls away, the flow's one line stays on top with the phase, the step and Go to it", async () => {
    const user = userEvent.setup();
    const seen: ((e: { isIntersecting: boolean }[]) => void)[] = [];
    const had = (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver;
    (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = class {
      constructor(cb: (e: { isIntersecting: boolean }[]) => void) { seen.push(cb); }
      observe() {}
      disconnect() {}
    };
    try {
      render(<App />);
      const bar = await screen.findByRole("region", { name: "This flow" });
      expect(bar).toHaveTextContent(/phase ac-gate \(5 of 5\)/);
      expect(screen.queryByRole("region", { name: "The flow now" })).toBeNull();
      act(() => seen.forEach((cb) => cb([{ isIntersecting: false }])));
      const now = await screen.findByRole("region", { name: "The flow now" });
      expect(now).toHaveTextContent("waiting for you");
      expect(now.querySelector(".fl-now-p")).toHaveTextContent("phase 5 of 5 · ac-gate");
      expect(now.querySelector(".fl-now-s")).toHaveTextContent("AC gate (AC-002)");
      expect(now).toHaveTextContent("182k tokens · $2.86");
      await user.click(within(now).getByRole("button", { name: "Go to it" }));
      const card = screen.getByRole("region", { name: "Gate waits for you" });
      await waitFor(() => expect(document.activeElement && card.contains(document.activeElement)).toBe(true));
      act(() => seen.forEach((cb) => cb([{ isIntersecting: true }])));
      await waitFor(() => expect(screen.queryByRole("region", { name: "The flow now" })).toBeNull());
    } finally {
      (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = had;
    }
  });
});
