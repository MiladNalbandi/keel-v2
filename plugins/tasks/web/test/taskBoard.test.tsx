// 0.18.57 the new Tasks page inside keel's web (the default; "Classic view" goes back to the page of tasks.test.tsx):
// five columns with Testing holding PP and Ready for release, Blocked in front when a task is, the list grouped by status
// with folding groups, the filters, "N tasks need you", the Jira sync line, a new task, and the Classic view switch.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { db } from "../../../../web/src/test/setup";

const calls = (method: string, path: string) =>
  db.calls.filter((c) => c.method === method && c.path === path);
const main = () => within(document.getElementById("main")!);

async function openTasks(hash = "#/tasks") {
  location.hash = hash;
  render(<App />);
  return main().findByRole("heading", { name: "Tasks", level: 1 });
}

/** Two more tasks: one on PP that waits for you, one ready for release. */
function addTesting() {
  const base = db.tk.tasks[0];
  db.tk.tasks.push(
    {
      ...base,
      id: "tk_5",
      title: "Cart totals in cents",
      type: "bug",
      status: "testing_pp",
      external_key: "ABC-5",
      pr_url: "https://github.com/acme/app/pull/9",
      reviewers: [
        { login: "ana", on: "github", state: "approved" },
        { login: "bo", on: "github", state: "requested" },
      ],
    },
    {
      ...base,
      id: "tk_6",
      title: "Coupon codes ignore case",
      status: "ready_prod",
      external_key: "ABC-6",
      assignee: "Someone Else",
    },
  );
  db.tk.items.push({
    id: 120,
    task_id: "tk_5",
    project_id: "ludus-engine",
    kind: "task",
    stage: "pp",
    title: "Confirm PP testing for ABC-5",
    detail: "Test it in PP.",
    created_at: "",
    done_at: null,
  });
}

