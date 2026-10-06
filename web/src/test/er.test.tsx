// The Map's database diagram (components/er): tables with key icons and typed columns, a line per foreign key, find a
// table, keys only, selection with neighbours and the Structure panel, keyboard, drag + Reset layout remembered per
// project, export, the other levels on the same canvas, and the empty state that says where keel looked.

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { DbSchema, KeelMap } from "../api";
import { BoxDiagram, endpointGroups, moduleBoxes, systemBoxes } from "../components/er/BoxDiagram";
import { ErDiagram, posKey } from "../components/er/ErDiagram";
import * as fx from "./fixtures";
import shop from "./fixtures/er-shop.json";
import { server } from "./setup";

const schema = shop as unknown as DbSchema;
const tables = schema.tables.filter((t) => t.kind === "table");
const fks = schema.relations.filter((r) => r.kind === "fk");

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
afterEach(() => vi.useRealTimers());

const box = (c: HTMLElement, id: string) => c.querySelector<SVGGElement>(`[data-tid="${id}"]`)!;
const translate = (el: Element) => el.getAttribute("transform");
function press(el: Element, x = 100, y = 100) {
  fireEvent.pointerDown(el, { clientX: x, clientY: y, button: 0, pointerId: 1 });
}
function release(el: Element, x = 100, y = 100) {
  fireEvent.pointerUp(el, { clientX: x, clientY: y, button: 0, pointerId: 1 });
}
const click = (el: Element) => { press(el); release(el); };
const draw = () => render(<ErDiagram schema={schema} pid="shop" name="shop" />);

