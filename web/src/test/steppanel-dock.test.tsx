// keel 0.18.66 the step panel is docked on the right of the Flow page, next to the steps: a click on a step shows it
// there (the right column's Step tab; the init flow's own dock). A menu bar picks Overview · Decided · Rules · Commands.
// » folds the column to a thin bar ("Step details · <step>"), « opens it again, and the browser remembers open or closed
// and the last tab. Keel's reason for a gate it did not ask reads as plain words, and "Waits for you." is said once.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { StepExplanation, ThreadState } from "../api";
import { App } from "../App";
import { logDecision, plainLine, plainWhy } from "../components/eventRows";
import { StepInfoBody } from "../components/StepInfo";
import * as fx from "./fixtures";
import { db, server } from "./setup";
import { openShopFlow, SHOP_TID, shopEvent } from "./shopFlow";

const side = () => screen.findByRole("complementary", { name: "Now, the selected step and checkpoints" });

/** What the engine explains for a gate like init's "not a git repository" (runtime/explain.py gate_info). */
const gateStep = (): StepExplanation => ({
  id: "spec_gate", name: "spec approval", kind: "gate", phase: "spec", thread: true,
  phase_meaning: "Write the spec. Only the spec changes.",
  rules: {
    phase: "spec",
    buckets: [
      { bucket: "specs", what: "specs", may: "edit", label: "edit" },
      { bucket: "migration", what: "database migrations", may: "new-only", label: "create new files only" },
      { bucket: "api-main", what: "backend production code", may: "read-only", label: "read only" },
      { bucket: "protected-env", what: ".env files (secrets)", may: "no-access", label: "never read or written" },
    ],
    shell_refused: [
      '"git push --force" (it skips checks keel relies on)',
      '"git push -f" (it skips checks keel relies on)',
      "git commit: the engine's commit step makes every commit",
    ],
    reads_blocked: ".env files, and build output (dist/)",
  },
  next: [],
  gate: {
    answers: [
      { label: "init", to: "git_init", to_name: "git init and the first commit", text: "goes to git init and the first commit" },
      { label: "stop", to: "end", to_name: "the end", text: "finishes the flow" },
    ],
    notes: ["A plain approve picks init.", "Pauses only when data.git_question is set; otherwise keel approves it and logs \"not asked\"."],
    costs: "Waits for you. No tokens.",
  },
});

const explainGate = () => server.use(http.post("/api/projects/shop/workflows/explain-step", () => HttpResponse.json(gateStep())));