describe("Tasks (new page): the board", () => {
  it("shows five columns, Testing with PP and Ready for release, Blocked in front, and Done with the shipped ones", async () => {
    addTesting();
    await openTasks();
    const board = await main().findByLabelText("Task board");
    const heads = within(board)
      .getAllByRole("heading", { level: 2 })
      .map((h) => h.textContent);
    expect(heads).toEqual([
      "Blocked1",
      "To do1",
      "In progress0",
      "In review1",
      "Testing2",
      "Done1",
    ]);
    const todo = screen.getByTestId("col-todo");
    expect(
      within(todo).getByRole("button", { name: "ABC-1: Rank players weekly" }),
    ).toHaveTextContent("ABC-1·story");
    const review = screen.getByTestId("col-in_review");
    const card = within(review).getByRole("button", {
      name: "ABC-2: Fix the tally queue",
    });
    expect(card).toHaveTextContent("PR #7 · 1 of 2 approved");
    const testing = screen.getByTestId("col-testing");
    expect(testing).toHaveTextContent("On PP, then ready for release");
    expect(
      within(testing).getByRole("button", {
        name: "ABC-5: Cart totals in cents",
      }),
    ).toHaveTextContent("waits for you");
    expect(
      within(testing).getByRole("button", {
        name: "ABC-6: Coupon codes ignore case",
      }),
    ).toHaveTextContent("ready for release");
    const blocked = screen.getByTestId("col-blocked");
    expect(
      within(blocked).getByRole("button", { name: /Local clean-up/ }),
    ).toHaveTextContent("verify_green failed 3 times");
    expect(screen.getByTestId("col-done")).toHaveTextContent("Shipped thing");
    expect(screen.getByTestId("col-in_progress")).toHaveTextContent(
      "Nothing here",
    );
    expect(screen.getByTestId("sync-line")).toHaveTextContent(
      "Jira Cloud as Dev One",
    );
    expect(screen.getByTestId("sync-line")).toHaveTextContent("every 5 min");
    expect(screen.getByText("6 tasks")).toBeInTheDocument();
  });

  it("says what needs you on top, and Open goes to that task", async () => {
    addTesting();
    const user = userEvent.setup();
    await openTasks();
    const box = await main().findByRole("region", {
      name: "Tasks that need you",
    });
    expect(box).toHaveTextContent("1 task needs you");
    expect(box).toHaveTextContent(
      "ABC-5Cart totals in centsconfirm it works on PP",
    );
    await user.click(
      within(box).getByRole("button", {
        name: "Open ABC-5: confirm it works on PP",
      }),
    );
    const d = await screen.findByRole("dialog", {
      name: "ABC-5 · Cart totals in cents",
    });
    expect(
      within(d).getByRole("group", { name: "Confirm PP testing for ABC-5" }),
    ).toBeInTheDocument();
  });

  it("filters by source, everyone / mine and text, and Show all tasks clears them", async () => {
    const user = userEvent.setup();
    await openTasks();
    await screen.findByTestId("col-blocked");
    const src = main().getByRole("group", { name: "Source" });
    expect(src).toHaveTextContent("All 4Jira 3Local 1");
    await user.click(
      within(main().getByRole("group", { name: "Whose tasks" })).getByRole(
        "button",
        { name: "Mine" },
      ),
    );
    expect(screen.queryByTestId("col-blocked")).toBeNull(); // the blocked one is someone else's
    expect(screen.getByText("3 of 4 tasks")).toBeInTheDocument();
    await user.click(
      within(main().getByRole("group", { name: "Whose tasks" })).getByRole(
        "button",
        { name: "Everyone" },
      ),
    );
    await user.click(within(src).getByRole("button", { name: "Local 1" }));
    expect(
      within(src).getByRole("button", { name: "Local 1" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByTestId("task-card")).toHaveLength(1);
    await user.click(within(src).getByRole("button", { name: "All 4" }));
    await user.type(screen.getByLabelText("Find a task"), "abc-2");
    expect(screen.getAllByTestId("task-card")).toHaveLength(1);
    await user.clear(screen.getByLabelText("Find a task"));
    await user.type(screen.getByLabelText("Find a task"), "nothing like this");
    expect(
      await main().findByText(/No task fits these filters/),
    ).toBeInTheDocument();
    await user.click(main().getByRole("button", { name: "Show all tasks" }));
    expect(screen.getAllByTestId("task-card")).toHaveLength(4);
  });

  it("switches to a list grouped by status whose groups fold, and remembers the choice", async () => {
    const user = userEvent.setup();
    await openTasks();
    await main().findByLabelText("Task board");
    await user.click(
      within(main().getByRole("group", { name: "View" })).getByRole("button", {
        name: "List",
      }),
    );
    const list = main().getByLabelText("Task list");
    expect(main().queryByLabelText("Task board")).toBeNull();
    const todo = within(list).getByRole("button", { name: /^To do/ });
    expect(todo).toHaveAttribute("aria-expanded", "true");
    expect(
      within(list).getByRole("button", { name: "ABC-1: Rank players weekly" }),
    ).toBeInTheDocument();
    await user.click(todo);
    expect(todo).toHaveAttribute("aria-expanded", "false");
    expect(
      within(list).queryByRole("button", {
        name: "ABC-1: Rank players weekly",
      }),
    ).toBeNull();
    expect(JSON.parse(localStorage.getItem("keel2.tasks.layout")!)).toBe(
      "list",
    );
    await user.click(
      within(list).getByRole("button", { name: "ABC-2: Fix the tally queue" }),
    );
    expect(
      await screen.findByRole("dialog", {
        name: "ABC-2 · Fix the tally queue",
      }),
    ).toBeInTheDocument();
  });

  it("Sync now syncs Jira and the PR reviews and says what changed", async () => {
    const user = userEvent.setup();
    await openTasks();
    await screen.findByTestId("col-todo");
    await user.click(
      within(screen.getByTestId("sync-line")).getByRole("button", {
        name: "Sync now",
      }),
    );
    await waitFor(() =>
      expect(
        calls("POST", "/api/projects/ludus-engine/tasks/sync"),
      ).toHaveLength(1),
    );
    expect(
      await screen.findByText(
        "Synced: 1 new, 0 changed, 1 moved; 1 PR(s) read.",
      ),
    ).toBeInTheDocument();
  });

  it("a failed sync says why; without Jira it points to Settings › Connections", async () => {
    db.tk.sync.last_sync_error = "Jira refused the login (401).";
    await openTasks();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Last sync failed: Jira refused the login (401).",
    );
  });

  it("without tasks says what to do, and without Jira offers to connect it", async () => {
    db.tk.tasks = [];
    db.tk.sync.connected = false;
    await openTasks();
    expect(await screen.findByText("No task yet")).toBeInTheDocument();
    expect(screen.getByTestId("sync-line")).toHaveTextContent(
      "Not connected to Jira",
    );
    expect(
      within(screen.getByTestId("sync-line")).getByRole("link", {
        name: "Connect Jira",
      }),
    ).toHaveAttribute("href", "#/connections");
    expect(
      main().getAllByRole("link", { name: "Connect Jira" })[0],
    ).toHaveAttribute("href", "#/connections");
  });

  it("New task makes a task and opens it", async () => {
    const user = userEvent.setup();
    await openTasks();
    await user.click(main().getByRole("button", { name: "New task" }));
    const d = await screen.findByRole("dialog", { name: "New task" });
    expect(
      within(d).getByRole("button", { name: "Create task" }),
    ).toBeDisabled();
    await user.type(within(d).getByLabelText("Title"), "Export the scores");
    await user.click(within(d).getByRole("radio", { name: /bug/ }));
    await user.type(
      within(d).getByLabelText("Description"),
      "CSV for the admins.",
    );
    await user.type(within(d).getByLabelText("Jira key (optional)"), "ABC-9");
    await user.type(
      within(d).getByLabelText("GitHub reviewers (optional)"),
      "ana, bo",
    );
    await user.click(within(d).getByRole("button", { name: "Create task" }));
    await waitFor(() =>
      expect(calls("POST", "/api/projects/ludus-engine/tasks")).toHaveLength(1),
    );
    expect(calls("POST", "/api/projects/ludus-engine/tasks")[0].body).toEqual({
      title: "Export the scores",
      type: "bug",
      description: "CSV for the admins.",
      external_key: "ABC-9",
      reviewers: ["ana", "bo"],
    });
    expect(
      await screen.findByRole("dialog", { name: "ABC-9 · Export the scores" }),
    ).toBeInTheDocument();
  });
});

describe("Tasks (new page): a name on a task", () => {
  it("is a filter of its own, and the board says it is a label", async () => {
    addTesting();
    await openTasks();
    const board = await main().findByLabelText("Task board");
    const pick = main().getByLabelText("Assigned to");
    expect(pick.closest("label")).toHaveAttribute("title", expect.stringContaining("a label, not an account"));
    expect(within(board).getByText("Cart totals in cents")).toBeInTheDocument();
    await userEvent.selectOptions(pick, "Someone Else");
    await waitFor(() => expect(within(board).queryByText("Cart totals in cents")).toBeNull());
    expect(within(board).getByText("Coupon codes ignore case")).toBeInTheDocument();
    await userEvent.selectOptions(pick, "");
    expect(await within(board).findByText("Cart totals in cents")).toBeInTheDocument();
  });

});

describe("Tasks: the Classic view switch", () => {
  it("goes to the classic page and back, and this browser remembers it", async () => {
    const user = userEvent.setup();
    await openTasks();
    await main().findByLabelText("Task board");
    await user.click(main().getByRole("button", { name: "Classic view" }));
    expect(await screen.findByTestId("col-ready_prod")).toBeInTheDocument(); // the classic board has a Ready column
    expect(localStorage.getItem("keel2.tasks.classic")).toBe("true");
    await user.click(main().getByRole("button", { name: "New view" }));
    expect(await main().findByLabelText("Task board")).toBeInTheDocument();
    expect(localStorage.getItem("keel2.tasks.classic")).toBe("false");
  });

  it("opens the classic page when this browser chose it", async () => {
    localStorage.setItem("keel2.tasks.classic", "true");
    await openTasks();
    expect(await screen.findByTestId("col-ready_prod")).toBeInTheDocument();
  });

  it("a broken stored value falls back to the new page", async () => {
    localStorage.setItem("keel2.tasks.classic", "{not json");
    await openTasks();
    expect(await main().findByLabelText("Task board")).toBeInTheDocument();
  });
});