describe("database diagram", () => {
  it("draws every table as a box with its typed columns and key icons, and a line per foreign key", () => {
    const { container } = draw();
    expect(screen.getAllByRole("button", { name: /^Table / })).toHaveLength(tables.length);
    expect(screen.getAllByRole("button", { name: /^View / })).toHaveLength(1);
    const order = box(container, "customer_order");
    expect(order).toHaveAccessibleName("Table customer_order, 9 columns");
    expect(order).toHaveTextContent("shipping_address");
    expect(order).toHaveTextContent("bigint?");                        // nullable: the type ends in "?"
    // a key icon on every primary-key column, a foreign-key icon on every referencing one
    const pkCols = tables.reduce((n, t) => n + t.columns.filter((c) => c.pk).length, 0);
    const icons = (k: string) => container.querySelectorAll(`.erd-boxes use[href="#erd-i-${k}"]`).length;
    expect(icons("key") + icons("pkfk")).toBe(pkCols);
    expect(icons("fkey") + icons("pkfk")).toBe(tables.reduce((n, t) => n + t.columns.filter((c) => c.fk && !c.pk).length + t.columns.filter((c) => c.fk && c.pk).length, 0));
    expect(container.querySelectorAll(".erd-edge.fk")).toHaveLength(fks.length);
    expect(container.querySelectorAll(".erd-edge.uses")).toHaveLength(2);
    expect(screen.getByText(/28 tables, 1 view, 41 foreign keys/)).toBeInTheDocument();
    // a schema other than the default one shows next to the name
    expect(within(box(container, "billing.invoice") as unknown as HTMLElement).getByText("billing")).toHaveClass("erd-schema");
  });

  it("find: lists matching tables and columns, highlights them, Enter jumps to the first", async () => {
    const user = userEvent.setup();
    const { container } = draw();
    await user.type(screen.getByRole("combobox", { name: "Find a table" }), "ord");
    const list = screen.getByRole("listbox", { name: "Matching tables" });
    expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual(
      expect.arrayContaining([expect.stringContaining("order_line"), expect.stringContaining("customer_order"), expect.stringContaining("order_totals")]));
    expect(box(container, "order_line")).toHaveClass("match");
    expect(box(container, "tenant")).not.toHaveClass("match");
    await user.keyboard("{Enter}");
    const first = within(list).getAllByRole("option")[0].textContent!;
    await waitFor(() => expect(container.querySelector('[aria-pressed="true"]')).toHaveAttribute("data-tid", first.replace(/^.*?(\w+)$/, "$1")));
    await user.clear(screen.getByRole("combobox", { name: "Find a table" }));
    await user.type(screen.getByRole("combobox", { name: "Find a table" }), "zzz");
    expect(screen.getByText(/No table or column matches/)).toBeInTheDocument();
  });

  it("keys only hides the plain columns; names only keeps just the headers", async () => {
    const user = userEvent.setup();
    const { container } = draw();
    await user.click(screen.getByRole("radio", { name: "Keys only" }));
    const order = box(container, "customer_order");
    expect(order).toHaveTextContent("coupon_code");
    expect(order).not.toHaveTextContent("total_cents");
    expect(JSON.parse(localStorage.getItem("keel2.er.shop.prefs")!)).toMatchObject({ mode: "keys" });
    await user.click(screen.getByRole("radio", { name: "Names only" }));
    expect(box(container, "customer_order").querySelectorAll(".erd-row")).toHaveLength(0);
    expect(container.querySelectorAll(".erd-edge.fk")).toHaveLength(fks.length);   // the lines stay, on the headers
  });

  it("the View menu turns types off and hides the tables without relations", async () => {
    const user = userEvent.setup();
    const { container } = draw();
    await user.click(screen.getByRole("button", { name: "View" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: "Column types" }));
    expect(box(container, "customer_order").querySelectorAll(".erd-type")).toHaveLength(0);
    await user.click(screen.getByRole("menuitemcheckbox", { name: "Tables without relations" }));
    expect(box(container, "audit_log")).toBeNull();
    await user.click(screen.getByRole("button", { name: /unlinked hidden/ }));
    expect(box(container, "audit_log")).not.toBeNull();
  });

  it("selecting a table lights its relations and neighbours, dims the rest, and Enter opens its structure", async () => {
    const { container } = draw();
    click(box(container, "customer_order"));
    expect(box(container, "customer_order")).toHaveAttribute("aria-pressed", "true");
    expect(container.querySelector(".erd-scene")).toHaveClass("focusing");
    expect(box(container, "app_user")).toHaveClass("hi");                 // it references app_user
    expect(box(container, "order_line")).toHaveClass("hi");               // order_line references it
    expect(box(container, "brand")).toHaveClass("dim");
    const lit = [...container.querySelectorAll(".erd-edge.hi")].map((e) => e.getAttribute("data-rid"));
    expect(lit).toHaveLength(schema.relations.filter((r) => r.from === "customer_order" || r.to === "customer_order").length);
    // the rows at both ends of a lit line are marked
    expect(box(container, "address").querySelector(".erd-row.hi")).toHaveTextContent("id");
    fireEvent.keyDown(screen.getByRole("application"), { key: "Enter" });
    const st = screen.getByRole("complementary", { name: "Structure of customer_order" });
    expect(within(st).getByRole("link", { name: "db/migration/V3__orders.sql" })).toHaveAttribute("href", expect.stringContaining("#/repo/db%2Fmigration%2FV3__orders.sql%3A"));
    expect(within(st).getByText("Referenced by").parentElement).toBeInTheDocument();
    const refs = within(st).getByRole("heading", { name: /^References/ }).parentElement!;
    expect(within(refs).getAllByRole("button").map((b) => b.textContent)).toEqual(expect.arrayContaining([expect.stringContaining("tenant"), expect.stringContaining("coupon")]));
    const idx = within(st).getByRole("heading", { name: /^Indexes/ }).parentElement!;
    expect(idx).toHaveTextContent("ix_order_user (user_id, placed_at)");
    await userEvent.setup().click(within(refs).getAllByRole("button").find((b) => b.textContent!.includes("coupon"))!);
    expect(screen.getByRole("complementary", { name: "Structure of coupon" })).toBeInTheDocument();
    expect(box(container, "coupon")).toHaveAttribute("aria-pressed", "true");
    // Open migration links to the Repo page at the line that creates the table
    expect(screen.getByRole("link", { name: /Open migration/ })).toHaveAttribute("href", expect.stringContaining("V3__orders.sql"));
  });

  it("keyboard: arrows walk between tables, Enter opens the structure, Esc clears", () => {
    const { container } = draw();
    const canvas = screen.getByRole("application", { name: /Database diagram of shop/ });
    fireEvent.keyDown(canvas, { key: "ArrowRight" });
    const first = container.querySelector('[aria-pressed="true"]')!.getAttribute("data-tid")!;
    fireEvent.keyDown(canvas, { key: "ArrowRight" });
    const second = container.querySelector('[aria-pressed="true"]')!.getAttribute("data-tid")!;
    expect(second).not.toBe(first);
    expect(screen.getByRole("status")).toHaveTextContent(`${second} selected`);
    fireEvent.keyDown(canvas, { key: "Enter" });
    expect(screen.getByRole("complementary", { name: `Structure of ${second}` })).toBeInTheDocument();
    fireEvent.keyDown(canvas, { key: "Escape" });
    expect(container.querySelector('[aria-pressed="true"]')).toBeNull();
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  it("a dragged box keeps its place for the project; Reset layout forgets it", async () => {
    const { container, unmount } = draw();
    const before = translate(box(container, "tenant"));
    press(box(container, "tenant"), 100, 100);
    fireEvent.pointerMove(box(container, "tenant"), { clientX: 180, clientY: 150, pointerId: 1 });
    fireEvent.pointerMove(box(container, "tenant"), { clientX: 260, clientY: 220, pointerId: 1 });
    release(box(container, "tenant"), 260, 220);
    const after = translate(box(container, "tenant"));
    expect(after).not.toBe(before);
    const saved = JSON.parse(localStorage.getItem(posKey("shop", "all"))!);
    expect(saved.tenant).toBeDefined();
    // the lines of the moved box are routed again, still orthogonal
    const d = container.querySelector(`.erd-edge[data-rid="fk:app_user:0"] .erd-line`)!.getAttribute("d")!;
    expect(d).toMatch(/^M/);
    unmount();
    const again = draw();
    expect(translate(box(again.container, "tenant"))).toBe(after);
    await userEvent.setup().click(screen.getByRole("button", { name: "Reset layout" }));
    expect(translate(box(again.container, "tenant"))).toBe(before);
    expect(localStorage.getItem(posKey("shop", "all"))).toBeNull();
  });

  it("export writes an SVG with the tables in it, and offers PNG", async () => {
    const user = userEvent.setup();
    const blobs: Blob[] = [];
    URL.createObjectURL = vi.fn((b: Blob | MediaSource) => { blobs.push(b as Blob); return "blob:x"; });
    URL.revokeObjectURL = vi.fn();
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    draw();
    await user.click(screen.getByRole("button", { name: "Export" }));
    expect(screen.getByRole("menuitem", { name: "PNG image" })).toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "SVG image" }));
    await waitFor(() => expect(clicked).toHaveBeenCalled());
    expect(blobs[0].type).toBe("image/svg+xml");
    const text = await new Promise<string>((ok) => { const f = new FileReader(); f.onload = () => ok(String(f.result)); f.readAsText(blobs[0]); });
    expect(text).toContain("<svg");
    expect(text).toContain("customer_order");
    expect(text).not.toContain("tabindex");
    clicked.mockRestore();
  });

  it("opens on a given table with its structure (from a table box of the modules level)", () => {
    const { container } = render(<ErDiagram schema={schema} pid="shop" name="shop" initial="billing.payment" />);
    expect(box(container, "billing.payment")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("complementary", { name: "Structure of payment" })).toBeInTheDocument();
  });

  it("a table with very many columns folds to +N more, which unfolds", () => {
    const wide: DbSchema = {
      tables: [{ id: "wide", name: "wide", kind: "table", columns: Array.from({ length: 40 }, (_, i) => ({ name: `c${i}`, type: "int", nullable: true, pk: i === 0, unique: false })) }],
      relations: [],
    };
    const { container } = render(<ErDiagram schema={wide} pid="w" name="w" />);
    expect(box(container, "wide").querySelectorAll(".erd-row")).toHaveLength(20);
    const more = screen.getByText("+20 more");
    click(more);
    expect(box(container, "wide").querySelectorAll(".erd-row")).toHaveLength(40);
  });
});

