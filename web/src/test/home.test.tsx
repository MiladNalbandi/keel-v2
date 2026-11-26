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
  // Ask comes first (the person's wish); ⇥ in the empty box goes to Build a flow
  return screen.findByRole("heading", { name: "Ask keel about ludus-engine" });
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

  it("asks first; ⇥ in the empty box goes to Build a flow, whose ↩ opens Start a flow with what to build", async () => {
    const user = userEvent.setup();
    await openHome();
    const modes = within(screen.getByRole("group", { name: "What the box does" })).getAllByRole("button");
    expect(modes.map((b) => b.textContent)).toEqual(["Ask KeelBot", "Build a flow"]);
    expect(modes[0]).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("textbox", { name: "Your question" }));
    await user.keyboard("{Tab}");
    expect(screen.getByRole("heading", { name: "What should keel build in ludus-engine?" })).toBeInTheDocument();
    const box = screen.getByRole("textbox", { name: "What to build" });
    expect(box).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByText("Say what to build first.")).toBeInTheDocument();
    await user.type(box, "Players see their rank next to the top 10{Enter}");
    const drawer = await screen.findByRole("dialog", { name: /Start a flow/ });
    expect(within(drawer).getByDisplayValue("Players see their rank next to the top 10")).toBeInTheDocument();
  });

  it("Ask KeelBot starts a chat with the question and opens KeelBot's page, no ⌘K pop-up", async () => {
    const user = userEvent.setup();
    await openHome();
    await user.type(screen.getByRole("textbox", { name: "Your question" }), "where is the score saved?");
    await user.click(screen.getByRole("button", { name: "Ask ↩" }));
    await waitFor(() => expect(location.hash).toMatch(/helper|keelbot/));
    expect(screen.queryByRole("dialog", { name: "Launcher" })).toBeNull();
    const db = (globalThis as unknown as { __db: { helper: { sessions: { title: string; mode: string }[] } } }).__db;
    expect(db.helper.sessions[0]).toMatchObject({ title: "where is the score saved?", mode: "ask" });
  });

  it("a keel without a model says so under the box, with a link, and leaves no empty chat", async () => {
    // keel 0.31: the api refuses a KeelBot turn that would answer with the fake model on a real project
    server.use(http.post("/api/projects/:pid/helper/sessions/:sid/turn", () => HttpResponse.json(
      { error: "KeelBot has no model yet", hint: "Add an API key, or sign in to GitHub Copilot, in Settings › Models & keys and press Use for all agents." },
      { status: 409 })));
    const user = userEvent.setup();
    await openHome();
    await user.type(screen.getByRole("textbox", { name: "Your question" }), "where is the score saved?");
    await user.click(screen.getByRole("button", { name: "Ask ↩" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("KeelBot has no model yet");
    expect(within(alert).getByRole("link", { name: "Open Models & keys" })).toHaveAttribute("href", "#/settings/models");
    expect(location.hash).not.toMatch(/helper|keelbot/);
    await waitFor(() => expect(db.helper.sessions).toHaveLength(0));
  });

  it("the menu's New flow and the N key open Start a flow; N does nothing while typing", async () => {
    const user = userEvent.setup();
    await openHome();
    await user.click(screen.getByRole("button", { name: /^New flow/ }));
    expect(await screen.findByRole("dialog", { name: /Start a flow/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /Start a flow/ })).toBeNull());
    // typing n in Home's box types it
    await user.type(screen.getByRole("textbox", { name: "Your question" }), "n");
    expect(screen.queryByRole("dialog", { name: /Start a flow/ })).toBeNull();
    act(() => (document.activeElement as HTMLElement | null)?.blur());
    fireEvent.keyDown(window, { key: "n" });
    expect(await screen.findByRole("dialog", { name: /Start a flow/ })).toBeInTheDocument();
  });

  it("#233 tells what keel gives your agent at first sight, links to the MCP set-up, and folds to one line", async () => {
    const user = userEvent.setup();
    localStorage.removeItem("keel2.home.intro");
    await openHome();
    const intro = screen.getByRole("heading", { name: /Your agent connects to keel/ }).closest("section") as HTMLElement;
    expect(intro).toHaveTextContent("does not take control of your agents");
    const gives = within(within(intro).getByRole("list", { name: "What keel gives your agent" })).getAllByRole("listitem");
    expect(gives.map((g) => g.querySelector("b")?.textContent)).toEqual([
      "Project knowledge", "Code and commit style", "Rules", "Wiki", "Graph", "Shared context",
    ]);
    expect(intro).toHaveTextContent("Your agent wants to commit.");
    expect(within(intro).getByRole("link", { name: "Connect your agent ›" })).toHaveAttribute("href", "#/help");
    await user.click(within(intro).getByRole("button", { name: "Hide what keel gives your agent" }));
    expect(screen.queryByRole("heading", { name: /Your agent connects to keel/ })).toBeNull();
    expect(localStorage.getItem("keel2.home.intro")).toBe("hidden");
    await user.click(screen.getByRole("button", { name: "What keel gives your agent ›" }));
    expect(screen.getByRole("heading", { name: /Your agent connects to keel/ })).toBeInTheDocument();
    expect(localStorage.getItem("keel2.home.intro")).toBeNull();
  });
});
