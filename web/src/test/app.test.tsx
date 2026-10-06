import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";

const nav = () => screen.getByRole("navigation", { name: "Screens" });

describe("navigation and project switching", () => {
  it("shows the grouped nav like the mockup", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "ludus-engine" });
    const n = within(nav());
    for (const g of ["Run", "Project", "Build", "Control"]) expect(n.getAllByText(g).length).toBeGreaterThan(0);
    for (const l of ["Flow", "Live agents", "Jobs", "Repo", "Map", "Wiki", "Workflows", "Agents", "Skill hub", "Stacks", "Tools (MCP)", "Budget", "Settings", "Connections"]) {
      expect(n.getByRole("link", { name: new RegExp(`^${l.replace(/[()]/g, "\\$&")}`) })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: /All projects/ })).toHaveTextContent("◆ 1");
  });

  it("moves between screens with the hash and shows the breadcrumb", async () => {
    render(<App />);
    expect(await screen.findByText(/AC gate — AC-002/)).toBeInTheDocument();
    expect(screen.getByText("ludus-engine › Run")).toBeInTheDocument();
    await act(async () => { location.hash = "#/repo"; window.dispatchEvent(new HashChangeEvent("hashchange")); });
    expect(await screen.findByRole("heading", { name: "Repo" })).toBeInTheDocument();
    expect(screen.getByText("ludus-engine › Project")).toBeInTheDocument();
    expect(within(nav()).getByRole("link", { name: "Repo" })).toHaveAttribute("aria-current", "page");
  });

  it("switches project from the picker, remembers it, and refetches the screen", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(/AC gate — AC-002/);
    await user.selectOptions(screen.getByLabelText("Project"), "yegi");
    expect(await screen.findByText("Nothing running here yet")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Now showing YegiResearcher");
    expect(localStorage.getItem("keel2.project")).toBe("yegi");
    expect(screen.getByText("YegiResearcher › Run")).toBeInTheDocument();
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
