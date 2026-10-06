import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { GraphOverview } from "../api";
import { fileHref } from "../components/er/Structure";
import { autoDepth, fold, groupBoxes, isTest, overviewBoxes, usesText } from "../components/graph/model";
import { parseHash } from "../routes";
import * as fx from "./fixtures";
import { db } from "./setup";

type Overview = Extract<GraphOverview, { available: true }>;
const o = () => fx.graphOverview() as Overview;
const main = () => within(document.getElementById("main")!);
const box = (id: string) => document.querySelector(`[data-tid="${id}"]`) as SVGGElement | null;
const route = () => parseHash(location.hash);

describe("code graph: boxes and lines", () => {
  it("tells test code apart", () => {
    expect(isTest("src/test/java/x/ScoreServiceTest.java")).toBe(true);
    expect(isTest("engine/src/main/java/x/ScoreService.java")).toBe(false);
    expect(isTest("web/src/pages/Map.test.tsx")).toBe(true);
    expect(isTest("e2e/journey.spec.ts")).toBe(true);
    expect(isTest("api/ScoreServiceIT.kt")).toBe(true);
  });

  it("folds packages to a depth, and picks the deepest that still fits", () => {
    const g = o().groups.find((x) => x.id === "package:com.x.app.port")!;
    expect(fold(g, 1)).toEqual({ key: "p:app", label: "app", kind: "package" });
    expect(fold(g, 2).label).toBe("app.port");
    expect(fold(g, "all").label).toBe("app.port");
    expect(autoDepth(o().groups)).toBe(2);
    expect(autoDepth(o().groups, 3)).toBe(1);               // five boxes at depth 2 are too many for 3
  });

  it("the overview: a box per package, test code hidden, lines from what is used to the user", () => {
    const { boxes, edges } = overviewBoxes(o(), 1, false);
    expect(boxes.map((b) => b.title)).toEqual(["app", "domain", "web"]);         // app.port folds into app; no test box
    const app = boxes.find((b) => b.title === "app")!;
    expect(app.sub).toBe("1 class · 1 interface · 2 files");
    expect(app.rows[0].t).toBe("ScoreService  ← 3");                             // the most used first, uses from outside
    const webApp = edges.find((e) => e.to === "p:web")!;
    expect(webApp.from).toBe("p:app");                                             // the arrow points at what is used
    expect(webApp.weight).toBe(3);
    expect(webApp.tip).toBe("web uses app: 3 uses (2 calls, 1 creates)");
    expect(edges.some((e) => e.from === e.to)).toBe(false);                        // app -> app.port is inside "app" now
    expect(overviewBoxes(o(), 1, true).boxes.map((b) => b.title)).toContain("src");
    expect(usesText({ references: 1, calls: 3 })).toBe("3 calls, 1 refers to");
  });

  it("one package: its classes, and a grey box for each package it touches", () => {
    const g = groupBoxes(o(), "p:app", 2, false);
    expect(g.label).toBe("app");
    expect(g.boxes.map((b) => [b.id, b.kind])).toEqual([
      ["class:svc", "app"], ["x:p:domain", "ext"], ["x:p:web", "ext"], ["x:p:app.port", "ext"],
    ]);
    const svc = g.boxes[0];
    expect(svc.sub).toBe("class · ScoreService.java");
    expect(svc.rows[0].t).toBe("3 members · used 3× · uses 5×");
    expect(svc.cite).toEqual({ rel: "app/ScoreService.java", line: 3 });
    expect(g.boxes.find((b) => b.id === "x:p:web")!.sub).toBe("uses this 3×");
    expect(g.boxes.find((b) => b.id === "x:p:domain")!.sub).toBe("used here 4×");
  });
});

