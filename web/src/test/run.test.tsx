// v0.4.2 UX: the shell — the sidebar and the compact phone bar with its menu.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";

async function at(hash: string) {
  location.hash = hash;
  render(<App />);
}

describe("shell", () => {
  it("the phone bar is menu + project + bell; the menu holds every screen, the status and the theme switch", async () => {
    const user = userEvent.setup();
    await at("#/flow");
    await screen.findByText(/AC gate — AC-002/);
    // no group switcher and no second Live pill in the bar any more: one row of screens
    expect(screen.queryByRole("tablist", { name: "Sections" })).not.toBeInTheDocument();
    expect(screen.getAllByTestId("live")).toHaveLength(1);
    const menu = screen.getByRole("button", { name: "Menu" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    await user.click(menu);
    const sheet = await screen.findByRole("dialog", { name: "Menu" });
    const nav = within(sheet).getByRole("navigation", { name: "All screens" });
    expect(within(nav).getByRole("link", { name: /^Budget/ })).toHaveAttribute("href", "#/budget");
    expect(within(nav).getByRole("link", { name: /^Flow/ })).toHaveFocus();
    expect(within(sheet).getByTestId("live")).toBeInTheDocument();
    const before = document.documentElement.dataset.theme;
    await user.click(within(sheet).getByRole("button", { name: /Switch to the (light|dark) theme/ }));
    expect(document.documentElement.dataset.theme).not.toBe(before);
    expect(localStorage.getItem("keel2.theme")).toBe(document.documentElement.dataset.theme);
    await user.click(within(nav).getByRole("link", { name: /^Jobs/ }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Menu" })).not.toBeInTheDocument());
    await user.click(menu);
    await screen.findByRole("dialog", { name: "Menu" });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Menu" })).not.toBeInTheDocument());
    expect(menu).toHaveFocus();
  });

  it("the sidebar nav starts with All projects and marks the screen you are on", async () => {
    await at("#/settings");
    const nav = await screen.findByRole("navigation", { name: "Screens" });
    const links = within(nav).getAllByRole("link");
    expect(links[0]).toHaveTextContent("All projects");
    expect(within(nav).getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");
  });
});
