// The Map plugin's page inside keel's web, as the full image has it (web/src/test/setup.ts runs every plugin's
// setup()): the single "No map yet", the database diagram from the map's schema, an older map, the empty state that
// says where keel looked, a table box that opens the diagram on that table, the Database part's Query panel in the
// slot map.er.query, and no keel v1 button. Moved from web/src/test (pages, er, plugins, v02) with the page.

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeAll, describe, expect, it, vi } from "vitest";
// keel's own app and test harness: this test runs the Map inside keel's web, as keel loads it
import { App } from "../../../../web/src/App";
import type { DbSchema } from "../../../../web/src/api";
import * as fx from "../../../../web/src/test/fixtures";
import shop from "../../../../web/src/test/fixtures/er-shop.json";
import { db, server } from "../../../../web/src/test/setup";
import plugin from "../index";
import { missingWhy } from "../Map";

const schema = shop as unknown as DbSchema;
const main = () => within(document.getElementById("main")!);
const calls = (method: string, path: string) =>
  db.calls.filter((c) => c.method === method && c.path === path);

// jsdom has no PointerEvent: React listens for "pointerdown" & co., so a MouseEvent with a pointerId does
class FakePointerEvent extends MouseEvent {
  pointerId: number;
  pointerType: string;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
    this.pointerType = init.pointerType ?? "mouse";
  }
}
beforeAll(() => vi.stubGlobal("PointerEvent", FakePointerEvent));

const box = (c: HTMLElement, id: string) =>
  c.querySelector<SVGGElement>(`[data-tid="${id}"]`)!;

describe("the Map plugin's web part", () => {
  it("is a plugin whose setup() puts the Map page in the menu, in the Project group between KeelBot and Graph", async () => {
    expect(plugin.name).toBe("map");
    expect(typeof plugin.setup).toBe("function");
    location.hash = "#/flow";
    render(<App />);
    const nav = await screen.findByRole("navigation", { name: "Screens" });
    const links = within(nav)
      .getAllByRole("link")
      .map((l) => l.textContent?.trim());
    expect(links.indexOf("Map")).toBe(links.indexOf("KeelBot") + 1);
    expect(links.indexOf("Graph")).toBe(links.indexOf("Map") + 1);
  });
});

describe("Map", () => {
  it("says 'No map yet' once, with what building does and the button", async () => {
    server.use(
      http.get("/api/projects/:pid/map", () =>
        HttpResponse.json({ missing: "No map yet. Build it to draw one." }),
      ),
    );
    location.hash = "#/map";
    render(<App />);
    expect(await main().findByText("No map yet")).toBeInTheDocument();
    expect(main().getAllByText(/No map yet/)).toHaveLength(1);
    expect(
      main().getByRole("button", { name: "Build the map" }),
    ).toBeInTheDocument();
    expect(missingWhy("No map yet. Build it to draw one.")).toMatch(
      /^Build it to see the system/,
    );
    expect(missingWhy("No map yet: the repo has no commit.")).toBe(
      "the repo has no commit.",
    );
  });

  it("has no Open in keel v1", async () => {
    location.hash = "#/map";
    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "Map" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /keel v1/ }),
    ).not.toBeInTheDocument();
  });
});

