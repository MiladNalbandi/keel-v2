// v0.6.0 keel's Helper in the Repo page: open it (button, ⌘I), ask, watch the steps live, follow its file:line links,
// commands with /, mentions with @, the selected lines, stop, the model — and the panel's pure helpers.

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { fileLink, replaceTyping, starters, typingAt, usageText } from "../components/helper/model";
import { db, FakeEventSource } from "./setup";

const panel = () => screen.findByRole("complementary", { name: "Helper" });
const turns = () => db.calls.filter((c) => c.method === "POST" && /\/helper\/sessions\/[^/]+\/turn$/.test(c.path));

/** The engine answers: the session gets the answer, then the turn's step and its end arrive as live events. */
function answer(text: string, steps: { kind: string; text: string; path?: string }[] = []) {
  const sess = db.helper.sessions[0];
  const n = (sess.messages?.length ?? 0) + 1;
  const call = `call-${sess.id}-${n - 1}`;
  act(() => {
    steps.forEach((s, i) => FakeEventSource.emit("helper.step", {
      type: "helper.step", thread_id: sess.id, project_id: "ludus-engine", call_id: call, step: "helper", at: new Date().toISOString(),
      data: { n: i + 1, ...s },
    }));
  });
  sess.messages = [...(sess.messages ?? []), { n, role: "helper", text, call_id: call,
    data: { status: "done", provider: "claude", model: "sonnet", tokens_in: 1200, tokens_out: 300, cost_usd: 0, ms: 4200 }, at: new Date().toISOString() }];
  sess.status = "idle";
  sess.busy = false;
  sess.tokens_in += 1200;
  sess.tokens_out += 300;
  sess.turns += 1;
  act(() => {
    FakeEventSource.emit("helper.finished", { type: "helper.finished", thread_id: sess.id, project_id: "ludus-engine", call_id: call, step: "helper",
      at: new Date().toISOString(), data: { status: "done", tokens_in: 1200, tokens_out: 300 } });
  });
}

describe("Helper: the panel's helpers", () => {
  it("reads file:line links, what is being typed, and the words for usage", () => {
    expect(fileLink("api/ScoreController.kt:9")).toEqual({ path: "api/ScoreController.kt", line: 9 });
    expect(fileLink("./web/src/App.tsx:12-14")).toEqual({ path: "web/src/App.tsx", line: 12 });
    expect(fileLink("https://x.io/a.js:3")).toBeNull();
    expect(fileLink("/etc/passwd:1")).toBeNull();
    expect(fileLink("AC-2")).toBeNull();
    expect(typingAt("/ex", 3)).toEqual({ kind: "command", query: "ex", start: 0 });
    expect(typingAt("why @Sco", 8)).toEqual({ kind: "mention", query: "Sco", start: 4 });
    expect(typingAt("mail me at a@b", 14)).toBeNull();                                      // not after a space
    expect(typingAt("/explain it", 11)).toBeNull();
    expect(replaceTyping("why @Sco now", { kind: "mention", query: "Sco", start: 4 }, 8, "ScoreService"))
      .toEqual({ text: "why @ScoreService  now", caret: 18 });
    expect(usageText(1500, 0)).toBe("1.5k tokens");
    expect(usageText(2_400_000, 1.234)).toBe("2.4M tokens · $1.23");
    expect(starters({ flowWaits: true, openFile: "a.kt" })[0]).toBe("/gate");
  });
});

