// v0.4.2 page polish: Budget in three parts with readable numbers, Connections that render before the check answers,
// the Map's single "No map yet", Agents as scannable rows with tool chips, Tools with inline tests and a matrix that
// shows each server's state, and empty states with a way out (Skill hub, Stacks, Wiki).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { Limit } from "../api";
import { toolChips } from "../pages/Agents";
import { limitUsed } from "../pages/Budget";
import { connState } from "../pages/Connections";
import { missingWhy } from "../pages/Map";
import * as fx from "./fixtures";
import { db, server } from "./setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);
const main = () => within(document.getElementById("main")!);

const LIMITS: Limit[] = [
  { id: "claude", name: "Claude subscription", unit: "tokens in the last 5 hours", used: 122973, cap: 0, note: "Set the cap your plan allows. 0 = not set.", source: "last run" },
  { id: "copilot", name: "Copilot premium requests", unit: "requests this month", used: 212, cap: 300, note: "Resets on the 1st." },
  { id: "api", name: "API keys (all)", unit: "USD spent this month", used: 12.5, cap: 100, note: "" },
];

describe("Budget", () => {
  it("is in three parts: what the providers report, what keel counted, the limits that stop a flow", async () => {
    location.hash = "#/budget";
    render(<App />);
    for (const name of ["What the providers report", "What keel counted", "Limits that stop a flow"]) {
      expect(await main().findByRole("region", { name })).toBeInTheDocument();
    }
    const counted = main().getByRole("region", { name: "What keel counted" });
    expect(await within(counted).findByText("1.90M")).toBeInTheDocument();          // tokens this month, not 1900000
    expect(within(counted).getByText("Cost at API prices")).toBeInTheDocument();
    const stop = main().getByRole("region", { name: "Limits that stop a flow" });
    expect(await within(stop).findByText("600k tokens", { selector: "b" })).toBeInTheDocument(); // the project's cap per flow from Settings
    expect(within(stop).getByText(/pause before the next agent at/)).toBeInTheDocument();
    expect(within(stop).getByRole("link", { name: "Change in Settings" })).toHaveAttribute("href", "#/settings");
  });

  it("an account with no cap says 'No cap set' in k/M and offers to set one, focused in the drawer", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/limits", () => HttpResponse.json(LIMITS)));
    location.hash = "#/budget";
    render(<App />);
    const claude = await main().findByTestId("limit-claude");
    expect(claude).toHaveTextContent("123k tokens in the last 5 hours");
    expect(claude).not.toHaveTextContent("122973");
    expect(claude).not.toHaveTextContent("of 0");
    expect(within(claude).getByText("No cap set")).toBeInTheDocument();
    expect(main().getByTestId("limit-copilot")).toHaveTextContent("212 requests this month");
    expect(main().getByTestId("limit-copilot")).toHaveTextContent("71% of 300");
    expect(main().getByTestId("limit-api")).toHaveTextContent("$12.50 spent this month");

    await user.click(within(claude).getByRole("button", { name: "Set a cap for Claude subscription" }));
    const dlg = await screen.findByRole("dialog", { name: "Account limits" });
    const input = within(dlg).getByLabelText(/^Claude subscription \(/);
    await waitFor(() => expect(input).toHaveFocus());
    expect(input).toHaveValue("");
    await user.type(input, "1.2M");
    await user.click(within(dlg).getByRole("button", { name: "Save" }));
    await waitFor(() => expect((calls("PUT", "/api/limits")[0]?.body as Limit[])?.find((l) => l.id === "claude")?.cap).toBe(1_200_000));
  });

  it("a flow without an estimate shows a dash, not 0 and 0%", async () => {
    server.use(http.get("/api/projects/:pid/budget", () => HttpResponse.json({ ...fx.budget, recent: [{ title: "add a logger", estimate: null, real: 516795, status: "waiting" }] })));
    location.hash = "#/budget";
    render(<App />);
    const table = await main().findByRole("table", { name: "Estimate vs real" });
    const cells = within(table).getAllByRole("cell");
    expect(cells.map((c) => c.textContent)).toEqual(["add a logger", "—", "517k", "—", "waiting"]);
  });

  it("limitUsed splits the number from its unit", () => {
    expect(limitUsed(LIMITS[0])).toEqual(["123k", "tokens in the last 5 hours"]);
    expect(limitUsed(LIMITS[2])).toEqual(["$12.50", "spent this month"]);
    expect(limitUsed({ ...LIMITS[0], unit: "% of 5-hour window", used: 62 })).toEqual(["62%", "of 5-hour window"]);
  });
});

