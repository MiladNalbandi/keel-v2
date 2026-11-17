// Code › Database: the side bar's "+", "Add one" and each connection's ⚙ open the connections popup over the Code page
// (the same list and editor as Settings › Databases) instead of leaving the page. A save closes it and the tree reads
// its connections again; Esc and Close close it without a save. Settings › Databases stays a page of its own.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { db, server } from "../../../../web/src/test/setup";
import type { DbConnection } from "../dbApi";
import type { DbEntry } from "../dbConfig";

const PID = "ludus-engine";

function entry(over: Partial<DbEntry>): DbEntry {
  return {
    id: over.name ?? "x",
    name: "x",
    source: "project",
    overridden: false,
    hidden: false,
    project: PID,
    driver: "postgres",
    kind: "postgres",
    env: "local",
    connect_mode: "host",
    host: "localhost",
    port: 5432,
    username: "app",
    default_db: "app",
    url_override: null,
    socket_path: null,
    env_var: null,
    note: null,
    agent_access: "read",
    agent_access_effective: "read",
    ask_first: false,
    keep_password: "keel",
    has_password: true,
    url: "postgres://app@localhost:5432/app",
    shown: "postgres://app:•••@localhost:5432/app", // keel:allow-secret
    found_in: null,
    projects: [],
    ok: true,
    server: "PostgreSQL 16.4",
    tables: 3,
    error: null,
    checked_at: null,
    can_change: true,
    ...over,
  };
}

function conn(name: string): DbConnection {
  return {
    name,
    kind: "postgres",
    env: "local",
    shown: `postgres://app:•••@localhost/${name}`, // keel:allow-secret
    ok: true,
    server: "PostgreSQL 16.4",
    tables: 2,
    can_change: true,
  };
}

let own: DbEntry[] = [];
const sent: { method: string; path: string; body: Record<string, unknown> }[] =
  [];
const treeReads = () =>
  db.calls.filter(
    (c) =>
      c.method === "GET" && c.path === `/api/projects/${PID}/db/connections`,
  ).length;

beforeEach(() => {
  db.plugins.db = true;
  sent.length = 0;
  own = [entry({ name: "local" })];
  db.dbConns = [conn("local")];
  const body = async (request: Request) =>
    (await request.json()) as Record<string, unknown>;
  server.use(
    http.get(`/api/projects/:pid/db/connections`, ({ request }) => {
      db.calls.push({
        method: "GET",
        path: new URL(request.url).pathname,
        body: null,
      });
      return HttpResponse.json(db.dbConns);
    }),
    http.get("/api/projects/:pid/databases", () => HttpResponse.json(own)),
    http.get("/api/databases", () => HttpResponse.json([])),
    http.post("/api/projects/:pid/databases", async ({ request }) => {
      const b = await body(request);
      sent.push({
        method: "POST",
        path: new URL(request.url).pathname,
        body: b,
      });
      const made = entry({ ...(b as Partial<DbEntry>), id: String(b.name) });
      own.push(made);
      db.dbConns.push(conn(made.name));
      return HttpResponse.json(made);
    }),
    http.put(
      "/api/projects/:pid/databases/:id",
      async ({ request, params }) => {
        const b = await body(request);
        sent.push({
          method: "PUT",
          path: new URL(request.url).pathname,
          body: b,
        });
        const i = own.findIndex((c) => c.id === params.id);
        own[i] = { ...own[i], ...(b as Partial<DbEntry>) };
        return HttpResponse.json(own[i]);
      },
    ),
    http.delete("/api/projects/:pid/databases/:id", ({ request, params }) => {
      sent.push({
        method: "DELETE",
        path: new URL(request.url).pathname,
        body: {},
      });
      own = own.filter((c) => c.id !== params.id);
      db.dbConns = db.dbConns.filter((c) => c.name !== params.id);
      return new HttpResponse(null, { status: 204 });
    }),
  );
});

async function openCode() {
  location.hash = "#/repo";
  render(<App />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Database" }),
  );
  return screen.findByRole("tree", { name: "Databases" });
}

