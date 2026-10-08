// v0.15.4 keyboard first: the key cheat sheet (⌘/ or ?), Code's IntelliJ-style keys (panels that show and hide, ⇧Esc,
// close / next / previous tab, Recent files) and the menu by keyboard (F6, arrows, ↩, Esc, ⌃1–⌃9). In jsdom isMac is
// false, so ⌘ is Ctrl and the menu's page keys are Ctrl+Alt+1–9.

import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";

const key = (init: KeyboardEventInit, target: Element | Window = window) =>
  fireEvent.keyDown(target, init);
const sheet = () => screen.getByRole("dialog", { name: "Keyboard keys" });
const rowOf = (label: string) =>
  within(sheet()).getByText(label).closest(".ks-row") as HTMLElement;
const edTabs = () =>
  screen.getAllByRole("tab").filter((t) => t.closest(".ed-tabs"));
const activeTab = () =>
  edTabs().find((t) => t.getAttribute("aria-selected") === "true");

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

  it("finds a key on every page, and Every page shows the other pages' keys", async () => {
    const user = userEvent.setup();
    location.hash = "#/flow";
    render(<App />);
    await screen.findByRole("button", { name: "Hide the menu" });
    key({ key: "/", code: "Slash", ctrlKey: true });
    const dlg = await screen.findByRole("dialog", { name: "Keyboard keys" });
    await user.type(within(dlg).getByRole("searchbox"), "editor tab");
    // Code's keys are found from the Flow page too, with the key the browser does not keep
    expect(rowOf("Close the editor tab")).toHaveTextContent("Alt+W");
    expect(rowOf("Close the editor tab")).toHaveTextContent(
      "instead of Ctrl+W, which the browser keeps",
    );
    expect(rowOf("Next editor tab")).toHaveTextContent("Alt+Shift+]");
    expect(within(dlg).queryByText("Show or hide the menu")).toBeNull();
    await user.clear(within(dlg).getByRole("searchbox"));
    await user.type(within(dlg).getByRole("searchbox"), "zzqx");
    expect(within(dlg).getByText(/No key matches/)).toBeInTheDocument();
    await user.clear(within(dlg).getByRole("searchbox"));
    expect(within(dlg).queryByRole("heading", { name: "Code" })).toBeNull();
    await user.click(within(dlg).getByRole("checkbox", { name: "Every page" }));
    expect(
      within(dlg).getByRole("heading", { name: "Code" }),
    ).toBeInTheDocument();
    expect(
      within(dlg).getByRole("heading", {
        name: "Code Review (in a review's file tab)",
      }),
    ).toBeInTheDocument();
  });

  it("follows the keymap chosen for Code: IntelliJ's keys or VS Code's", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    await screen.findByRole("heading", { name: "Code" });
    key({ key: "/", code: "Slash", ctrlKey: true });
    const dlg = await screen.findByRole("dialog", { name: "Keyboard keys" });
    expect(
      within(dlg).getByRole("heading", { name: "Code" }),
    ).toBeInTheDocument(); // this page's keys
    expect(rowOf("Files: show or hide")).toHaveTextContent("Ctrl+1");
    expect(rowOf("Files: show or hide")).toHaveTextContent("Alt+1");
    expect(rowOf("Next change")).toHaveTextContent("F7");
    expect(
      rowOf("Recent files (in a review's file: Recent places)"),
    ).toHaveTextContent("Ctrl+E");
    // VS Code's keymap: its own keys, and IntelliJ's ⌘1 is gone
    await user.click(within(dlg).getByRole("button", { name: "VS Code" }));
    expect(localStorage.getItem("keel2.keymap")).toBe("vscode");
    expect(rowOf("Next change")).toHaveTextContent("Alt+F5");
    expect(rowOf("Show or hide the side bar")).toHaveTextContent("Ctrl+B");
    expect(within(dlg).queryByText("Files: show or hide")).toBeNull();
    fireEvent.keyDown(within(dlg).getByRole("searchbox"), { key: "Escape" });
    // a sheet opened later reads the saved keymap
    key({ key: "?", shiftKey: true });
    await screen.findByRole("dialog", { name: "Keyboard keys" });
    expect(rowOf("Go to declaration (or ⌘-click a name)")).toHaveTextContent(
      "F12",
    );
  });
});

/** Three pinned tabs, README.md active. */
function withTabs() {
  sessionStorage.setItem(
    "keel2.repo.tabs.ludus-engine",
    JSON.stringify({
      tabs: [
        "README.md",
        "api/ScoreController.kt",
        "api/ScoreRepository.kt",
      ].map((p) => ({
        id: p,
        kind: "file",
        path: p,
        preview: false,
        view: "code",
      })),
      active: "README.md",
    }),
  );
}

