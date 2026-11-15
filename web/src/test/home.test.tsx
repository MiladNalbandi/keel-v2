// keel 0.28 Home (the redesign): one box to start a flow or ask KeelBot, what waits for you, what runs and the recent
// flows; the menu's New flow and the N key open the same Start a flow drawer.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { RunRow } from "../api";
import { db, server } from "./setup";

const PID = "ludus-engine";
const now = new Date().toISOString();
const row = (o: Partial<RunRow>): RunRow => ({
  thread_id: "th_x", title: "A flow", workflow_id: "feature", status: "done", phase: null, current: null, waiting: null,
  acs_done: 0, acs_total: 0, tokens: 0, where: "folder", branch: null, error: null, created_at: now, updated_at: now, ...o,
});

function openHome() {
  location.hash = "";
  render(<App />);
  return screen.findByRole("heading", { name: "What should keel build in ludus-engine?" });
}

describe("Home (keel 0.28)", () => {
  it("is the start page: the box, what waits for you, what runs and the recent flows", async () => {
    db.runs[PID] = [
      row({ thread_id: "th_run", title: "Empty cart message", status: "running", current: "coder", acs_done: 1, acs_total: 3 }),
      row({ thread_id: "th_done", title: "Greeting for long names", status: "done", cost_usd: 0.42 }),
    ];
    server.use(
      http.get("/api/inbox", () =>
        HttpResponse.json({
          items: [{ project_id: PID, project_name: PID, thread_id: "th_7f3a", flow: "Scores for players", workflow_id: "feature",
            step: "ac_gate", kind: "gate", title: "Approve AC-2", detail: "", more: false, options: ["approve", "reject"] }],
          count: 1, kinds: ["gate"], projects: [{ id: PID, name: PID, count: 1 }],
        }),
      ),
    );
    await openHome();
    const wait = (await screen.findByRole("heading", { name: "Waiting for you" })).closest("section") as HTMLElement;
    const card = await within(wait).findByRole("link", { name: /Approve AC-2/ });
    expect(card).toHaveAttribute("href", "#/flow/th_7f3a");
    expect(card).toHaveTextContent("Gate");
    const running = (await screen.findByRole("heading", { name: "Running now" })).closest("section") as HTMLElement;
    expect(within(running).getByRole("link", { name: /Empty cart message/ })).toHaveAttribute("href", "#/flow/th_run");
    const recent = screen.getByRole("heading", { name: "Recent" }).closest("section") as HTMLElement;
    expect(within(recent).getByRole("link", { name: /Greeting for long names/ })).toHaveTextContent("$0.42");
    // the menu marks Home
    expect(within(screen.getByRole("navigation", { name: "Screens" })).getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
  });

  it("↩ opens Start a flow with what to build; an empty box says so first", async () => {
    const user = userEvent.setup();
    await openHome();
    const box = screen.getByRole("textbox", { name: "What to build" });
    await user.click(box);
    await user.keyboard("{Enter}");
    expect(screen.getByText("Say what to build first.")).toBeInTheDocument();
    await user.type(box, "Players see their rank next to the top 10{Enter}");
    const drawer = await screen.findByRole("dialog", { name: /Start a flow/ });
    expect(within(drawer).getByDisplayValue("Players see their rank next to the top 10")).toBeInTheDocument();
  });

  it("Ask KeelBot opens ⌘K with the question, where KeelBot answers", async () => {
    const user = userEvent.setup();
    await openHome();
    await user.click(screen.getByRole("button", { name: "Ask KeelBot" }));
    expect(screen.getByRole("heading", { name: "Ask about ludus-engine" })).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Your question" }), "where is the score saved?");
    await user.click(screen.getByRole("button", { name: "Ask ↩" }));
    const launcher = await screen.findByRole("dialog", { name: "Launcher" });
    // the ? prefix is ⌘K's KeelBot mode: the question stays, the prefix shows as its chip
    expect(within(launcher).getByDisplayValue(/where is the score saved\?/)).toBeInTheDocument();
  });

  it("the menu's New flow and the N key open Start a flow; N does nothing while typing", async () => {
    const user = userEvent.setup();
    await openHome();
    await user.click(screen.getByRole("button", { name: /^New flow/ }));
    expect(await screen.findByRole("dialog", { name: /Start a flow/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /Start a flow/ })).toBeNull());
    // typing n in Home's box types it
    await user.type(screen.getByRole("textbox", { name: "What to build" }), "n");
    expect(screen.queryByRole("dialog", { name: /Start a flow/ })).toBeNull();
    act(() => (document.activeElement as HTMLElement | null)?.blur());
    fireEvent.keyDown(window, { key: "n" });
    expect(await screen.findByRole("dialog", { name: /Start a flow/ })).toBeInTheDocument();
  });
});
