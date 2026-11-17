// Plan 5b: the Code page shows the project's code graph index and can rebuild it. Moved from web/src/test with the
// plugin: it runs inside keel's web, as the full image has it. v0.17.1: both are in the status bar, next to the project
// name (not in the page head), and stay there in Focus mode.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
// keel's own app and test harness
import { App } from "../../../../web/src/App";
import { restorePlugins, withoutPages } from "../../../../web/src/test/coreOnly";
import { db, server } from "../../../../web/src/test/setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);

describe("Repo index badge", () => {
  it("shows ready with counts and age, and Rebuild starts indexing", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    const badge = await screen.findByText(/^Index: ready/);
    expect(badge).toHaveTextContent("Index: ready · 412 files · 3100 symbols · 5 min ago");
    const bar = document.querySelector<HTMLElement>("footer.ide-sb")!;
    expect(bar).toContainElement(badge);
    const head = screen.getByRole("heading", { name: "Code" }).closest(".top") as HTMLElement;
    expect(head).not.toContainElement(badge);
    expect(within(bar).getByRole("group", { name: "Code graph index" })).toHaveTextContent("↻ Rebuild");
    await user.click(within(bar).getByRole("button", { name: "Rebuild" }));
    expect(await screen.findByText("Index: indexing…")).toBeInTheDocument();
    expect(calls("POST", "/api/projects/ludus-engine/index/rebuild")).toHaveLength(1);
    await waitFor(() => expect(screen.getByRole("button", { name: "Rebuild" })).toBeDisabled());
  });

  it("stays in the status bar in Focus mode", async () => {
    localStorage.setItem("keel2.repo.focus", "1");
    location.hash = "#/repo";
    render(<App />);
    const badge = await screen.findByText(/^Index: ready/);
    const bar = document.querySelector<HTMLElement>("footer.ide-sb")!;
    expect(bar).toContainElement(badge);
    expect(within(bar).getByRole("button", { name: "Rebuild" })).toBeEnabled();
    expect(screen.queryByRole("heading", { name: "Code" })).toBeNull();
  });

  it("shows why the index failed", async () => {
    db.index = { project: "ludus-engine", status: "failed", files: 0, symbols: 0, error: "codegraph index exited 1" };
    location.hash = "#/repo";
    render(<App />);
    const badge = await screen.findByText("Index: failed: codegraph index exited 1");
    expect(badge).toHaveAttribute("title", "codegraph index exited 1");
    expect(badge).toHaveClass("bad");
  });

  it("a keel without CodeGraph says so calmly: it is no failure", async () => {
    db.index = { project: "ludus-engine", status: "failed", files: 0, symbols: 0, error: "CodeGraph is not installed on this keel (agents use grep instead)." };
    location.hash = "#/repo";
    render(<App />);
    const badge = await screen.findByText("Index: no code graph on this keel · agents use grep");
    expect(badge).not.toHaveClass("bad");
  });
});

// Step 6: the index is the Graph plugin's api; a keel without the Graph (the Review set) shows no index row and asks
// nothing.
describe("Repo index badge without the Graph plugin", () => {
  afterEach(() => restorePlugins());

  it("shows no index row and does not ask /index", async () => {
    const asked: string[] = [];
    const on = ({ request }: { request: Request }) => void asked.push(new URL(request.url).pathname);
    server.events.on("request:start", on);
    try {
      withoutPages("graph");
      location.hash = "#/repo";
      render(<App />);
      expect(await screen.findByRole("button", { name: "Update from main" })).toBeInTheDocument();
      expect(screen.queryByText(/^Index:/)).toBeNull();
      expect(screen.queryByRole("button", { name: "Rebuild" })).toBeNull();
      expect(asked.filter((p) => /\/index(\/|$)/.test(p))).toEqual([]);
    } finally {
      server.events.removeListener("request:start", on);
    }
  });
});
