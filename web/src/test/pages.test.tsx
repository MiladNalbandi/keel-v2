// v0.4.2 page polish: Budget in parts with readable numbers, Models & keys rows that render before the check answers,
// Agents as scannable rows with tool chips, Tools with inline tests and a matrix that
// shows each server's state, and empty states with a way out (Skill hub, Stacks; the Wiki's: plugins/wiki/web/test).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { Limit } from "../api";
import { toolChips } from "../pages/Agents";
import { limitUsed } from "../pages/Budget";
import { connState } from "../pages/Connections";
import * as fx from "./fixtures";
import { db, server } from "./setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);
const main = () => within(document.getElementById("main")!);

const LIMITS: Limit[] = [
  { id: "claude", name: "Claude", unit: "tokens this month", used: 122973, cap: 0, note: "0 = not set." },
  { id: "codex", name: "GPT / Codex", unit: "requests this month", used: 212, cap: 300, note: "Resets on the 1st." },
  { id: "api", name: "API keys (all)", unit: "USD spent this month", used: 12.5, cap: 100, note: "" },
];

describe("Budget", () => {
  it("is in two parts: what keel counted, the limits that stop a flow (#101 no provider plan windows)", async () => {
    location.hash = "#/budget";
    render(<App />);
    for (const name of ["What keel counted", "Limits that stop a flow"]) {
      expect(await main().findByRole("region", { name })).toBeInTheDocument();
    }
    expect(main().queryByRole("region", { name: "What the providers report" })).toBeNull();
    const counted = main().getByRole("region", { name: "What keel counted" });
    expect(await within(counted).findByText("1.90M")).toBeInTheDocument();          // tokens this month, not 1900000
    expect(within(counted).getByText("Cost at API prices")).toBeInTheDocument();
    const stop = main().getByRole("region", { name: "Limits that stop a flow" });
    // the rules for every flow, editable here; the project's own cap per flow says so, with Reset
    const every = within(stop).getByTestId("for-every-flow");
    expect(await within(every).findByLabelText("Cap per flow (tokens)")).toHaveValue("500k");
    expect(within(every).getByTestId("row-cap_tokens")).toHaveTextContent("ludus-engine uses 600k tokens");
    // the plan-window rows (warn at, pause before the next agent at) are gone
    expect(within(every).queryByRole("group", { name: "Pause before the next agent at" })).toBeNull();
    expect(within(every).queryByText(/plan window/)).toBeNull();
    expect(within(every).getByRole("link", { name: "change in Models & keys" })).toHaveAttribute("href", "#/settings/models");
  });

  it("an account with no cap says 'No cap set' in k/M and offers to set one, focused in the drawer", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/limits", () => HttpResponse.json(LIMITS)));
    location.hash = "#/budget";
    render(<App />);
    const claude = await main().findByTestId("limit-claude");
    expect(claude).toHaveTextContent("123k tokens this month");
    expect(claude).not.toHaveTextContent("122973");
    expect(claude).not.toHaveTextContent("of 0");
    expect(within(claude).getByText("No cap set")).toBeInTheDocument();
    expect(main().getByTestId("limit-codex")).toHaveTextContent("212 requests this month");
    expect(main().getByTestId("limit-codex")).toHaveTextContent("71% of 300");
    expect(main().getByTestId("limit-api")).toHaveTextContent("$12.50 spent this month");

    await user.click(within(claude).getByRole("button", { name: "Set a cap for Claude" }));
    const dlg = await screen.findByRole("dialog", { name: "Account limits" });
    const input = within(dlg).getByLabelText(/^Claude \(/);
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

  it("lists every project this month, keel Product's own runs too, adding up to the total", async () => {
    // e2e 0.18.54: the product model's 28 calls ($2.57) were in no total
    server.use(http.get("/api/projects/:pid/budget", () => HttpResponse.json({ ...fx.budget,
      projects: [{ project_id: "keel-product", name: "keel Product", tokens: 420000, cost_usd: 2.57, api_cost_usd: 2.57, calls: 28 },
                 { project_id: "shop", name: "shop", tokens: 1900000, cost_usd: 7.2, api_cost_usd: 0, calls: 90 }],
      all: { tokens: 2320000, cost_usd: 9.77 } })));
    location.hash = "#/budget";
    render(<App />);
    const table = await main().findByRole("table", { name: "Each project, this month" });
    expect(within(table).getByTestId("project-spend-keel-product")).toHaveTextContent("keel Product420k$2.57$2.57");
    expect(within(table).getByTestId("project-spend-all")).toHaveTextContent("All projects2.32M$9.77$2.57");
  });

  it("limitUsed splits the number from its unit", () => {
    expect(limitUsed(LIMITS[0])).toEqual(["123k", "tokens this month"]);
    expect(limitUsed(LIMITS[2])).toEqual(["$12.50", "spent this month"]);
    expect(limitUsed({ ...LIMITS[0], unit: "% of 5-hour window", used: 62 })).toEqual(["62%", "of 5-hour window"]);
  });
});

describe("Models & keys: the providers (#101 API keys only)", () => {
  it("shows every provider's row at once, each with its own Checking…, then fills them in", async () => {
    let release = () => {};
    const wait = new Promise<void>((r) => { release = r; });
    server.use(http.get("/api/connections", async () => { await wait; return HttpResponse.json(fx.connections); }));
    location.hash = "#/settings/models";
    render(<App />);
    for (const id of ["claude", "codex", "copilot", "fake"]) {
      expect(within(await main().findByTestId(`conn-${id}`)).getByRole("status")).toHaveTextContent("Checking…");
    }
    expect(main().getByText("Checking what is installed…")).toBeInTheDocument();
    release();
    const claude = await main().findByRole("group", { name: "Claude" });
    expect(within(claude).getByText("No API key")).toBeInTheDocument();
    expect(within(claude).getByRole("button", { name: "Add the key" })).toHaveClass("p");
    expect(within(claude).queryByRole("button", { name: /log ?in|login/i })).toBeNull();
    expect(main().queryByTestId("conn-codex")).toBeNull();          // the api knows only Claude here
    expect(main().getByText("1 provider needs a key before its agents can run.")).toBeInTheDocument();
  });

  it("adds, changes and removes the key; only its last characters show", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings/models";
    render(<App />);
    const claude = await main().findByRole("group", { name: "Claude" });
    const field = within(claude).getByLabelText(/^Anthropic API key/);
    await user.type(field, "sk-ant-api03-secret-9KQ");
    await user.click(within(claude).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT", "/api/secrets/ANTHROPIC_API_KEY")[0]?.body).toEqual({ value: "sk-ant-api03-secret-9KQ" }));
    expect(await within(claude).findByPlaceholderText("…9KQ (saved)")).toBeInTheDocument();
    expect(within(claude).getByText("API key saved")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("sk-ant-api03-secret-9KQ")).not.toBeInTheDocument();
    // saved: the button changes it, and Remove takes it out
    expect(within(claude).getByRole("button", { name: "Change" })).toBeDisabled();
    await user.click(within(claude).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(calls("DELETE", "/api/secrets/ANTHROPIC_API_KEY")).toHaveLength(1));
    expect(await within(claude).findByText("No API key")).toBeInTheDocument();
  });

  it("a refused key says why and how to copy it again", async () => {
    server.use(http.put("/api/secrets/:name", () => HttpResponse.json({ error: "The Anthropic API key has characters a key never has (#).",
      hint: "A key shown over two lines is often copied with extra characters. Copy it in one piece and save it again." }, { status: 400 })));
    const user = userEvent.setup();
    location.hash = "#/settings/models";
    render(<App />);
    const field = await main().findByLabelText(/^Anthropic API key/);
    await user.type(field, "sk-ant-api03-test_ABC#more");
    await user.click(within(field.closest(".ss-label") as HTMLElement).getByRole("button", { name: "Save" }));
    expect(await main().findByText(/characters a key never has \(#\)\. A key shown over two lines .* in one piece/)).toBeInTheDocument();
    expect(screen.queryByText("Saved and encrypted.")).not.toBeInTheDocument();
  });

  it("GitHub Copilot runs on GitHub Models: a GitHub token works, and so does Connections › GitHub's sign-in", async () => {
    const copilot = (key_set: boolean, key_hint?: string) => ({ id: "copilot", label: "GitHub Copilot", selected: "api", key_set, key_hint,
      modes: [{ id: "api", label: "GitHub Models (token)", ready: key_set, detail: "GITHUB_TOKEN" }] });
    server.use(http.get("/api/connections", () => HttpResponse.json({ ...fx.connections, providers: [copilot(false)] })));
    location.hash = "#/settings/models";
    render(<App />);
    const row = await main().findByRole("group", { name: "GitHub Copilot" });
    expect(within(row).getByText("No GitHub token")).toBeInTheDocument();
    expect(within(row).getByTestId("copilot-token-line")).toHaveTextContent("A GitHub token works, or the token of Connections › GitHub's “Sign in with GitHub”");
    expect(within(row).getByRole("link", { name: "Connections › GitHub" })).toHaveAttribute("href", "#/settings/connections");
    expect(within(row).getByTestId("way-copilot")).toHaveTextContent("GitHub Models (token)");
    expect(within(row).getByLabelText(/^GitHub token for GitHub Models/)).toHaveAttribute("id", "key-copilot");
    // signed in with GitHub, no GITHUB_TOKEN: ready, and the key field has nothing of its own to remove
    expect(connState(copilot(true, "from the GitHub sign-in") as never)).toMatchObject({ tone: "ok", text: "Ready, GitHub sign-in" });
    expect(connState(copilot(true, "…x9Z") as never)).toMatchObject({ tone: "ok", text: "API key saved" });
  });

  it("connState names what is missing", () => {
    const p = fx.connections.providers[0];
    expect(connState(p)).toMatchObject({ tone: "warn", need: "key", text: "No API key" });
    expect(connState({ ...p, key_set: true })).toMatchObject({ tone: "ok", text: "API key saved" });
    expect(connState({ ...p, id: "fake" })).toMatchObject({ tone: "ok", text: "Ready, no network" });
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
    // saved as the Copilot CLI before #101: it runs on GitHub Models, and shows so
    expect(impl).toHaveTextContent("Copilot · API key (GitHub Models)");
    expect(impl).not.toHaveTextContent("Copilot CLI");
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
    expect(heads).toEqual(["Agent", "keelok · 1 of 3Select all", "keel-v1turned off", "serenaturned off"]);   // 0.18.9 a usable server has Select all
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
});
