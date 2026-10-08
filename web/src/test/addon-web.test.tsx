// Plugins step 1: an add-on's web part loads at run time from the url /api/features lists (plugins[].web.entry), with
// its css linked once; with no web part, or a module that does not load, its page says so and keel goes on.

import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { hasAddonWeb, refreshFeatures, setAddonImporter } from "../addons";
import type { Features, PluginWeb } from "../api";
import { definePlugin, type AddonPageProps } from "../sdk";
import { db } from "./setup";

const ENTRY = "/plugins/demo/1.2.0/web/index.js";
const CSS = "/plugins/demo/1.2.0/web/style.css";

function demo(web: PluginWeb | null): Features {
  return {
    mode: "both",
    modes: ["dev", "product", "both"],
    parts: { dev: true, product: true },
    addons: [{ name: "demo", version: "1.2.0", title: "Demo", part: "product", on: true }],
    screens: [
      { id: "boards", label: "Boards", group: "Demo", needs_project: false, addon: "demo" },
      { id: "people", label: "People", group: "Demo", needs_project: false, addon: "demo" },
    ],
    plugins: [{ name: "demo", title: "Demo", version: "1.2.0", web }],
  };
}

const Boards = ({ arg }: AddonPageProps) => <h1>Boards {arg ?? "all"}</h1>;
const People = ({ pid }: AddonPageProps) => <h1>People of {pid || "no project"}</h1>;
const MODULE = { default: definePlugin({ name: "demo", pages: { boards: Boards, people: People } }) };

const main = () => within(document.getElementById("main")!);
const cssLinks = () => [...document.head.querySelectorAll('link[rel="stylesheet"]')].filter((l) => l.getAttribute("href") === CSS);

function open(hash: string, features: Features, load: (url: string) => Promise<unknown>) {
  db.features = features;
  const importer = vi.fn(load);
  setAddonImporter(importer);
  location.hash = hash;
  render(<App />);
  return importer;
}

beforeEach(() => cssLinks().forEach((l) => l.remove()));
afterEach(() => vi.restoreAllMocks());

describe("an add-on's web part, loaded at run time", () => {
  it("imports the entry /api/features lists and shows the add-on's page, with the rest of the link", async () => {
    const importer = open("#/boards/B-7", demo({ entry: ENTRY, css: [CSS] }), async () => MODULE);
    expect(await main().findByRole("heading", { name: "Boards B-7", level: 1 })).toBeInTheDocument();
    expect(importer).toHaveBeenCalledWith(ENTRY);
    const nav = screen.getByRole("navigation", { name: "Screens" });
    expect(within(nav).getByRole("link", { name: "Boards" })).toBeInTheDocument();
  });

  it("imports it once for all its pages and links its css once", async () => {
    const importer = open("#/boards", demo({ entry: ENTRY, css: [CSS] }), async () => MODULE);
    await main().findByRole("heading", { name: "Boards all" });
    expect(cssLinks()).toHaveLength(1);
    act(() => { location.hash = "#/people"; });
    expect(await main().findByRole("heading", { name: "People of ludus-engine" })).toBeInTheDocument();
    act(() => { location.hash = "#/boards"; });
    await main().findByRole("heading", { name: "Boards all" });
    expect(importer).toHaveBeenCalledTimes(1);
    expect(cssLinks()).toHaveLength(1);
  });

  it("says the page is not in this keel when the add-on lists no web part, and imports nothing", async () => {
    const importer = open("#/boards", demo(null), async () => MODULE);
    expect(await main().findByText("This page of demo is not in this keel.")).toHaveAttribute("role", "status");
    expect(importer).not.toHaveBeenCalled();
    expect(hasAddonWeb("demo")).toBe(false);
  });

  it("says the page could not be loaded when the import fails, and keel goes on", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    open("#/boards", demo({ entry: ENTRY, css: [] }), async () => {
      throw new TypeError("Failed to fetch dynamically imported module");
    });
    expect(await main().findByText("This page of demo could not be loaded.")).toHaveAttribute("role", "status");
    expect(error).toHaveBeenCalledWith("keel: the web part of demo did not load", expect.any(TypeError));
    const nav = screen.getByRole("navigation", { name: "Screens" });
    expect(within(nav).getByRole("link", { name: "Flow" })).toBeInTheDocument();
  });

  it("says the page could not be loaded when the module is not a plugin's web part", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    open("#/boards", demo({ entry: ENTRY, css: [] }), async () => ({ default: { name: "demo" } }));
    expect(await main().findByText("This page of demo could not be loaded.")).toHaveAttribute("role", "status");
  });

  it("says a screen the web part has no page for is not in this keel", async () => {
    open("#/people", demo({ entry: ENTRY, css: [] }), async () => ({ default: definePlugin({ name: "demo", pages: { boards: Boards } }) }));
    expect(await main().findByText("This page of demo is not in this keel.")).toHaveAttribute("role", "status");
  });

  it("knows which add-ons have a web part from /api/features", async () => {
    db.features = demo({ entry: ENTRY, css: [] });
    expect(hasAddonWeb("demo")).toBe(false);
    await refreshFeatures();
    expect(hasAddonWeb("demo")).toBe(true);
    expect(hasAddonWeb("other")).toBe(false);
  });
});
