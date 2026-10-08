// The CI/CD plugin's Jobs › Pipelines inside keel's web, as the full image has it (web/src/test/setup.ts runs every
// plugin's setup()): the runs, a failed one with its log, a re-run, the fix flow, and no tab while the plugin is off.
// Moved from web/src/test/ci-db.test.tsx with the page part.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
// keel's own app and test harness: this test runs the tab inside keel's web, as keel loads it
import { App } from "../../../../web/src/App";
import { slotItems, SLOTS } from "../../../../web/src/sdk";
import { db } from "../../../../web/src/test/setup";
import plugin from "../index";
import { runTone, type CiRun } from "../Pipelines";

const calls = (method: string, path: string) =>
  db.calls.filter((c) => c.method === method && c.path === path);
const ciRun = (id: number, failed: boolean, branch = "feat/euro"): CiRun => ({
  id,
  workflow: "ci",
  title: "feat: euro prices",
  branch,
  sha: "abc1234def",
  event: "push",
  status: "completed",
  conclusion: failed ? "failure" : "success",
  url: `https://github.com/o/r/actions/runs/${id}`,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  failed,
});

describe("the CI/CD plugin's web part", () => {
  it("is the plugin ci, and puts the Pipelines tab in Jobs as keel 0.15.1 had it", () => {
    expect(plugin.name).toBe("ci");
    const [tab] = slotItems<{
      id: string;
      title: string;
      sub: string;
      plugin?: string;
      order?: number;
    }>(SLOTS.jobsTab);
    expect(tab).toMatchObject({
      id: "pipelines",
      title: "Pipelines",
      sub: "Agent calls, and the project's CI pipelines (CI/CD plugin).",
      plugin: "ci",
      order: 10,
    });
  });

  it("colours a run by its state", () => {
    expect(runTone({ ...ciRun(1, false), status: "in_progress" })).toBe("run");
    expect(runTone(ciRun(1, true))).toBe("bad");
    expect(runTone(ciRun(1, false))).toBe("ok");
    expect(runTone({ ...ciRun(1, false), conclusion: "skipped" })).toBe("idle");
  });
});

describe("Run › Jobs › Pipelines", () => {
  it("lists the runs, opens a failed one with its log, and starts the fix flow", async () => {
    const user = userEvent.setup();
    db.plugins.ci = true;
    db.ciRuns = [ciRun(12, true), ciRun(11, false, "main")];
    location.hash = "#/jobs";
    render(<App />);
    await user.click(await screen.findByRole("tab", { name: "Pipelines" }));
    await waitFor(() => expect(location.hash).toBe("#/jobs/pipelines"));
    const table = await screen.findByRole("table", { name: "Pipeline runs" });
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    await user.click(
      within(table).getByRole("button", { name: "Open run #12" }),
    );
    const run = await screen.findByRole("group", { name: "Run #12" });
    expect(
      within(run).getByLabelText("The failed steps' log"),
    ).toHaveTextContent("assert 3 == 4");
    expect(run).toHaveTextContent("at run the tests");
    await user.click(
      within(run).getByRole("button", { name: "Run the failed jobs again" }),
    );
    await waitFor(() =>
      expect(
        calls("POST", "/api/projects/ludus-engine/ci/runs/12/rerun"),
      ).toHaveLength(1),
    );
    await user.click(within(run).getByRole("button", { name: "Fix it" }));
    await waitFor(() =>
      expect(
        calls("POST", "/api/projects/ludus-engine/ci/fix")[0]?.body,
      ).toEqual({ run: 12 }),
    );
    await waitFor(() => expect(location.hash).toBe("#/flow/t-ci-fix"));
  });

  it("checks now, and asks KeelBot why a run failed", async () => {
    const user = userEvent.setup();
    db.plugins.ci = true;
    db.ciRuns = [ciRun(12, true)];
    location.hash = "#/jobs/pipelines";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Check now" }));
    await waitFor(() =>
      expect(calls("POST", "/api/projects/ludus-engine/ci/check")).toHaveLength(
        1,
      ),
    );
    expect(await screen.findByText("No new failed run.")).toBeInTheDocument();
    await user.click(
      await screen.findByRole("button", { name: "Open run #12" }),
    );
    const run = await screen.findByRole("group", { name: "Run #12" });
    await user.click(
      within(run).getByRole("button", { name: "Ask KeelBot why" }),
    );
    await waitFor(() => expect(location.hash).toMatch(/^#\/(helper|keelbot)$/));
  });

  it("shows no Pipelines tab while the plugin is off", async () => {
    location.hash = "#/jobs";
    render(<App />);
    await screen.findByRole("heading", { name: "Jobs" });
    expect(screen.queryByRole("tab", { name: "Pipelines" })).toBeNull();
  });
});
