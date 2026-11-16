// keel 0.29 a connected session: the person's own Claude Code or OpenCode does the harness flow for keel. Start a flow
// offers "Run with: Connected session" only while one is connected; the Flow page's feed shows the session (client,
// since, last seen), the work order as a turn and the session's todo list live; Help › Start here says how to connect.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import type { SessionsView, ThreadState, Workflow } from "../api";
import { App } from "../App";
import { SESSION_SENTENCE } from "../components/Session";
import { closeWelcome } from "../components/welcomeState";
import * as fx from "./fixtures";
import { db, server } from "./setup";

const now = new Date().toISOString();
const PID = "ludus-engine";
const connected: SessionsView = {
  project: PID, connected_s: 120, gone_s: 600, waiting: [],
  connected: { id: "s-1", project: PID, client: "claude-code", connected_since: now, last_seen: now, seen_ago_s: 4, connected: true },
  sessions: [],
};
const harness: Workflow = {
  id: "harness", name: "harness (keel)", keel_rules: true, version: 1, yaml: "name: harness (keel)\n",
  steps: [
    { id: "plan", kind: "agent", name: "the harness plans the change", agent: "harness-planner", model: "default", phase: "triage" },
    { id: "plan_gate", kind: "gate", name: "approve the plan", lock: true, phase: "gate" },
    { id: "build", kind: "agent", name: "the harness builds the plan", agent: "harness-builder", model: "default", phase: "ac" },
    { id: "check", kind: "code", name: "keel runs the project's tests", action: "harness_check", phase: "ac" },
    { id: "done_gate", kind: "gate", name: "look at the change", lock: true, phase: "gate" },
  ],
};

const starts = () => db.calls.filter((c) => c.method === "POST" && /^\/api\/projects\/[^/]+\/flows$/.test(c.path));

async function openStart() {
  localStorage.setItem("keel2.project", "yegi");      // a project with no flow: its page has Start a flow
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "Start a flow" }));
  return screen.findByRole("dialog", { name: "Start a flow" });
}

describe("Start a flow: Run with (0.29)", () => {
  beforeEach(() => {
    db.workflows.push(structuredClone(harness));
  });

  it("offers the connected session only when one is connected, with how to connect otherwise", async () => {
    const user = userEvent.setup();
    const d = await openStart();
    await user.selectOptions(await within(d).findByLabelText("Workflow"), "harness");
    const run = await within(d).findByLabelText("Run with");
    expect(run).toHaveValue("keel");
    expect(within(run).getByRole("option", { name: "keel's harness · API key" })).toBeInTheDocument();
    expect(within(run).getByRole("option", { name: "Connected session · none connected now" })).toBeDisabled();
    expect(within(d).getByTestId("sf-session-how")).toHaveTextContent(SESSION_SENTENCE);
    // a flow that the harness does not stand in for has no choice
    await user.selectOptions(within(d).getByLabelText("Workflow"), "init");
    expect(within(d).queryByLabelText("Run with")).toBeNull();
  });

  it("with a connected session it names the client and since when, and starts the harness flow with runner session", async () => {
    const user = userEvent.setup();
    db.sessions.yegi = { ...structuredClone(connected), project: "yegi" };
    const d = await openStart();
    await user.selectOptions(await within(d).findByLabelText("Workflow"), "harness");
    const run = await within(d).findByLabelText("Run with");
    await waitFor(() => expect(within(run).getByRole("option", { name: "Connected session · claude-code" })).toBeEnabled());
    await user.selectOptions(run, "session");
    expect(within(d).getByTestId("sf-session")).toHaveTextContent(/claude-code connected since \d\d:\d\d · last seen 4 s ago/);
    expect(within(d).getByTestId("sf-session-note")).toHaveTextContent("done by claude-code");
    await user.type(within(d).getByLabelText("What to build"), "Show each owner's name on the pet page");
    await user.click(within(d).getByRole("button", { name: "Start flow" }));
    await waitFor(() => expect(starts()).toHaveLength(1));
    const body = starts()[0].body as { workflow_id: string; options: Record<string, unknown> };
    expect(body.workflow_id).toBe("harness");
    expect(body.options).toEqual({ runner: "session", session: "s-1", session_client: "claude-code" });
  });

  it("a build flow run by the session starts as the harness flow with that flow's intent", async () => {
    const user = userEvent.setup();
    db.sessions.yegi = { ...structuredClone(connected), project: "yegi" };
    const d = await openStart();
    const run = await within(d).findByLabelText("Run with");          // feature is the default workflow
    expect(within(run).getByRole("option", { name: "keel's feature flow · API key" })).toBeInTheDocument();
    await waitFor(() => expect(within(run).getByRole("option", { name: "Connected session · claude-code" })).toBeEnabled());
    await user.selectOptions(run, "session");
    await user.type(within(d).getByLabelText("What to build"), "Show each owner's name on the pet page");
    await user.click(within(d).getByRole("button", { name: "Start flow" }));
    await waitFor(() => expect(starts()).toHaveLength(1));
    const body = starts()[0].body as { workflow_id: string; options: Record<string, unknown> };
    expect(body.workflow_id).toBe("harness");
    expect(body.options).toMatchObject({ runner: "session", intent: "feature", requested_workflow: "feature" });
  });
});

