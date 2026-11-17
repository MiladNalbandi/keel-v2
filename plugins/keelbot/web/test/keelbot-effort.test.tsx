// 0.18.9 KeelBot's thinking effort: the answer's footer shows the effort it ran with, and the model picker saves one
// on the chat.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { db, FakeEventSource } from "../../../../web/src/test/setup";
import { answerModel } from "../model";

const panel = () => screen.findByRole("complementary", { name: "KeelBot" });
const turns = () => db.calls.filter((c) => c.method === "POST" && /\/helper\/sessions\/[^/]+\/turn$/.test(c.path));

/** The engine answers with `effort` on the message, as the api stores it. */
function answer(text: string, effort?: string, extra: Record<string, unknown> = {}) {
  const sess = db.helper.sessions[0];
  const n = (sess.messages?.length ?? 0) + 1;
  const call = `call-${sess.id}-${n - 1}`;
  sess.messages = [...(sess.messages ?? []), { n, role: "helper", text, call_id: call,
    data: { status: "done", provider: "claude", model: "sonnet", ...(effort ? { effort } : {}), tokens_in: 1200, tokens_out: 300, cost_usd: 0, ms: 4200, ...extra },
    at: new Date().toISOString() }];
  sess.status = "idle";
  sess.busy = false;
  sess.turns += 1;
  act(() => {
    FakeEventSource.emit("helper.finished", { type: "helper.finished", thread_id: sess.id, project_id: "ludus-engine", call_id: call, step: "helper",
      at: new Date().toISOString(), data: { status: "done", tokens_in: 1200, tokens_out: 300 } });
  });
}

describe("KeelBot: effort", () => {
  it("answerModel adds the effort only when the call had one", () => {
    expect(answerModel({ model: "sonnet", effort: "high" })).toBe("sonnet · high");
    expect(answerModel({ model: "sonnet" })).toBe("sonnet");
  });

  it("the answer's footer shows the effort", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    const p = await panel();
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "Why?{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(1));
    answer("Because.", "high");
    await within(p).findByText("Because.");
    expect(p.querySelector(".hp-meta")).toHaveTextContent("Claude sonnet · high · 1.5k tokens (1.2k in · 300 out) · 4.2 s");
  });

  it("the model picker saves the effort on the chat", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    const p = await panel();
    await user.click(within(p).getByRole("button", { name: "Model" }));
    const effort = await within(p).findByLabelText("Effort");
    await user.selectOptions(effort, "high");
    await waitFor(() => {
      const patch = db.calls.find((c) => c.method === "PATCH" && /\/helper\/sessions\/[^/]+$/.test(c.path));
      expect((patch?.body as { model?: { effort?: string } })?.model).toMatchObject({ provider: "claude", model: "sonnet", effort: "high" });
    });
    await waitFor(() => expect(within(p).getByRole("button", { name: "Model" })).toHaveTextContent("sonnet · high"));
  });

  it("a Copilot answer shows what came from the cache and its AI credits", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    const p = await panel();
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "Why?{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(1));
    answer("Because.", undefined, { provider: "copilot", model: "auto", tokens_in: 400, tokens_out: 50, tokens_cached: 20000, ai_credits: 0.42 });
    await within(p).findByText("Because.");
    expect(p.querySelector(".hp-meta")).toHaveTextContent("400 new · 20.0k cached · 50 out");
    expect(p.querySelector(".hp-meta")).toHaveTextContent("about 0.42 AI credits");
  });
});