describe("the other map levels", () => {
  const level = fx.map.levels.system!;

  it("the system level draws its boxes and lines and a box opens its level", async () => {
    const onDrill = vi.fn();
    const { boxes, edges } = systemBoxes({
      ...level,
      nodes: [...level.nodes, { id: "app:code", kind: "app", title: "code", drill: "modules", rows: [{ t: "3 folders" }], x: 0, y: 0, w: 1, h: 1 }],
      edges: [...level.edges, { from: "app:code", to: "db:main", kind: "sql", d: "" }],
    });
    const { container } = render(<BoxDiagram pid="p" level="system" name="p" boxes={boxes} edges={edges} onDrill={onDrill} />);
    expect(screen.getAllByRole("button", { name: /web|postgres|code/ }).filter((b) => b.tagName === "g")).toHaveLength(3);
    expect(container.querySelectorAll(".gedge")).toHaveLength(2);
    fireEvent.doubleClick(box(container, "app:code"));
    expect(onDrill).toHaveBeenCalledWith(expect.objectContaining({ drill: "modules" }));
  });

  it("the modules level groups the endpoints and its table boxes open the database diagram", () => {
    const m: KeelMap = {
      ...fx.map, schema,
      api: { contract: "openapi.yaml", endpoints: [
        { method: "GET", path: "/api/v1/products" }, { method: "POST", path: "/api/v1/products" }, { method: "GET", path: "/api/v1/orders/{id}" },
      ] },
    };
    const level = { width: 1, height: 1, edges: [], nodes: [
      { id: "mod:web", kind: "app", title: "web", sub: "6 file(s)", rows: [{ t: "6 .ts" }], x: 0, y: 0, w: 1, h: 1 },
      { id: "tbl:customer_order", kind: "data", title: "customer_order", rows: [{ t: "9 column(s)" }], x: 0, y: 0, w: 1, h: 1 },
    ] };
    const { boxes, bands } = moduleBoxes(m, level);
    expect(bands.map((b) => b.label)).toEqual(["Code", "API", "Database"]);
    expect(boxes.filter((b) => b.kind === "api").map((b) => b.title)).toEqual(["/api/v1/orders", "/api/v1/products"]);
    expect(boxes.find((b) => b.id === "tbl:customer_order")).toMatchObject({ drill: "er", table: "customer_order", rows: [{ t: "9 columns, 5 FK" }] });
    expect(endpointGroups([{ method: "GET", path: "/a/b" }]).groups[0][0]).toBe("/a");
  });
});