function sessionThread(over: Partial<ThreadState> = {}): ThreadState {
  const t = structuredClone(fx.thread);
  return {
    ...t, workflow_id: "harness", title: "Show the owner name", status: "waiting", current: "build", phase: "ac", ac: null, acs: [],
    runner: "session",
    waiting: { step: "build", kind: "session", title: "Waits for the connected session: build the plan", detail: "", options: ["approve", "reject"],
      id: "q-build", choices: ["wait", "harness", "stop"],
      order: { kind: "build", said: "keel → session: build the plan", step: "build", root: "/workspace/pets", branch: "keel/owner-name",
        published_at: now, plan: "## Goal\nShow the owner's name.", instructions: "Build the plan you wrote in this session." } },
    session: {
      runner: "session", client: "claude-code", connected: true, findings: [],
      session: { id: "s-1", project: PID, client: "claude-code", connected_since: now, last_seen: now, seen_ago_s: 7, connected: true },
      todos: [{ text: "Read the pet page", status: "done" }, { text: "Add owner_name to the view", status: "in_progress" },
        { text: "Write the test", status: "pending" }],
      log: [{ step: "plan", kind: "plan", by: "claude-code", at: now, status: "done", summary: "## Goal\nShow the owner's name.",
        todos: [{ text: "Write the plan", status: "done" }] }],
    },
    ...over,
  };
}

