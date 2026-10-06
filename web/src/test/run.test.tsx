// v0.4.2 UX: the shell (sidebar and the compact phone bar with its menu) and the Run pages — Jobs shows the history
// right away, Live agents opens the last agent's feed, the Flow side panel shows what happened, All projects is the
// home with Answer / Open flow / Start a flow, and the Inbox folds long details and moves the focus on.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { Job } from "../api";
import { lastActivity } from "../pages/Projects";
import type { InboxItem, InboxView } from "../inboxApi";
import * as fx from "./fixtures";
import { db, FakeEventSource, server } from "./setup";

const main = () => within(document.getElementById("main")!);
const ago = (s: number) => new Date(Date.now() - s * 1000).toISOString();

/** GET /api/jobs answers from this list (filtered by status and project like the api). */
function jobsServer(list: Job[]) {
  server.use(http.get("/api/jobs", ({ request }) => {
    const u = new URL(request.url);
    const st = u.searchParams.get("status");
    const pid = u.searchParams.get("project");
    return HttpResponse.json(list.filter((j) => (!st || j.status === st) && (!pid || j.project_id === pid)));
  }));
}
const finishedOnly = () => fx.jobs.filter((j) => j.status !== "running");

async function at(hash: string) {
  location.hash = hash;
  render(<App />);
}