describe("The docked step panel", () => {
  it("shows the clicked step next to the steps, with a menu bar whose tabs switch", async () => {
    openShopFlow();
    explainGate();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^wait for you, spec approval, / }));
    const s = await side();
    const panel = await within(s).findByRole("region", { name: "What spec approval does" });
    // docked: in the right column, not a dialog over the page
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(panel.closest(".fl")).not.toBeNull();
    const bar = within(panel).getByRole("tablist", { name: "Step details" });
    expect(within(bar).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview", "Decided", "Rules", "Commands 3"]);
    expect(within(panel).getByRole("heading", { level: 2, name: "spec approval" })).toBeInTheDocument();
    expect(panel).toHaveTextContent("Step · ◆ Gate");

    // Overview: the step in plain words, its choices in one box, one line of facts
    const about = await within(panel).findByTestId("step-about");
    expect(about).toHaveTextContent("It waits for your answer, but only when the folder is not a git repository with a first commit. Otherwise keel goes on without asking.");
    const choices = within(panel).getByTestId("step-choices");
    expect(choices).toHaveTextContent("Your choices here");
    expect(choices).toHaveTextContent("initgoes to git init and the first commit");
    expect(choices).toHaveTextContent("A plain approve picks init.");
    expect(within(panel).getByTestId("step-meta").textContent).toBe("Waits for youno tokensphase spec");
    // "Waits for you." once, never twice
    expect(panel.textContent).not.toMatch(/Waits for you\.?\s*Waits for you/);

    // Rules: three boxes from the same data as the table, the full table one click away
    await user.click(within(bar).getByRole("tab", { name: "Rules" }));
    expect(within(bar).getByRole("tab", { name: "Rules" })).toHaveAttribute("aria-selected", "true");
    const boxes = within(panel).getByTestId("rule-boxes");
    expect(boxes.querySelector('[data-box="edit"]')).toHaveTextContent("✓ agents may editspecs · database migrations (create new files only)");
    expect(boxes.querySelector('[data-box="read"]')).toHaveTextContent("✕ read onlybackend production code");
    expect(boxes.querySelector('[data-box="never"]')).toHaveTextContent("✕ never.env files (secrets)");
    const more = within(panel).getByRole("button", { name: "The full table, file by file ›" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await user.click(more);
    const table = within(panel).getByRole("region", { name: "The full table, file by file" });
    expect(table.querySelectorAll("tr[data-bucket]")).toHaveLength(4);

    // Commands: one line each with why; a twin says "the same"
    await user.click(within(bar).getByRole("tab", { name: "Commands 3" }));
    const cmds = within(panel).getAllByRole("listitem");
    expect(cmds.map((li) => li.textContent)).toEqual([
      "git push --forceit skips checks keel relies on",
      "git push -fthe same",
      "git committhe engine's commit step makes every commit",
    ]);
    expect(cmds[0].querySelector("code")).toHaveTextContent("git push --force");
    expect(panel).toHaveTextContent("An agent never reads .env files, and build output (dist/).");

    // ← and → move along the menu bar
    within(bar).getByRole("tab", { name: "Commands 3" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(within(bar).getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(within(bar).getByRole("tab", { name: "Overview" })).toHaveFocus();
  });

  it("» folds it to a thin bar, « opens it again; a click on a step opens it too", async () => {
    openShopFlow();
    explainGate();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^wait for you, spec approval, / }));
    await within(await side()).findByRole("region", { name: "What spec approval does" });
    await user.click(screen.getByRole("button", { name: "Close the side panel" }));
    const bar = screen.getByRole("complementary", { name: "Side panel (closed)" });
    expect(within(bar).getByTestId("dock-label")).toHaveTextContent("Step details · spec approval");
    expect(screen.queryByRole("complementary", { name: "Now, the selected step and checkpoints" })).not.toBeInTheDocument();
    expect(bar.closest(".fl")).toHaveClass("is-folded");
    expect(localStorage.getItem("keel2.steppanel.open")).toBe("0");

    await user.click(within(bar).getByRole("button", { name: "Open the side panel" }));
    expect(await within(await side()).findByRole("region", { name: "What spec approval does" })).toBeInTheDocument();
    expect(localStorage.getItem("keel2.steppanel.open")).toBeNull();

    // folded again, a click on a step opens the panel on that step
    await user.click(screen.getByRole("button", { name: "Close the side panel" }));
    await user.click(screen.getByRole("button", { name: /^interview, agent/ }));
    expect(await within(await side()).findByRole("region", { name: "What interview does" })).toBeInTheDocument();
  });

  it("the browser remembers folded or open, and the last tab", async () => {
    openShopFlow();
    explainGate();
    const user = userEvent.setup();
    const first = render(<App />);
    await user.click(await screen.findByRole("button", { name: /^wait for you, spec approval, / }));
    const panel = await within(await side()).findByRole("region", { name: "What spec approval does" });
    await user.click(within(panel).getByRole("tab", { name: "Rules" }));
    await user.click(screen.getByRole("button", { name: "Close the side panel" }));
    expect(localStorage.getItem("keel2.steppanel.tab")).toBe("rules");
    first.unmount();

    // a reload: still folded (no step picked yet: the bar names the tab), the step opens on Rules
    render(<App />);
    const bar = await screen.findByRole("complementary", { name: "Side panel (closed)" });
    expect(within(bar).getByTestId("dock-label")).toHaveTextContent("Flow details · Now");
    await user.click(await screen.findByRole("button", { name: /^wait for you, spec approval, / }));
    const again = await within(await side()).findByRole("region", { name: "What spec approval does" });
    expect(within(again).getByRole("tab", { name: "Rules" })).toHaveAttribute("aria-selected", "true");
    expect(await within(again).findByTestId("rule-boxes")).toBeInTheDocument();
  });

  it("keeps working when the browser keeps nothing (a private window)", async () => {
    openShopFlow();
    explainGate();
    const had = Object.getOwnPropertyDescriptor(window, "localStorage")!;
    const broken = { getItem: () => { throw new Error("no"); }, setItem: () => { throw new Error("no"); }, removeItem: () => { throw new Error("no"); },
      clear: () => {}, key: () => null, length: 0 };
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^wait for you, spec approval, / }));
    Object.defineProperty(window, "localStorage", { value: broken, configurable: true });
    try {
      const panel = await within(await side()).findByRole("region", { name: "What spec approval does" });
      await user.click(within(panel).getByRole("tab", { name: "Rules" }));
      await user.click(screen.getByRole("button", { name: "Close the side panel" }));
      expect(screen.getByRole("complementary", { name: "Side panel (closed)" })).toBeInTheDocument();
    } finally {
      Object.defineProperty(window, "localStorage", had);
    }
  });

  it("Decided shows keel's not-asked reason in plain words, with who and when", async () => {
    openShopFlow();
    explainGate();
    db.decisions[SHOP_TID] = [
      shopEvent("gate.decided", "spec_gate", { gate: "spec approval", decision: "approve", why: "not asked, git_question: not given", by: "engine" }, 5),
    ];
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^wait for you, spec approval, / }));
    const panel = await within(await side()).findByRole("region", { name: "What spec approval does" });
    await user.click(await within(panel).findByRole("tab", { name: "Decided 1" }));
    const card = within(panel).getByTestId("decision");
    expect(within(card).getByTestId("decision-badge")).toHaveTextContent("approved");
    expect(card).toHaveTextContent("by keel");
    expect(card.querySelector("time")).toHaveAttribute("dateTime", "2026-10-17T10:00:05.000Z");
    expect(card).toHaveTextContent("Not asked: the folder is already a git repository with a first commit.");
    expect(card).not.toHaveTextContent("git_question");
  });
});