describe("Helper in the Repo page", () => {
  it("opens from the activity bar, says it is read-only and offers questions to start with", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Helper" }));
    const p = await panel();
    expect(within(p).getByText("Ask · read only")).toBeInTheDocument();
    expect(within(p).getByText(/It changes nothing in Ask mode/)).toBeInTheDocument();
    expect(within(p).getByRole("button", { name: "Where does this project start, and how is it organised?" })).toBeInTheDocument();
    expect(localStorage.getItem("keel2.repo.helper")).toBe("true");
    await user.click(within(p).getByRole("button", { name: "Close the Helper" }));
    expect(screen.queryByRole("complementary", { name: "Helper" })).toBeNull();
  });

  it("asks with the open file, shows the steps live, then the answer whose file:line opens the editor there", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo/api/ScoreController.kt:3";
    render(<App />);
    await screen.findByRole("region", { name: "Code of api/ScoreController.kt" });
    fireEvent.keyDown(window, { key: "i", metaKey: true });
    const p = await panel();
    await user.type(within(p).getByRole("textbox", { name: "Ask the Helper" }), "Where is a score saved?{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(1));
    expect(db.calls.some((c) => c.method === "POST" && c.path.endsWith("/helper/sessions"))).toBe(true);
    expect(turns()[0].body).toMatchObject({ text: "Where is a score saved?", open_file: "api/ScoreController.kt" });
    expect(within(p).getByText("Where is a score saved?")).toBeInTheDocument();

    answer("It is saved in `api/ScoreController.kt:9`, after the check.", [{ kind: "read", text: "fun save()", path: "api/ScoreController.kt" }]);
    const link = await within(p).findByRole("link", { name: "api/ScoreController.kt:9" });
    expect(p.querySelector(".hp-meta")).toHaveTextContent("Claude sonnet · 1.5k tokens · 4.2 s");
    await user.click(link);
    await waitFor(() => expect(document.querySelector('.cv-row[data-line="9"]')).toHaveClass("tgt"));
  });

  it("a missed helper.finished (the tab was hidden) never leaves the panel working: the next live tick reads the answer", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    const p = await panel();
    await user.type(within(p).getByRole("textbox", { name: "Ask the Helper" }), "Quick one{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(1));
    expect(await within(p).findByRole("button", { name: "Stop" })).toBeInTheDocument();
    // the engine answered, but its helper.finished event never reached this page
    const sess = db.helper.sessions[0];
    sess.messages = [...(sess.messages ?? []), { n: 2, role: "helper", text: "Done while you were away.", call_id: "c-away", data: { status: "done" }, at: "" }];
    sess.status = "idle";
    sess.busy = false;
    act(() => FakeEventSource.emit("project.changed", { id: "ludus-engine" }));      // any live tick (or the stream coming back)
    expect(await within(p).findByText("Done while you were away.")).toBeInTheDocument();
    await waitFor(() => expect(within(p).queryByRole("button", { name: "Stop" })).toBeNull());
  });

  it("offers commands after / and files, symbols and criteria after @, and sends the mentions", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    const box = within(await panel()).getByRole("textbox", { name: "Ask the Helper" });
    await user.type(box, "/de");
    const cmds = await screen.findByRole("listbox", { name: "Commands" });
    expect(within(cmds).getByRole("option", { name: /\/deploy-notes.*Our release notes \(this project\)/ })).toBeInTheDocument();
    await user.clear(box);
    await user.type(box, "why @Score");
    const opts = await screen.findByRole("listbox", { name: "Mentions" });
    await waitFor(() => expect(within(opts).getByRole("option", { name: /@Score\b.*class · domain\/Score.java:3/ })).toBeInTheDocument());
    expect(within(opts).getAllByRole("option").some((o) => o.textContent?.includes("ScoreController.kt"))).toBe(true);
    await user.keyboard("{ArrowDown}");
    await user.click(within(opts).getByRole("option", { name: /@Score\b.*class/ }));
    expect(box).toHaveValue("why @Score ");
    await user.type(box, "used?{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(1));
    expect(turns()[0].body).toMatchObject({ text: "why @Score used?",
      mentions: [{ kind: "symbol", value: "Score", file: "domain/Score.java", line: 3 }] });
  });

  it("takes the selected lines (Ask), and Stop stops a running answer", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo/api/ScoreController.kt:3";
    render(<App />);
    await screen.findByRole("region", { name: "Code of api/ScoreController.kt" });
    const r3 = document.querySelector('.cv-row[data-line="3"] .cv-code')!, r5 = document.querySelector('.cv-row[data-line="5"] .cv-code')!;
    const range = document.createRange();
    range.setStart(r3.firstChild ?? r3, 0);
    range.setEnd(r5.firstChild ?? r5, 1);
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    await user.click(screen.getByRole("button", { name: "Ask the Helper" }));
    const p = await panel();
    expect(within(p).getByText("api/ScoreController.kt:3-5")).toBeInTheDocument();
    // the first Enter picks the command in the list, the second sends it
    await user.type(within(p).getByRole("textbox", { name: "Ask the Helper" }), "/explain{Enter}{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(1));
    expect(turns()[0].body).toMatchObject({ text: "/explain", selection: { path: "api/ScoreController.kt", from: 3, to: 5 } });
    expect(within(p).queryByText("api/ScoreController.kt:3-5", { selector: ".hp-compose *" })).toBeNull();   // sent, so cleared
    await user.click(await within(p).findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(db.calls.some((c) => c.method === "POST" && c.path.endsWith("/stop"))).toBe(true));
  });

  it("changes the model of the chat and keeps chats to switch between", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    const p = await panel();
    await user.type(within(p).getByRole("textbox", { name: "Ask the Helper" }), "First question{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(1));
    answer("First answer.");
    await within(p).findByText("First answer.");
    expect(within(p).getByRole("combobox", { name: "Chat" })).toHaveDisplayValue("First question");
    await user.click(within(p).getByRole("button", { name: "New chat" }));
    expect(within(p).getByRole("combobox", { name: "Chat" })).toHaveDisplayValue("New chat");
    expect(within(p).queryByText("First answer.")).toBeNull();
    await user.selectOptions(within(p).getByRole("combobox", { name: "Chat" }), "First question");
    expect(await within(p).findByText("First answer.")).toBeInTheDocument();
    await user.click(within(p).getByRole("button", { name: "Delete this chat" }));
    await waitFor(() => expect(db.helper.sessions).toHaveLength(0));
  });
});
