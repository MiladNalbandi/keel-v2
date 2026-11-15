// keel 0.28 the redesign, the Build pages: the page head's buttons carry their key, and that letter clicks them (not
// while typing, not with a modifier, not while a dialog is open). The page's N beats the shell's N (New flow).

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";

describe("Build pages: the page keys", () => {
  it("Agents: the head shows N and T, and N opens New agent instead of Start a flow", async () => {
    location.hash = "#/agents";
    render(<App />);
    const add = await screen.findByRole("button", { name: "New agent" });
    expect(add).toHaveAttribute("aria-keyshortcuts", "N");
    expect(screen.getByRole("button", { name: "Test all" })).toHaveAttribute("aria-keyshortcuts", "T");
    await screen.findByRole("table", { name: "keel agents" });
    fireEvent.keyDown(document.body, { key: "n" });
    expect(await screen.findByRole("dialog", { name: "New agent" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: /Start a flow/ })).not.toBeInTheDocument();
    // a dialog is open: T does nothing
    fireEvent.keyDown(document.body, { key: "t" });
    expect(screen.queryByRole("group", { name: "Confirm" })).not.toBeInTheDocument();
  });

  it("a key does nothing while typing or with a modifier", async () => {
    const user = userEvent.setup();
    location.hash = "#/agents";
    render(<App />);
    await screen.findByRole("table", { name: "keel agents" });
    const search = screen.getByRole("searchbox", { name: "Search agents" });
    await user.type(search, "n");
    expect(search).toHaveValue("n");
    fireEvent.keyDown(document.body, { key: "n", metaKey: true });
    fireEvent.keyDown(document.body, { key: "t", ctrlKey: true });
    expect(screen.queryByRole("dialog", { name: "New agent" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Confirm" })).not.toBeInTheDocument();
  });

  it("Safety & History: Library, Import and New rule sit in the head on the rules tab only", async () => {
    location.hash = "#/rules";
    render(<App />);
    const lib = await screen.findByRole("button", { name: "Library" });
    expect(lib).toHaveAttribute("aria-keyshortcuts", "L");
    fireEvent.keyDown(document.body, { key: "n" });
    expect(await screen.findByRole("dialog", { name: "New rule" })).toBeInTheDocument();
  });

  it("Workflows: New workflow, Import and the Library switch are in the head", async () => {
    const user = userEvent.setup();
    location.hash = "#/workflows/feature";
    render(<App />);
    const show = await screen.findByRole("group", { name: "Show" });
    expect(screen.getByRole("button", { name: "New workflow" })).toHaveAttribute("aria-keyshortcuts", "N");
    expect(screen.getByRole("button", { name: "Import" })).toHaveAttribute("aria-keyshortcuts", "I");
    const library = screen.getAllByRole("button", { name: "Library" }).find((b) => show.contains(b))!;
    await user.click(library);
    await waitFor(() => expect(library).toHaveAttribute("aria-pressed", "true"));
  });
});