describe("Connections", () => {
  it("shows every provider's card at once, each with its own Checking…, then fills them in", async () => {
    let release = () => {};
    const wait = new Promise<void>((r) => { release = r; });
    server.use(http.get("/api/connections", async () => { await wait; return HttpResponse.json(fx.connections); }));
    location.hash = "#/connections";
    render(<App />);
    for (const id of ["claude", "codex", "copilot", "fake"]) {
      expect(within(await main().findByTestId(`conn-${id}`)).getByRole("status")).toHaveTextContent("Checking…");
    }
    expect(main().getByText("Checking what is installed…")).toBeInTheDocument();
    release();
    const claude = await main().findByRole("article", { name: "Claude" });
    expect(within(claude).getByText("No login saved")).toBeInTheDocument();
    expect(within(claude).getByRole("button", { name: "Set up login" })).toHaveClass("primary");
    expect(within(claude).getByText(/keel has no Claude login yet/)).toBeInTheDocument();
    expect(main().queryByTestId("conn-codex")).toBeNull();          // the api knows only Claude here
    expect(main().getByText("1 provider needs a login or a key before its agents can run.")).toBeInTheDocument();
  });

  it("a saved login reads Ready and offers Log in again, not as the main button", async () => {
    server.use(http.get("/api/connections", () => HttpResponse.json({ ...fx.connections,
      providers: [{ ...fx.connections.providers[0], login_set: true, login_hint: "…AAA" }] })));
    location.hash = "#/connections";
    render(<App />);
    const claude = await main().findByRole("article", { name: "Claude" });
    expect(within(claude).getByText("Ready, logged in")).toBeInTheDocument();
    expect(within(claude).getByRole("button", { name: "Log in again" })).not.toHaveClass("primary");
    expect(main().getByText("Every provider is ready.")).toBeInTheDocument();
  });

  it("connState names what is missing", () => {
    const p = fx.connections.providers[0];
    expect(connState({ ...p, login_set: true })).toMatchObject({ tone: "ok" });
    expect(connState(p)).toMatchObject({ need: "login", text: "No login saved" });
    expect(connState({ ...p, selected: "api" })).toMatchObject({ need: "key", text: "No API key" });
    expect(connState({ ...p, modes: p.modes.map((m) => ({ ...m, ready: false })) })).toMatchObject({ need: "cli", text: "claude CLI not installed" });
  });
});

describe("Map", () => {
  it("says 'No map yet' once, with what building does and the button", async () => {
    server.use(http.get("/api/projects/:pid/map", () => HttpResponse.json({ missing: "No map yet. Build it to draw one." })));
    location.hash = "#/map";
    render(<App />);
    expect(await main().findByText("No map yet")).toBeInTheDocument();
    expect(main().getAllByText(/No map yet/)).toHaveLength(1);
    expect(main().getByRole("button", { name: "Build the map" })).toBeInTheDocument();
    expect(missingWhy("No map yet. Build it to draw one.")).toMatch(/^Build it to see the system/);
    expect(missingWhy("No map yet: the repo has no commit.")).toBe("the repo has no commit.");
  });
});

