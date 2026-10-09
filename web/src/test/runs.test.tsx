// v0.9.0 the Workflows page's folders and run counts, the Flow page's tabs and history (Run section), and the pages'
// names (Code, KeelBot). Moved out of keelbot.test.tsx when KeelBot became a plugin (its buttons are tested with it:
// plugins/keelbot/web/test); #/keelbot is KeelBot's page, registered by its plugin as the full image has it.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { RunRow } from "../api";
import { App } from "../App";
import { parseHash } from "../routes";
import { db, FakeEventSource } from "./setup";

describe("Workflows: folders and runs", () => {
  it("puts a workflow in a folder, shows the folder in the list, and its runs open the newest flow", async () => {
    const user = userEvent.setup();
    db.workflows = db.workflows.map((w) =>
      w.id === "feature"
        ? {
            ...w,
            runs: 3,
            last_run: {
              thread_id: "t-old",
              title: "Euro prices",
              status: "done",
              at: new Date().toISOString(),
            },
          }
        : w,
    );
    location.hash = "#/workflows/feature";
    render(<App />);
    const folder = await screen.findByRole("combobox", {
      name: "Folder of this workflow",
    });
    await user.type(folder, "Daily{Enter}");
    await waitFor(() =>
      expect(
        db.calls.find(
          (c) =>
            c.method === "PUT" &&
            c.path === "/api/projects/ludus-engine/workflows/feature/folder",
        )?.body,
      ).toEqual({ folder: "Daily" }),
    );
    const rail = screen.getByRole("navigation", { name: "Workflows" });
    expect(await within(rail).findByText("📁 Daily")).toBeInTheDocument();
    expect(
      within(rail).getByRole("button", { name: /feature/ }),
    ).toHaveTextContent("3 runs");
    await user.click(
      screen.getByRole("button", { name: "3 runs · last: done ▸" }),
    );
    await waitFor(() => expect(location.hash).toBe("#/flow/t-old"));
  });
});

const run = (
  id: string,
  title: string,
  status: string,
  workflow = "feature",
  extra: Partial<RunRow> = {},
): RunRow => ({
  thread_id: id,
  title,
  workflow_id: workflow,
  status,
  phase: "green",
  current: "s5",
  waiting: status === "waiting" ? "AC gate" : null,
  acs_done: 1,
  acs_total: 3,
  tokens: 120_000,
  where: "worktree",
  branch: `feat/${id}`,
  error: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  ...extra,
});

describe("Flow page (Run): flows side by side and the history", () => {
  it("has a tab per flow that runs now and the history of every flow, one workflow's or all", async () => {
    const user = userEvent.setup();
    const open = db.flows["ludus-engine"].thread!.thread_id;
    db.runs["ludus-engine"] = [
      run(open, "Score ranks", "waiting"),
      run("t-b", "Euro prices", "running"),
      run("t-c", "Old fix", "done", "fix"),
      run("t-d", "Older feature", "failed"),
    ];
    location.hash = "#/flow";
    render(<App />);
    const tabs = await screen.findByRole("tablist", {
      name: "Flows that run now",
    });
    expect(
      within(tabs)
        .getAllByRole("tab")
        .map((t) => t.textContent),
    ).toEqual(["Score ranksfeature", "Euro pricesfeature"]);
    expect(
      within(tabs).getByRole("tab", { name: /Score ranks/ }),
    ).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("button", { name: /History/ }));
    const table = within(screen.getByRole("region", { name: "This project's flows" })).getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(5);
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Show the flows of one workflow" }),
      "fix",
    );
    expect(within(within(screen.getByRole("region", { name: "This project's flows" })).getByRole("table")).getAllByRole("row")).toHaveLength(
      2,
    );
    await user.click(
      screen.getByRole("button", { name: "Open the flow Old fix" }),
    );
    await waitFor(() => expect(location.hash).toBe("#/flow/t-c"));
    act(() => FakeEventSource.emit("heartbeat", { type: "heartbeat" }));
  });
});

describe("the pages' names", () => {
  it("the Code page and KeelBot have their own names in the menu and in the address", () => {
    expect(parseHash("#/code/api/A.kt:3")).toEqual({
      page: "repo",
      arg: "api/A.kt:3",
    });
    expect(parseHash("#/keelbot")).toEqual({ page: "helper", arg: undefined });
  });
});
