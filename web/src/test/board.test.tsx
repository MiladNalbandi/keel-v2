// v0.7.x: several flows at once. The Flow page's board (a flow in the project folder, others in worktrees of their
// own), opening one flow by id, removing a finished flow's worktree, where a new flow runs, and the Inbox by flow.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { BoardFlow, ThreadState } from "../api";
import type { InboxItem } from "../inboxApi";
import * as fx from "./fixtures";
import { db, server } from "./setup";

const flowCard = (over: Partial<BoardFlow>): BoardFlow => ({
  thread_id: "th_7f3a",
  title: "Scores for players",
  workflow_id: "feature",
  status: "waiting",
  phase: "ac-gate",
  current: "s8",
  waiting: { step: "s8", kind: "gate", title: "AC gate" },
  where: "folder",
  worktree: null,
  branch: "feat/scores",
  files: ["src/price.js"],
  updated_at: new Date().toISOString(),
  ...over,
});

function twoFlows() {
  db.boards["ludus-engine"] = {
    flows: [
      flowCard({}),
      flowCard({
        thread_id: "th_w2",
        title: "Dollar prices",
        status: "running",
        phase: "green",
        waiting: null,
        where: "worktree",
        worktree: "flow-dollar-prices-a1b2c3",
        branch: "feat/dollar-prices",
        files: ["src/price.js", "src/usd.js"],
      }),
      flowCard({
        thread_id: "th_old",
        title: "Old idea",
        status: "done",
        phase: "none",
        waiting: null,
        where: "worktree",
        worktree: "flow-old-idea-9f9f9f",
        branch: "feat/old-idea",
        files: [],
        worktree_left: true,
      }),
    ],
    overlaps: [{ file: "src/price.js", flows: ["th_7f3a", "th_w2"] }],
    conflicts: [{ a: "th_7f3a", b: "th_w2", files: ["src/price.js"] }],
    order: ["th_w2", "th_7f3a"],
  };
  const t: ThreadState = {
    ...structuredClone(fx.thread),
    thread_id: "th_w2",
    title: "Dollar prices",
    status: "running",
    waiting: undefined,
    phase: "green",
  };
  db.threadViews.th_w2 = {
    thread: t,
    workflow: structuredClone(fx.featureWorkflow),
  };
}

describe("the flow board", () => {
  it("shows the project's flows side by side, what they share and where they conflict, and opens one by id", async () => {
    const user = userEvent.setup();
    twoFlows();
    location.hash = "#/flow";
    render(<App />);
    const board = await screen.findByRole("region", {
      name: "Flows of this project",
    });
    expect(within(board).getAllByRole("article")).toHaveLength(3);
    expect(within(board).getByText("feat/dollar-prices")).toBeInTheDocument();
    expect(within(board).getAllByText("own worktree")).toHaveLength(2);
    expect(within(board).getByText("waits: AC gate")).toBeInTheDocument();
    expect(
      within(board).getByText(/2 files changed · 1 shared with another flow/),
    ).toBeInTheDocument();
    expect(
      within(board)
        .getByText("Files more than one flow changes")
        .closest("div"),
    ).toHaveTextContent("src/price.js — Scores for players, Dollar prices");
    expect(
      within(board)
        .getByText("Branches that will not merge cleanly")
        .closest("div"),
    ).toHaveTextContent("Scores for players and Dollar prices: src/price.js");
    expect(
      within(board).getByText(
        /Merge order: 1\. Dollar prices 2\. Scores for players/,
      ),
    ).toBeInTheDocument();
    // the project folder's flow is the one shown first
    expect(
      within(board)
        .getByRole("button", { name: "Open the flow Scores for players" })
        .closest("article"),
    ).toHaveAttribute("aria-current", "true");

    await user.click(
      within(board).getByRole("button", {
        name: "Open the flow Dollar prices",
      }),
    );
    await waitFor(() => expect(location.hash).toBe("#/flow/th_w2"));
    await waitFor(() =>
      expect(
        within(screen.getByRole("region", { name: "Flows of this project" }))
          .getByRole("button", { name: "Open the flow Dollar prices" })
          .closest("article"),
      ).toHaveAttribute("aria-current", "true"),
    );
    expect(
      await screen.findByRole("heading", { name: /Dollar prices/ }),
    ).toBeInTheDocument();
  });

  it("removes a finished flow's worktree (its branch stays)", async () => {
    const user = userEvent.setup();
    twoFlows();
    location.hash = "#/flow";
    render(<App />);
    const board = await screen.findByRole("region", {
      name: "Flows of this project",
    });
    await user.click(
      within(board).getByRole("button", { name: "Remove the worktree" }),
    );
    expect(
      await screen.findByText(
        `The worktree of "Old idea" is gone; its branch feat/old-idea stays.`,
      ),
    ).toBeInTheDocument();
    expect(
      db.calls.some(
        (c) =>
          c.method === "POST" &&
          c.path === "/api/threads/th_old/worktree/remove",
      ),
    ).toBe(true);
    await waitFor(() =>
      expect(
        within(
          screen.getByRole("region", { name: "Flows of this project" }),
        ).getAllByRole("article"),
      ).toHaveLength(2),
    );
  });

  it("has no board while only the project folder runs a flow", async () => {
    location.hash = "#/flow";
    render(<App />);
    await screen.findByText(/AC gate — AC-002/);
    expect(
      screen.queryByRole("region", { name: "Flows of this project" }),
    ).toBeNull();
  });
});

