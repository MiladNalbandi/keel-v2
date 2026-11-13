// #101 every model runs on its provider's API in keel's own harness: no plan windows (no "Claude subscription now",
// no Copilot premium requests, no 5-hour or weekly windows). The Budget page and the budget bar show keel's own count
// and caps only, and nothing asks /api/usage/providers any more.

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { agoText, untilText } from "../format";
import { db } from "./setup";

const main = () => within(document.getElementById("main")!);

describe("no plan windows (#101)", () => {
  it("the Budget page shows what keel counted and the limits, and never asks for the providers' windows", async () => {
    location.hash = "#/budget";
    render(<App />);
    expect(await main().findByRole("region", { name: "What keel counted" })).toBeInTheDocument();
    expect(main().getByRole("region", { name: "Limits that stop a flow" })).toBeInTheDocument();
    expect(main().queryByRole("region", { name: "What the providers report" })).toBeNull();
    expect(main().queryByLabelText("Provider usage")).toBeNull();
    expect(main().queryByText(/premium requests/i)).toBeNull();
    expect(main().queryByText(/subscription/i)).toBeNull();
    expect(db.calls.some((c) => c.path.startsWith("/api/usage/providers"))).toBe(false);
  });

  it("the budget bar shows today, this month and the flow, with no provider chips", async () => {
    location.hash = "#/projects";
    render(<App />);
    const bar = await screen.findByRole("region", { name: "Budget at a glance" });
    await within(bar).findByTestId("bb-today");
    expect(within(bar).queryByTestId("bb-providers")).toBeNull();
    expect(bar.querySelector("[data-testid^='bb-prov-']")).toBeNull();
    expect(db.calls.some((c) => c.path.startsWith("/api/usage/providers"))).toBe(false);
  });

  it("the time words", () => {
    const now = Date.parse("2026-10-05T12:00:00Z");
    expect(untilText("2026-10-05T12:41:30Z", now)).toBe("41 min");
    expect(agoText("2026-10-05T09:00:00Z", now)).toBe("3h ago");
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
