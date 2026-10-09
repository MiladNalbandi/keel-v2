// The Database plugin's web part inside keel's web, as the full image has it (web/src/test/setup.ts runs every plugin's
// setup()): its pieces in their slots, Connections › Databases, Code › Database (IntelliJ style), KeelBot's query
// button, and a plugin block's settings on the Workflows page. Moved from web/src/test (plugins, ci-db) with the part;
// Map › Query is tested with the Map plugin (plugins/map/web/test).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
// keel's own app and test harness: this test runs the part inside keel's web, as keel loads it
import { App } from "../../../../web/src/App";
import { slotItems, SLOTS } from "../../../../web/src/sdk";
import { db } from "../../../../web/src/test/setup";
import plugin from "../index";
import { statementAt } from "../DbTool";

const calls = (method: string, path: string) =>
  db.calls.filter((c) => c.method === method && c.path === path);

function chatWith(text: string) {
  db.helper.sessions.push({
    id: "h1",
    project: "ludus-engine",
    root: "/w",
    mode: "ask",
    title: "Data",
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
      { n: 1, role: "user", text: "q", data: {}, at: new Date().toISOString() },
      {
        n: 2,
        role: "helper",
        text,
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
  return screen.findByRole("complementary", { name: "KeelBot" });
}

describe("the Database plugin's web part", () => {
  it("is the plugin db, and puts its pieces where keel 0.15.1 had them", () => {
    expect(plugin.name).toBe("db");
    const one = (slot: string) =>
      slotItems<{
        id: string;
        title?: string;
        order?: number;
        plugin?: string;
      }>(slot).filter((i) => i.id === "db" || i.id === "databases");
    expect(
      one(SLOTS.connectionsKind).map((i) => [i.id, i.title, i.order]),
    ).toEqual([["databases", "Databases", 40]]);
    expect(
      one(SLOTS.codeActivity).map((i) => [i.id, i.title, i.order, i.plugin]),
    ).toEqual([["db", "Database", 50, "db"]]);
    expect(one(SLOTS.codeTab).map((i) => i.id)).toEqual(["db"]);
    expect(
      slotItems<{ id: string; prefix: string; plugin?: string }>(
        SLOTS.workflowActions,
      )
        .filter((i) => i.id === "db")
        .map((i) => [i.prefix, i.plugin]),
    ).toEqual([["db:", "db"]]);
    expect(one(SLOTS.mapErQuery).map((i) => i.id)).toEqual(["db"]);
    expect(
      slotItems<{ id: string; kind: string }>(SLOTS.keelbotCard)
        .filter((i) => i.id === "db")
        .map((i) => i.kind),
    ).toEqual(["keel-query"]);
  });
});

describe("Connections › Databases", () => {
  it("uses a database keel found, saves and tests it", async () => {
    const user = userEvent.setup();
    db.plugins.db = true;
    location.hash = "#/connections";
    render(<App />);
    const found = await screen.findByRole("group", {
      name: "Databases keel found in the project",
    });
    expect(found).toHaveTextContent(
      "postgres://app:•••@localhost:15432/scores",
    ); // keel:allow-secret
    await user.click(within(found).getByRole("button", { name: "Use it" }));
    const form = screen.getByRole("form", { name: "Add a database" });
    await user.click(
      within(form).getByRole("button", { name: "Save and test" }),
    );
    await waitFor(() =>
      expect(
        calls("POST", "/api/projects/ludus-engine/db/connections")[0]?.body,
      ).toMatchObject({
        name: "local",
        env: "local",
        source: "docker-compose.yml (service db)",
      }),
    );
    const card = await screen.findByRole("article", { name: "Database local" });
    expect(card).toHaveTextContent("connected · 23 tables");
    expect(card).toHaveTextContent("local: changes with your OK");
  });

  it("shows nothing while the plugin is off for the project", async () => {
    location.hash = "#/connections";
    render(<App />);
    expect(await screen.findByLabelText("GitHub token")).toBeInTheDocument();
    expect(
      screen.queryByRole("group", {
        name: "Databases keel found in the project",
      }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Add a database" })).toBeNull();
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
        ok: true, // keel:allow-secret
        server: "PostgreSQL 16.4",
        tables: 2,
        can_change: true,
      },
      {
        name: "prod",
        kind: "postgres",
        env: "prod",
        shown: "postgres://ro:•••@db/app",
        ok: true, // keel:allow-secret
        server: "PostgreSQL 16.4",
        tables: 2,
        can_change: false,
      },
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

    // the console moves to local: the tab follows, the text stays, the old answer goes
    await user.selectOptions(
      within(consoleTab).getByRole("combobox", {
        name: "Database of this console",
      }),
      "local",
    );
    const moved = await screen.findByLabelText("Console on local");
    expect(screen.queryByLabelText("Console on prod")).not.toBeInTheDocument();
    expect(within(moved).getByRole("textbox", { name: "SQL" })).toHaveValue(
      "select 1;",
    );
    expect(within(moved).queryByText("read only")).not.toBeInTheDocument();
    expect(
      within(moved).queryByRole("table", { name: /Rows from/ }),
    ).not.toBeInTheDocument();
  });
});

describe("KeelBot's query button", () => {
  it("a change KeelBot gives is counted first, then run after the person's press", async () => {
    const user = userEvent.setup();
    const p = await chatWith(
      'Zero them:\n```keel-query\n{"sql": "update scores set value = 0", "connection": "local"}\n```',
    );
    const q = await within(p).findByRole("region", { name: "Query on local" });
    await user.click(within(q).getByRole("button", { name: "Run" }));
    await user.click(
      await within(q).findByRole("button", { name: "Run it (3 rows)" }),
    );
    await waitFor(() =>
      expect(
        calls("POST", "/api/projects/ludus-engine/db/query").map(
          (c) => (c.body as { confirm: boolean }).confirm,
        ),
      ).toEqual([false, true]),
    );
    expect(await within(q).findByRole("status")).toHaveTextContent(
      "3 rows changed in local.",
    );
  });

  it("a read shows its rows, and a block it cannot read says so", async () => {
    const user = userEvent.setup();
    const p = await chatWith(
      'Here:\n```keel-query\n{"sql": "select id, name from players"}\n```\n```keel-query\nnot json\n```',
    );
    const q = await within(p).findByRole("region", {
      name: "Query on the local database",
    });
    await user.click(within(q).getByRole("button", { name: "Run" }));
    expect(
      await within(q).findByRole("table", { name: "Rows from local" }),
    ).toHaveTextContent("Ada");
    expect(within(p).getByRole("note")).toHaveTextContent(
      "KeelBot's query could not be read.",
    );
  });
});

describe("Workflows: a plugin block's settings", () => {
  it("lists the plugin's blocks for a code step and edits its with: settings", async () => {
    const user = userEvent.setup();
    db.plugins.db = true;
    location.hash = "#/workflows/feature";
    render(<App />);
    await user.click(
      (
        await screen.findAllByRole("button", {
          name: /^verify_red, plain code/,
        })
      )[0],
    );
    const act = await screen.findByLabelText("What it runs");
    await user.clear(act);
    await user.type(act, "db:check");
    expect(
      await screen.findByText(/Database plugin: a data check/),
    ).toBeInTheDocument();
    await user.type(screen.getByLabelText("sql"), "select 1");
    await user.type(screen.getByLabelText("expect (optional)"), "none");
    // the suggestions list the project's plugin blocks next to keel's own actions
    expect(
      document.querySelector('#wact-list option[value="db:check"]'),
    ).toHaveTextContent("Database: a data check");
    expect(
      document.querySelector('#wact-list option[value="commit"]'),
    ).not.toBeNull();
  });
});
