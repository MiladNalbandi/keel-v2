// v0.15.4 run history: when no flow runs or waits, the Flow page lists every run of the project. A run opens read only
// (nothing that changes a flow is called), only the newest stopped run can be resumed, and a run can be deleted from the
// history after an in-page confirm, never while it runs or waits or while it can still be resumed.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { RunRow, ThreadState } from "../api";
import * as fx from "./fixtures";
import { db, server } from "./setup";

const PID = "ludus-engine";
const at = (minAgo: number) =>
  new Date(Date.now() - minAgo * 60_000).toISOString();

function row(
  over: Partial<RunRow> & Pick<RunRow, "thread_id" | "title" | "status">,
): RunRow {
  return {
    workflow_id: "feature",
    phase: "green",
    current: null,
    waiting: null,
    acs_done: 1,
    acs_total: 3,
    tokens: 120_000,
    where: "folder",
    branch: null,
    error: null,
    created_at: at(600),
    updated_at: at(560),
    cost_usd: 0.5,
    latest: false,
    ...over,
  };
}

function view(
  tid: string,
  title: string,
  status: ThreadState["status"],
  error?: string,
) {
  db.threadViews[tid] = {
    thread: {
      ...structuredClone(fx.thread),
      thread_id: tid,
      title,
      status,
      waiting: undefined,
      ...(error ? { error } : {}),
    },
    workflow: structuredClone(fx.featureWorkflow),
  };
}

/** The current flow (th_7f3a) is done; a newer flow was stopped in a worktree; two older ones failed or were stopped. */
function seed(current: ThreadState["status"] = "done") {
  const f = db.flows[PID];
  f.thread = {
    ...f.thread!,
    status: current,
    waiting: current === "waiting" ? f.thread!.waiting : undefined,
  };
  db.runs[PID] = [
    row({
      thread_id: "th_old",
      title: "Old fix",
      status: "failed",
      workflow_id: "fix",
      error: "The tests failed after 3 tries",
      created_at: at(3000),
      updated_at: at(2950),
    }),
    row({
      thread_id: "th_7f3a",
      title: "Scores for players",
      status: current,
      created_at: at(900),
      updated_at: at(800),
      acs_done: 3,
      acs_total: 3,
    }),
    row({
      thread_id: "th_new",
      title: "Dollar prices",
      status: "stopped",
      where: "worktree",
      branch: "feat/dollar-prices",
      created_at: at(60),
      updated_at: at(20),
      cost_usd: 1.2,
      latest: true,
    }),
    row({
      thread_id: "th_older",
      title: "Older stop",
      status: "stopped",
      created_at: at(5000),
      updated_at: at(4990),
    }),
  ];
  view("th_old", "Old fix", "failed", "The tests failed after 3 tries");
  view("th_new", "Dollar prices", "stopped");
  view("th_older", "Older stop", "stopped");
}

const changes = () =>
  db.calls.filter(
    (c) => c.method !== "GET" && !c.path.endsWith("/explain-step"),
  );