describe("Jobs", () => {
  it("shows the history right away when nothing runs, with agent, flow step, model, tokens, time and status", async () => {
    jobsServer(finishedOnly());
    await at("#/jobs");
    const table = await main().findByRole("table", { name: "Agent calls" });
    expect(screen.queryByRole("tab", { name: /Running now/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Running now/ })).not.toBeInTheDocument();
    expect(main().getByText("nothing running")).toBeInTheDocument();
    const head = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(head).toEqual(expect.arrayContaining(["Agent", "Flow step", "Model", "Tokens in / out", "Time", "Status"]));
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("implementer");
    expect(row).toHaveTextContent("green · s5 · AC-002");
    expect(row).toHaveTextContent("Copilot gpt-5");
    expect(row).toHaveTextContent("31k / 6k");
    expect(row).toHaveTextContent("4m10s");
    expect(row).toHaveTextContent("done");
  });

  it("keeps Running now on top while something runs, with the history under it", async () => {
    await at("#/jobs");
    const now = await main().findByRole("region", { name: /Running now/ });
    expect(within(now).getByText("ac-reviewer")).toBeInTheDocument();
    expect(within(now).getByRole("link", { name: "Watch live" })).toHaveAttribute("href", "#/live/j-482");
    expect(main().getByText("1 running now")).toBeInTheDocument();
    expect(await main().findByRole("table", { name: "Agent calls" })).toBeInTheDocument();
  });

  it("a row opens its steps in place and closes again", async () => {
    const user = userEvent.setup();
    jobsServer(finishedOnly());
    await at("#/jobs");
    const table = await main().findByRole("table", { name: "Agent calls" });
    const row = within(table).getAllByRole("row")[1];
    await user.click(row);
    await waitFor(() => expect(location.hash).toBe("#/jobs/j-480"));
    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(await within(table).findByText("implementer · green · step feed")).toBeInTheDocument();
    expect(await within(table).findByRole("region", { name: "Outcome" })).toBeInTheDocument();
    await user.click(within(table).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(location.hash).toBe("#/jobs"));
  });

  it("empty: says what to do and offers Start a flow; filters that match nothing offer Clear filters", async () => {
    const user = userEvent.setup();
    jobsServer([]);
    await at("#/jobs");
    expect(await main().findByText("No agent has run in this project yet")).toBeInTheDocument();
    await user.click(main().getByRole("button", { name: "Start a flow" }));
    expect(await screen.findByRole("dialog", { name: "Start a flow" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await user.selectOptions(main().getByLabelText("Filter by status"), "failed");
    expect(await main().findByText("No call matches these filters")).toBeInTheDocument();
    await user.click(main().getAllByRole("button", { name: "Clear filters" })[0]);
    expect(main().getByLabelText("Filter by status")).toHaveValue("");
    expect(await main().findByText("No agent has run in this project yet")).toBeInTheDocument();
  });
});

describe("Live agents", () => {
  it("with nobody working, shows the last agent's feed and lists the recent ones (not folded)", async () => {
    jobsServer(finishedOnly());
    await at("#/live");
    expect(await main().findByText("No agent is working right now. This is the last one that ran.")).toBeInTheDocument();
    expect(main().getByRole("heading", { name: "implementer · AC-002" })).toBeInTheDocument();
    expect(await main().findByRole("region", { name: "Outcome" })).toBeInTheDocument();
    const recent = main().getByRole("region", { name: "Recent" });
    const pick = within(recent).getByRole("button", { name: /implementer/ });
    expect(pick).toHaveAttribute("aria-pressed", "true");
    expect(pick).toHaveTextContent("green · AC-002 · Copilot");
    expect(main().getByText("No agent is working in this project. When a flow reaches an agent step, it shows up here.")).toBeInTheDocument();
  });

  it("while an agent works, its feed is the one shown", async () => {
    await at("#/live");
    expect(await main().findByRole("heading", { name: "ac-reviewer · AC-002" })).toBeInTheDocument();
    expect(main().queryByText(/This is the last one that ran/)).not.toBeInTheDocument();
    expect(main().getByText("1 agent working now")).toBeInTheDocument();
  });

  it("when no agent ever ran, the empty state says how to start", async () => {
    jobsServer([]);
    await at("#/live");
    expect(await main().findByText("No agent has run in this project yet")).toBeInTheDocument();
    expect(main().getByRole("button", { name: "Start a flow" })).toBeInTheDocument();
    expect(main().getByRole("link", { name: "Open the flow" })).toHaveAttribute("href", "#/flow");
  });
});

describe("Flow side panel: Now", () => {
  it("while the flow waits, Events shows what happened (the saved steps, newest first); All checkpoints opens them", async () => {
    const user = userEvent.setup();
    await at("#/flow");
    const side = await screen.findByRole("complementary", { name: "Now, the selected step and checkpoints" });
    const rows = await within(side).findAllByTestId("event-checkpoint");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("commit feat(AC-002) a81c3f0"), expect.stringContaining("verify_green"), expect.stringContaining("green"),
    ]);
    expect(within(side).queryByText("Nothing happened yet. Events show here as they happen.")).not.toBeInTheDocument();
    expect(within(side).getByRole("button", { name: "Go to it" })).toBeInTheDocument();
    await user.click(within(side).getByRole("button", { name: "All checkpoints" }));
    expect(within(side).getByRole("tab", { name: "Checkpoints" })).toHaveAttribute("aria-selected", "true");
    expect(await within(side).findByText("#31")).toBeInTheDocument();
  });

  it("live events come first, then the earlier saved steps", async () => {
    await at("#/flow");
    const side = await screen.findByRole("complementary", { name: "Now, the selected step and checkpoints" });
    await within(side).findAllByTestId("event-checkpoint");
    act(() => FakeEventSource.emit("gate.waiting", { type: "gate.waiting", thread_id: "th_7f3a", project_id: "ludus-engine", step: "ac_gate",
      at: new Date().toISOString(), data: { title: "AC gate" } }));
    const events = await within(side).findByLabelText("What happened, newest first");
    await waitFor(() => expect(events.firstElementChild).toHaveTextContent("AC gate waits for you"));
    expect(within(events).getByText("Earlier")).toBeInTheDocument();
    expect(within(events).getAllByTestId("event-checkpoint")).toHaveLength(3);
  });
});

describe("All projects (home)", () => {
  it("shows each project's status with Answer, Watch live, Open flow and Start a flow; no usage card", async () => {
    await at("#/projects");
    const list = await main().findByRole("list", { name: "Projects" });
    const ludus = within(list).getByRole("listitem", { name: /ludus-engine/ });
    expect(ludus).toHaveTextContent("1 waiting for you");
    expect(ludus).toHaveTextContent("1 agent working");
    expect(ludus).toHaveTextContent("ac-gate");
    expect(ludus).toHaveTextContent("ACs 1 / 3");
    expect(ludus).toHaveTextContent("⎇ feat/scores");
    await waitFor(() => expect(ludus).toHaveTextContent("working now"));
    expect(within(ludus).getByRole("link", { name: "Answer what waits in ludus-engine" })).toHaveAttribute("href", "#/inbox/ludus-engine");
    expect(within(ludus).getByRole("button", { name: "Watch the agents of ludus-engine" })).toBeInTheDocument();
    const yegi = within(list).getByRole("listitem", { name: /YegiResearcher/ });
    expect(yegi).toHaveTextContent("No flow yet");
    expect(within(yegi).queryByRole("button", { name: /Open the flow/ })).not.toBeInTheDocument();
    expect(main().getByText("◆ 1 waiting for you")).toBeInTheDocument();
    expect(main().queryByTestId("usage-claude")).toBeNull();
  });

  it("Start a flow on a row opens the drawer for that project, without switching the shown project", async () => {
    const user = userEvent.setup();
    await at("#/projects");
    await user.click(await main().findByRole("button", { name: "Start a flow in YegiResearcher" }));
    const dlg = await screen.findByRole("dialog", { name: "Start a flow" });
    expect(within(dlg).getByLabelText("Project")).toHaveValue("yegi");
    expect(localStorage.getItem("keel2.project")).toBeNull();
  });

  it("Watch live and Open flow switch the project and open that screen", async () => {
    const user = userEvent.setup();
    await at("#/projects");
    await user.click(await main().findByRole("button", { name: "Watch the agents of platform" }));
    await waitFor(() => expect(location.hash).toBe("#/live"));
    expect(localStorage.getItem("keel2.project")).toBe("platform");
  });

  it("Answer opens the Inbox filtered to that project", async () => {
    const user = userEvent.setup();
    inboxServer(inboxItems());
    await at("#/projects");
    await user.click(await main().findByRole("link", { name: "Answer what waits in ludus-engine" }));
    await waitFor(() => expect(location.hash).toBe("#/inbox/ludus-engine"));
    expect(await screen.findByLabelText("In project")).toHaveValue("ludus-engine");
    expect(await screen.findAllByTestId("inbox-item")).toHaveLength(1);
  });

  it("durations past an hour read as hours and minutes", async () => {
    const { dur } = await import("../format");
    expect(dur(250_000)).toBe("4m10s");
    expect(dur(4_319_000)).toBe("1h 11m");
    expect(dur(40 * 3600_000 + 5 * 60_000)).toBe("40h 05m");
  });

  it("last activity is the newest call per project; a running one counts as now", () => {
    const m = lastActivity([
      { ...fx.jobs[1], project_id: "a", ended_at: ago(600) },
      { ...fx.jobs[1], project_id: "a", ended_at: ago(60) },
      { ...fx.jobs[0], project_id: "b" },
      { ...fx.jobs[1], project_id: "b", ended_at: ago(5) },
    ]);
    expect(Date.now() - Date.parse(m.a.at)).toBeLessThan(70_000);
    expect(m.a.running).toBe(false);
    expect(m.b.running).toBe(true);
  });
});

describe("shell", () => {
  it("the phone bar is menu + project + bell; the menu holds every screen, the status and the theme switch", async () => {
    const user = userEvent.setup();
    await at("#/flow");
    await screen.findByText(/AC gate — AC-002/);
    // no group switcher and no second Live pill in the bar any more: one row of screens
    expect(screen.queryByRole("tablist", { name: "Sections" })).not.toBeInTheDocument();
    expect(screen.getAllByTestId("live")).toHaveLength(1);
    const menu = screen.getByRole("button", { name: "Menu" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    await user.click(menu);
    const sheet = await screen.findByRole("dialog", { name: "Menu" });
    const nav = within(sheet).getByRole("navigation", { name: "All screens" });
    expect(within(nav).getByRole("link", { name: /^Budget/ })).toHaveAttribute("href", "#/budget");
    expect(within(nav).getByRole("link", { name: /^Flow/ })).toHaveFocus();
    expect(within(sheet).getByTestId("live")).toBeInTheDocument();
    const before = document.documentElement.dataset.theme;
    await user.click(within(sheet).getByRole("button", { name: /Switch to the (light|dark) theme/ }));
    expect(document.documentElement.dataset.theme).not.toBe(before);
    expect(localStorage.getItem("keel2.theme")).toBe(document.documentElement.dataset.theme);
    await user.click(within(nav).getByRole("link", { name: /^Jobs/ }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Menu" })).not.toBeInTheDocument());
    await user.click(menu);
    await screen.findByRole("dialog", { name: "Menu" });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Menu" })).not.toBeInTheDocument());
    expect(menu).toHaveFocus();
  });

  it("the sidebar nav starts with All projects and marks the screen you are on", async () => {
    await at("#/settings");
    const nav = await screen.findByRole("navigation", { name: "Screens" });
    const links = within(nav).getAllByRole("link");
    expect(links[0]).toHaveTextContent("All projects");
    expect(within(nav).getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");
  });
});

// ---------- inbox ----------

function inboxItems(): InboxItem[] {
  return [
    { project_id: "ludus-engine", project_name: "ludus-engine", thread_id: "th_7f3a", flow: "Scores for players", workflow_id: "feature",
      step: "ac_gate", kind: "gate", title: "AC gate · AC-002", detail: Array.from({ length: 14 }, (_, i) => `line ${i + 1} of the review`).join("\n"),
      more: false, options: ["approve", "reject"], id: "q-ac2", ac: "AC-002", since: ago(600) },
    { project_id: "platform", project_name: "platform", thread_id: "th_p2", flow: "Add retries", workflow_id: "change",
      step: "commit", kind: "dependency", title: "Approve new dependency", detail: "pyproject.toml: httpx", more: false,
      options: ["approve", "reject"], labels: { approve: "Allow", reject: "Refuse" }, id: "q-dep", since: ago(60) },
  ];
}

function inboxServer(list: InboxItem[]) {
  const state = { list: [...list] };
  server.use(
    http.get("/api/inbox", () => HttpResponse.json({
      items: state.list, count: state.list.length, kinds: [...new Set(state.list.map((i) => i.kind))].sort(),
      projects: [...new Set(state.list.map((i) => i.project_id))].map((id) => ({ id, name: id, count: state.list.filter((i) => i.project_id === id).length })),
    } satisfies InboxView)),
    http.post("/api/inbox/:tid/act", async ({ request, params }) => {
      db.calls.push({ method: "POST", path: `/api/inbox/${params.tid}/act`, body: await request.json() });
      state.list = state.list.filter((i) => i.thread_id !== params.tid);
      return HttpResponse.json({ thread_id: params.tid, status: "running" });
    }),
  );
}

describe("Inbox", () => {
  it("folds a long detail behind Show more", async () => {
    const user = userEvent.setup();
    inboxServer(inboxItems());
    await at("#/inbox");
    const gate = await screen.findByRole("article", { name: "AC gate · AC-002" });
    const more = within(gate).getByRole("button", { name: "Show more" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await user.click(more);
    expect(within(gate).getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
    // a short detail has no fold
    expect(within(screen.getByRole("article", { name: "Approve new dependency" })).queryByRole("button", { name: "Show more" })).toBeNull();
  });

  it("after an answer the focus moves to the next item, and to the empty state after the last", async () => {
    const user = userEvent.setup();
    inboxServer(inboxItems());
    await at("#/inbox");
    const gate = await screen.findByRole("article", { name: "AC gate · AC-002" });
    await user.click(within(gate).getByRole("button", { name: "Approve AC-002" }));
    await waitFor(() => expect(screen.getByRole("article", { name: "Approve new dependency" })).toHaveFocus());
    await user.click(within(screen.getByRole("article", { name: "Approve new dependency" })).getByRole("button", { name: "Allow" }));
    const empty = await screen.findByText("Nothing is waiting for you");
    await waitFor(() => expect(empty.closest(".empty-state")).toHaveFocus());
  });
});