describe("Map page", () => {
  const withSchema = {
    ...fx.map,
    schema,
    sources: {
      migrations: ["db/migration/V1__accounts.sql"],
      looked_in: ["db/migration"],
    },
  };

  it("the Database tab draws the diagram from the map's schema", async () => {
    localStorage.setItem("keel2.project", "ludus-engine");
    localStorage.setItem("keel2.map.ludus-engine.tab", "er");
    server.use(
      http.get("/api/projects/:pid/map", () => HttpResponse.json(withSchema)),
    );
    location.hash = "#/map";
    render(<App />);
    expect(
      await screen.findByRole("application", {
        name: /Database diagram of ludus-engine: 29 tables shown, 41 foreign keys/,
      }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Legend")).toHaveTextContent("primary key");
  });

  it("an older map (only levels.er) still draws, and says a rebuild adds the details", async () => {
    localStorage.setItem("keel2.project", "ludus-engine");
    localStorage.setItem("keel2.map.ludus-engine.tab", "er");
    location.hash = "#/map";
    render(<App />);
    expect(
      await screen.findByRole("button", { name: "Table scores, 2 columns" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/built by an older keel/)).toBeInTheDocument();
  });

  it("no migrations: says where keel looked, how to point it, and builds the map", async () => {
    const user = userEvent.setup();
    localStorage.setItem("keel2.project", "ludus-engine");
    localStorage.setItem("keel2.map.ludus-engine.tab", "er");
    const empty = {
      ...fx.map,
      levels: { system: fx.map.levels.system },
      schema: { tables: [], relations: [] },
      sources: {
        migrations: [],
        looked_in: [
          "apps/api/src/main/resources/db/migration (backend.dir + backend.migrations)",
          "db/changelog (Liquibase SQL)",
        ],
        configured: false,
      },
    };
    let rebuilt = 0;
    server.use(
      http.get("/api/projects/:pid/map", () => HttpResponse.json(empty)),
      http.post("/api/projects/:pid/map/rebuild", () => {
        rebuilt++;
        return HttpResponse.json(empty);
      }),
    );
    location.hash = "#/map";
    render(<App />);
    const region = await screen.findByRole("region", {
      name: "No database diagram",
    });
    expect(
      within(region).getByRole("heading", { name: "No SQL migrations found" }),
    ).toBeInTheDocument();
    expect(region).toHaveTextContent("db/changelog (Liquibase SQL)");
    expect(region).toHaveTextContent("map:");
    await user.click(
      within(region).getByRole("button", { name: "Build the map" }),
    );
    await waitFor(() => expect(rebuilt).toBe(1));
  });

  it("a table box of the modules level opens the database diagram on that table", async () => {
    const user = userEvent.setup();
    localStorage.setItem("keel2.project", "ludus-engine");
    localStorage.setItem("keel2.map.ludus-engine.tab", "modules");
    const m = {
      ...withSchema,
      levels: {
        ...fx.map.levels,
        modules: {
          width: 1,
          height: 1,
          edges: [],
          nodes: [
            {
              id: "tbl:customer_order",
              kind: "data",
              title: "customer_order",
              rows: [{ t: "9 column(s)" }],
              x: 0,
              y: 0,
              w: 1,
              h: 1,
            },
          ],
        },
      },
    };
    server.use(http.get("/api/projects/:pid/map", () => HttpResponse.json(m)));
    location.hash = "#/map";
    const { container } = render(<App />);
    await screen.findByRole("application", { name: /Modules map/ });
    await act(async () => {
      fireEvent.doubleClick(box(container, "tbl:customer_order"));
    });
    expect(
      await screen.findByRole("complementary", {
        name: "Structure of customer_order",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Database (ER)" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.click(screen.getByRole("tab", { name: "System" }));
    expect(localStorage.getItem("keel2.map.ludus-engine.tab")).toBe("system");
  });
});

describe("Map › Query (the Database part's panel in the slot map.er.query)", () => {
  it("opens a table's rows, and counts a change before it runs it", async () => {
    const user = userEvent.setup();
    db.plugins.db = true;
    db.dbConns = [
      {
        name: "local",
        kind: "postgres",
        env: "local",
        shown: "postgres://app:•••@localhost/app", // keel:allow-secret (a masked fixture)
        ok: true,
        server: "PostgreSQL 16.4",
        tables: 2,
        can_change: true,
      },
    ];
    localStorage.setItem("keel2.map.ludus-engine.tab", "er");
    location.hash = "#/map";
    render(<App />);
    const panel = await screen.findByRole("region", { name: "Query" });
    await user.click(
      await within(panel).findByRole("button", { name: "players" }),
    );
    await waitFor(() =>
      expect(
        calls("POST", "/api/projects/ludus-engine/db/query")[0]?.body,
      ).toEqual({
        connection: "local",
        sql: "SELECT * FROM players LIMIT 20",
        change: true,
        confirm: false,
      }),
    );
    const rows = await within(panel).findByRole("table", {
      name: "Rows from local",
    });
    expect(within(rows).getAllByRole("row")).toHaveLength(3);
    expect(within(rows).getByText("NULL")).toHaveClass("qp-null");
    await user.clear(within(panel).getByRole("textbox", { name: "SQL" }));
    await user.type(
      within(panel).getByRole("textbox", { name: "SQL" }),
      "update scores set value = 0",
    );
    await user.click(within(panel).getByRole("button", { name: "Run" }));
    const confirm = await within(panel).findByRole("group", {
      name: "Change data",
    });
    expect(confirm).toHaveTextContent("This changes 3 rows in local (local).");
    await user.click(
      within(confirm).getByRole("button", { name: "Run it (3 rows)" }),
    );
    expect(await within(panel).findByRole("status")).toHaveTextContent(
      "3 rows changed in local.",
    );
    expect(
      calls("POST", "/api/projects/ludus-engine/db/query").at(-1)?.body,
    ).toMatchObject({ confirm: true });
  });
});
