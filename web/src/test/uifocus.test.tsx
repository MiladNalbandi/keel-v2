// keel 0.18.6: the dashboard and keel TUI on the same project and KeelBot chat (the shared focus, api ui/UiFocus.kt).
// keel 0.18.63: a browser can keep its own project and chat (Settings › This browser › Follow other keel windows).
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { FOCUS_CLIENT, shareFocus } from "../state";
import { db, FakeEventSource } from "./setup";

const shown = () => screen.getAllByText(/ludus-engine|platform|yegi/).map((e) => e.textContent).join(" ");

describe("the shared focus", () => {
  it("opens on the project keel TUI is on", async () => {
    db.focus = { project: "platform", chats: {}, by: "tui", client: "tui-1" };
    location.hash = "#/flow";
    render(<App />);
    await waitFor(() => expect(localStorage.getItem("keel2.project")).toBe("platform"));
    expect(shown()).toContain("platform");
  });

  it("follows keel TUI to another project, and never its own change", async () => {
    location.hash = "#/flow";
    render(<App />);
    await screen.findAllByText(/ludus-engine/);
    act(() => FakeEventSource.emit("ui.focus", { project: "yegi", chats: {}, by: "tui", client: "tui-1" }));
    await waitFor(() => expect(localStorage.getItem("keel2.project")).toBe("yegi"));
    act(() => FakeEventSource.emit("ui.focus", { project: "platform", chats: {}, by: "web", client: FOCUS_CLIENT }));
    await new Promise((r) => setTimeout(r, 50));
    expect(localStorage.getItem("keel2.project")).toBe("yegi");
  });

  it("follows keel TUI to the KeelBot chat it opened", async () => {
    location.hash = "#/keelbot";
    render(<App />);
    await screen.findAllByText(/ludus-engine/);
    act(() => FakeEventSource.emit("ui.focus", { project: "ludus-engine", chats: { "ludus-engine": "h-tui" }, by: "tui", client: "tui-1" }));
    await waitFor(() => expect(localStorage.getItem("keel2.helper.ludus-engine.session")).toBe("h-tui"));
  });
});

// keel 0.18.63 Settings › This browser › Follow other keel windows: off, this browser keeps its own project and KeelBot
// chat. It follows no other window, and its own picks move no other window (no PUT /api/ui/focus).
describe("a browser that keeps its own project and chat", () => {
  const shared = () => db.calls.filter((c) => c.method === "PUT" && c.path === "/api/ui/focus");
  const later = () => new Promise((r) => setTimeout(r, 50));

  it("follows the other windows at first: a project picked here is shared", async () => {
    location.hash = "#/projects/yegi/flow";
    render(<App />);
    await waitFor(() => expect(localStorage.getItem("keel2.project")).toBe("yegi"));
    await waitFor(() => expect(shared().map((c) => c.body)).toContainEqual(expect.objectContaining({ project: "yegi", by: "web", client: FOCUS_CLIENT })));
  });

  it("switched off: a project picked here is not shared, nor a KeelBot chat", async () => {
    localStorage.setItem("keel2.followFocus", "0");
    location.hash = "#/projects/yegi/flow";
    render(<App />);
    await waitFor(() => expect(localStorage.getItem("keel2.project")).toBe("yegi"));
    await later();
    expect(shared()).toEqual([]);
    // what KeelBot's chat shares when the person opens one goes the same way
    shareFocus({ chat: "h-1", chat_project: "yegi" });
    await later();
    expect(shared()).toEqual([]);
  });

  it("switched off: opens on its own project, not the one keel TUI is on, and does not follow keel TUI", async () => {
    localStorage.setItem("keel2.followFocus", "0");
    localStorage.setItem("keel2.project", "yegi");
    db.focus = { project: "platform", chats: {}, by: "tui", client: "tui-1" };
    location.hash = "#/flow";
    render(<App />);
    await screen.findByRole("button", { name: "Project: YegiResearcher" });
    await later();
    act(() => FakeEventSource.emit("ui.focus", { project: "ludus-engine", chats: {}, by: "tui", client: "tui-1" }));
    await later();
    expect(localStorage.getItem("keel2.project")).toBe("yegi");
    expect(screen.getByRole("button", { name: "Project: YegiResearcher" })).toBeInTheDocument();
  });

  it("switched off: the KeelBot chat keel TUI has open, or opens, does not open here", async () => {
    localStorage.setItem("keel2.followFocus", "0");
    db.focus = { project: "ludus-engine", chats: { "ludus-engine": "h-start" }, by: "tui", client: "tui-1" };
    location.hash = "#/keelbot";
    render(<App />);
    await screen.findAllByText(/ludus-engine/);
    await screen.findByRole("complementary", { name: "KeelBot" });
    act(() => FakeEventSource.emit("ui.focus", { project: "ludus-engine", chats: { "ludus-engine": "h-tui" }, by: "tui", client: "tui-1" }));
    await later();
    expect(localStorage.getItem("keel2.helper.ludus-engine.session")).toBeNull();
  });

  it("the switch in Settings › This browser counts at once; on again, it follows again", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings/general";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "This browser" }));
    const sw = screen.getByRole("switch", { name: "Follow other keel windows" });
    expect(sw).toHaveAttribute("aria-checked", "true");
    await user.click(sw);
    expect(sw).toHaveAttribute("aria-checked", "false");
    expect(localStorage.getItem("keel2.followFocus")).toBe("0");
    act(() => FakeEventSource.emit("ui.focus", { project: "yegi", chats: {}, by: "tui", client: "tui-1" }));
    await later();
    expect(localStorage.getItem("keel2.project")).not.toBe("yegi");
    await user.click(sw);
    expect(localStorage.getItem("keel2.followFocus")).toBe("1");
    act(() => FakeEventSource.emit("ui.focus", { project: "yegi", chats: {}, by: "tui", client: "tui-1" }));
    await waitFor(() => expect(localStorage.getItem("keel2.project")).toBe("yegi"));
  });
});
