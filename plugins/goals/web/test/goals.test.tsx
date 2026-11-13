// The Goals page inside keel's web (web/src/test/setup.ts runs every plugin's setup()): adding a goal, planning, the plan that waits, starting
// a step, a limit that blocks, and the plain words about owners.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { render, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { slotItems, SLOTS } from "../../../../web/src/sdk";
import type { InboxCardItem } from "../../../../web/src/sdk";
import { server } from "../../../../web/src/test/setup";
import type { Goal, GoalView } from "../Goals";

const GOAL: Goal = { id: "g1", project_id: "p", title: "Export invoices", why: null, done_when: "People can export", cap_tokens: 100000, max_flows: 3, status: "new", plan_thread: null, plan_ready: false,
  created_at: "2026-10-01T10:00:00Z", updated_at: "2026-10-01T10:00:00Z" };
const PROG = { done: 0, total: 0, tokens: 0, cost_usd: 0, flows: 0, blocked: null };
const LIMITS = ["A goal has no named owner. keel has one person."];
const STEP = { id: "s1", n: 1, title: "Add the button", request: "Put a button", workflow: "small", skipped: false, thread_id: null, state: "todo", tokens: 0, cost_usd: 0 };

let view: GoalView;
let calls: { what: string; body?: unknown }[];
const main = () => within(document.getElementById("main")!);
const open = (hash: string) => { location.hash = hash; render(<App />); };

beforeEach(() => {
  view = { goal: GOAL, steps: [], progress: PROG, draft: null, limits: LIMITS };
  calls = [];
  server.use(
    http.get("/api/projects/:pid/goals", () => HttpResponse.json([{ goal: view.goal, progress: view.progress }])),
    http.post("/api/projects/:pid/goals", async ({ request }) => {
      calls.push({ what: "add", body: await request.json() });
      return HttpResponse.json(GOAL);
    }),
    http.get("/api/projects/:pid/goals/:id", () => HttpResponse.json(view)),
    http.post("/api/projects/:pid/goals/:id/plan", () => {
      calls.push({ what: "plan" });
      view = { ...view, goal: { ...view.goal, status: "planning", plan_thread: "tp" }, draft: [{ title: "Add the button", request: "x", workflow: "small" }] };
      return HttpResponse.json(view.goal);
    }),
    http.post("/api/projects/:pid/goals/:id/steps/:sid/start", () => {
      calls.push({ what: "start" });
      view = { ...view, steps: [{ ...STEP, thread_id: "t1", state: "running" }], progress: { ...PROG, total: 1, flows: 1, blocked: "A step of this goal runs or waits. Finish it first." } };
      return HttpResponse.json(view);
    }),
    http.post("/api/projects/:pid/goals/:id/steps/:sid/skip", () => {
      calls.push({ what: "skip" });
      return HttpResponse.json(view);
    }),
    http.post("/api/projects/:pid/goals/:id/status/:status", ({ params }) => {
      calls.push({ what: `status:${String(params.status)}` });
      view = { ...view, goal: { ...view.goal, status: params.status as Goal["status"] } };
      return HttpResponse.json(view.goal);
    }),
  );
});

describe("Goals", () => {
  it("lists goals and adds one with its limits, and starts nothing", async () => {
    open("#/goals");
    const table = await main().findByRole("table", { name: "Goals" });
    expect(within(table).getByRole("link", { name: "Export invoices" }).getAttribute("href")).toBe("#/goals/g1");
    const form = main().getByRole("form", { name: "Add a goal" });
    // #97 text fields say so: keel's field style is for text inputs (it looked like the browser's own boxes before)
    for (const f of within(form).getAllByRole("textbox")) expect(f).toHaveAttribute("type", "text");
    await userEvent.type(within(form).getByLabelText(/^The goal/), "New goal");
    await userEvent.type(within(form).getByLabelText(/^Done when/), "It works");
    await userEvent.type(within(form).getByLabelText(/^Most tokens/), "5000");
    await userEvent.click(within(form).getByRole("button", { name: "Add it" }));
    await waitFor(() => expect(calls[0]?.what).toBe("add"));
    expect(calls[0].body).toMatchObject({ title: "New goal", done_when: "It works", cap_tokens: 5000, max_flows: null });
    expect(calls.map((c) => c.what)).toEqual(["add"]);
  });

  it("says what goes to the model company before planning, and shows the plan that waits", async () => {
    open("#/goals/g1");
    expect(await main().findByText(/go to your model company/)).toBeTruthy();
    await userEvent.click(main().getByRole("button", { name: "Plan it" }));
    expect((await main().findByRole("list", { name: "Proposed steps" })).textContent).toContain("Add the button");
    expect(main().getByRole("link", { name: "Read and approve the plan" }).getAttribute("href")).toBe("#/flow/tp");
  });

  it("says plainly that a goal has no named owner", async () => {
    open("#/goals/g1");
    expect(await main().findByText(/no named owner/)).toBeTruthy();
  });

  it("a person starts a step; while it runs the next is blocked and it says why", async () => {
    view = { ...view, goal: { ...GOAL, status: "active" }, steps: [STEP], progress: { ...PROG, total: 1 } };
    open("#/goals/g1");
    const row = await main().findByRole("listitem", { name: "Add the button" });
    await userEvent.click(within(row).getByRole("button", { name: "Start this step" }));
    expect(await main().findByText(/runs or waits/)).toBeTruthy();
    expect(calls).toEqual([{ what: "start" }]);
    expect(main().getByRole("link", { name: "Open its flow" }).getAttribute("href")).toBe("#/flow/t1");
  });

  it("a limit that is reached turns Start off and says so", async () => {
    view = { ...view, goal: { ...GOAL, status: "active" }, steps: [STEP], progress: { ...PROG, total: 1, blocked: "The goal's limit of 3 flows is reached." } };
    open("#/goals/g1");
    expect(await main().findByText(/limit of 3 flows/)).toBeTruthy();
    expect((main().getByRole("button", { name: "Start this step" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("only a person says it is done", async () => {
    view = { ...view, goal: { ...GOAL, status: "active" } };
    open("#/goals/g1");
    await userEvent.click(await main().findByRole("button", { name: "It is done" }));
    await waitFor(() => expect(calls).toEqual([{ what: "status:done" }]));
  });

  it("has its card in the Inbox", () => {
    expect(slotItems<InboxCardItem>(SLOTS.inboxCard).find((c) => c.id === "goals")?.kinds).toEqual({ "goal-step": "goal's next step" });
  });

  it("imports only react, @keel/web-sdk and its own files", () => {
    const here = join(__dirname, "..");
    const outside = readdirSync(here).filter((f) => /\.tsx?$/.test(f))
      .flatMap((f) => [...readFileSync(join(here, f), "utf8").matchAll(/from "([^"]+)"/g)].map((m) => m[1]))
      .filter((from) => !from.startsWith("./") && from !== "react" && from !== "@keel/web-sdk");
    expect(outside).toEqual([]);
  });
});