describe("where a new flow runs", () => {
  it("next to a flow in the project folder, it runs in a worktree of its own and the page opens it", async () => {
    const user = userEvent.setup();
    location.hash = "#/flow";
    render(<App />);
    await screen.findByText(/AC gate — AC-002/);
    await user.click(
      await screen.findByRole("button", { name: "Start another flow" }),
    );
    const dlg = await screen.findByRole("dialog", { name: "Start a flow" });
    const where = within(dlg).getByLabelText("Where it runs");
    expect(where).toHaveValue("worktree");
    expect(
      within(where).getByRole("option", {
        name: /The project folder \(a flow runs there\)/,
      }),
    ).toBeDisabled();
    await user.type(
      within(dlg).getByLabelText("What to build"),
      "Dollar prices",
    );
    await user.click(within(dlg).getByRole("button", { name: "Start flow" }));
    await waitFor(() => expect(location.hash).toMatch(/^#\/flow\/th_/));
    const sent = db.calls.find(
      (c) =>
        c.method === "POST" && c.path === "/api/projects/ludus-engine/flows",
    )!.body as Record<string, unknown>;
    expect(sent.where).toBeUndefined(); // auto: the api picks the worktree
    expect(
      await screen.findByText(/It runs in a worktree of its own/),
    ).toBeInTheDocument();
  });

  it("in a free project folder, a worktree is a choice", async () => {
    const user = userEvent.setup();
    localStorage.setItem("keel2.project", "yegi");
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: "Start a flow" }),
    );
    const dlg = await screen.findByRole("dialog", { name: "Start a flow" });
    const where = within(dlg).getByLabelText("Where it runs");
    expect(where).toHaveValue("folder");
    await user.selectOptions(where, "worktree");
    await user.type(
      within(dlg).getByLabelText("What to build"),
      "Try the cache",
    );
    await user.click(within(dlg).getByRole("button", { name: "Start flow" }));
    await waitFor(() =>
      expect(db.calls.some((c) => c.path === "/api/projects/yegi/flows")).toBe(
        true,
      ),
    );
    expect(
      (
        db.calls.find((c) => c.path === "/api/projects/yegi/flows")!
          .body as Record<string, unknown>
      ).where,
    ).toBe("worktree");
  });
});

describe("the Inbox with several flows of one project", () => {
  it("groups the gates under each flow", async () => {
    const ago = (s: number) => new Date(Date.now() - s * 1000).toISOString();
    const gate = (tid: string, flow: string, since: number): InboxItem => ({
      project_id: "ludus-engine",
      project_name: "ludus-engine",
      thread_id: tid,
      flow,
      workflow_id: "feature",
      step: "ac_gate",
      kind: "gate",
      title: `AC gate of ${flow}`,
      detail: "tests pass",
      more: false,
      options: ["approve", "reject"],
      id: `q-${tid}`,
      since: ago(since),
    });
    const list = [
      gate("th_a", "Euro prices", 300),
      gate("th_b", "Dollar prices", 200),
      gate("th_a2", "Euro prices", 100),
    ].map((x, i) => ({ ...x, thread_id: i === 2 ? "th_a" : x.thread_id }));
    server.use(
      http.get("/api/inbox", () =>
        HttpResponse.json({
          items: list,
          count: list.length,
          kinds: ["gate"],
          projects: [{ id: "ludus-engine", name: "ludus-engine", count: 3 }],
        }),
      ),
    );
    location.hash = "#/inbox";
    render(<App />);
    const euro = await screen.findByRole("region", {
      name: "ludus-engine · Euro prices",
    });
    expect(within(euro).getAllByTestId("inbox-item")).toHaveLength(2);
    expect(
      within(
        screen.getByRole("region", { name: "ludus-engine · Dollar prices" }),
      ).getAllByTestId("inbox-item"),
    ).toHaveLength(1);
  });
});