describe("Code › Database: the connections popup", () => {
  it("+ opens the popup on a new draft over the Code page, and a save adds it to the tree and closes", async () => {
    const user = userEvent.setup();
    const tree = await openCode();
    await within(tree).findByRole("treeitem", { name: "Database local" });
    await user.click(screen.getByRole("button", { name: "Add a database" }));
    const dialog = await screen.findByRole("dialog", {
      name: `Databases of ${PID}`,
    });
    const form = await within(dialog).findByRole("form", {
      name: "New database connection",
    });
    expect(location.hash).toBe("#/repo"); // it did not go to Connections
    expect(within(form).getByLabelText("Name")).toHaveFocus();
    const before = treeReads();
    await user.type(within(form).getByLabelText("Name"), "scores");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(sent[0]).toMatchObject({
      method: "POST",
      path: `/api/projects/${PID}/databases`,
      body: { name: "scores" },
    });
    expect(treeReads()).toBeGreaterThan(before);
    expect(
      await within(tree).findByRole("treeitem", { name: "Database scores" }),
    ).toBeInTheDocument();
    expect(location.hash).toBe("#/repo");
  });

  it("⚙ opens the popup with that connection, to edit or remove it", async () => {
    const user = userEvent.setup();
    const tree = await openCode();
    const local = await within(tree).findByRole("treeitem", {
      name: "Database local",
    });
    await user.click(
      within(local).getByRole("button", { name: "Edit connection local" }),
    );
    const dialog = await screen.findByRole("dialog");
    const form = await within(dialog).findByRole("form", {
      name: "Edit local",
    });
    expect(within(form).getByLabelText("Name")).toHaveValue("local");
    expect(location.hash).toBe("#/repo");

    const before = treeReads();
    await user.click(within(form).getByRole("button", { name: "Remove" }));
    await user.click(
      within(form).getByRole("button", { name: "Remove local" }),
    );
    await waitFor(() => expect(sent.at(-1)?.method).toBe("DELETE"));
    expect(sent.at(-1)?.path).toBe(`/api/projects/${PID}/databases/local`);
    await waitFor(() => expect(treeReads()).toBeGreaterThan(before));
    await waitFor(() =>
      expect(
        within(tree).queryByRole("treeitem", { name: "Database local" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("an edit saved with Save closes the popup and reloads the tree", async () => {
    const user = userEvent.setup();
    const tree = await openCode();
    const local = await within(tree).findByRole("treeitem", {
      name: "Database local",
    });
    await user.click(
      within(local).getByRole("button", { name: "Edit connection local" }),
    );
    const form = await screen.findByRole("form", { name: "Edit local" });
    const before = treeReads();
    await user.selectOptions(
      within(form).getByLabelText("Environment"),
      "test",
    );
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(sent[0]).toMatchObject({
      method: "PUT",
      path: `/api/projects/${PID}/databases/local`,
      body: { env: "test" },
    });
    expect(treeReads()).toBeGreaterThan(before);
  });

  it("Esc and Close close it without a save", async () => {
    const user = userEvent.setup();
    const tree = await openCode();
    await within(tree).findByRole("treeitem", { name: "Database local" });
    await user.click(screen.getByRole("button", { name: "Add a database" }));
    let form = await screen.findByRole("form", {
      name: "New database connection",
    });
    await user.type(within(form).getByLabelText("Name"), "half done");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add a database" }));
    const dialog = await screen.findByRole("dialog");
    form = await within(dialog).findByRole("form", {
      name: "New database connection",
    });
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(sent).toEqual([]);
    expect(location.hash).toBe("#/repo");
  });

  it("No database yet: Add one opens the same popup", async () => {
    const user = userEvent.setup();
    db.dbConns = [];
    own = [];
    await openCode();
    await user.click(await screen.findByRole("button", { name: "Add one" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByRole("form", {
        name: "New database connection",
      }),
    ).toBeInTheDocument();
    expect(location.hash).toBe("#/repo");
  });

  it("Settings › Databases is still a page: no popup, the list and the editor in the page", async () => {
    const user = userEvent.setup();
    location.hash = "#/databases";
    render(<App />);
    const side = await screen.findByRole("complementary", {
      name: "Database connections",
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      screen.getByText("Pick a connection on the left, or add one."),
    ).toBeInTheDocument();
    await user.click(within(side).getByRole("button", { name: "+ Add" }));
    await screen.findByRole("form", { name: "New database connection" });
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
    expect(
      screen.getByText("Pick a connection on the left, or add one."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
