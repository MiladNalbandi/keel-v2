// v0.6.0 KeelBot in the Code page: open it (button, ⌘I), ask, watch the steps live, follow its file:line links,
// commands with /, mentions with @, the selected lines, stop, the model — and the panel's pure helpers. Inside keel's
// web as the full image has it (web/src/test/setup.ts runs every plugin's setup()); moved with the plugin.

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
// keel's own app and test harness: the panel runs inside keel's web, as keel loads it
import { App } from "../../../../web/src/App";
import { eventLine } from "../../../../web/src/components/events";
import { db, FakeEventSource } from "../../../../web/src/test/setup";
import { fileLink, replaceTyping, starters, typingAt, usageText } from "../model";

const panel = () => screen.findByRole("complementary", { name: "KeelBot" });
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
    const ev = (type: string, data: Record<string, unknown>) => eventLine({ type, data, thread_id: "t", project_id: "p", at: "" } as never).text;
    expect(ev("helper.commit", { message: "feat(AC-2): limit is 100 94baef0" })).toBe("KeelBot's change committed: feat(AC-2): limit is 100 94baef0");
    expect(ev("helper.permission", { command: "mkdir -p notes" })).toBe("KeelBot asks to run: mkdir -p notes");
    expect(ev("helper.permission.answered", { decision: "deny", why: "no" })).toBe(`KeelBot's command refused — "no"`);
  });
});

describe("Helper in the Code page", () => {
  it("opens from the activity bar, says it is read-only and offers questions to start with", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "KeelBot" }));
    const p = await panel();
    expect(within(p).getByText("Ask · read only")).toBeInTheDocument();
    expect(within(p).getByText(/It changes nothing in Ask mode/)).toBeInTheDocument();
    expect(within(p).getByRole("button", { name: "Where does this project start, and how is it organised?" })).toBeInTheDocument();
    expect(localStorage.getItem("keel2.repo.helper")).toBe("true");
    await user.click(within(p).getByRole("button", { name: "Close KeelBot" }));
    expect(screen.queryByRole("complementary", { name: "KeelBot" })).toBeNull();
  });

  it("asks with the open file, shows the steps live, then the answer whose file:line opens the editor there", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo/api/ScoreController.kt:3";
    render(<App />);
    await screen.findByRole("region", { name: "Code of api/ScoreController.kt" });
    fireEvent.keyDown(window, { key: "i", metaKey: true });
    const p = await panel();
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "Where is a score saved?{Enter}");
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
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "Quick one{Enter}");
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
    const box = within(await panel()).getByRole("textbox", { name: "Ask KeelBot" });
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
    await user.click(screen.getByRole("button", { name: "Ask KeelBot" }));
    const p = await panel();
    expect(within(p).getByText("api/ScoreController.kt:3-5")).toBeInTheDocument();
    // the first Enter picks the command in the list, the second sends it
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "/explain{Enter}{Enter}");
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
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "First question{Enter}");
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

