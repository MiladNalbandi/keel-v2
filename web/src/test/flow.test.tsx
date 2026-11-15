import { render, screen, waitFor, within } from "@testing-library/react";
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
    expect(await screen.findByText(/running/, { selector: ".running-card .pill" })).toBeInTheDocument();
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

  it("v0.17.5 a stuck criterion shows its real choices (retry / skip / edit / stop), not only approve", async () => {
    const user = userEvent.setup();
    db.flows["ludus-engine"].thread!.waiting = { step: "verify_green", kind: "fix", title: "AC-002 is stuck at verify_green",
      detail: "failed 3 times", options: ["approve", "reject"], choices: ["retry", "skip", "edit", "stop"] };
    render(<App />);
    expect(await screen.findByRole("button", { name: "edit" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "skip" }));
    await waitFor(() => expect(db.calls.find((c) => c.path.endsWith("/resume"))?.body).toEqual({ decision: "approve", payload: { choice: "skip" } }));
  });

  it("#116 a step that crashed answers with Retry the step / Stop the flow, not Approve / Send back", async () => {
    const user = userEvent.setup();
    db.flows["ludus-engine"].thread!.waiting = { step: "commit", kind: "crash", title: "commit failed",
      detail: "git was locked by a lock file.", options: ["approve", "reject"], choices: ["retry", "stop"] };
    render(<App />);
    expect(await screen.findByRole("button", { name: "retry" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Approve/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop the flow" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "retry" }));
    await waitFor(() => expect(db.calls.find((c) => c.path.endsWith("/resume"))?.body).toEqual({ decision: "approve", payload: { choice: "retry" } }));
  });

  it("0.18.13 with choice_info: one card per choice with its words, the default marked, a reason only where it is needed", async () => {
    const user = userEvent.setup();
    db.flows["ludus-engine"].thread!.waiting = { step: "verify_green", kind: "fix", title: "AC-002 is stuck at verify_green",
      detail: "failed 3 times", options: ["approve", "reject"], choices: ["retry", "skip", "stop"], choice_info: [
        { name: "retry", label: "Try again", description: "one more round, with your note", why: "optional", default: true },
        { name: "skip", label: "Skip it for now", description: "move AC-002 to the end of the list", why: "required", default: false },
        { name: "stop", label: "Stop the flow", description: "the commits so far stay", why: "none", default: false }] };
    render(<App />);
    const gate = await screen.findByRole("region", { name: "Gate waits for you" });
    const cards = within(gate).getAllByRole("radio");
    expect(cards.map((c) => c.textContent)).toEqual([
      "1Try againdefaultone more round, with your note", "2Skip it for nowneeds a reasonmove AC-002 to the end of the list",
      "3Stop the flowthe commits so far stay"]);
    // the raw names and the old reason box are gone
    expect(within(gate).queryByRole("button", { name: "skip" })).not.toBeInTheDocument();
    expect(within(gate).queryByRole("button", { name: "Reject fix" })).not.toBeInTheDocument();
    await user.click(cards[1]);
    await user.click(within(gate).getByRole("button", { name: "Send: Skip it for now" }));
    expect(within(gate).getByRole("alert")).toHaveTextContent("needs a reason");
    expect(db.calls.some((c) => c.path.endsWith("/resume"))).toBe(false);
    await user.type(within(gate).getByLabelText("Why (needed for “Skip it for now”)"), "blocked by AC-003");
    await user.click(within(gate).getByRole("button", { name: "Send: Skip it for now" }));
    await waitFor(() => expect(db.calls.find((c) => c.path.endsWith("/resume"))?.body).toEqual(
      { decision: "approve", why: "blocked by AC-003", payload: { choice: "skip" } }));
  });

  it("0.18.13 without choice_info a gate's exit named reject still asks for the reason first", async () => {
    const user = userEvent.setup();
    db.flows["ludus-engine"].thread!.waiting = { step: "spec_gate", kind: "gate", title: "spec", detail: "", options: ["approve"],
      choices: ["approve", "reject"] };
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "reject" }));
    expect(db.calls.some((c) => c.path.endsWith("/resume"))).toBe(false);
    await user.type(screen.getByLabelText("Why (needed to send back)"), "the error case is missing");
    await user.click(screen.getByRole("button", { name: "reject" }));
    await waitFor(() => expect(db.calls.find((c) => c.path.endsWith("/resume"))?.body).toEqual(
      { decision: "approve", why: "the error case is missing", payload: { choice: "reject" } }));
  });

  it("0.18.13 init's three questions are a clarify form: the recommended first, every recommended page ticked", async () => {
    const user = userEvent.setup();
    db.flows["ludus-engine"].thread!.waiting = { step: "questions", kind: "clarify", title: "Three questions before setup", detail: "",
      options: ["approve"], labels: { approve: "Send my answers" }, questions: [
        { id: "run", question: "How does the project run?", options: [{ label: "locally" }, { label: "docker compose", recommended: true }] },
        { id: "services", question: "Which services does it need?", options: [{ label: "postgres", recommended: true }, { label: "none" }] },
        { id: "knowledge", question: "Which knowledge pages?", multiple: true, options: [
          { label: "architecture", recommended: true }, { label: "domain", recommended: true }, { label: "data" }] }] };
    render(<App />);
    const form = await screen.findByTestId("clarify-form");
    expect(within(within(form).getByRole("radiogroup", { name: "How does the project run?" })).getAllByRole("radio")[0]).toHaveTextContent("docker compose");
    expect(within(form).getAllByRole("checkbox").map((c) => c.getAttribute("aria-checked"))).toEqual(["true", "true", "false"]);
    expect(within(form).getByRole("status")).toHaveTextContent("1 of 3 answered");
    await user.click(within(form).getByRole("radio", { name: /none/ }));
    await user.click(screen.getByRole("button", { name: "Send my answers" }));
    await waitFor(() => expect(db.calls.find((c) => c.path.endsWith("/resume"))?.body).toEqual(
      { decision: "approve", payload: { answers: { services: "none", knowledge: ["architecture", "domain"] } } }));
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
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("tab", { name: "Checkpoints" }));
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

describe("Flow gate: questions next to choices (0.18.13)", () => {
  it("a gate that asks a question next to its choices sends the choice and the answers together", async () => {
    const user = userEvent.setup();
    const t = db.flows["ludus-engine"].thread!;
    t.waiting = {
      step: "s8", kind: "gate", title: "lenses for the hunt", detail: "Pick the lenses to run.", options: ["approve"],
      choices: ["confirm", "stop"],
      choice_info: [
        { name: "confirm", label: "Run these lenses", description: "the hunters run the ticked lenses", why: "optional", default: true },
        { name: "stop", label: "Stop the hunt", description: "nothing runs", why: "none", default: false },
      ],
      questions: [{ id: "lenses", question: "Which lenses?", why: "", multiple: true,
        options: [{ label: "security", description: "", recommended: true }, { label: "concurrency", description: "", recommended: false }] }],
    };
    render(<App />);
    const card = await screen.findByRole("region", { name: "Gate waits for you" });
    expect(within(card).getByText("Which lenses?")).toBeInTheDocument();
    await user.click(within(card).getByRole("checkbox", { name: /concurrency/ }));
    await user.click(within(card).getByRole("button", { name: /^Send/ }));
    await waitFor(() => {
      const body = db.calls.find((c) => c.method === "POST" && c.path === "/api/threads/th_7f3a/resume")?.body as
        { decision: string; payload: { choice: string; answers: Record<string, string[]> } } | undefined;
      expect(body?.payload.choice).toBe("confirm");
      expect(body?.payload.answers.lenses).toEqual(["security", "concurrency"]);
    });
  });
});