describe("Graph page", () => {
  it("is in the Project group under Map, and draws the packages", async () => {
    location.hash = "#/graph";
    render(<App />);
    const links = [...document.querySelectorAll(".side .nav a")].map((a) => a.textContent?.trim());
    expect(links.indexOf("Graph")).toBe(links.indexOf("Map") + 1);
    expect(await main().findByRole("heading", { name: "Graph" })).toBeInTheDocument();
    expect(main().getByText(/6 classes, functions and files in 5 packages and folders, 9 uses/)).toBeInTheDocument();
    await waitFor(() => expect(box("p:app")).not.toBeNull());
    expect(box("p:web")).not.toBeNull();
    expect(box("p:app.port")).not.toBeNull();                                      // depth 2 fits: five boxes or fewer
    expect(main().getByLabelText("Package depth")).toHaveValue("2");
    expect(main().getByText(/Tests \(1 hidden\)/)).toBeInTheDocument();
  });

  it("depth and tests change the boxes and are remembered", async () => {
    const user = userEvent.setup();
    location.hash = "#/graph";
    render(<App />);
    await waitFor(() => expect(box("p:app.port")).not.toBeNull());
    await user.selectOptions(main().getByLabelText("Package depth"), "1");
    await waitFor(() => expect(box("p:app.port")).toBeNull());
    expect(localStorage.getItem("keel2.graph.ludus-engine.depth")).toBe("1");
    await user.click(main().getByRole("checkbox", { name: /Tests/ }));
    await waitFor(() => expect(box("f:src")).not.toBeNull());
    expect(localStorage.getItem("keel2.graph.ludus-engine.tests")).toBe("1");
  });

  it("a double click opens a package; a class there opens the symbol", async () => {
    const user = userEvent.setup();
    location.hash = "#/graph";
    render(<App />);
    await waitFor(() => expect(box("p:app")).not.toBeNull());
    await user.dblClick(box("p:app")!.querySelector(".erd-title")!);
    await waitFor(() => expect(route()).toEqual({ page: "graph", arg: "in:p:app" }));
    expect(await main().findByRole("navigation", { name: "Where you are in the graph" })).toHaveTextContent("All packages›app");
    await waitFor(() => expect(box("class:svc")).not.toBeNull());
    expect(box("x:p:web")).not.toBeNull();
    await user.dblClick(box("class:svc")!.querySelector(".erd-title")!);
    await waitFor(() => expect(route()).toEqual({ page: "graph", arg: "class:svc" }));
  });

  it("one symbol: who uses it, what it uses, its members, impact and links to the code", async () => {
    location.hash = "#/graph/class:svc";
    render(<App />);
    const panel = await screen.findByRole("complementary", { name: "About ScoreService" });
    expect(panel).toHaveTextContent("Keeps the scores.");
    expect(panel).toHaveTextContent("2 units depend on it, directly or through others");
    expect(within(panel).getByRole("link", { name: "app/ScoreService.java:3" })).toHaveAttribute("href", fileHref({ rel: "app/ScoreService.java", line: 3 }));
    const usedBy = within(panel).getByRole("heading", { name: /Used by/ }).parentElement!;
    expect(usedBy).toHaveTextContent("ScoreController3×ScoreController.java:12");
    const uses = within(panel).getByRole("heading", { name: /^Uses/ }).parentElement!;
    expect(within(uses).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Score4×ScoreService.java:9", "ScorePort1×ScoreService.java:10"]);
    expect(within(panel).getByRole("link", { name: "save" })).toHaveAttribute("href", "#/graph/method%3Asave");
    // the diagram: the symbol in the middle, users left, used right, a line per use
    await waitFor(() => expect(box("class:ctl")).not.toBeNull());
    const x = (id: string) => Number(box(id)!.getAttribute("transform")!.match(/translate\(([-\d.]+)/)![1]);
    expect(x("class:ctl")).toBeLessThan(x("class:svc"));
    expect(x("class:score")).toBeGreaterThan(x("class:svc"));
    expect(document.querySelectorAll(".gfd .gedge")).toHaveLength(3);
    expect(document.querySelector(".gfd .gedge.u-calls")).not.toBeNull();
    expect(main().getByRole("navigation", { name: "Where you are in the graph" })).toHaveTextContent("All packages›app›ScoreService");
  });

  it("a click lights a box's lines; a double click on a member puts it in the middle; 2 steps asks again", async () => {
    const user = userEvent.setup();
    location.hash = "#/graph/class:svc";
    render(<App />);
    await waitFor(() => expect(box("class:score")).not.toBeNull());
    await user.click(box("class:score")!.querySelector(".erd-title")!);
    await waitFor(() => expect(document.querySelectorAll(".gfd .gedge.lit")).toHaveLength(1));
    expect(document.querySelectorAll(".gfd .gedge.dim")).toHaveLength(2);
    expect(screen.getByRole("complementary", { name: "About ScoreService" })).toHaveTextContent("Selected: Score");
    const before = db.calls.filter((c) => c.path.endsWith("/graph/node")).length;
    await user.click(main().getByRole("radio", { name: "2 steps" }));
    await waitFor(() => expect(db.calls.filter((c) => c.path.endsWith("/graph/node")).length).toBe(before + 1));
    expect(localStorage.getItem("keel2.graph.steps")).toBe("2");
    await waitFor(() => expect(document.querySelector('[data-mid="method:save"]')).not.toBeNull());
    await user.dblClick(document.querySelector('[data-mid="method:save"] .g-row')!);
    await waitFor(() => expect(route()).toEqual({ page: "graph", arg: "method:save" }));
  });

  it("finds a symbol by name and opens it", async () => {
    const user = userEvent.setup();
    location.hash = "#/graph";
    render(<App />);
    const input = await main().findByRole("combobox", { name: "Find a symbol" });
    await user.type(input, "sav");
    const list = await main().findByRole("listbox", { name: "Matching symbols" });
    expect(await within(list).findByRole("option", { name: /ScoreService\.save/ })).toHaveTextContent("method · ScoreService.java:8");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(route()).toEqual({ page: "graph", arg: "method:save" }));
  });

  it("no index yet: says why and builds it", async () => {
    const user = userEvent.setup();
    db.graph = { available: false, status: "missing", reason: "No code graph yet: rebuild the index to make one." };
    location.hash = "#/graph";
    render(<App />);
    expect(await main().findByText("No code graph yet: rebuild the index to make one.")).toBeInTheDocument();
    await user.click(main().getByRole("button", { name: "Build the index" }));
    await waitFor(() => expect(db.calls.some((c) => c.method === "POST" && c.path.endsWith("/index/rebuild"))).toBe(true));
    await act(async () => undefined);
  });
});
