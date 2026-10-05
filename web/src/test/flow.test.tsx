import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { db } from "./setup";

describe("Flow gate", () => {
  it("Approve resumes the thread with decision approve", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Approve AC-002" }));
    await waitFor(() => expect(db.calls.some((c) => c.path === "/api/threads/th_7f3a/resume")).toBe(true));
    const call = db.calls.find((c) => c.path === "/api/threads/th_7f3a/resume")!;
    expect(call.method).toBe("POST");
    expect(call.body).toEqual({ decision: "approve" });
    // after the refetch the gate card is gone and the agent runs
    expect(await screen.findByText(/running/, { selector: ".pill" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Gate waits for you" })).not.toBeInTheDocument();
  });

  it("a gate with named exits shows one button per choice and resumes with it", async () => {
    const user = userEvent.setup();
    db.flows["ludus-engine"].thread!.waiting = { step: "decide", kind: "gate", title: "diagnosis", detail: "Ranked: the race wins.",
      options: ["approve", "reject"], choices: ["fix", "feature", "unresolved"] };
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "feature" }));
    await waitFor(() => expect(db.calls.find((c) => c.path.endsWith("/resume"))?.body).toEqual({ decision: "approve", payload: { choice: "feature" } }));
  });

  it("Send back needs a reason, then sends it", async () => {
    const user = userEvent.setup();
    render(<App />);
    const back = await screen.findByRole("button", { name: "Send back to red" });
    await user.click(back);
    expect(db.calls.some((c) => c.path.endsWith("/resume"))).toBe(false);
    await user.type(screen.getByLabelText("Why (needed to send back)"), "also check the message text");
    await user.click(back);
    await waitFor(() => expect(db.calls.find((c) => c.path.endsWith("/resume"))?.body).toEqual({ decision: "reject", why: "also check the message text" }));
  });

  it("shows checkpoints, ACs and the budget meter", async () => {
    render(<App />);
    expect(await screen.findByText("#31")).toBeInTheDocument();
    expect(screen.getByText("[API] Refuse a negative score with 400")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Tokens used of the cap" })).toHaveAttribute("aria-valuenow", "182000");
    expect(await screen.findByText("estimate 410k")).toBeInTheDocument();
  });

  it("shows the empty state with Start a flow when nothing runs", async () => {
    const user = userEvent.setup();
    localStorage.setItem("keel2.project", "yegi");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Start a flow" }));
    expect(await screen.findByRole("dialog", { name: "Start a flow" })).toBeInTheDocument();
    expect(await screen.findByText(/tokens expected for about 3 ACs/)).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