describe("Code: more IntelliJ keys", () => {
  it("⌘1, ⌘9 (and ⌥1, ⌥9) show their panel and hide it when it is shown; ⇧Esc hides the side bar", async () => {
    location.hash = "#/repo";
    render(<App />);
    const files = await screen.findByRole("button", { name: "Explorer" });
    const ide = document.querySelector(".ide")!;
    expect(files).toHaveAttribute("aria-pressed", "true");
    expect(files).toHaveAttribute("title", expect.stringContaining("Ctrl+1"));
    key({ key: "1", code: "Digit1", ctrlKey: true });
    await waitFor(() => expect(files).toHaveAttribute("aria-pressed", "false"));
    expect(ide).toHaveClass("side-closed");
    key({ key: "1", code: "Digit1", ctrlKey: true });
    await waitFor(() => expect(files).toHaveAttribute("aria-pressed", "true"));
    expect(ide).not.toHaveClass("side-closed");
    // ⌘9: Git; again hides it
    key({ key: "9", code: "Digit9", ctrlKey: true });
    const git = screen.getByRole("button", { name: "Source control" });
    await waitFor(() => expect(git).toHaveAttribute("aria-pressed", "true"));
    key({ key: "9", code: "Digit9", ctrlKey: true });
    await waitFor(() => expect(git).toHaveAttribute("aria-pressed", "false"));
    expect(ide).toHaveClass("side-closed");
    // ⌥1 for a browser that keeps ⌘1
    key({ key: "¡", code: "Digit1", altKey: true });
    await waitFor(() => expect(files).toHaveAttribute("aria-pressed", "true"));
    // ⇧Esc hides the active tool window, and is not one of Esc Esc that leaves Focus mode
    key({ key: "Escape", shiftKey: true });
    await waitFor(() => expect(ide).toHaveClass("side-closed"));
    expect(files).toHaveAttribute("aria-pressed", "false");
    key({ key: "|", code: "Backslash", ctrlKey: true, shiftKey: true });
    await waitFor(() => expect(document.documentElement.dataset.focus).toBe("code"));
    key({ key: "Escape", shiftKey: true });
    key({ key: "Escape", shiftKey: true });
    expect(document.documentElement.dataset.focus).toBe("code"); // still in Focus mode
  });

  it("closes the editor tab (⌥W) and moves to the next and previous tab (⌥⇧] ⌥⇧[), round the row", async () => {
    withTabs();
    location.hash = "#/repo";
    render(<App />);
    await screen.findByRole("region", { name: "Code of README.md" });
    expect(edTabs()).toHaveLength(3);
    key({ key: "”", code: "BracketRight", altKey: true, shiftKey: true });
    await waitFor(() =>
      expect(activeTab()).toHaveTextContent("ScoreController.kt"),
    );
    key({ key: "”", code: "BracketRight", altKey: true, shiftKey: true });
    await waitFor(() =>
      expect(activeTab()).toHaveTextContent("ScoreRepository.kt"),
    );
    key({ key: "”", code: "BracketRight", altKey: true, shiftKey: true });
    await waitFor(() => expect(activeTab()).toHaveTextContent("README.md")); // round
    key({ key: "“", code: "BracketLeft", altKey: true, shiftKey: true });
    await waitFor(() =>
      expect(activeTab()).toHaveTextContent("ScoreRepository.kt"),
    );
    // ⌥W closes the active tab; its neighbour comes forward
    key({ key: "∑", code: "KeyW", altKey: true });
    await waitFor(() => expect(edTabs()).toHaveLength(2));
    expect(activeTab()).toHaveTextContent("ScoreController.kt");
    expect(
      screen.getByRole("button", { name: "Close ScoreController.kt" }),
    ).toHaveAttribute("title", expect.stringContaining("Alt+W"));
    // not while typing: ⌥W types a letter on a Mac
    const filter = screen.getByRole("searchbox", { name: "Filter files" });
    key({ key: "∑", code: "KeyW", altKey: true }, filter);
    expect(edTabs()).toHaveLength(2);
  });

  it("Recent files (⌘E) lists the tabs you looked at, open or closed, and ↩ goes back to one", async () => {
    withTabs();
    location.hash = "#/repo";
    render(<App />);
    await screen.findByRole("region", { name: "Code of README.md" });
    key({ key: "”", code: "BracketRight", altKey: true, shiftKey: true });
    await screen.findByRole("region", {
      name: "Code of api/ScoreController.kt",
    });
    key({ key: "e", code: "KeyE", ctrlKey: true });
    let pop = await screen.findByRole("dialog", { name: "Recent files" });
    expect(
      within(pop)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual([expect.stringContaining("README.md")]);
    fireEvent.keyDown(within(pop).getByRole("combobox"), { key: "Enter" });
    await screen.findByRole("region", { name: "Code of README.md" });
    expect(screen.queryByRole("dialog", { name: "Recent files" })).toBeNull();
    // a closed tab stays in the list and opens again
    key({ key: "∑", code: "KeyW", altKey: true });
    await waitFor(() => expect(edTabs()).toHaveLength(2));
    key({ key: "e", code: "KeyE", ctrlKey: true });
    pop = await screen.findByRole("dialog", { name: "Recent files" });
    const first = within(pop).getAllByRole("option")[0];
    expect(first).toHaveTextContent("README.md");
    expect(first).toHaveTextContent("closed");
    // type to filter; Esc closes without opening
    fireEvent.change(within(pop).getByRole("combobox"), {
      target: { value: "nothing-like-this" },
    });
    expect(within(pop).queryAllByRole("option")).toHaveLength(0);
    fireEvent.keyDown(within(pop).getByRole("combobox"), { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Recent files" })).toBeNull();
    key({ key: "e", code: "KeyE", ctrlKey: true });
    pop = await screen.findByRole("dialog", { name: "Recent files" });
    fireEvent.keyDown(within(pop).getByRole("combobox"), { key: "Enter" });
    await screen.findByRole("region", { name: "Code of README.md" });
    expect(edTabs()).toHaveLength(3);
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
