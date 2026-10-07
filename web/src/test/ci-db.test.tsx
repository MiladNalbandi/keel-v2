// v0.11.0 the CI/CD plugin in the web (Run › Jobs › Pipelines, KeelBot's CI button, Settings › When CI fails) and the
// Database plugin's IntelliJ-style tool on the Code page (connections ▸ tables ▸ columns, a console, a table's data).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CiRun } from "../api";
import { App } from "../App";
import { statementAt } from "../components/plugins/DbTool";
import { db } from "./setup";

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

  it("shows no Pipelines tab while the plugin is off", async () => {
    location.hash = "#/jobs";
    render(<App />);
    await screen.findByRole("heading", { name: "Jobs" });
    expect(screen.queryByRole("tab", { name: "Pipelines" })).toBeNull();
  });
});

describe("KeelBot's CI button and the setting", () => {
  it("fix starts the flow from KeelBot's answer", async () => {
    const user = userEvent.setup();
    db.helper.sessions.push({
      id: "h1",
      project: "ludus-engine",
      root: "/w",
      mode: "ask",
      title: "CI",
      status: "idle",
      busy: false,
      model: { provider: "claude", mode: "subscription", model: "sonnet" },
      tokens_in: 0,
      tokens_out: 0,
      tokens_cached: 0,
      cost_usd: 0,
      turns: 1,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      messages: [
        {
          n: 1,
          role: "helper",
          text: 'The test assert is wrong.\n```keel-ci\n{"op": "fix", "run": 12}\n```',
          call_id: "c1",
          data: {
            status: "done",
            provider: "claude",
            model: "sonnet",
            tokens_in: 1,
            tokens_out: 1,
            cost_usd: 0,
            ms: 1,
          },
          at: new Date().toISOString(),
        },
      ],
    } as never);
    localStorage.setItem("keel2.helper.ludus-engine.session", "h1");
    location.hash = "#/keelbot";
    render(<App />);
    const card = await screen.findByRole("region", { name: "CI: Fix it" });
    await user.click(within(card).getByRole("button", { name: "Fix it" }));
    await waitFor(() =>
      expect(
        calls("POST", "/api/projects/ludus-engine/ci/fix")[0]?.body,
      ).toEqual({ run: 12 }),
    );
    expect(await within(card).findByRole("status")).toHaveTextContent(
      "The fix flow started",
    );
  });

  it("Settings › When CI fails saves the choice", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings";
    render(<App />);
    await user.selectOptions(
      await screen.findByLabelText("When CI fails"),
      "fix",
    );
    await waitFor(() =>
      expect(db.calls.at(-1)?.body).toEqual({ ci_on_failure: "fix" }),
    );
  });
});

describe("Code › Database (IntelliJ style)", () => {
  it("picks the statement under the cursor, or the selection", () => {
    const text = "select 1;\nselect 'a;b' from t;\n\nupdate t set x = 1";
    expect(statementAt(text, 3)).toBe("select 1");
    expect(statementAt(text, 14)).toBe("select 'a;b' from t");
    expect(statementAt(text, text.length)).toBe("update t set x = 1");
    expect(statementAt(text, 0, 6)).toBe("select");
  });

  it("shows every connection as a tree, opens a table's data and a console that runs the statement", async () => {
    const user = userEvent.setup();
    db.plugins.db = true;
    db.dbConns = [
      {
        name: "local",
        kind: "postgres",
        env: "local",
        shown: "postgres://app:•••@localhost/app",
        ok: true,
        server: "PostgreSQL 16.4",
        tables: 2,
        can_change: true,
      }, // keel:allow-secret
      {
        name: "prod",
        kind: "postgres",
        env: "prod",
        shown: "postgres://ro:•••@db/app",
        ok: true,
        server: "PostgreSQL 16.4",
        tables: 2,
        can_change: false,
      }, // keel:allow-secret
    ];
    location.hash = "#/repo";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Database" }));
    const tree = await screen.findByRole("tree", { name: "Databases" });
    expect(
      within(tree).getByRole("treeitem", { name: "Database local" }),
    ).toBeInTheDocument();
    expect(
      within(tree).getByRole("treeitem", { name: "Database prod" }),
    ).toHaveTextContent("prod");
    await user.click(within(tree).getByRole("button", { name: "Open local" }));
    const scores = await within(tree).findByRole("treeitem", {
      name: "Table scores",
    });
    await user.click(
      within(scores).getByRole("button", { name: "Columns of scores" }),
    );
    expect(
      within(scores).getByRole("treeitem", { name: "Column id" }),
    ).toBeInTheDocument();
    await user.click(within(tree).getByRole("button", { name: "players" }));
    const data = await screen.findByLabelText("players in local");
    await waitFor(() =>
      expect(
        calls("POST", "/api/projects/ludus-engine/db/query")[0]?.body,
      ).toEqual({
        connection: "local",
        sql: "SELECT * FROM players LIMIT 100",
        change: true,
        confirm: false,
      }),
    );
    expect(
      await within(data).findByRole("table", { name: "Rows from local" }),
    ).toBeInTheDocument();
    await user.click(within(data).getByRole("tab", { name: "Structure" }));
    expect(
      within(data).getByRole("table", { name: "Structure of players" }),
    ).toHaveTextContent("primary key");

    await user.click(
      within(tree).getByRole("button", { name: "New console on prod" }),
    );
    const consoleTab = await screen.findByLabelText("Console on prod");
    const sql = within(consoleTab).getByRole("textbox", { name: "SQL" });
    await user.clear(sql);
    await user.type(sql, "select 1;");
    await user.click(within(consoleTab).getByRole("button", { name: /Run/ }));
    await waitFor(() =>
      expect(
        calls("POST", "/api/projects/ludus-engine/db/query").at(-1)?.body,
      ).toEqual({
        connection: "prod",
        sql: "select 1",
        change: false,
        confirm: false,
      }),
    ); // prod: read only
    expect(within(consoleTab).getByText("read only")).toBeInTheDocument();
  });
});
