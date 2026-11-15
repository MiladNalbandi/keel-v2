import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { allPageNames, allPagesNav } from "./menu";

const nav = () => screen.getByRole("navigation", { name: "Screens" });

describe("navigation and project switching", () => {
  it("shows the redesigned menu: the main places, the pins, the recent flows and the foot (keel 0.28)", async () => {
    render(<App />);
    await screen.findByRole("button", { name: /^Project: ludus-engine/ });
    const n = within(nav());
    const names = n.getAllByRole("link").map((a) => a.querySelector(".nav-l")?.textContent);
    // the six main places first, then the first pins (Review, Tasks, Wiki)
    expect(names.slice(0, 6)).toEqual(["Home", "Inbox", "Flow", "Code", "KeelBot", "Live agents"]);
    expect(names.slice(6, 9)).toEqual(["Review", "Tasks", "Wiki"]);
    // the foot: All pages (a button), Add features, Settings, Start here
    expect(names.slice(-3)).toEqual(["Add features", "Settings", "Start here"]);
    expect(n.getByRole("button", { name: "All pages" })).toBeInTheDocument();
    // keel 0.18.57 Control has one entry: Budget, Connections, Databases and Plugins are Settings tabs
    for (const l of ["Budget", "Connections", "Databases", "Plugins", "Workflows", "Jobs"]) expect(n.queryByRole("link", { name: new RegExp(`^${l}`) })).toBeNull();
    // every page is in All pages, group by group
    const all = await allPageNames();
    for (const l of ["Home", "Inbox", "Flow", "Tasks", "Live agents", "Jobs", "Flow doctor", "Code", "Map", "Graph", "Wiki", "Workflows", "Agents", "Skill hub", "Stacks", "Tools (MCP)", "Safety & History", "Settings", "Start here", "Add features"])
      expect(all).toContain(l);
    expect(all.indexOf("Jobs")).toBe(all.indexOf("Live agents") + 1);
  });

  it("pins a page from All pages and from its top bar; the pins stay in this browser", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(/AC gate — AC-002/);
    // All pages: ◇ pins Map, ◆ unpins it
    await allPagesNav();
    await user.click(screen.getByRole("button", { name: "Pin Map" }));
    expect(screen.getByRole("button", { name: "Unpin Map" })).toHaveAttribute("aria-pressed", "true");
    await user.keyboard("{Escape}");
    expect(within(nav()).getByRole("link", { name: "Map" })).toHaveAttribute("href", "#/map");
    expect(JSON.parse(localStorage.getItem("keel.nav.pins") ?? "[]")).toEqual(["review", "tasks", "wiki", "map"]);
    // the top bar of a page that is not a main place pins and unpins it too
    await act(async () => {
      location.hash = "#/workflows";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    const pin = await screen.findByRole("button", { name: "◇ Pin" });
    await user.click(pin);
    expect(await screen.findByRole("button", { name: "◆ Pinned" })).toHaveAttribute("aria-pressed", "true");
    expect(within(nav()).getByRole("link", { name: "Workflows" })).toHaveAttribute("aria-current", "page");
    await user.click(screen.getByRole("button", { name: "◆ Pinned" }));
    await waitFor(() => expect(within(nav()).queryByRole("link", { name: "Workflows" })).toBeNull());
  });

  it("moves between screens with the hash and shows the breadcrumb", async () => {
    render(<App />);
    expect(await screen.findByText(/AC gate — AC-002/)).toBeInTheDocument();
    expect(screen.getByText("ludus-engine › Work")).toBeInTheDocument();
    await act(async () => { location.hash = "#/repo"; window.dispatchEvent(new HashChangeEvent("hashchange")); });
    expect(await screen.findByRole("heading", { name: "Code" })).toBeInTheDocument();
    expect(screen.getByText("ludus-engine › Project")).toBeInTheDocument();
    expect(within(nav()).getByRole("link", { name: "Code" })).toHaveAttribute("aria-current", "page");
  });

  it("switches project from the picker, remembers it, and refetches the screen", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(/AC gate — AC-002/);
    // v0.15.7 the picker is a searchable switcher: open it, choose the project
    await user.click(screen.getByRole("button", { name: /^Project: ludus-engine/ }));
    await user.click(within(screen.getByRole("listbox", { name: "Projects" })).getByRole("option", { name: "YegiResearcher" }));
    expect(await screen.findByText("Nothing running here yet")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Now showing YegiResearcher");
    expect(localStorage.getItem("keel2.project")).toBe("yegi");
    expect(screen.getByText("YegiResearcher › Work")).toBeInTheDocument();
  });

  it("opens a project from All projects", async () => {
    const user = userEvent.setup();
    location.hash = "#/projects";
    render(<App />);
    const row = (await screen.findByText("/workspace/platform")).closest<HTMLElement>('[data-testid="project-row"]')!;
    await user.click(row);
    await waitFor(() => expect(location.hash).toBe("#/flow"));
    expect(localStorage.getItem("keel2.project")).toBe("platform");
  });

  it("shows the live indicator once the event stream is open", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getAllByTestId("live")[0]).toHaveTextContent("Live"));
  });
});
