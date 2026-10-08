// v0.5.0: the Tasks plugin's page inside keel's web, as the full image has it (web/src/test/setup.ts runs every
// plugin's setup()): the Tasks board and drawer, and the task items in the Inbox (the slot inbox.card). Moved from
// web/src/test/tasks.test.tsx with the page; Connections › Jira and the MCP catalog are the Jira plugin's tests.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
// keel's own app and test harness: this test runs the Tasks page inside keel's web, as keel loads it
import { App } from "../../../../web/src/App";
import type { InboxItem, InboxView } from "../../../../web/src/inboxApi";
import { db, server } from "../../../../web/src/test/setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);
const main = () => within(document.getElementById("main")!);

async function openTasks(hash = "#/tasks") {
  location.hash = hash;
  render(<App />);
  return main().findByRole("heading", { name: "Tasks", level: 1 });
}

describe("Tasks board", () => {
  it("is in the Run group and shows the tasks as columns with their key, type, flow, PR and reviewers", async () => {
    await openTasks();
    const nav = screen.getByRole("navigation", { name: "Screens" });
    const run = within(nav).getAllByRole("link").map((a) => a.textContent);
    expect(run.indexOf("Tasks")).toBe(run.indexOf("Flow") + 1);
    expect(within(nav).getByRole("link", { name: "Tasks" })).toHaveAttribute("aria-current", "page");

    const todo = await screen.findByTestId("col-todo");
    expect(within(todo).getByRole("button", { name: "ABC-1: Rank players weekly" })).toBeInTheDocument();
    const review = screen.getByTestId("col-in_review");
    const card = within(review).getByRole("button", { name: "ABC-2: Fix the tally queue" });
    expect(card).toHaveTextContent("bug");
    expect(card).toHaveTextContent("fix · done");
    expect(card).toHaveTextContent("PR #7");
    expect(card).toHaveTextContent("@ana ✓");
    // a blocked task gets its own column, with the reason
    const blocked = screen.getByTestId("col-blocked");
    expect(within(blocked).getByRole("button", { name: /Local clean-up/ })).toHaveTextContent("verify_green failed 3 times");
    expect(screen.getByTestId("col-done")).toHaveTextContent("Shipped thing");
    expect(screen.getByTestId("sync-line")).toHaveTextContent("Jira Cloud as Dev One");
    expect(screen.getByTestId("sync-line")).toHaveTextContent("every 5 min");
    expect(screen.getByText("4 tasks")).toBeInTheDocument();
  });

  it("filters mine / all, by source and by text", async () => {
    const user = userEvent.setup();
    await openTasks();
    await screen.findByTestId("col-blocked");
    await user.click(screen.getByRole("tab", { name: "Mine" }));
    expect(screen.queryByTestId("col-blocked")).toBeNull();                 // the blocked one is someone else's
    expect(screen.getByText("3 of 4 tasks")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "All" }));
    await user.selectOptions(screen.getByLabelText("Source"), "local");
    expect(screen.getAllByTestId("task-card")).toHaveLength(1);
    await user.selectOptions(screen.getByLabelText("Source"), "");
    await user.type(screen.getByLabelText("Find a task"), "abc-2");
    expect(screen.getAllByTestId("task-card")).toHaveLength(1);
  });

  it("Sync now syncs Jira and the PR reviews and says what changed", async () => {
    const user = userEvent.setup();
    await openTasks();
    await screen.findByTestId("col-todo");
    await user.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/tasks/sync")).toHaveLength(1));
    expect(await screen.findByText("Synced: 1 new, 0 changed, 1 moved; 1 PR(s) read.")).toBeInTheDocument();
  });

  it("without tasks says what to do, and without Jira offers to connect it", async () => {
    db.tk.tasks = [];
    db.tk.sync.connected = false;
    await openTasks();
    expect(await screen.findByText("No task yet")).toBeInTheDocument();
    expect(screen.getByTestId("sync-line")).toHaveTextContent("Not connected to Jira");
    expect(main().getAllByRole("link", { name: "Connect Jira" })[0]).toHaveAttribute("href", "#/connections");
  });

  it("New task makes a task and opens it", async () => {
    const user = userEvent.setup();
    await openTasks();
    await user.click(screen.getByRole("button", { name: "New task" }));
    const d = await screen.findByRole("dialog", { name: "New task" });
    expect(within(d).getByRole("button", { name: "Create task" })).toBeDisabled();
    await user.type(within(d).getByLabelText("Title"), "Export the scores");
    await user.click(within(d).getByRole("radio", { name: /bug/ }));
    await user.type(within(d).getByLabelText("Description"), "CSV for the admins.");
    await user.type(within(d).getByLabelText("Jira key (optional)"), "ABC-9");
    await user.type(within(d).getByLabelText("GitHub reviewers (optional)"), "ana, bo");
    await user.click(within(d).getByRole("button", { name: "Create task" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/tasks")).toHaveLength(1));
    expect(calls("POST", "/api/projects/ludus-engine/tasks")[0].body).toEqual({
      title: "Export the scores", type: "bug", description: "CSV for the admins.", external_key: "ABC-9", reviewers: ["ana", "bo"],
    });
    expect(await screen.findByRole("dialog", { name: "ABC-9 · Export the scores" })).toBeInTheDocument();
  });
});

describe("Task drawer: uncommitted files", () => {
  it("offers the workspace Doctor when the start is refused for uncommitted files", async () => {
    // Real report: the task drawer only showed "This project has uncommitted changes (10 files)…" with no way to fix it.
    server.use(
      http.post("/api/tasks/:id/start", () => HttpResponse.json(
        { error: "This project has uncommitted changes (2 files): .serena/project.yml, docs/x.md", hint: "Commit or stash them first." }, { status: 409 })),
      http.post("/api/projects/:pid/doctor/workspace", () => HttpResponse.json({
        by: "rules", summary: "2 files", files: [], note: null,
        plan: [{ id: "local", title: "Build output, editor and tool folders", why: "Made by tools on this computer.", action: "exclude",
          files: [".serena/project.yml"], patterns: [".serena/"] }],
      })),
    );
    const user = userEvent.setup();
    await openTasks();
    await user.click(await screen.findByRole("button", { name: "ABC-1: Rank players weekly" }));
    const d = await screen.findByRole("dialog", { name: "ABC-1 · Rank players weekly" });
    const start = within(d).getByRole("region", { name: "Start a flow" });
    await waitFor(() => expect(within(start).getByLabelText("Workflow")).toHaveValue("feature"));
    await user.click(within(start).getByRole("button", { name: "Start flow" }));
    await user.click(await within(start).findByRole("button", { name: "Ask the Doctor what to do with these files" }));
    expect(await within(start).findByText("Build output, editor and tool folders")).toBeInTheDocument();
    expect(within(start).getAllByText(".git/info/exclude", { exact: false }).length).toBeGreaterThan(0);
  });
});

describe("Task drawer", () => {
  it("starts a flow with the workflow its type picks, takes the PR link, and walks it through PP to done", async () => {
    const user = userEvent.setup();
    await openTasks();
    await user.click(await screen.findByRole("button", { name: "ABC-1: Rank players weekly" }));
    const d = await screen.findByRole("dialog", { name: "ABC-1 · Rank players weekly" });
    expect(within(d).getByRole("link", { name: "Open in Jira ↗" })).toHaveAttribute("href", "https://acme.atlassian.net/browse/ABC-1");
    const start = within(d).getByRole("region", { name: "Start a flow" });
    await waitFor(() => expect(within(start).getByLabelText("Workflow")).toHaveValue("feature"));   // a story → feature
    await user.click(within(start).getByRole("radio", { name: /Important only/ }));
    await user.click(within(start).getByRole("button", { name: "Start flow" }));
    await waitFor(() => expect(calls("POST", "/api/tasks/tk_1/start")).toHaveLength(1));
    expect(calls("POST", "/api/tasks/tk_1/start")[0].body).toEqual({ workflow_id: "feature", run_mode: "important" });
    expect(await within(d).findByText("In progress", { selector: ".pill" })).toBeInTheDocument();
    expect(within(d).getByRole("button", { name: "Open flow ▸" })).toBeInTheDocument();

    await user.type(within(d).getByLabelText("PR link"), "https://github.com/acme/app/pull/12");
    await user.click(within(d).getByRole("button", { name: "Save link" }));
    expect(await within(d).findByText("In review", { selector: ".pill" })).toBeInTheDocument();
    expect(calls("POST", "/api/tasks/tk_1/pr")[0].body).toEqual({ url: "https://github.com/acme/app/pull/12" });

    await user.click(within(d).getByRole("button", { name: "Mark approved" }));
    const pp = await within(d).findByRole("group", { name: "Confirm PP testing for ABC-1" });
    await user.click(within(pp).getByRole("button", { name: "PP works, confirm" }));
    await waitFor(() => expect(calls("POST", "/api/inbox/tasks/100/act")).toHaveLength(1));
    const ship = await within(d).findByRole("group", { name: "Ship ABC-1 to production" });
    await user.click(within(ship).getByRole("button", { name: "Shipped, confirm" }));
    expect(await within(d).findByText("Done", { selector: ".pill" })).toBeInTheDocument();
    const history = within(d).getByRole("list", { name: "History" });
    expect(within(history).getAllByRole("listitem").length).toBeGreaterThanOrEqual(5);
    expect(history).toHaveTextContent("In progress → In review");
  });

  it("sends back with a reason, and a deep link opens the task", async () => {
    const user = userEvent.setup();
    await openTasks("#/tasks/tk_2");
    const d = await screen.findByRole("dialog", { name: "ABC-2 · Fix the tally queue" });
    expect(await within(d).findByText("@ana · approved")).toBeInTheDocument();
    await user.click(within(d).getByRole("button", { name: "Send back" }));
    const box = within(d).getByRole("group", { name: "Send back" });
    expect(within(box).getByRole("button", { name: "Send back" })).toBeDisabled();
    await user.type(within(box).getByLabelText("Why does it go back?"), "the totals are off");
    await user.click(within(box).getByRole("button", { name: "Send back" }));
    await waitFor(() => expect(calls("POST", "/api/tasks/tk_2/status")).toHaveLength(1));
    expect(calls("POST", "/api/tasks/tk_2/status")[0].body).toEqual({ to: "in_progress", note: "the totals are off" });
  });

  it("cancels and deletes only after a second click", async () => {
    const user = userEvent.setup();
    await openTasks("#/tasks/tk_1");
    const d = await screen.findByRole("dialog", { name: "ABC-1 · Rank players weekly" });
    await user.click(await within(d).findByRole("button", { name: "Cancel task" }));
    expect(calls("POST", "/api/tasks/tk_1/status")).toHaveLength(0);
    await user.click(within(within(d).getByRole("group", { name: "Confirm" })).getByRole("button", { name: "Cancel task" }));
    await waitFor(() => expect(calls("POST", "/api/tasks/tk_1/status")[0]?.body).toEqual({ to: "cancelled" }));
    await user.click(await within(d).findByRole("button", { name: "Delete" }));
    await user.click(within(within(d).getByRole("group", { name: "Confirm" })).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls("DELETE", "/api/tasks/tk_1")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

describe("Inbox task items", () => {
  const items = (): InboxItem[] => [
    { project_id: "ludus-engine", project_name: "ludus-engine", thread_id: "th_2", flow: "Fix the tally queue", workflow_id: "fix", step: null,
      kind: "task", title: "Confirm PP testing for ABC-2", detail: "Test it in PP.", more: false, options: [], id: "task-item-100", since: new Date().toISOString(),
      task: { id: "tk_2", item_id: 100, key: "ABC-2", url: "https://acme.atlassian.net/browse/ABC-2", title: "Fix the tally queue", status: "testing_pp", stage: "pp",
        pr_url: null, actions: [{ id: "confirm", label: "PP works, confirm", needs_note: false }, { id: "send_back", label: "Send back", needs_note: true }] } },
    { project_id: "ludus-engine", project_name: "ludus-engine", thread_id: "", flow: "Ops", workflow_id: null, step: null,
      kind: "jira-manual", title: "Move OPS-7 to In Progress in Jira (keel could not: no Jira connection for this project)", detail: "Move it by hand.",
      more: false, options: [], id: "task-item-101", since: new Date().toISOString(),
      task: { id: "tk_9", item_id: 101, key: "OPS-7", url: null, title: "Ops", status: "in_progress", stage: "In Progress", pr_url: null,
        actions: [{ id: "done", label: "Done", needs_note: false }] } },
  ];

  it("shows the task items next to the gates and answers them with their own buttons", async () => {
    const user = userEvent.setup();
    let list = items();
    db.tk.items.push({ id: 100, task_id: "tk_2", project_id: "ludus-engine", kind: "task", stage: "pp", title: "Confirm PP testing for ABC-2", detail: "", created_at: "", done_at: null },
      { id: 101, task_id: "tk_2", project_id: "ludus-engine", kind: "jira-manual", stage: "In Progress", title: "Move", detail: "", created_at: "", done_at: null });
    server.use(
      http.get("/api/inbox", () => HttpResponse.json({ items: list, count: list.length, kinds: ["jira-manual", "task"], projects: [{ id: "ludus-engine", name: "ludus-engine", count: list.length }] } satisfies InboxView)),
      http.post("/api/inbox/tasks/:id/act", async ({ request, params }) => {
        db.calls.push({ method: "POST", path: `/api/inbox/tasks/${params.id}/act`, body: await request.json() });
        list = list.filter((i) => i.task?.item_id !== Number(params.id));
        return HttpResponse.json({ id: "tk_2" });
      }),
    );
    location.hash = "#/inbox";
    render(<App />);
    const pp = await screen.findByRole("article", { name: "Confirm PP testing for ABC-2" });
    expect(pp).toHaveTextContent("task");
    expect(within(pp).getByRole("link", { name: "Open in Jira ↗" })).toHaveAttribute("href", "https://acme.atlassian.net/browse/ABC-2");
    await user.click(within(pp).getByRole("button", { name: "Send back" }));
    expect(calls("POST", "/api/inbox/tasks/100/act")).toHaveLength(0);                     // a reason first
    await user.type(within(pp).getByLabelText("Note (needed to send back)"), "wrong totals");
    await user.click(within(pp).getByRole("button", { name: "Send back" }));
    await waitFor(() => expect(calls("POST", "/api/inbox/tasks/100/act")[0]?.body).toEqual({ action: "send_back", note: "wrong totals" }));

    const manual = await screen.findByRole("article", { name: /Move OPS-7 to In Progress in Jira/ });
    expect(manual).toHaveTextContent("move in Jira");
    await user.click(within(manual).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(calls("POST", "/api/inbox/tasks/101/act")[0]?.body).toEqual({ action: "done" }));
    expect(await screen.findByText("Nothing is waiting for you")).toBeInTheDocument();
  });

  it("Open task goes to the task's drawer", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/inbox", () => HttpResponse.json({ items: items().slice(0, 1), count: 1, kinds: ["task"], projects: [] })));
    location.hash = "#/inbox";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Open task ABC-2 in ludus-engine" }));
    await waitFor(() => expect(location.hash).toBe("#/tasks/tk_2"));
    expect(await screen.findByRole("dialog", { name: "ABC-2 · Fix the tally queue" })).toBeInTheDocument();
  });
});