describe("Helper Fix mode at a gate", () => {
  const diff = "--- a/api/ScoreController.kt\n+++ b/api/ScoreController.kt\n@@ -1,2 +1,2 @@\n-val max = 10\n+val max = 100\n";
  const startFix = async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    const p = await panel();
    const fixBtn = within(p).getByRole("button", { name: "Fix" });
    await waitFor(() => expect(fixBtn).toBeEnabled());                       // the fixture's flow waits at the AC gate
    await user.click(fixBtn);
    expect(within(p).getByText("Fix", { selector: ".hp-mode" })).toBeInTheDocument();
    expect(within(p).getByText(/Fixing at the gate/)).toHaveTextContent(/^Fixing at the gate Scores for players\. keel's rules/);
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "Raise the limit to 100{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(1));
    // the engine picked the phase of the work under review (the AC gate's own phase lets only notes change)
    expect(await within(p).findByText("Fix · green")).toBeInTheDocument();
    expect(within(p).getByText(/Fixing at the gate/)).toHaveTextContent(/^Fixing at the gate Scores for players · rules of the green phase\./);
    expect(db.calls.find((c) => c.method === "POST" && c.path.endsWith("/helper/sessions"))?.body).toMatchObject({ mode: "fix" });
    expect(db.helper.sessions[0]).toMatchObject({ mode: "fix", thread_id: "th_7f3a" });
    return { user, p, sid: db.helper.sessions[0].id };
  };

  it("asks before a command that changes something, lists the changed files, undoes one, and Done lets keel commit", async () => {
    const { user, p, sid } = await startFix();
    // KeelBot wants to run a command: a card in the panel
    db.helper.questions = [{ id: "q_1", session: sid, project: "ludus-engine", thread_id: "th_7f3a", kind: "bash", command: "./gradlew spotlessApply",
      title: "KeelBot asks to run a command", at: new Date().toISOString() }];
    act(() => FakeEventSource.emit("helper.permission", { type: "helper.permission", thread_id: sid, project_id: "ludus-engine", at: new Date().toISOString(), data: {} }));
    const card = await within(p).findByRole("group", { name: "KeelBot asks to run a command" });
    expect(within(card).getByText("./gradlew spotlessApply")).toBeInTheDocument();
    await user.type(within(card).getByRole("textbox", { name: "Why not (optional)" }), "fine");
    await user.click(within(card).getByRole("button", { name: "Always" }));
    await waitFor(() => expect(db.calls.some((c) => c.path.endsWith("/helper/permissions/q_1"))).toBe(true));
    expect(db.calls.find((c) => c.path.endsWith("/helper/permissions/q_1"))?.body).toEqual({ decision: "always", why: "fine" });
    await waitFor(() => expect(within(p).queryByRole("group", { name: "KeelBot asks to run a command" })).toBeNull());

    // the answer is in: the files it changed
    db.helper.changes[sid] = [
      { path: "api/ScoreController.kt", status: "modified", added: 1, removed: 1, diff },
      { path: "api/Limits.kt", status: "added", added: 3, removed: 0, diff: "--- /dev/null\n+++ b/api/Limits.kt\n@@ -0,0 +1,3 @@\n+a\n+b\n+c\n" },
    ];
    answer("I raised the limit in `api/ScoreController.kt:1`.");
    const box = await within(p).findByRole("region", { name: "What KeelBot changed" });
    expect(within(box).getByText("api/Limits.kt")).toBeInTheDocument();
    expect(within(box).getByLabelText("3 lines added")).toHaveTextContent("+3");
    await user.click(within(box).getByRole("button", { name: "Diff of api/ScoreController.kt" }));
    expect(within(box).getByRole("table", { name: "Changes in api/ScoreController.kt" })).toHaveTextContent("val max = 100");
    await user.click(within(box).getByRole("button", { name: "Undo api/Limits.kt" }));
    await waitFor(() => expect(within(box).queryByText("api/Limits.kt")).toBeNull());
    expect(db.calls.find((c) => c.path.endsWith("/undo"))?.body).toEqual({ path: "api/Limits.kt" });

    await user.type(within(box).getByRole("textbox", { name: "Commit message" }), "Limit is 100");
    await user.click(within(box).getByRole("button", { name: "Done: run the checks and commit" }));
    expect(await screen.findByText("keel committed 1 file (c0ffee1).")).toBeInTheDocument();
    expect(db.calls.find((c) => c.path.endsWith("/done"))?.body).toEqual({ message: "Limit is 100" });
    expect(await within(p).findByText("keel committed KeelBot's change: helper: fix")).toBeInTheDocument();
    await waitFor(() => expect(within(p).queryByRole("region", { name: "What KeelBot changed" })).toBeNull());
  });

  it("a missed helper.finished still shows the files the answer changed", async () => {
    const { p, sid } = await startFix();
    db.helper.changes[sid] = [{ path: "notes/scope.md", status: "added", added: 3, removed: 0, diff: "--- /dev/null\n+++ b/notes/scope.md\n@@ -0,0 +1 @@\n+a\n" }];
    const sess = db.helper.sessions[0];
    sess.messages = [...(sess.messages ?? []), { n: 2, role: "helper", text: "Wrote it.", call_id: "c-x", data: { status: "done" }, at: "" }];
    sess.status = "idle";
    sess.busy = false;
    act(() => FakeEventSource.emit("project.changed", { id: "ludus-engine" }));        // a tick, but no helper.finished
    const box = await within(p).findByRole("region", { name: "What KeelBot changed" });
    expect(within(box).getByText("notes/scope.md")).toBeInTheDocument();
  });

  it("a failed Done shows why, and hands the checks' output back to KeelBot", async () => {
    const { user, p, sid } = await startFix();
    db.helper.changes[sid] = [{ path: "api/ScoreController.kt", status: "modified", added: 1, removed: 1, diff }];
    db.helper.done = { ok: false, step: "checks", command: "./gradlew test", error: "The checks failed, so keel did not commit.",
      output: "ScoreTest > rejects 101 FAILED\nexpected 400 but was 200" };
    answer("Done.");
    const box = await within(p).findByRole("region", { name: "What KeelBot changed" });
    await user.click(within(box).getByRole("button", { name: "Done: run the checks and commit" }));
    const failed = await within(p).findByRole("alert");
    expect(failed).toHaveTextContent("Not committed. The checks failed, so keel did not commit.");
    expect(failed).toHaveTextContent("expected 400 but was 200");
    await user.click(within(failed).getByRole("button", { name: "Ask KeelBot to fix it" }));
    await waitFor(() => expect(turns()).toHaveLength(2));
    const asked = (turns()[1].body as { text: string }).text;
    expect(asked).toContain("The checks failed after your change (`./gradlew test`)");
    expect(asked).toContain("expected 400 but was 200");
    expect(within(p).queryByRole("alert")).toBeNull();
  });

  it("is off while no flow waits, and Ask stays read-only", async () => {
    db.flows["ludus-engine"].thread!.status = "running";
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    const p = await panel();
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });        // the flow is read
    expect(within(p).getByRole("button", { name: "Fix" })).toBeDisabled();
    expect(within(p).getByRole("button", { name: "Ask" })).toHaveAttribute("aria-pressed", "true");
    expect(within(p).getByText("Ask · read only")).toBeInTheDocument();
    // the flow reaches a gate; the page hears of it on the next live tick (also when the stream comes back)
    db.flows["ludus-engine"].thread!.status = "waiting";
    act(() => FakeEventSource.emit("project.changed", { id: "ludus-engine" }));
    await waitFor(() => expect(within(p).getByRole("button", { name: "Fix" })).toBeEnabled());
  });
});