describe("The init flow's step panel", () => {
  const initFlow = () => {
    const t = db.flows["ludus-engine"].thread!;
    db.flows["ludus-engine"] = {
      workflow: fx.initWorkflow,
      thread: { ...t, workflow_id: "init", status: "running", waiting: undefined, current: "i3", ladder: [] } as ThreadState,
    };
  };

  it("is docked too: it shows the step that runs now, then the clicked one; » folds it", async () => {
    initFlow();
    const user = userEvent.setup();
    render(<App />);
    const dock = await screen.findByRole("complementary", { name: "Step details" });
    expect(await within(dock).findByRole("region", { name: "What ladder ‖ knowledge build does" })).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /^discover, agent/ }));
    expect(await within(dock).findByRole("region", { name: "What discover does" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(within(dock).getByRole("button", { name: "Close the step panel" }));
    const bar = screen.getByRole("complementary", { name: "Step panel (closed)" });
    expect(within(bar).getByTestId("dock-label")).toHaveTextContent("Step details · discover");
    await user.click(within(bar).getByRole("button", { name: "Open the step panel" }));
    expect(await screen.findByRole("region", { name: "What discover does" })).toBeInTheDocument();
  });

  it("keeps the drawer on a narrow screen", async () => {
    initFlow();
    const had = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: q.includes("max-width: 1099px"), media: q, addEventListener() {}, removeEventListener() {}, onchange: null })) as unknown as typeof window.matchMedia;
    try {
      const user = userEvent.setup();
      render(<App />);
      await user.click(await screen.findByRole("button", { name: /^discover, agent/ }));
      const dlg = await screen.findByRole("dialog", { name: "What discover does" });
      expect(within(dlg).getByRole("tablist", { name: "Step details" })).toBeInTheDocument();
      expect(screen.queryByRole("complementary", { name: "Step details" })).not.toBeInTheDocument();
    } finally {
      window.matchMedia = had;
    }
  });
});

describe("Plain words", () => {
  it("maps keel's reasons for a gate it did not ask, and keeps a person's note as written", () => {
    expect(plainWhy("not asked, git_question: not given")).toBe("Not asked: the folder is already a git repository with a first commit.");
    expect(plainWhy("not asked, contract: no")).toBe("Not asked: this change does not touch the API contract.");
    expect(plainWhy("not asked, hunt.mode: auto")).toBe("Not asked: the hunt runs without stops (mode auto).");
    // unknown ones keep their words, humanized
    expect(plainWhy("not asked, review_plan.ask: not given")).toBe("Not asked: review plan ask was not set.");
    expect(plainWhy("not asked, WEB: no")).toBe("Not asked: the WEB marker was no.");
    expect(plainWhy("waived (no_gates)")).toBe("Waived: no_gates.");
    expect(plainWhy("AC-2 misses the empty cart")).toBe("AC-2 misses the empty cart");
    expect(logDecision("gate git_repo approve: not asked, git_question: not given"))
      .toMatchObject({ note: "Not asked: the folder is already a git repository with a first commit." });
    // the last run's note and its gate log lines too
    expect(plainLine("not a git repository: not asked, git_question: not given"))
      .toBe("not a git repository: Not asked: the folder is already a git repository with a first commit.");
    expect(plainLine("gate git_repo approve: not asked, git_question: not given"))
      .toBe("approved: Not asked: the folder is already a git repository with a first commit.");
    expect(plainLine("gate interview_gate answered the explorer's 2 question(s)")).toBe("gate interview_gate answered the explorer's 2 question(s)");
    expect(plainLine("test-author · RED done")).toBe("test-author · RED done");
  });

  it("says Waits for you once in the builder's explanation of a gate", () => {
    const { container } = render(<StepInfoBody x={gateStep()} />);
    expect(container.textContent).toContain("Waits for you. No tokens.");
    expect(container.textContent).not.toMatch(/Waits for you\.\s*Waits for you/);
  });
});

describe("Narrow screens", () => {
  it("keep the drawer with the same menu bar; no » there", async () => {
    openShopFlow();
    explainGate();
    const had = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: q.includes("max-width: 1099px"), media: q, addEventListener() {}, removeEventListener() {}, onchange: null })) as unknown as typeof window.matchMedia;
    try {
      const user = userEvent.setup();
      render(<App />);
      await user.click(await screen.findByRole("button", { name: /^wait for you, spec approval, / }));
      const dlg = await screen.findByRole("dialog", { name: "What spec approval does" });
      await user.click(within(dlg).getByRole("tab", { name: "Commands 3" }));
      await waitFor(() => expect(within(dlg).getAllByRole("listitem")).toHaveLength(3));
      expect(screen.queryByRole("button", { name: "Close the side panel" })).not.toBeInTheDocument();
    } finally {
      window.matchMedia = had;
    }
  });
});
