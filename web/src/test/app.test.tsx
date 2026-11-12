import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";

const nav = () => screen.getByRole("navigation", { name: "Screens" });

describe("navigation and project switching", () => {
  it("shows the grouped nav like the mockup", async () => {
    render(<App />);
    await screen.findByRole("button", { name: /^Project: ludus-engine/ });
    const n = within(nav());
    // #93 the groups with a header; the Inbox right under All projects; Settings and Start here at the foot
    for (const g of ["Work", "Watch", "Project", "Build"]) expect(n.getByRole("button", { name: new RegExp(`^${g}`) })).toHaveAttribute("aria-expanded", "true");
    const links = n.getAllByRole("link").map((a) => a.querySelector(".nav-l")?.textContent);
    expect(links.slice(0, 2)).toEqual(["All projects", "Inbox"]);
    expect(links.slice(-2)).toEqual(["Settings", "Start here"]);
    for (const l of ["Flow", "Live agents", "Jobs", "Code", "Map", "Graph", "Wiki", "Workflows", "Agents", "Skill hub", "Stacks", "Tools (MCP)", "Settings"]) {
      expect(n.getByRole("link", { name: new RegExp(`^${l.replace(/[()]/g, "\\$&")}`) })).toBeInTheDocument();
    }
    // keel 0.18.57 Control has one entry: Budget, Connections, Databases and Plugins are Settings tabs
    for (const l of ["Budget", "Connections", "Databases", "Plugins"]) expect(n.queryByRole("link", { name: new RegExp(`^${l}`) })).toBeNull();
    expect(screen.getByRole("link", { name: /All projects/ })).toHaveTextContent("◆ 1");
  });

  it("#93 folds a group from its header, keeps the page you are on, and remembers the fold", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(/AC gate — AC-002/);
    const n = within(nav());
    const build = n.getByRole("button", { name: /^Build/ });
    await user.click(build);
    expect(build).toHaveAttribute("aria-expanded", "false");
    expect(n.getByRole("link", { name: "Workflows" })).toHaveClass("nav-folded");
    expect(JSON.parse(localStorage.getItem("keel.nav.folded") ?? "[]")).toEqual(["build"]);
    // the Flow page is open: folding Work hides the other pages and keeps Flow
    await user.click(n.getByRole("button", { name: /^Work/ }));
    expect(n.getByRole("link", { name: /^Flow/ })).not.toHaveClass("nav-folded");
    expect(n.getByRole("link", { name: /^Tasks/ })).toHaveClass("nav-folded");
    // the keys still count every page: Tasks keeps its number
    expect(n.getByRole("link", { name: /^Tasks/ })).toHaveAttribute("title", expect.stringMatching(/Tasks/));
    await user.click(build);
    expect(n.getByRole("link", { name: "Workflows" })).not.toHaveClass("nav-folded");
    expect(JSON.parse(localStorage.getItem("keel.nav.folded") ?? "[]")).toEqual(["run"]);
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