describe("Helper side sessions (their own worktree and branch)", () => {
  it("works on its own branch, keeps a change there, and hands it over as a task and as a flow on the branch", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    db.flows["ludus-engine"].thread!.status = "done";                      // no flow waits: Side needs none
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    const p = await panel();
    await user.click(within(p).getByRole("button", { name: "Side" }));
    expect(within(p).getByText(/A side session works in its own copy of the project/)).toBeInTheDocument();
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "Try a price helper{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(1));
    expect(db.calls.find((c) => c.method === "POST" && c.path.endsWith("/helper/sessions"))?.body).toMatchObject({ mode: "side" });
    const sid = db.helper.sessions[0].id;
    expect(await within(p).findByText("Side · 1")).toBeInTheDocument();
    expect(within(p).getByText("keel/helper/1")).toBeInTheDocument();

    db.helper.changes[sid] = [{ path: "src/price.js", status: "added", added: 4, removed: 0, diff: "--- /dev/null\n+++ b/src/price.js\n@@ -0,0 +1 @@\n+x\n" }];
    answer("Added `src/price.js:1`.");
    const box = await within(p).findByRole("region", { name: "What KeelBot changed" });
    // the file is in the worktree, not the project folder: its name shows the diff instead of opening the editor
    await user.click(within(box).getByRole("button", { name: "src/price.js" }));
    expect(within(box).getByRole("table", { name: "Changes in src/price.js" })).toBeInTheDocument();
    expect(within(p).getByRole("button", { name: "Start a flow on the branch" })).toBeDisabled();       // nothing kept yet
    await user.click(within(box).getByRole("button", { name: "Keep: run the checks and commit on the branch" }));
    expect(await screen.findByText("Kept on keel/helper/1: keel committed 1 file (c0ffee1).")).toBeInTheDocument();
    expect(await within(p).findByText(/Kept on the branch: 1 commit/)).toBeInTheDocument();

    await user.click(within(p).getByRole("button", { name: "Make a task" }));
    expect(await screen.findByText("Task created: Try a price helper. It names the branch keel/helper/1.")).toBeInTheDocument();
    expect(db.calls.some((c) => c.path.endsWith(`/helper/sessions/${sid}/task`))).toBe(true);

    await user.click(within(p).getByRole("button", { name: "Start a flow on the branch" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("checks out keel/helper/1 in the project folder"));
    await waitFor(() => expect(db.calls.some((c) => c.path.endsWith(`/helper/sessions/${sid}/flow`))).toBe(true));
    await waitFor(() => expect(location.hash).toBe("#/flow"));
    confirm.mockRestore();
  });

  it("throws a side session away after a confirm", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    const p = await panel();
    await user.click(within(p).getByRole("button", { name: "Side" }));
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "Try it{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(1));
    answer("Tried.");
    await user.click(await within(p).findByRole("button", { name: "Throw away" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("keel/helper/1 are deleted"));
    await waitFor(() => expect(db.helper.sessions).toHaveLength(0));
    confirm.mockRestore();
  });
});

