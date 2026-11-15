// keel 0.28 Add features: keel's plugins as cards by kind, where each is on (the same control as Settings › Plugins),
// a pin for its page, and Build your own feature (a flow with the "plugin" workflow).
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { InstalledPlugin } from "../marketplaceApi";
import { db } from "./setup";

const plugin = (o: Partial<InstalledPlugin> & { name: string }): InstalledPlugin => ({
  title: o.name, summary: `What ${o.name} does.`, version: "1.0.0", from: "image", on: true, status: "loaded", parts: ["web"], ...o,
});

async function openFeatures() {
  db.market.installed = [
    plugin({ name: "review", title: "Code Review", summary: "Review a branch or a pull request." }),
    plugin({ name: "map", title: "Map", summary: "How the project is built." }),
    plugin({ name: "tasks", title: "Tasks", summary: "The work as a board." }),
    plugin({ name: "cluster", title: "Cluster", summary: "Your clusters like k9s.", on: false, status: "off" }),
    plugin({ name: "db", title: "Database", summary: "Connect databases." }),
  ];
  location.hash = "#/features";
  render(<App />);
  return screen.findByRole("heading", { name: "Add features", level: 1 });
}

describe("Add features (keel 0.28)", () => {
  it("shows the features as cards by kind, with their state, and is in the menu's foot", async () => {
    await openFeatures();
    expect(within(screen.getByRole("navigation", { name: "Screens" })).getByRole("link", { name: "Add features" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    const project = await screen.findByRole("region", { name: "Project" });
    expect(within(project).getByText("Code Review")).toBeInTheDocument();
    expect(within(project).getByText("Map")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Work" })).getByText("Tasks")).toBeInTheDocument();
    const watch = screen.getByRole("region", { name: "Watch" });
    expect(within(watch).getByText("Cluster").closest(".ft-card")).toHaveTextContent("off");
    expect(within(screen.getByRole("region", { name: "Connect" })).getByText("Database")).toBeInTheDocument();
    // find filters the cards
    await userEvent.setup().type(screen.getByRole("textbox", { name: "Find a feature" }), "k9s");
    await waitFor(() => expect(screen.queryByRole("region", { name: "Project" })).toBeNull());
    expect(screen.getByRole("region", { name: "Watch" })).toHaveTextContent("Cluster");
  });

  it("pins a feature's page to the menu and opens it", async () => {
    const user = userEvent.setup();
    await openFeatures();
    const map = (await screen.findByText("Map")).closest(".ft-card") as HTMLElement;
    await user.click(within(map).getByRole("button", { name: "◇ Pin to menu" }));
    expect(within(map).getByRole("button", { name: "◆ Pinned" })).toHaveAttribute("aria-pressed", "true");
    expect(within(screen.getByRole("navigation", { name: "Screens" })).getByRole("link", { name: "Map" })).toHaveAttribute("href", "#/map");
    expect(within(map).getByRole("link", { name: "Open" })).toHaveAttribute("href", "#/map");
  });

  it("Build your own feature asks for a name and what it does, then opens Start a flow with the plugin workflow", async () => {
    const user = userEvent.setup();
    await openFeatures();
    await user.click(screen.getByRole("button", { name: /^Build it/ }));
    expect(screen.getByText("Give it a name and say what it should do.")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Release notes");
    await user.type(screen.getByRole("textbox", { name: "What should it do?" }), "A page that writes release notes from the merged flows{Enter}");
    const drawer = await screen.findByRole("dialog", { name: /Start a flow/ });
    expect(within(drawer).getByDisplayValue("New keel feature: Release notes")).toBeInTheDocument();
    expect(within(drawer).getByDisplayValue(/Build a keel plugin called "Release notes"\. A page that writes release notes/)).toBeInTheDocument();
  });
});