describe("run history", () => {
  it("lists every run, newest first, with its status, time, cost, result and branch, only while no flow runs or waits", async () => {
    seed();
    location.hash = "#/flow";
    render(<App />);
    const runs = await screen.findByRole("region", { name: "Runs" });
    const rows = within(runs).getAllByRole("listitem");
    expect(rows.map((r) => r.getAttribute("aria-label"))).toEqual([
      "Run Dollar prices",
      "Run Scores for players",
      "Run Old fix",
      "Run Older stop",
    ]);
    expect(rows[0]).toHaveTextContent("stopped");
    expect(rows[0]).toHaveTextContent("$1.20");
    expect(rows[0]).toHaveTextContent("120k tokens");
    expect(rows[0]).toHaveTextContent("feat/dollar-prices");
    expect(rows[0]).toHaveTextContent("took 40m");
    expect(rows[1]).toHaveTextContent("ACs done 3 of 3");
    expect(rows[1]).toHaveTextContent("current");
    expect(rows[2]).toHaveTextContent("The tests failed after 3 tries");
    // the History fold-out above is not shown twice
    expect(screen.queryByRole("button", { name: /History/ })).toBeNull();
    // search and the status filter
    await userEvent.type(
      within(runs).getByRole("searchbox", { name: "Search the runs" }),
      "dollar",
    );
    expect(within(runs).getAllByRole("listitem")).toHaveLength(1);
    await userEvent.clear(
      within(runs).getByRole("searchbox", { name: "Search the runs" }),
    );
    await userEvent.selectOptions(
      within(runs).getByRole("combobox", {
        name: "Show the runs with one status",
      }),
      "stopped",
    );
    expect(
      within(runs)
        .getAllByRole("listitem")
        .map((r) => r.getAttribute("aria-label")),
    ).toEqual(["Run Dollar prices", "Run Older stop"]);
    expect(changes()).toEqual([]);
  });

  it("is not there while the current flow waits: the page is as it was, with its History fold-out", async () => {
    seed("waiting");
    location.hash = "#/flow";
    render(<App />);
    expect(
      await screen.findByRole("region", { name: "Gate waits for you" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Runs" })).toBeNull();
    expect(screen.getByRole("button", { name: /History/ })).toBeInTheDocument();
  });

  it("Load opens a run read only: a banner, its steps, outcome and checkpoints, no Rewind and no call that changes a flow; Back returns", async () => {
    const user = userEvent.setup();
    seed();
    location.hash = "#/flow";
    render(<App />);
    const runs = await screen.findByRole("region", { name: "Runs" });
    await user.click(
      within(runs).getByRole("button", { name: "Load the run Old fix" }),
    );
    const banner = await screen.findByRole("region", {
      name: "An earlier run (read only)",
    });
    expect(banner).toHaveTextContent("changes nothing in the current flow");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Old fix" }),
    ).toBeInTheDocument();
    expect(screen.getByText("The flow failed.")).toBeInTheDocument();
    expect(
      screen.getAllByText("Only the last stopped flow can be resumed.").length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByRole("button", { name: "Resume the flow" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop flow" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Start a flow" })).toBeNull();
    // its steps, in each view
    await user.click(screen.getByRole("button", { name: "Table" }));
    await user.click(screen.getByRole("button", { name: "Graph" }));
    await user.click(screen.getByRole("button", { name: "Blocks" }));
    // its checkpoints, without Rewind
    await user.click(screen.getByRole("tab", { name: "Checkpoints" }));
    expect(
      await screen.findByText(`#${fx.checkpoints[1].n}`),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rewind here" })).toBeNull();
    expect(
      within(runs).getByRole("button", { name: "Load the run Old fix" }),
    ).toBeDisabled();
    expect(changes()).toEqual([]);
    // the current flow did not move
    expect(db.flows[PID].thread!.status).toBe("done");

    await user.click(
      within(banner).getByRole("button", { name: "Back to the current flow" }),
    );
    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Scores for players",
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "An earlier run (read only)" }),
    ).toBeNull();
    expect(changes()).toEqual([]);
  });

  it("Resume shows only on the newest run when it was stopped; it asks first, then rewinds that run and shows it live", async () => {
    const user = userEvent.setup();
    seed();
    location.hash = "#/flow";
    render(<App />);
    const runs = await screen.findByRole("region", { name: "Runs" });
    const resumes = within(runs).getAllByRole("button", {
      name: /^Resume the run/,
    });
    expect(resumes.map((b) => b.getAttribute("aria-label"))).toEqual([
      "Resume the run Dollar prices",
    ]);
    expect(
      within(runs).getByRole("listitem", { name: "Run Older stop" }),
    ).toHaveTextContent("Only the last stopped flow can be resumed.");

    // loaded, the newest stopped run offers Resume too
    await user.click(
      within(runs).getByRole("button", { name: "Load the run Dollar prices" }),
    );
    expect(
      await screen.findByRole("region", { name: "An earlier run (read only)" }),
    ).toHaveTextContent("Resume goes on from its last saved step");
    expect(
      await screen.findByRole("button", { name: "Resume the flow" }),
    ).toBeInTheDocument();

    await user.click(resumes[0]);
    const ask = await within(runs).findByRole("group", {
      name: "Resume the flow",
    });
    const newest = [...fx.checkpoints].sort((a, b) => b.n - a.n)[0];
    expect(ask).toHaveTextContent(`Resume after ${newest.step}`);
    expect(changes()).toEqual([]);
    await user.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(
      within(runs).queryByRole("group", { name: "Resume the flow" }),
    ).toBeNull();

    await user.click(resumes[0]);
    await user.click(
      within(
        await within(runs).findByRole("group", { name: "Resume the flow" }),
      ).getByRole("button", { name: "Yes, resume" }),
    );
    await waitFor(() =>
      expect(changes()).toEqual([
        {
          method: "POST",
          path: "/api/threads/th_new/rewind",
          body: { checkpoint_id: newest.id },
        },
      ]),
    );
    await waitFor(() => expect(location.hash).toBe("#/flow/th_new"));
  });

  it("Delete asks in the page first; a run that runs, waits or can still be resumed cannot be deleted and says why", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm");
    seed();
    db.runs[PID].push(
      row({
        thread_id: "th_run",
        title: "Runs elsewhere",
        status: "running",
        where: "worktree",
        created_at: at(10),
        updated_at: at(1),
      }),
    );
    const deleted: string[] = [];
    server.use(
      http.delete("/api/projects/:pid/flows/:tid", ({ params }) => {
        const tid = params.tid as string;
        if (tid === "th_older")
          return HttpResponse.json(
            {
              error: "This flow can still be resumed",
              hint: "Resume it first.",
            },
            { status: 409 },
          );
        deleted.push(tid);
        db.calls.push({
          method: "DELETE",
          path: `/api/projects/${params.pid}/flows/${tid}`,
          body: null,
        });
        db.runs[PID] = db.runs[PID].filter((r) => r.thread_id !== tid);
        return HttpResponse.json({ ok: true, thread_id: tid });
      }),
    );
    location.hash = "#/flow";
    render(<App />);
    const runs = await screen.findByRole("region", { name: "Runs" });
    const del = (title: string) =>
      within(runs).getByRole("button", { name: `Delete the run ${title}` });

    // refused here, with the reason next to the disabled button
    expect(del("Runs elsewhere")).toBeDisabled();
    expect(del("Runs elsewhere")).toHaveAccessibleDescription(
      "It still runs. Stop it first.",
    );
    expect(
      within(runs).getByRole("button", {
        name: "Open the flow Runs elsewhere",
      }),
    ).toBeInTheDocument();
    expect(del("Dollar prices")).toBeDisabled();
    expect(del("Dollar prices")).toHaveAccessibleDescription(
      "It can still be resumed, so it stays.",
    );
    expect(del("Scores for players")).toBeEnabled();

    // asked in the page; Cancel deletes nothing
    await user.click(del("Old fix"));
    let ask = within(runs).getByRole("group", { name: "Confirm" });
    expect(ask).toHaveTextContent("Delete “Old fix” from the history?");
    await user.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(deleted).toEqual([]);

    // a run that is open read only goes back to the current flow when it is deleted
    await user.click(
      within(runs).getByRole("button", { name: "Load the run Old fix" }),
    );
    await screen.findByRole("region", { name: "An earlier run (read only)" });
    await user.click(del("Old fix"));
    ask = within(runs).getByRole("group", { name: "Confirm" });
    await user.click(
      within(ask).getByRole("button", { name: "Yes, delete it" }),
    );
    await waitFor(() => expect(deleted).toEqual(["th_old"]));
    await waitFor(() =>
      expect(
        within(runs).queryByRole("listitem", { name: "Run Old fix" }),
      ).toBeNull(),
    );
    expect(
      screen.queryByRole("region", { name: "An earlier run (read only)" }),
    ).toBeNull();
    expect(await screen.findByRole("status")).toHaveTextContent(
      "“Old fix” is gone from the history.",
    );

    // the server's refusal is shown, and the run stays
    await user.click(del("Older stop"));
    await user.click(
      within(within(runs).getByRole("group", { name: "Confirm" })).getByRole(
        "button",
        { name: "Yes, delete it" },
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "This flow can still be resumed Resume it first.",
      ),
    );
    expect(
      within(runs).getByRole("listitem", { name: "Run Older stop" }),
    ).toBeInTheDocument();
    expect(changes().filter((c) => c.method !== "DELETE")).toEqual([]);
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});
