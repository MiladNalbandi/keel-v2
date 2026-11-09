// #89 Safety & History › History: the project's record of every tool call, its filters and the chain check.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { HistoryLine } from "../historyApi";
import { server } from "./setup";

const LINES: HistoryLine[] = [
  { seq: 4, at: "2026-11-09T10:04:00Z", thread: "t-7", step: "green", agent: "dev", source: "flow", kind: "tool", tool: "Bash",
    text: "psql -c 'DROP TABLE orders'", action: "deny", ok: false, tier: "T3", rule: "builtin.sql-drop",
    reason: "It drops or truncates a database object. That data is gone.", hash: "abcdef1234567890" },
  { seq: 3, at: "2026-11-09T10:03:00Z", thread: "t-7", step: "green", agent: "dev", source: "flow", kind: "tool", tool: "Bash",
    text: "git push origin main", action: "allow", ok: true, tier: "T2", rule: "builtin.push", reason: "It changes the server's copy." },
  { seq: 2, at: "2026-11-09T10:02:00Z", thread: "t-7", step: "green", agent: "dev", source: "flow", kind: "edit", tool: "Edit",
    text: "Edit src/app.py (+1 -1)", path: "src/app.py", action: "allow", ok: true, tier: "T1" },
  { seq: 1, at: "2026-11-09T10:01:00Z", thread: "s-2", source: "keelbot", agent: "KeelBot", kind: "read", tool: "Read",
    text: "src/app.py", path: "src/app.py", action: "allow", ok: true, tier: "T0" },
];

function serve(chain = { ok: true, text: "4 lines, chain intact" }) {
  const asked: URLSearchParams[] = [];
  server.use(
    http.get("/api/projects/:pid/history", ({ request }) => {
      const u = new URL(request.url);
      asked.push(u.searchParams);
      const action = u.searchParams.get("action");
      const items = LINES.filter((l) => !action || l.action === action);
      return HttpResponse.json({ items, more: false, next: null,
        counts: { total: 4, action: { allow: 3, deny: 1 }, tier: { T0: 1, T1: 1, T2: 1, T3: 1 }, rule: { "builtin.sql-drop": 1 } } });
    }),
    http.get("/api/projects/:pid/history/verify", () => HttpResponse.json(chain)),
  );
  return asked;
}

describe("Safety & History › History", () => {
  it("shows every call grouped by run, with its tier, what keel decided and why", async () => {
    serve();
    location.hash = "#/rules/history";
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Safety & History", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "History" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText(/Record intact: 4 lines, chain intact/)).toBeInTheDocument();
    const totals = screen.getByLabelText("Totals");
    expect(within(totals).getByText("calls recorded").previousSibling).toHaveTextContent("4");
    const run = screen.getByRole("region", { name: "Run t-7" });
    const drop = within(run).getByText("psql -c 'DROP TABLE orders'").closest("li") as HTMLElement;
    expect(within(drop).getByText("blocked")).toBeInTheDocument();
    expect(within(drop).getByTitle("cannot be undone")).toHaveTextContent("T3");
    expect(within(drop).getByText(/That data is gone/)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Run s-2" })).getByText("KeelBot")).toBeInTheDocument();
  });

  it("opens a call's detail and filters to the blocked ones", async () => {
    const user = userEvent.setup();
    const asked = serve();
    location.hash = "#/rules/history";
    render(<App />);
    const row = (await screen.findByText("psql -c 'DROP TABLE orders'")).closest("button") as HTMLElement;
    await user.click(row);
    const li = row.closest("li") as HTMLElement;
    expect(within(li).getByText("builtin.sql-drop")).toBeInTheDocument();
    expect(within(li).getByText("Sent back to the model")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/Decision/), "deny");
    await waitFor(() => expect(asked.some((p) => p.get("action") === "deny")).toBe(true));
    await waitFor(() => expect(screen.queryByText("git push origin main")).not.toBeInTheDocument());
  });

  it("says loudly when a line of the record was changed", async () => {
    serve({ ok: false, text: "line 3 was changed after it was written" });
    location.hash = "#/rules/history";
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The record was changed: line 3 was changed after it was written");
  });

  it("switches between the rules and the history", async () => {
    const user = userEvent.setup();
    serve();
    location.hash = "#/rules";
    render(<App />);
    await user.click(await screen.findByRole("tab", { name: "History" }));
    await waitFor(() => expect(location.hash).toBe("#/rules/history"));
    expect(await screen.findByLabelText("Totals")).toBeInTheDocument();
  });
});
