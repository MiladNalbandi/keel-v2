// v0.15.3 a stopped flow says why and offers Resume (a rewind to its newest checkpoint, asked first) and a way back to
// an earlier step; the menu shows keel's version at its foot.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import * as fx from "./fixtures";
import { db } from "./setup";

describe("a stopped flow", () => {
  it("offers Resume after its last saved step, asks first, then rewinds there; or opens the checkpoints", async () => {
    const user = userEvent.setup();
    const f = db.flows["ludus-engine"];
    f.thread = { ...f.thread!, status: "stopped", waiting: undefined };
    location.hash = "#/flow";
    render(<App />);
    expect(await screen.findByText(/It stopped while a step ran/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Resume the flow" }));
    const ask = await screen.findByRole("group", { name: "Resume the flow" });
    const newest = [...fx.checkpoints].sort((a, b) => b.n - a.n)[0];
    expect(ask).toHaveTextContent(`Resume after ${newest.step}`);
    expect(db.calls.some((l) => l.path.endsWith("/rewind"))).toBe(false);
    await user.click(within(ask).getByRole("button", { name: "Yes, resume" }));
    await waitFor(() => expect(db.calls.find((l) => l.path.endsWith("/rewind"))?.body).toEqual({ checkpoint_id: newest.id }));

    await user.click(await screen.findByRole("button", { name: /Go back to an earlier step/ }));
    expect(screen.getByRole("tab", { name: "Checkpoints" })).toHaveAttribute("aria-selected", "true");
  });

  it("0.18.74 Finish by hand: tick what you finished, say what you did, keel commits and ends the flow", async () => {
    const user = userEvent.setup();
    const f = db.flows["ludus-engine"];
    f.thread = { ...f.thread!, status: "stopped", waiting: undefined };
    const open = f.thread.acs.filter((a) => a.status !== "done");
    location.hash = "#/flow";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Finish by hand…" }));
    const form = screen.getByRole("form", { name: "Finish by hand" });
    await user.click(within(form).getByRole("checkbox", { name: new RegExp(open[0].id) }));
    await user.type(within(form).getByRole("textbox", { name: /What you did/ }), "fixed the order myself");
    await user.click(within(form).getByRole("button", { name: "Commit and finish" }));
    await waitFor(() => expect(db.calls.find((l) => l.path.endsWith("/finish-by-hand"))?.body)
      .toEqual({ done: [open[0].id], message: "fixed the order myself" }));
  });

  it("Stop flow asks first: Keep it running stops nothing, Esc closes, Stop the flow stops it", async () => {
    const user = userEvent.setup();
    location.hash = "#/flow";
    render(<App />);
    const stops = () => db.calls.filter((c) => c.method === "POST" && c.path.endsWith("/stop"));
    await user.click(await screen.findByRole("button", { name: "Stop flow" }));
    let ask = screen.getByRole("alertdialog", { name: "Stop this flow?" });
    await user.click(within(ask).getByRole("button", { name: "Keep it running" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Stop flow" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(stops()).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Stop flow" }));
    ask = screen.getByRole("alertdialog", { name: "Stop this flow?" });
    await user.click(within(ask).getByRole("button", { name: "Stop the flow" }));
    await waitFor(() => expect(stops()).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  });

  it("the menu's foot shows keel's version", async () => {
    render(<App />);
    expect(await screen.findAllByTitle("keel v2 version")).not.toHaveLength(0);
  });
});
