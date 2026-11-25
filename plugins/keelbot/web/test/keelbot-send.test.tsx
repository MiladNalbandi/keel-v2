// #235 KeelBot's chat sends at once: the message shows at Enter (before the api answers), "Sending…" lasts only until
// the api took it, a failure keeps the message with the reason and a Retry; and the chat follows new text unless the
// person scrolled up, where a "New messages ↓" button shows.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { db, FakeEventSource, server } from "../../../../web/src/test/setup";
import { atBottom, outgoingShown } from "../model";

const panel = () => screen.findByRole("complementary", { name: "KeelBot" });
const turns = () => db.calls.filter((c) => c.method === "POST" && /\/helper\/sessions\/[^/]+\/turn$/.test(c.path));

describe("KeelBot send: the pure helpers", () => {
  it("keeps an outgoing message until the chat read back has it, and knows the end of the chat", () => {
    const out = [{ id: 1, text: "a", state: "sending" as const }, { id: 2, text: "b", state: "sent" as const, n: 3 }];
    expect(outgoingShown(out, [{ n: 1 }, { n: 2 }]).map((o) => o.id)).toEqual([1, 2]);
    expect(outgoingShown(out, [{ n: 3 }]).map((o) => o.id)).toEqual([1]);
    expect(atBottom({ scrollHeight: 1000, scrollTop: 600, clientHeight: 380 })).toBe(true);
    expect(atBottom({ scrollHeight: 1000, scrollTop: 100, clientHeight: 380 })).toBe(false);
  });
});

describe("KeelBot send in the panel", () => {
  it("shows the message at Enter, empties the input at once, and Sending… ends when the api took it", async () => {
    let release!: () => void;
    const held = new Promise<void>((r) => { release = r; });
    server.use(http.post("/api/projects/:pid/helper/sessions/:sid/turn", async () => {
      await held;            // the api is slow: the person still sees the message and an empty input
      return undefined;      // passthrough to the default handler
    }));
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "KeelBot" }));
    const p = await panel();
    const box = within(p).getByRole("textbox", { name: "Ask KeelBot" });
    await user.type(box, "Where is a score saved?{Enter}");
    const out = await within(p).findByTestId("hp-outgoing");
    expect(out).toHaveTextContent("Where is a score saved?");
    expect(within(out).getByRole("status")).toHaveTextContent("Sending…");
    expect(box).toHaveValue("");
    act(() => release());
    await waitFor(() => expect(turns()).toHaveLength(1));
    await waitFor(() => expect(within(p).queryByText("Sending…")).toBeNull());
    await waitFor(() => expect(within(p).getAllByText("Where is a score saved?", { selector: ".hp-text" })).toHaveLength(1));
  });

  it("a failed send keeps the message with the reason, and Retry sends it again", async () => {
    let fail = true;
    server.use(http.post("/api/projects/:pid/helper/sessions/:sid/turn", async () => {
      if (!fail) return undefined;
      await delay(10);
      return HttpResponse.json({ error: "The engine is not running", hint: "Start keel again." }, { status: 503 });
    }));
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "KeelBot" }));
    const p = await panel();
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "Hello?{Enter}");
    const alert = await within(p).findByRole("alert");
    expect(alert).toHaveTextContent("Not sent: The engine is not running");
    fail = false;
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(within(p).queryByRole("alert")).toBeNull());
    await waitFor(() => expect(turns()).toHaveLength(1));   // the failed one never reached the (logging) test api
  });

  it("follows KeelBot's live text at the bottom; scrolled up, it stays and offers New messages ↓", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "KeelBot" }));
    const p = await panel();
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "Explain it{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(1));
    const log = within(p).getByRole("log", { name: "Conversation" });
    // jsdom has no layout: a tall chat, scrolled to its end
    let top = 0;
    Object.defineProperty(log, "scrollHeight", { configurable: true, get: () => 2000 });
    Object.defineProperty(log, "clientHeight", { configurable: true, get: () => 400 });
    Object.defineProperty(log, "scrollTop", { configurable: true, get: () => top, set: (v: number) => { top = v; } });
    const sess = db.helper.sessions[0];
    const call = `call-${sess.id}-1`;   // the test api: the call of the chat's first message
    const delta = (text: string) => act(() => {
      FakeEventSource.emit("helper.delta", { type: "helper.delta", thread_id: sess.id, project_id: "ludus-engine", call_id: call,
        step: "helper", at: new Date().toISOString(), data: { text } });
    });
    delta("First words");
    await waitFor(() => expect(top).toBe(2000));
    // the person scrolls up to read
    top = 100;
    act(() => { log.dispatchEvent(new Event("scroll")); });
    delta("First words and more words");
    const btn = await within(p).findByRole("button", { name: "New messages: scroll down" });
    expect(top).toBe(100);
    log.scrollTo = ((o: ScrollToOptions) => { top = o.top ?? top; }) as never;
    await user.click(btn);
    expect(top).toBe(2000);
    expect(within(p).queryByRole("button", { name: "New messages: scroll down" })).toBeNull();
  });
});
