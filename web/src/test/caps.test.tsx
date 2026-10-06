// v0.4.2 project caps: the Budget page says how caps work and what each one leaves now; Start a flow shows the caps
// that apply, the api's refusal of a used-up cap, and what the caps changed.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import * as fx from "./fixtures";
import { db, server } from "./setup";

const main = () => within(screen.getByRole("main"));

describe("Budget: caps left", () => {
  it("says how caps work and what each cap leaves now", async () => {
    db.caps.push({ id: "c3", scope: "day", limit: 1_000_000, unit: "tokens", action: "cheaper" }, { id: "c4", scope: "step", limit: 2, unit: "usd", action: "stop" });
    db.capUse.day.tokens = 550_000;
    location.hash = "#/budget";
    render(<App />);
    const stop = await main().findByRole("region", { name: "Limits that stop a flow" });
    expect(within(stop).getByText(/A flow starts with the smallest cap left; keel checks it before every agent step\./)).toBeInTheDocument();
    const table = await within(stop).findByRole("table", { name: "Caps" });
    expect(within(table).getByRole("columnheader", { name: "Left now" })).toBeInTheDocument();
    await waitFor(() => expect(within(table).getByTestId("cap-left-c1")).toHaveTextContent("all of it, every flow"));
    expect(within(table).getByTestId("cap-left-c2")).toHaveTextContent("$96.50 left this month · $3.50 used this month");
    expect(within(table).getByTestId("cap-left-c3")).toHaveTextContent("450k left today · 550k used today");
    expect(within(table).getByTestId("cap-left-c4")).toHaveTextContent("not checked: keel counts tokens per step, not dollars");
    // the smallest one left binds: 450k of the day cap, $96.50 of the month
    const next = within(stop).getByTestId("next-flow");
    expect(next).toHaveTextContent("A flow started now gets 450k tokens (All flows, per day), then switch to cheaper models; $96.50 of reported cost (API keys, per month), then stop.");
  });

  it("a used-up cap says when it resets and that a flow cannot start", async () => {
    db.capUse.month.usd = 120;
    location.hash = "#/budget";
    render(<App />);
    const table = await main().findByRole("table", { name: "Caps" });
    await waitFor(() => expect(within(table).getByTestId("cap-left-c2")).toHaveTextContent("used up · resets on the 1st"));
    expect(main().getByTestId("next-flow")).toHaveTextContent("A flow cannot start now: The cap \"api_month\" is used up.");
  });

  it("the cap drawer explains each scope and refuses nothing it cannot check silently", async () => {
    const user = userEvent.setup();
    location.hash = "#/budget";
    render(<App />);
    await main().findByRole("table", { name: "Caps" });
    await user.click(screen.getAllByRole("button", { name: "Add cap" })[0]);
    const add = await screen.findByRole("dialog", { name: "Add cap" });
    await user.selectOptions(within(add).getByLabelText("What it limits"), "day");
    expect(within(add).getByText(/Counts every run of this project since 00:00 UTC/)).toBeInTheDocument();
    await user.selectOptions(within(add).getByLabelText("What it limits"), "step");
    await user.selectOptions(within(add).getByLabelText("Unit"), "usd");
    expect(within(add).getByRole("note")).toHaveTextContent("keel cannot count dollars per step: this cap would limit nothing. Pick tokens.");
  });
});

describe("Start a flow: caps", () => {
  it("shows the api's refusal of a used-up cap", async () => {
    const user = userEvent.setup();
    server.use(http.post("/api/projects/:pid/flows", () => HttpResponse.json({
      error: "The cap \"All flows, per day: 100k tokens\" is used up: 550k tokens used today.",
      hint: "It resets at 00:00 UTC tomorrow (2026-10-07). Raise or delete the cap in Budget › Limits that stop a flow, or start the flow after the reset.",
    }, { status: 409 })));
    localStorage.setItem("keel2.project", "yegi");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Start a flow" }));
    const dlg = await screen.findByRole("dialog", { name: "Start a flow" });
    await user.type(within(dlg).getByLabelText("What to build"), "Rank next to top 10");
    await user.click(within(dlg).getByRole("button", { name: "Start flow" }));
    expect(await within(dlg).findByText("The cap \"All flows, per day: 100k tokens\" is used up: 550k tokens used today.")).toBeInTheDocument();
    expect(within(dlg).getByText(/It resets at 00:00 UTC tomorrow \(2026-10-07\)/)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Start a flow" })).toBeInTheDocument();     // it stays open
  });

  it("says that the project's caps apply and what the caps changed once started", async () => {
    const user = userEvent.setup();
    db.caps.push({ id: "c3", scope: "day", limit: 300_000, unit: "tokens", action: "pause" });
    db.capUse.day.tokens = 100_000;
    server.use(http.post("/api/projects/:pid/flows", () => HttpResponse.json({
      ...fx.thread, status: "running", waiting: undefined, cap_note: "This flow gets 200k tokens, then pause and ask: what is left today of \"All flows, per day: 300k tokens\".",
    })));
    localStorage.setItem("keel2.project", "yegi");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Start a flow" }));
    const dlg = await screen.findByRole("dialog", { name: "Start a flow" });
    expect(await within(dlg).findByTestId("sf-caps")).toHaveTextContent("This project's caps apply too (Budget): the smallest one left wins, now at most 200k tokens, at most $96.50 of reported cost.");
    await user.type(within(dlg).getByLabelText("What to build"), "Rank next to top 10");
    await user.click(within(dlg).getByRole("button", { name: "Start flow" }));
    expect(await screen.findByText(/Flow started\. This flow gets 200k tokens, then pause and ask/)).toBeInTheDocument();
  });
});
