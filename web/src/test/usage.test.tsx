import { render, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { ProviderUsage } from "../api";
import { agoText, untilText, usageLine } from "../components/UsageStrip";
import { db } from "./setup";

const main = () => within(document.getElementById("main")!);

describe("Usage dashboard", () => {
  it("the Budget page shows a card per set-up provider with its windows, source and age", async () => {
    location.hash = "#/budget";
    render(<App />);
    const claude = await main().findByTestId("usage-claude");
    expect(claude).toHaveTextContent("5h 62% · week 31% · resets in 1h 12m");
    expect(claude).toHaveTextContent("as of last run, 12 min ago");
    expect(main().getByTestId("usage-codex")).toHaveTextContent("5h 20% · week 8%");
    expect(main().getByTestId("usage-codex")).toHaveTextContent("codex app-server, just now");
    expect(main().queryByTestId("usage-copilot")).toBeNull();          // not set up: no card
    expect(within(claude).getByLabelText("Claude 62% used")).toBeInTheDocument();
  });

  it("the sidebar has the compact strip (source and age in the tooltip); All projects does not repeat it", async () => {
    location.hash = "#/projects";
    render(<App />);
    const side = document.querySelector(".side") as HTMLElement;
    const claude = await within(side).findByTestId("usage-claude");
    expect(claude).toHaveTextContent("Claude62%");
    expect(claude).toHaveTextContent("5h 62% · week 31%");
    expect(claude).toHaveAttribute("title", expect.stringContaining("as of last run, 12 min ago"));
    expect(within(claude).getByLabelText("Claude 62% used")).toBeInTheDocument();
    await main().findByRole("list", { name: "Projects" });
    expect(main().queryByTestId("usage-claude")).toBeNull();
  });

  it("refresh asks the api again and shows the new numbers", async () => {
    const user = userEvent.setup();
    location.hash = "#/budget";
    render(<App />);
    const codex = await main().findByTestId("usage-codex");
    await user.click(within(codex).getByRole("button", { name: "Refresh Codex" }));
    await waitFor(() => expect(db.calls.some((c) => c.method === "POST" && c.path === "/api/usage/providers/codex/refresh")).toBe(true));
    await waitFor(() => expect(main().getByTestId("usage-codex")).toHaveTextContent("5h 70% · week 70%"));
  });

  it("no card at all when nothing is set up; the Budget page says how to set one up", async () => {
    db.usage = [];
    location.hash = "#/budget";
    render(<App />);
    expect(await main().findByText("No provider is set up yet. Save a login or a key in Connections.")).toBeInTheDocument();
    expect(document.querySelector(".ustrip")).toBeNull();
  });

  it("the card line for Copilot and API keys, and the time words", () => {
    const now = Date.parse("2026-10-05T12:00:00Z");
    const copilot: ProviderUsage = { id: "copilot", name: "Copilot", kind: "subscription", source: "GitHub (unofficial)", live: true, can_refresh: true,
      windows: [{ window: "month", label: "monthly", used_pct: 0.707, used: 212, cap: 300, remaining: 88, resets_at: "2026-11-01T00:00:00Z" }] };
    expect(usageLine(copilot, now)).toBe("212 / 300 premium · resets Nov 1");
    const api: ProviderUsage = { id: "api", name: "API keys", kind: "api", source: "keel's own count", live: false, can_refresh: false,
      windows: [{ window: "month", label: "this month", used: 12.5, cap: 100 }] };
    expect(usageLine(api, now)).toBe("$12.50 of $100.00 this month");
    expect(untilText("2026-10-05T12:41:30Z", now)).toBe("41 min");
    expect(agoText("2026-10-05T09:00:00Z", now)).toBe("3h ago");
  });
});

describe("usageLine without a percentage", () => {
  it("says the window's status instead of '?'", async () => {
    // Real e2e: Claude's subscription reported only status allowed, and the card said "5h ? · resets in 37 min".
    const { usageLine } = await import("../components/UsageStrip");
    const soon = new Date(Date.now() + 37 * 60_000).toISOString();
    const u = { id: "claude", name: "Claude", kind: "subscription", windows: [{ window: "five_hour", label: "5-hour", used_pct: null, status: "allowed", resets_at: soon }] };
    expect(usageLine(u as never)).toMatch(/^5h ok · resets in 3[67] min/);
  });
});

describe("plainText", () => {
  it("turns a review's markdown into one short plain line for a notification", async () => {
    // Real e2e: the pop-up showed "## whole-branch code review ## Review of `git diff main...HEAD` **Spec context:**".
    const { plainText } = await import("../format");
    expect(plainText("## Review of `git diff main...HEAD`\n**Spec context:** the [cart](src/cart.js) --- ok")).toBe(
      "Review of git diff main...HEAD Spec context: the cart ok");
    expect(plainText("x".repeat(300), 10)).toBe("xxxxxxxxx…");
  });
});
