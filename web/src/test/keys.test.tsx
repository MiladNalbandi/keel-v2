// v0.15.4 keyboard first: the key cheat sheet (⌘/ or ?) and the menu by keyboard (F6, arrows, ↩, Esc, ⌃1–⌃9). In jsdom
// isMac is false, so ⌘ is Ctrl and the menu's page keys are Ctrl+Alt+1–9. The parts' areas of keys come through slot
// keys.area (tested here with a part of the test's own); the Code page's keys are tested with the Code plugin
// (plugins/code/web/test/keys.test.tsx).

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { registerSlot } from "../sdk/registry";
import { SLOTS, type KeysAreaItem } from "../sdk/slots";

const key = (init: KeyboardEventInit, target: Element | Window = window) =>
  fireEvent.keyDown(target, init);
const sheet = () => screen.getByRole("dialog", { name: "Keyboard keys" });
const rowOf = (label: string) =>
  within(sheet()).getByText(label).closest(".ks-row") as HTMLElement;

describe("the key cheat sheet", () => {
  it("opens with ⌘/ and with ?, lists the keys for this page, and Esc closes it and gives the focus back", async () => {
    location.hash = "#/flow";
    render(<App />);
    const hide = await screen.findByRole("button", { name: "Hide the menu" });
    hide.focus();
    key({ key: "/", code: "Slash", ctrlKey: true });
    const dlg = await screen.findByRole("dialog", { name: "Keyboard keys" });
    expect(dlg).toHaveAttribute("aria-modal", "true");
    await waitFor(() =>
      expect(
        within(dlg).getByRole("searchbox", { name: "Find a key" }),
      ).toHaveFocus(),
    );
    expect(
      within(dlg).getByRole("heading", { name: "Everywhere" }),
    ).toBeInTheDocument();
    expect(
      within(dlg).getByRole("heading", {
        name: "The menu (after you jump into it)",
      }),
    ).toBeInTheDocument();
    expect(
      within(dlg).getByRole("heading", { name: "Flow" }),
    ).toBeInTheDocument();
    expect(within(dlg).queryByRole("heading", { name: "Code" })).toBeNull(); // another page's keys
    expect(rowOf("This list of keys")).toHaveTextContent("Ctrl+/");
    expect(rowOf("Jump into the menu")).toHaveTextContent("F6");
    expect(rowOf("Jump into the menu")).toHaveTextContent("Ctrl+Alt+M");
    expect(rowOf("Open page 1 to 9 of the menu")).toHaveTextContent(
      "Ctrl+Alt+1 … Ctrl+Alt+9",
    );
    fireEvent.keyDown(within(dlg).getByRole("searchbox"), { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Keyboard keys" })).toBeNull();
    expect(hide).toHaveFocus();

    key({ key: "?", shiftKey: true });
    expect(
      await screen.findByRole("dialog", { name: "Keyboard keys" }),
    ).toBeInTheDocument();
    // ⌘/ again closes it
    key(
      { key: "/", code: "Slash", ctrlKey: true },
      within(sheet()).getByRole("searchbox"),
    );
    expect(screen.queryByRole("dialog", { name: "Keyboard keys" })).toBeNull();
  });

  it("does not open with ? while you type in a field", async () => {
    location.hash = "#/flow";
    render(<App />);
    await screen.findByRole("button", { name: "Hide the menu" });
    const field = document.createElement("input");
    document.body.append(field);
    field.focus();
    key({ key: "?", shiftKey: true }, field);
    expect(screen.queryByRole("dialog", { name: "Keyboard keys" })).toBeNull();
    // ⌘/ still works from a field
    key({ key: "/", code: "Slash", ctrlKey: true }, field);
    expect(
      await screen.findByRole("dialog", { name: "Keyboard keys" }),
    ).toBeInTheDocument();
    field.remove();
  });

  it("shows a part's area of keys (slot keys.area) by its order, on its pages, and in the keymap chosen", async () => {
    const user = userEvent.setup();
    const area: KeysAreaItem = {
      id: "pt-demo",
      title: "A part's keys",
      pages: ["workflows"],
      order: 75,
      rows: (k) => [{ label: "Do the part's thing", keys: [k === "intellij" ? "alt+f9" : "shift+f9"] }],
    };
    const gone = registerSlot(SLOTS.keysArea, area);
    try {
      location.hash = "#/workflows";
      render(<App />);
      await screen.findByRole("button", { name: "Hide the menu" });
      key({ key: "/", code: "Slash", ctrlKey: true });
      const dlg = await screen.findByRole("dialog", { name: "Keyboard keys" });
      // this page's areas first, each in its order: Workflows (80) after the part's (75)
      const heads = within(dlg).getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
      expect(heads.indexOf("A part's keys")).toBeGreaterThanOrEqual(0);
      expect(heads.indexOf("A part's keys")).toBeLessThan(heads.indexOf("Workflows"));
      expect(heads.indexOf("Workflows")).toBeLessThan(heads.indexOf("Everywhere"));
      expect(rowOf("Do the part's thing")).toHaveTextContent("Alt+F9");
      await user.click(within(dlg).getByRole("button", { name: "VS Code" }));
      expect(rowOf("Do the part's thing")).toHaveTextContent("Shift+F9");
      fireEvent.keyDown(within(dlg).getByRole("searchbox"), { key: "Escape" });
      // another page: the part's area shows only with Every page
      await act(async () => {
        location.hash = "#/flow";
        await new Promise((r) => setTimeout(r, 20));
      });
      key({ key: "/", code: "Slash", ctrlKey: true });
      const again = await screen.findByRole("dialog", { name: "Keyboard keys" });
      expect(within(again).queryByRole("heading", { name: "A part's keys" })).toBeNull();
      await user.click(within(again).getByRole("checkbox", { name: "Every page" }));
      expect(within(again).getByRole("heading", { name: "A part's keys" })).toBeInTheDocument();
    } finally {
      gone();
      localStorage.removeItem("keel2.keymap");
    }
  });
});

describe("the menu by keyboard", () => {
  it("F6 jumps into the menu at this page; ↑↓ Home End move, ↩ opens, Esc goes back to the page", async () => {
    location.hash = "#/flow";
    render(<App />);
    const nav = await screen.findByRole("navigation", { name: "Screens" });
    const links = within(nav).getAllByRole("link");
    const flow = within(nav).getByRole("link", { name: "Flow" });
    key({ key: "F6" });
    expect(flow).toHaveFocus();
    key({ key: "ArrowDown" }, flow);
    expect(links[links.indexOf(flow) + 1]).toHaveFocus();
    key({ key: "ArrowUp" }, document.activeElement!);
    expect(flow).toHaveFocus();
    key({ key: "End" }, flow);
    expect(links[links.length - 1]).toHaveFocus();
    key({ key: "Home" }, document.activeElement!);
    expect(links[0]).toHaveFocus();
    key({ key: "ArrowUp" }, links[0]); // stays at the top
    expect(links[0]).toHaveFocus();
    // ↩ opens the page (the links are real links)
    const tasks = within(nav).getByRole("link", { name: "Tasks" });
    tasks.focus();
    key({ key: "Enter" }, tasks);
    await waitFor(() => expect(location.hash).toBe("#/tasks"));
    // Esc: back to the page
    key({ key: "Escape" }, document.activeElement!);
    expect(document.getElementById("main")).toHaveFocus();
    // ⌃⌘M (Ctrl+Alt+M off a Mac) jumps in too, at the page now shown
    key({ key: "µ", code: "KeyM", ctrlKey: true, altKey: true });
    await waitFor(() =>
      expect(within(nav).getByRole("link", { name: "Tasks" })).toHaveFocus(),
    );
  });

  it("works in the folded rail as well", async () => {
    localStorage.setItem("keel2.nav.hidden", "1");
    location.hash = "#/flow";
    render(<App />);
    const rail = await screen.findByRole("navigation", {
      name: "Screens (icons)",
    });
    key({ key: "F6" });
    const flow = within(rail).getByRole("link", { name: "Flow" });
    expect(flow).toHaveFocus();
    key({ key: "ArrowDown" }, flow);
    expect(within(rail).getByRole("link", { name: "Tasks" })).toHaveFocus();
    key({ key: "Escape" }, document.activeElement!);
    expect(document.getElementById("main")).toHaveFocus();
    expect(
      screen.getByRole("button", { name: "Show the menu" }),
    ).toHaveAttribute("title", expect.stringContaining("F6"));
  });

  it("⌃1–⌃9 open the first nine pages in menu order, and the tooltips say so", async () => {
    location.hash = "#/flow";
    render(<App />);
    const nav = await screen.findByRole("navigation", { name: "Screens" });
    const links = within(nav).getAllByRole("link");
    expect(links[0]).toHaveAttribute("title", "All projects (Ctrl+Alt+1)");
    expect(links[2]).toHaveAttribute(
      "title",
      expect.stringContaining("(Ctrl+Alt+3)"),
    );
    expect(links[9]?.getAttribute("title") ?? "").not.toMatch(/Ctrl/); // only nine
    key({ key: "3", code: "Digit3", ctrlKey: true, altKey: true });
    await waitFor(() =>
      expect(location.hash).toBe(links[2].getAttribute("href")),
    );
    key({ key: "1", code: "Digit1", ctrlKey: true, altKey: true });
    await waitFor(() => expect(location.hash).toBe("#/projects"));
    // Ctrl+1 alone stays the browser's (and Code's ⌘1): no page change
    key({ key: "2", code: "Digit2", ctrlKey: true });
    expect(location.hash).toBe("#/projects");
  });
});
