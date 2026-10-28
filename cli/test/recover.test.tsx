// keel 0.18.18 KeelBot when something fails, in keel TUI: its permission card while it works (the warning, what "in
// this chat" keeps, a deny with the person's reason, Esc says no, a card answered elsewhere goes), and its question
// card with the ways forward, whose answer goes back to it as the next message.
import React from "react";
import { describe, expect, it } from "vitest";
import { render } from "ink-testing-library";
import { App, type Probe } from "../src/ui/App";
import { KeelCore } from "../src/core/keel";
import { makeTheme } from "../src/core/theme";
import { stripAnsi } from "../src/script";
import { stubKeel, tick, waitFor } from "./stub";

const none = makeTheme({ NO_COLOR: "1" }, 0);

function setup() {
  const stub = stubKeel();
  const core = new KeelCore({
    url: "http://keel.test",
    fetch: stub.fetch,
    noLive: true,
  });
  const probe: Probe = {
    started: false,
    busy: false,
    pick: false,
    ask: false,
    chatBusy: false,
    answers: 0,
  };
  const r = render(<App core={core} probe={probe} width={120} theme={none} />);
  const frame = () => stripAnsi(r.lastFrame() ?? "");
  const key = async (s: string, n = 1) => {
    for (let i = 0; i < n; i++) {
      r.stdin.write(s);
      await tick(30);
    }
  };
  return { stub, core, probe, r, frame, key };
}

const ev = (type: string, data: object) => ({
  event: type,
  data: JSON.stringify({
    type,
    thread_id: "s1",
    project_id: "demo",
    at: "x",
    step: "helper",
    data,
  }),
});
const decided = (t: ReturnType<typeof setup>, id: string) =>
  t.stub.calls.find((c) => c.path === `/api/approvals/${id}/decide`)?.body;

const CARD = `The checkout failed: your folder has uncommitted changes in README.md that main would overwrite.

\`\`\`keel-questions
[{"id": "changes", "question": "What should I do with your uncommitted changes?", "why": "git cannot check out main over them",
  "options": [{"label": "Commit them first on this branch"},
              {"label": "Stash them, check out main, pull, then stash pop", "description": "nothing is lost", "recommended": true},
              {"label": "Discard them", "description": "they are lost"},
              {"label": "Leave it as it is"}]}]
\`\`\``;

async function busyChat(t: ReturnType<typeof setup>) {
  await waitFor(() => t.probe.started);
  await t.key("checkout the main and update");
  await t.key("\r");
  await waitFor(() => t.probe.chatBusy);
}

describe("KeelBot's permission card while it works", () => {
  it("shows the warning and the command's family, and keeps it for the chat", async () => {
    const t = setup();
    await busyChat(t);
    t.core.onMessage(
      ev("helper.permission", {
        id: "p1",
        command: "git checkout main",
        title: "checkout",
        always: "git checkout *",
        warning:
          'A flow works in this folder ("Player ranks" waiting): this changes the files it works on.',
      }),
    );
    await waitFor(() => t.probe.pick);
    expect(t.frame()).toContain(
      "KeelBot asks to run: git checkout main? ⚠ A flow works in this folder",
    );
    expect(t.frame()).toContain("Allow git checkout * in this chat");
    expect(t.frame()).toContain("Deny, and say why");
    await t.key("\x1b[B");
    await t.key("\r");
    await waitFor(() => !!decided(t, "p1"));
    expect(decided(t, "p1")).toEqual({ decision: "always", why: "" });
    await waitFor(() =>
      t
        .frame()
        .includes("allowed in this chat: git checkout main (git checkout *)"),
    );
    t.r.unmount();
  });

  it("a command that throws work away has no 'in this chat'; a deny carries the person's reason", async () => {
    const t = setup();
    await busyChat(t);
    t.core.onMessage(
      ev("helper.permission", {
        id: "p2",
        command: "git reset --hard",
        title: "reset",
        always: "",
        warning:
          "git reset --hard throws away every uncommitted change in the folder; it cannot be undone.",
      }),
    );
    await waitFor(() => t.probe.pick);
    expect(t.frame()).toContain(
      "⚠ git reset --hard throws away every uncommitted change",
    );
    expect(t.frame()).not.toContain("in this chat");
    await t.key("\x1b[B", 2); // Allow once, Deny, Deny and say why
    await t.key("\r");
    await waitFor(() => t.probe.ask);
    expect(t.frame()).toContain("Why not git reset --hard? (KeelBot reads it)");
    await t.key("my notes are in there");
    await t.key("\r");
    await waitFor(() => !!decided(t, "p2"));
    expect(decided(t, "p2")).toEqual({
      decision: "deny",
      why: "my notes are in there",
    });
    await waitFor(() =>
      t.frame().includes("refused: git reset --hard (my notes are in there)"),
    );
    t.r.unmount();
  });

  it("Esc says no (the command does not wait ten minutes), and a card answered elsewhere goes", async () => {
    const t = setup();
    await busyChat(t);
    t.core.onMessage(
      ev("helper.permission", {
        id: "p3",
        command: "git stash",
        title: "stash",
        always: "git stash",
      }),
    );
    await waitFor(() => t.probe.pick);
    await t.key("\x1b");
    await waitFor(() => !!decided(t, "p3"));
    expect(decided(t, "p3")).toEqual({
      decision: "deny",
      why: "The person closed the question without an answer.",
    });
    t.core.onMessage(
      ev("helper.permission", {
        id: "p4",
        command: "git pull",
        title: "pull",
        always: "git pull",
      }),
    );
    await waitFor(() => t.frame().includes("KeelBot asks to run: git pull?"));
    t.core.onMessage(
      ev("helper.permission.answered", { id: "p4", decision: "once" }),
    );
    await waitFor(() => !t.frame().includes("KeelBot asks to run: git pull?"));
    expect(t.frame()).toContain("KeelBot's question ended");
    expect(decided(t, "p4")).toBeUndefined();
    t.r.unmount();
  });
});