describe("Map page", () => {
  const withSchema = { ...fx.map, schema, sources: { migrations: ["db/migration/V1__accounts.sql"], looked_in: ["db/migration"] } };

  it("the Database tab draws the diagram from the map's schema", async () => {
    localStorage.setItem("keel2.project", "ludus-engine");
    localStorage.setItem("keel2.map.ludus-engine.tab", "er");
    server.use(http.get("/api/projects/:pid/map", () => HttpResponse.json(withSchema)));
    location.hash = "#/map";
    render(<App />);
    expect(await screen.findByRole("application", { name: /Database diagram of ludus-engine: 29 tables shown, 41 foreign keys/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Legend")).toHaveTextContent("primary key");
  });

  it("an older map (only levels.er) still draws, and says a rebuild adds the details", async () => {
    localStorage.setItem("keel2.project", "ludus-engine");
    localStorage.setItem("keel2.map.ludus-engine.tab", "er");
    location.hash = "#/map";
    render(<App />);
    expect(await screen.findByRole("button", { name: "Table scores, 2 columns" })).toBeInTheDocument();
    expect(screen.getByText(/built by an older keel/)).toBeInTheDocument();
  });

  it("no migrations: says where keel looked, how to point it, and builds the map", async () => {
    const user = userEvent.setup();
    localStorage.setItem("keel2.project", "ludus-engine");
    localStorage.setItem("keel2.map.ludus-engine.tab", "er");
    const empty = { ...fx.map, levels: { system: fx.map.levels.system }, schema: { tables: [], relations: [] },
      sources: { migrations: [], looked_in: ["apps/api/src/main/resources/db/migration (backend.dir + backend.migrations)", "db/changelog (Liquibase SQL)"], configured: false } };
    let rebuilt = 0;
    server.use(http.get("/api/projects/:pid/map", () => HttpResponse.json(empty)),
      http.post("/api/projects/:pid/map/rebuild", () => { rebuilt++; return HttpResponse.json(empty); }));
    location.hash = "#/map";
    render(<App />);
    const region = await screen.findByRole("region", { name: "No database diagram" });
    expect(within(region).getByRole("heading", { name: "No SQL migrations found" })).toBeInTheDocument();
    expect(region).toHaveTextContent("db/changelog (Liquibase SQL)");
    expect(region).toHaveTextContent("map:");
    await user.click(within(region).getByRole("button", { name: "Build the map" }));
    await waitFor(() => expect(rebuilt).toBe(1));
  });

  it("a table box of the modules level opens the database diagram on that table", async () => {
    const user = userEvent.setup();
    localStorage.setItem("keel2.project", "ludus-engine");
    localStorage.setItem("keel2.map.ludus-engine.tab", "modules");
    const m = { ...withSchema, levels: { ...fx.map.levels, modules: { width: 1, height: 1, edges: [], nodes: [
      { id: "tbl:customer_order", kind: "data", title: "customer_order", rows: [{ t: "9 column(s)" }], x: 0, y: 0, w: 1, h: 1 }] } } };
    server.use(http.get("/api/projects/:pid/map", () => HttpResponse.json(m)));
    location.hash = "#/map";
    const { container } = render(<App />);
    await screen.findByRole("application", { name: /Modules map/ });
    await act(async () => { fireEvent.doubleClick(box(container, "tbl:customer_order")); });
    expect(await screen.findByRole("complementary", { name: "Structure of customer_order" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Database (ER)" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "System" }));
    expect(localStorage.getItem("keel2.map.ludus-engine.tab")).toBe("system");
  });
});

describe("Repo page from a map link", () => {
  it("opens the file at the line a table or column cites", async () => {
    localStorage.setItem("keel2.project", "ludus-engine");
    location.hash = `#/repo/${encodeURIComponent("api/ScoreController.kt:3")}`;
    render(<App />);
    const pre = await screen.findByLabelText("First lines of the file, line 3 marked");
    expect(pre.querySelector(".at")).toHaveTextContent("class ScoreController");
    expect(screen.getByText("a81c3f0 feat(AC-002)")).toBeInTheDocument();
  });
});
