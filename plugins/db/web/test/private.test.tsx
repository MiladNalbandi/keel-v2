// #96 Results only to you, inside keel's web: Settings › Databases › MCP clients (what Claude Code, OpenCode … may do with
// databases), Results for you (the list, a result opened from the link an MCP client gets, rows and columns picked,
// Forget), KeelBot's card for a keel-result block (Send to KeelBot sends only what the person picked), the new Access
// choice in the editor, and the Markdown and CSV the person copies.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AskKeelBot } from "@keel/web-sdk";
import { App } from "../../../../web/src/App";
import { AppProvider } from "../../../../web/src/state";
import { db, server } from "../../../../web/src/test/setup";
import { asCsv, asMarkdown, ResultCard } from "../Private";

const PID = "ludus-engine";
const RESULT = {
  id: "r_0123456789ab",
  project: PID,
  connection: "crm",
  env: "prod",
  kind: "postgres",
  sql: "select id, email, plan from customers order by id",
  who: "Claude Code",
  thread: null,
  at: "2026-11-13T10:00:00+00:00",
  columns: ["id", "email", "plan"],
  rows: [[1, "ann@example.com", "pro"], [2, "bo@example.com", null], [3, "chen@example.com", "free"]],
  count: 3,
  truncated: false,
  masked: [],
  ms: 7,
};

beforeEach(() => {
  db.plugins.db = true;
  db.dbResults = [structuredClone(RESULT)];
  db.dbMcp = "connection";
  server.use(
    http.get("/api/databases", () => HttpResponse.json([])),
    http.get("/api/projects/:pid/databases", () => HttpResponse.json([])),
  );
});

describe("the picked part as text", () => {
  it("is a Markdown table (pipes escaped, NULL said) and CSV (quoted where it must be)", () => {
    expect(asMarkdown(["a", "b|c"], [[1, null], ["x|y", "line\nbreak"]])).toBe(
      "| a | b\\|c |\n|---|---|\n| 1 | NULL |\n| x\\|y | line break |",
    );
    expect(asCsv(["a", "b"], [[1, null], ['say "hi"', "x,y"]])).toBe('a,b\r\n1,\r\n"say ""hi""","x,y"\r\n');
  });
});

describe("Settings › Databases", () => {
  it("keeps MCP clients to results only to you, or to no database", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings/databases";
    render(<App />);
    const group = await screen.findByRole("radiogroup", { name: "What MCP clients may do with databases" });
    expect(within(group).getByRole("radio", { name: /Follow each connection's Access/ })).toBeChecked();
    await user.click(within(group).getByRole("radio", { name: /No databases/ }));
    await waitFor(() => expect(within(group).getByRole("radio", { name: /No databases/ })).toBeChecked());
    expect(db.dbMcp).toBe("none");
  });

  it("lists the results only the person sees and opens one from its link: pick, then forget", async () => {
    const user = userEvent.setup();
    location.hash = `#/projects/${PID}/settings/databases/results/${RESULT.id}`;
    render(<App />);
    const list = await screen.findByRole("list", { name: "Results for you" });
    expect(within(list).getByRole("button", { name: `Open result ${RESULT.id} from crm, asked by Claude Code` }))
      .toHaveTextContent("3 rows");
    const drawer = await screen.findByRole("dialog");
    const table = await within(drawer).findByRole("table", { name: `Result ${RESULT.id}` });
    expect(within(drawer).getByText(/Asked by/)).toHaveTextContent("Asked by Claude Code · crm (prod)");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    await user.click(within(drawer).getByRole("checkbox", { name: "email" }));
    expect(within(table).queryByText("ann@example.com")).toBeNull();
    await user.click(within(drawer).getByRole("checkbox", { name: "Row 2" }));
    expect(within(drawer).getByText(/^Picked:/)).toHaveTextContent("Picked: 2 rows, 2 columns");
    expect(within(drawer).queryByRole("button", { name: "Send to KeelBot" })).toBeNull();   // only in KeelBot's chat
    await user.click(within(drawer).getByRole("button", { name: "Forget it" }));
    await waitFor(() => expect(db.dbResults).toEqual([]));
  });

  it("marks a private connection in the list", async () => {
    server.use(http.get("/api/projects/:pid/databases", () => HttpResponse.json([{
      id: "c1", name: "crm", source: "project", overridden: false, hidden: false, project: PID, driver: "postgres", kind: "postgres",
      env: "prod", connect_mode: "host", host: "db", port: 5432, username: "app", default_db: "crm", url_override: null,
      socket_path: null, env_var: null, note: null, agent_access: "private", agent_access_effective: "private", ask_first: false,
      keep_password: "keel", has_password: true, url: "postgres://app@db:5432/crm", shown: "postgres://app:•••@db:5432/crm", // keel:allow-secret
      found_in: null, projects: [], ok: true, server: null, tables: null, error: null, checked_at: null, can_change: false }])));
    location.hash = "#/settings/databases";
    render(<App />);
    const item = await screen.findByRole("button", { name: "crm, postgres, prod, results only to you" });
    expect(item).toHaveTextContent("results to you");
  });

  it("offers agents a connection whose results only the person sees", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings/databases";
    render(<App />);
    const side = await screen.findByRole("complementary", { name: "Database connections" });
    await user.click(within(side).getByRole("button", { name: /new connection|add/i }));
    const form = await screen.findByRole("form");
    await user.click(within(form).getByRole("tab", { name: "Access" }));
    const priv = within(form).getByRole("radio", { name: /Run queries, results only to you/ });
    await user.click(priv);
    expect(priv).toBeChecked();
  });
});

describe("KeelBot's card for a keel-result block", () => {
  it("shows the rows to the person and sends KeelBot only the rows and columns they picked", async () => {
    const user = userEvent.setup();
    const ask = vi.fn();
    render(
      <AppProvider>
        <AskKeelBot.Provider value={ask}>
          <ResultCard pid={PID} block={{ kind: "keel-result", body: `{"id": "${RESULT.id}"}` } as never} />
        </AskKeelBot.Provider>
      </AppProvider>,
    );
    const card = screen.getByRole("region", { name: `Result ${RESULT.id}` });
    expect(card).toHaveTextContent("Only you see this");
    await within(card).findByRole("table");
    await user.click(within(card).getByRole("checkbox", { name: "plan" }));
    await user.click(within(card).getByRole("checkbox", { name: "Row 1" }));
    await user.click(within(card).getByRole("button", { name: "Send to KeelBot" }));
    expect(ask).toHaveBeenCalledTimes(1);
    const text = ask.mock.calls[0][0] as string;
    expect(text).toContain(`rows I picked from result ${RESULT.id} (crm): 2 rows, 2 columns`);
    expect(text).toContain("| id | email |\n|---|---|\n| 2 | bo@example.com |\n| 3 | chen@example.com |");
    expect(text).not.toContain("ann@example.com");
    expect(text).not.toContain("pro");
  });

  it("says when the result is gone", async () => {
    render(<AppProvider><ResultCard pid={PID} block={{ kind: "keel-result", body: '{"id": "r_gone"}' } as never} /></AppProvider>);
    expect(await screen.findByRole("alert")).toHaveTextContent("This result is gone.");
  });
});
