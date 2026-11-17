// Database connections global or per project (keel 0.18.38): Settings › Databases and a project's Databases inside keel's
// web. The list is grouped by scope with each database's logo and environment badge; the editor adds a connection for
// all projects or this one, tests it (and offers the server's databases), keeps the password out of the URL, and its
// Access tab never offers agents a change on prod. At a narrow width the list and the editor take turns.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { db, server } from "../../../../web/src/test/setup";
import type { DbEntry } from "../dbConfig";
import { groupEntries } from "../dbConfig";

const PID = "ludus-engine";

function entry(over: Partial<DbEntry>): DbEntry {
  return {
    id: over.name ?? "x",
    name: "x",
    source: "global",
    overridden: false,
    hidden: false,
    project: null,
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

let globals: DbEntry[] = [];
let own: DbEntry[] = [];
const sent: { method: string; path: string; body: Record<string, unknown> }[] =
  [];

beforeEach(() => {
  db.plugins.db = true;
  sent.length = 0;
  globals = [
    entry({
      name: "LOC 8.4",
      driver: "mysql",
      kind: "mysql",
      env: "local",
      port: 10109,
    }),
    entry({ name: "QA 8.0", driver: "mariadb", kind: "mysql", env: "test" }),
    entry({
      name: "PRO Metric",
      driver: "postgres",
      env: "prod",
      can_change: false,
    }),
  ];
  own = [
    entry({
      name: "tests.sqlite",
      source: "project",
      project: PID,
      driver: "sqlite",
      kind: "sqlite",
      default_db: "tests.sqlite",
      url: "sqlite:tests.sqlite",
    }),
    entry({
      name: "PP Metric",
      source: "project",
      project: PID,
      env: "staging",
      ok: false,
      error: "keel could not reach PP Metric",
    }),
  ];
  const keep = async (
    request: Request,
    path: string,
    scope: "global" | "project",
  ) => {
    const body = (await request.json()) as Record<string, unknown>;
    sent.push({ method: request.method, path, body });
    const made = entry({
      ...(body as Partial<DbEntry>),
      id: `new-${sent.length}`,
      source: scope,
      project: scope === "project" ? PID : null,
    });
    (scope === "global" ? globals : own).push(made);
    return HttpResponse.json(made);
  };
  const tested = async ({ request }: { request: Request }) => {
    sent.push({
      method: "POST",
      path: new URL(request.url).pathname,
      body: (await request.json()) as Record<string, unknown>,
    });
    return HttpResponse.json({
      ok: true,
      server: "PostgreSQL 16.4",
      tables: 23,
      ms: 12,
    });
  };
  server.use(
    http.get("/api/databases", () => HttpResponse.json(globals)),
    http.get("/api/projects/:pid/databases", () =>
      HttpResponse.json([...own, ...globals]),
    ),
    http.get("/api/projects/:pid/databases/suggest", () =>
      HttpResponse.json([
        {
          name: "db",
          kind: "postgres",
          url: "postgres://app:•••@localhost:15432/scores", // keel:allow-secret
          shown: "postgres://app:•••@localhost:15432/scores",
          env: "local",
          source: "docker-compose.yml (service db)",
          password: true,
        },
      ]),
    ), // keel:allow-secret
    http.post("/api/databases", ({ request }) =>
      keep(request, "/api/databases", "global"),
    ),
    http.post("/api/projects/:pid/databases", ({ request }) =>
      keep(request, `/api/projects/${PID}/databases`, "project"),
    ),
    http.post("/api/databases/test", tested),
    http.post("/api/projects/:pid/databases/test", tested),
    http.post("/api/projects/:pid/databases/server-databases", () =>
      HttpResponse.json({ databases: [{ name: "app" }, { name: "scores" }] }),
    ),
    http.post("/api/databases/server-databases", () =>
      HttpResponse.json({ databases: [{ name: "app" }, { name: "scores" }] }),
    ),
  );
});

async function openPage(hash = "#/databases") {
  location.hash = hash;
  render(<App />);
  return screen.findByRole("complementary", { name: "Database connections" });
}

describe("the list", () => {
  it("groups by scope: All projects first, then the project's own, each by name; a filter narrows it", () => {
    const g = groupEntries([...own, ...globals], "ludus-engine");
    expect(g.map((x) => [x.title, x.items.map((i) => i.name)])).toEqual([
      ["All projects", ["LOC 8.4", "PRO Metric", "QA 8.0"]],
      ["ludus-engine only", ["PP Metric", "tests.sqlite"]],
    ]);
    expect(
      groupEntries([...own, ...globals], undefined).map((x) => x.title),
    ).toEqual(["All projects"]);
    expect(
      groupEntries([...own, ...globals], "p", "metric").flatMap((x) =>
        x.items.map((i) => i.name),
      ),
    ).toEqual(["PRO Metric", "PP Metric"]);
  });

  it("shows each database's logo, its environment badge and the problems", async () => {
    const side = await openPage();
    const all = await within(side).findByRole("region", {
      name: "All projects · 3",
    });
    const mine = within(side).getByRole("region", {
      name: "ludus-engine only · 2",
    });
    const item = (where: HTMLElement, name: RegExp) =>
      within(where).getByRole("button", { name });
    expect(
      within(item(all, /^LOC 8\.4/)).getByRole("img", { name: "MySQL" }),
    ).toHaveAttribute("data-driver", "mysql");
    expect(
      within(item(all, /^QA 8\.0/)).getByRole("img", { name: "MariaDB" }),
    ).toBeInTheDocument();
    expect(
      within(item(all, /^PRO Metric/)).getByRole("img", { name: "PostgreSQL" }),
    ).toBeInTheDocument();
    expect(
      within(item(mine, /^tests\.sqlite/)).getByRole("img", { name: "SQLite" }),
    ).toBeInTheDocument();
    const badge = (where: HTMLElement, name: RegExp, text: string) =>
      within(item(where, name)).getByText(text);
    expect(badge(all, /^LOC 8\.4/, "local")).toHaveClass("t-ok");
    expect(badge(all, /^QA 8\.0/, "test")).toHaveClass("t-run");
    expect(badge(mine, /^PP Metric/, "staging")).toHaveClass("t-warn");
    expect(badge(all, /^PRO Metric/, "prod · read only")).toHaveClass("t-bad");
    const problems = within(side).getByRole("status");
    expect(problems).toHaveTextContent("1Problems· PP Metric not reachable");
  });
});

describe("the editor", () => {
  it("adds a connection for this project or for all projects", async () => {
    const user = userEvent.setup();
    const side = await openPage();
    await user.click(within(side).getByRole("button", { name: "+ Add" }));
    let form = await screen.findByRole("form", {
      name: "New database connection",
    });
    await user.type(within(form).getByLabelText("Name"), "LOC 9");
    await user.click(within(form).getByRole("button", { name: "MySQL" }));
    expect(within(form).getByRole("button", { name: "MySQL" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(form).getByLabelText("Port")).toHaveValue("3306");
    await user.type(within(form).getByLabelText("User"), "root");
    await user.type(within(form).getByLabelText("Password"), "Sup3rSecret");
    await user.type(within(form).getByLabelText("Default database"), "shop");
    // the URL the fields make: never the password
    expect(within(form).getByLabelText("URL")).toHaveValue(
      "mysql://root@localhost:3306/shop",
    );
    await user.click(
      within(form).getByRole("button", { name: "This project" }),
    );
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(
        sent.find((c) => c.path === `/api/projects/${PID}/databases`)?.body,
      ).toMatchObject({
        name: "LOC 9",
        driver: "mysql",
        port: 3306,
        username: "root",
        password: "Sup3rSecret",
        default_db: "shop",
        keep_password: "keel",
      }),
    );
    expect(sent[0].body).not.toHaveProperty("projects");

    await user.click(within(side).getByRole("button", { name: "+ Add" }));
    form = await screen.findByRole("form", { name: "New database connection" });
    await user.type(within(form).getByLabelText("Name"), "shared");
    // keel 0.18.55: with a project chosen, a new connection is that project's own unless the person picks All projects
    expect(
      within(form).getByRole("button", { name: "This project" }),
    ).toHaveAttribute("aria-pressed", "true");
    await user.click(within(form).getByRole("button", { name: "All projects" }));
    await user.click(within(form).getByRole("tab", { name: "Access" }));
    await user.click(
      within(form).getByRole("radio", { name: /Only these projects/ }),
    );
    expect(
      within(form).getByRole("group", { name: "Projects that see it" }),
    ).toHaveTextContent("ludus-engine");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(sent.find((c) => c.path === "/api/databases")?.body).toMatchObject(
        { name: "shared", projects: [PID] },
      ),
    );
  });

  it("is one page with one main: the editor is a section inside it", async () => {
    await openPage();
    await screen.findByText(/Pick a connection on the left/);
    expect(document.querySelectorAll("main main")).toHaveLength(0);
    expect(screen.getByRole("region", { name: "Connection" })).toBeInTheDocument();
  });

  it("tests the connection, shows the result and then offers the server's databases", async () => {
    const user = userEvent.setup();
    const side = await openPage();
    await user.click(within(side).getByRole("button", { name: /^LOC 8\.4/ }));
    const form = await screen.findByRole("form", { name: "Edit LOC 8.4" });
    expect(within(form).getByLabelText("Password")).toHaveValue("");
    await user.click(
      within(form).getByRole("button", { name: "Test connection" }),
    );
    expect(await within(form).findByRole("status")).toHaveTextContent(
      "✓ PostgreSQL 16.4 · 23 tables · 12 ms",
    );
    expect(sent.at(-1)?.body).toMatchObject({
      id: "LOC 8.4",
      driver: "mysql",
      port: 10109,
    });
    expect(sent.at(-1)?.body).not.toHaveProperty("password"); // the kept one is used by the api
    const pickDb = await within(form).findByRole("combobox", {
      name: "Default database",
    });
    expect(
      within(pickDb)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["(none: the server's default)", "app", "scores"]);
  });

  it("never offers agents a change on prod, and says why", async () => {
    const user = userEvent.setup();
    const side = await openPage();
    await user.click(within(side).getByRole("button", { name: /^PRO Metric/ }));
    const form = await screen.findByRole("form", { name: "Edit PRO Metric" });
    await user.click(within(form).getByRole("tab", { name: "Access" }));
    const change = within(form).getByRole("radio", {
      name: /Read and change, after you approve/,
    });
    expect(change).toBeDisabled();
    expect(change).toHaveAccessibleDescription(
      "Not for prod: prod is read only for agents, always.",
    );
    expect(
      within(form).getByRole("radio", { name: /Read only/ }),
    ).toBeChecked();
    await user.click(within(form).getByRole("tab", { name: "General" }));
    await user.selectOptions(
      within(form).getByLabelText("Environment"),
      "test",
    );
    await user.click(within(form).getByRole("tab", { name: "Access" }));
    expect(
      within(form).getByRole("radio", {
        name: /Read and change, after you approve/,
      }),
    ).toBeEnabled();
  });

  it("imports what keel found in the project: its address stays masked, the api reads the password again", async () => {
    const user = userEvent.setup();
    const side = await openPage("#/settings/databases");
    await user.click(
      within(side).getByRole("button", { name: "Find in project" }),
    );
    const found = await within(side).findByRole("group", {
      name: "Databases keel found in the project",
    });
    expect(found).toHaveTextContent(
      "postgres://app:•••@localhost:15432/scores",
    ); // keel:allow-secret
    await user.click(within(found).getByRole("button", { name: "Use it" }));
    const form = await screen.findByRole("form", {
      name: "New database connection",
    });
    expect(within(form).getByLabelText("Host")).toHaveValue("localhost");
    expect(within(form).getByLabelText("Port")).toHaveValue("15432");
    expect(
      within(form).getByText("Found in docker-compose.yml (service db)."),
    ).toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(
        sent.find((c) => c.path === `/api/projects/${PID}/databases`)?.body,
      ).toMatchObject({
        name: "db",
        url: "postgres://app:•••@localhost:15432/scores",
        source: "docker-compose.yml (service db)",
      }),
    ); // keel:allow-secret
  });
});

describe("a narrow screen", () => {
  const wide = window.matchMedia;
  afterEach(() => {
    window.matchMedia = wide;
  });

  it("shows the list, then the editor with a way back", async () => {
    window.matchMedia = ((q: string) => ({
      matches: q.includes("max-width"),
      media: q,
      addEventListener() {},
      removeEventListener() {},
      onchange: null,
    })) as unknown as typeof window.matchMedia;
    const user = userEvent.setup();
    const side = await openPage();
    expect(screen.queryByRole("form")).toBeNull();
    await user.click(within(side).getByRole("button", { name: /^QA 8\.0/ }));
    expect(
      await screen.findByRole("form", { name: "Edit QA 8.0" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("complementary", { name: "Database connections" }),
    ).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "← Back to the list" }),
    );
    expect(
      await screen.findByRole("complementary", {
        name: "Database connections",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("form")).toBeNull();
  });
});