describe("KeelBot's question card after a failure", () => {
  it("offers the ways forward, the recommended one first, an own answer too, and sends the pick back to KeelBot", async () => {
    const t = setup();
    await busyChat(t);
    t.stub.answer(CARD);
    t.core.onMessage(ev("helper.finished", { status: "done" }));
    await waitFor(() => t.probe.pick);
    const f = t.frame();
    expect(f).toContain(
      "The checkout failed: your folder has uncommitted changes in README.md",
    );
    expect(f).toContain("What should I do with your uncommitted changes?");
    // the recommended one first (KeelBot wrote it second), and a row for the person's own words
    expect(
      f.indexOf(
        "Stash them, check out main, pull, then stash pop (recommended)",
      ),
    ).toBeLessThan(f.indexOf("Commit them first"));
    expect(f).toContain("Other… (my own words)");
    await t.key("\r"); // the recommended one: one question, so it goes at once (0.18.61, no "Send these answers?")
    await waitFor(
      () =>
        t.stub.calls.filter(
          (c) => c.path === "/api/projects/demo/helper/sessions/s1/turn",
        ).length === 2,
    );
    const sent = t.stub.calls.filter(
      (c) => c.path === "/api/projects/demo/helper/sessions/s1/turn",
    )[1].body as { text: string };
    expect(sent.text).toBe(
      "Answers to your questions:\n1. What should I do with your uncommitted changes?\n" +
        "   → Stash them, check out main, pull, then stash pop",
    );
    t.r.unmount();
  });
});

describe("0.18.63 KeelBot's \"Switch to Edit\" option", () => {
  it("switches the chat to Edit before its answer goes, so the answer already runs in Edit", async () => {
    const t = setup();
    await busyChat(t);
    t.stub.answer(`Ask mode is read only, so I cannot change it here.

\`\`\`keel-questions
[{"id": "next", "question": "How do we go on?",
  "options": [{"label": "Switch to Edit and fix it", "mode": "edit", "recommended": true}, {"label": "Leave it"}]}]
\`\`\``);
    t.core.onMessage(ev("helper.finished", { status: "done" }));
    await waitFor(() => t.probe.pick);
    await t.key("\r");
    const turns = () => t.stub.calls.filter((c) => c.path === "/api/projects/demo/helper/sessions/s1/turn");
    await waitFor(() => turns().length === 2);
    const patch = t.stub.calls.findIndex((c) => c.method === "PATCH" && c.path === "/api/projects/demo/helper/sessions/s1");
    expect(t.stub.calls[patch]?.body).toEqual({ mode: "edit" });
    expect(patch).toBeLessThan(t.stub.calls.indexOf(turns()[1]));
    expect((turns()[1].body as { text: string }).text).toBe("Answers to your questions:\n1. How do we go on?\n   → Switch to Edit and fix it");
    expect(t.frame()).toContain("switched to Edit mode");
    t.r.unmount();
  });
});