describe("Flow page: a connected session (0.29)", () => {
  it("the feed shows the session, its finished turn, the work order as a turn and its todo list live", async () => {
    const user = userEvent.setup();
    db.flows[PID] = { thread: sessionThread(), workflow: structuredClone(harness) };
    render(<App />);
    const f = await screen.findByRole("region", { name: "What happens" });
    const line = await within(f).findByTestId("session-line");
    expect(line).toHaveTextContent(/Connected session·?claude-code/);
    expect(line).toHaveTextContent(/connected since \d\d:\d\d/);
    expect(line).toHaveTextContent("last seen 7 s ago");
    expect(within(f).getByText("keel → session: plan this")).toBeInTheDocument();
    const card = within(f).getByRole("region", { name: "Waits for the connected session" });
    expect(card).toHaveTextContent("keel → session: build the plan");
    expect(card).toHaveTextContent("no tokens used");
    const list = within(card).getByRole("list", { name: "The session's todo list, live" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((i) => i.className)).toEqual(["is-done", "is-in_progress", "is-pending"]);
    expect(items.map((i) => i.querySelector(".sess-mark")?.textContent)).toEqual(["✓", "•", "☐"]);
    expect(within(card).getByText("1 of 3 done")).toBeInTheDocument();
    // what the session was told
    await user.click(within(card).getByRole("button", { name: "Show the work order (what the session was told)" }));
    expect(within(card).getByTestId("session-order")).toHaveTextContent("Show the owner's name.");
    // no person's gate here: the gate card is not shown
    expect(screen.queryByRole("region", { name: "Gate waits for you" })).toBeNull();
    // Details › Now shows it too
    expect(await screen.findByTestId("session-now")).toHaveTextContent("waits for the session");
  });

  it("a gone session: the card says so and Keep waiting answers the work order with the choice wait", async () => {
    const user = userEvent.setup();
    const t = sessionThread();
    t.waiting = { ...t.waiting!, gone: true, title: "The connected session is gone" };
    t.session = { ...t.session!, gone: true, connected: false, session: { ...t.session!.session!, seen_ago_s: 720, connected: false } };
    db.flows[PID] = { thread: t, workflow: structuredClone(harness) };
    render(<App />);
    const card = await screen.findByRole("region", { name: "Waits for the connected session" });
    expect(within(card).getByRole("alert")).toHaveTextContent("The connected session is gone.");
    expect(await screen.findAllByTestId("session-line")).not.toHaveLength(0);
    expect(screen.getAllByTestId("session-line")[0]).toHaveTextContent("not seen for 12 min");
    expect(within(card).getByRole("button", { name: "Run this step on keel's harness" })).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Keep waiting" }));
    await waitFor(() => expect(db.calls.find((c) => c.path.endsWith("/resume"))?.body)
      .toEqual({ decision: "approve", payload: { choice: "wait", question_id: "q-build" } }));
  });
});

describe("Help › Start here: connect your session (0.29)", () => {
  it("shows the MCP command for Claude Code, the OpenCode config and the sentence, each with Copy", async () => {
    const user = userEvent.setup();
    closeWelcome();
    location.hash = "#/help";
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Start here" });
    const help = await screen.findByTestId("session-connect");
    expect(within(help).getByLabelText("Connect Claude Code to keel")).toHaveTextContent("claude mcp add keel -- keel2 mcp --write");
    expect(within(help).getByLabelText("Connect OpenCode to keel")).toHaveTextContent('"command": [');
    expect(within(help).getByLabelText("Connect OpenCode to keel")).toHaveTextContent('"--write"');
    expect(within(help).getByLabelText("What to tell the session")).toHaveTextContent(SESSION_SENTENCE);
    await user.click(within(help).getByRole("button", { name: "Copy: What to tell the session" }));
    expect(await navigator.clipboard.readText()).toBe(
      "Work for keel: call keel_session_open, then keel_session_next, and follow its work orders.");
  });
});

describe("Inbox: a gone connected session (0.29)", () => {
  it("offers keep waiting, keel's harness for this step, and stop, as named choices", async () => {
    const user = userEvent.setup();
    const posted: unknown[] = [];
    server.use(
      http.get("/api/inbox", () => HttpResponse.json({ count: 1, kinds: ["session"], projects: [], items: [{
        project_id: PID, project_name: PID, thread_id: "th_sess1", flow: "Show the owner name", workflow_id: "harness", step: "build",
        kind: "session", title: "The connected session is gone", detail: "No session was seen for 11 minutes.", more: false,
        options: ["approve", "reject"], choices: ["wait", "harness", "stop"], id: "q-build", since: now,
        labels: { approve: "Keep waiting", reject: "Stop the flow" },
        choice_info: [
          { name: "wait", label: "Keep waiting", description: "the work order stays", why: "none", default: true },
          { name: "harness", label: "Run this step on keel's harness", description: "your API key", why: "none", default: false },
          { name: "stop", label: "Stop the flow", description: "nothing is committed", why: "optional", default: false }] }] })),
      http.post("/api/inbox/:tid/act", async ({ request }) => { posted.push(await request.json()); return HttpResponse.json({ ok: true }); }),
    );
    location.hash = "#/inbox";
    render(<App />);
    const card = await screen.findByRole("article", { name: "The connected session is gone" });
    expect(card).toHaveTextContent("session gone");
    await user.click(within(card).getByRole("radio", { name: /Run this step on keel's harness/ }));
    await user.click(within(card).getByRole("button", { name: "Send: Run this step on keel's harness" }));
    await waitFor(() => expect(posted).toEqual([{ decision: "approve", id: "q-build", payload: { choice: "harness" } }]));
  });
});