describe("Agents", () => {
  it("each row: the agent, what it does, phases, model and effort, and its tools as chips", async () => {
    location.hash = "#/agents";
    render(<App />);
    const table = await main().findByRole("table", { name: "keel agents" });
    const impl = within(table).getByRole("button", { name: "Edit implementer" }).closest("tr")!;
    expect(impl).toHaveTextContent("Writes the minimum code to make the failing test pass.");
    expect(impl).toHaveTextContent("green");
    expect(impl).toHaveTextContent("gpt-5");
    expect(impl).toHaveTextContent("Copilot · Copilot CLI");
    expect(within(impl).getByText("keel", { selector: ".chip" })).toHaveClass("c-mcp");
    expect(within(impl).getByText("this project")).toBeInTheDocument();
  });

  it("search narrows the list; no match says so and clears", async () => {
    const user = userEvent.setup();
    location.hash = "#/agents";
    render(<App />);
    await main().findByRole("table", { name: "keel agents" });
    await user.type(main().getByRole("searchbox", { name: "Search agents" }), "spec");
    expect(main().getByRole("button", { name: "Edit explorer" })).toBeInTheDocument();
    expect(main().queryByRole("button", { name: "Edit implementer" })).toBeNull();
    await user.type(main().getByRole("searchbox", { name: "Search agents" }), "zzz");
    expect(main().getByText("No agent matches")).toBeInTheDocument();
    await user.click(main().getByRole("button", { name: "Clear the search" }));
    expect(main().getByRole("button", { name: "Edit implementer" })).toBeInTheDocument();
  });

  it("toolChips: read / edit / shell, then each MCP server", () => {
    expect(toolChips(["Read", "Grep", "Glob", "Edit", "Write", "Bash", "mcp:keel"]).map((c) => c.label)).toEqual(["read", "edit", "shell", "keel"]);
    expect(toolChips([])).toEqual([]);
    expect(toolChips(["a", "b", "c", "d"]).map((c) => c.label)).toEqual(["a", "b", "+2"]);
  });
});

describe("Tools (MCP)", () => {
  it("a server's test result stays next to it, and the matrix header shows each server's state", async () => {
    const user = userEvent.setup();
    location.hash = "#/tools";
    render(<App />);
    const keel = (await main().findByText("keel v2 (read-only)")).closest("tr")!;
    await user.click(within(keel).getByRole("button", { name: "Test" }));
    expect(await within(keel).findByText("Test OK: 2 tools")).toBeInTheDocument();
    const matrix = await main().findByRole("table", { name: "Who may use what" });
    const heads = within(matrix).getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads).toEqual(["Agent", "keelok · 1 of 3", "keel-v1turned off", "serenaturned off"]);
    expect(within(matrix).getByRole("checkbox", { name: "explorer may use keel" })).toBeChecked();
  });
});

describe("empty states with a way out", () => {
  it("Skill hub: a filter with no skill says so and shows all again", async () => {
    const user = userEvent.setup();
    location.hash = "#/skills";
    render(<App />);
    await main().findByRole("table", { name: "Skills" });
    await user.click(main().getByRole("tab", { name: /Claude skills/ }));
    expect(main().getByText("No skill matches")).toBeInTheDocument();
    await user.click(main().getByRole("button", { name: "Show all skills" }));
    expect(main().getByRole("tab", { name: /^All/ })).toHaveAttribute("aria-selected", "true");
    expect(main().getByRole("button", { name: "web-testing" })).toBeInTheDocument();
  });

  it("Stacks: Detect again reports what the fresh answer finds", async () => {
    const user = userEvent.setup();
    location.hash = "#/stacks";
    render(<App />);
    await main().findByRole("table", { name: "Stacks" });
    db.stacks = db.stacks.map((s) => ({ ...s, detected: true }));
    await user.click(main().getByRole("button", { name: "Detect again" }));
    expect(await screen.findByText("Detected in ludus-engine: ts-react · kotlin-spring")).toBeInTheDocument();
  });

  it("Wiki: an empty wiki says what fills it", async () => {
    server.use(http.get("/api/projects/:pid/wiki", () => HttpResponse.json({ sections: [{ id: "knowledge", title: "Knowledge", items: [] }] })));
    location.hash = "#/wiki";
    render(<App />);
    expect(await main().findByText("The wiki is empty")).toBeInTheDocument();
    expect(main().getByRole("link", { name: "Open Flow" })).toHaveAttribute("href", "#/flow");
  });
});
