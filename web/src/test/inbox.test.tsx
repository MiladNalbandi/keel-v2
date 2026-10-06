// v0.4.1: the Inbox (everything waiting, across projects), notification clean-up and run modes.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { RunModeSwitch } from "../components/RunMode";
import type { InboxItem, InboxView } from "../inboxApi";
import { AppProvider } from "../state";
import { db, FakeEventSource, server } from "./setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);
const ago = (s: number) => new Date(Date.now() - s * 1000).toISOString();

const items = (): InboxItem[] => [
  { project_id: "ludus-engine", project_name: "ludus-engine", thread_id: "th_7f3a", flow: "Scores for players", workflow_id: "feature",
    step: "ac_gate", kind: "gate", title: "AC gate · AC-002", detail: "RED 4be12d9 · GREEN a81c3f0 · tests 2/2 pass", more: false,
    options: ["approve", "reject"], id: "q-ac2", ac: "AC-002", run_mode: "manual", since: ago(600) },
  { project_id: "platform", project_name: "platform", thread_id: "th_p1", flow: "Fix the tally queue", workflow_id: "fix",
    step: "spec_gate", kind: "clarify", title: "The explorer has 1 question", detail: "1. Who sees it?", more: false, options: ["approve"],
    labels: { approve: "Send my answers" }, id: "q-cl", run_mode: "important", since: ago(300),
    questions: [{ id: "q1", question: "Who sees the queue?", options: [{ label: "admins", recommended: true }, { label: "everyone" }] }] },
  { project_id: "platform", project_name: "platform", thread_id: "th_p2", flow: "Add retries", workflow_id: "change",
    step: "commit", kind: "dependency", title: "Approve new dependency", detail: "pyproject.toml: httpx = \">=0.27\"", more: false,
    options: ["approve", "reject"], labels: { approve: "Allow", reject: "Refuse" }, id: "q-dep", since: ago(60) },
];

function inboxServer(list: InboxItem[]) {
  const state = { list: [...list] };
  server.use(
    http.get("/api/inbox", () => HttpResponse.json({
      items: state.list, count: state.list.length, kinds: [...new Set(state.list.map((i) => i.kind))].sort(),
      projects: [...new Set(state.list.map((i) => i.project_id))].map((id) => ({ id, name: id, count: state.list.filter((i) => i.project_id === id).length })),
    } satisfies InboxView)),
    http.post("/api/inbox/:tid/act", async ({ request, params }) => {
      const body = await request.json();
      db.calls.push({ method: "POST", path: `/api/inbox/${params.tid}/act`, body });
      state.list = state.list.filter((i) => i.thread_id !== params.tid);
      return HttpResponse.json({ thread_id: params.tid, status: "running" });
    }),
  );
  return state;
}

async function openInbox() {
  location.hash = "#/inbox";
  render(<App />);
  return screen.findByRole("heading", { name: "Inbox" });
}

describe("inbox", () => {
  it("is in the Run group with a live badge for every project's waiting flows", async () => {
    inboxServer(items());
    await openInbox();
    const nav = screen.getByRole("navigation", { name: "Screens" });
    const link = within(nav).getByRole("link", { name: /^Inbox/ });
    expect(link).toHaveAttribute("aria-current", "page");
    expect(within(link).getByTestId("inbox-count")).toHaveTextContent("1");      // fixtures: ludus-engine waits once
    expect(screen.getByText("All projects › Run")).toBeInTheDocument();
    // another project starts to wait: the api sends project.changed and the badge follows
    db.projects[1].waiting = 2;
    act(() => FakeEventSource.emit("project.changed", { id: "platform" }));
    await waitFor(() => expect(within(link).getByTestId("inbox-count")).toHaveTextContent("3"));
  });

  it("lists every waiting item across projects and filters by project and kind", async () => {
    const user = userEvent.setup();
    inboxServer(items());
    await openInbox();
    expect(await screen.findAllByTestId("inbox-item")).toHaveLength(3);
    expect(screen.getByText("3 waiting")).toBeInTheDocument();
    const dep = screen.getByRole("article", { name: "Approve new dependency" });
    expect(dep).toHaveTextContent("platform");
    expect(dep).toHaveTextContent("Add retries");
    expect(dep).toHaveTextContent("new dependency");
    expect(screen.getByRole("article", { name: "The explorer has 1 question" })).toHaveTextContent("Important only");

    await user.selectOptions(screen.getByLabelText("In project"), "platform");
    expect(screen.getAllByTestId("inbox-item")).toHaveLength(2);
    expect(screen.getByText("2 of 3 waiting")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Kind"), "dependency");
    expect(screen.getAllByTestId("inbox-item")).toHaveLength(1);
    await user.selectOptions(screen.getByLabelText("In project"), "ludus-engine");
    expect(screen.queryAllByTestId("inbox-item")).toHaveLength(0);
    expect(screen.getByText("Nothing is waiting for you")).toBeInTheDocument();
  });

  it("acts inline: approve, send back with a reason, answer questions, allow a dependency", async () => {
    const user = userEvent.setup();
    inboxServer(items());
    await openInbox();
    const gate = await screen.findByRole("article", { name: "AC gate · AC-002" });
    // send back needs a reason first
    await user.click(within(gate).getByRole("button", { name: "Send back" }));
    expect(calls("POST", "/api/inbox/th_7f3a/act")).toHaveLength(0);
    const why = within(gate).getByLabelText("Why (needed to send back)");
    await waitFor(() => expect(why).toHaveFocus());
    await user.type(why, "check the error text too");
    await user.click(within(gate).getByRole("button", { name: "Send back" }));
    await waitFor(() => expect(calls("POST", "/api/inbox/th_7f3a/act")[0]?.body)
      .toEqual({ decision: "reject", why: "check the error text too", id: "q-ac2" }));
    await waitFor(() => expect(screen.queryByRole("article", { name: "AC gate · AC-002" })).not.toBeInTheDocument());

    const ask = screen.getByRole("article", { name: "The explorer has 1 question" });
    await user.click(within(ask).getByRole("radio", { name: /everyone/ }));
    await user.click(within(ask).getByRole("button", { name: "Send my answers" }));
    await waitFor(() => expect(calls("POST", "/api/inbox/th_p1/act")[0]?.body)
      .toEqual({ decision: "approve", id: "q-cl", payload: { answers: { q1: "everyone" } } }));

    const dep = await screen.findByRole("article", { name: "Approve new dependency" });
    await user.click(within(dep).getByRole("button", { name: "Allow" }));
    await waitFor(() => expect(calls("POST", "/api/inbox/th_p2/act")[0]?.body).toEqual({ decision: "approve", id: "q-dep" }));
    expect(await screen.findByText("Nothing is waiting for you")).toBeInTheDocument();
  });

  it("shows a refused answer (the question changed) and opens the flow in its project", async () => {
    const user = userEvent.setup();
    inboxServer(items());
    server.use(http.post("/api/inbox/th_p2/act", () =>
      HttpResponse.json({ error: "This flow now asks something else", hint: "Reload the inbox and read the new question first." }, { status: 409 })));
    await openInbox();
    const dep = await screen.findByRole("article", { name: "Approve new dependency" });
    await user.click(within(dep).getByRole("button", { name: "Refuse" }));
    expect(await within(dep).findByRole("alert")).toHaveTextContent("This flow now asks something else");
    await user.click(within(dep).getByRole("button", { name: /Open flow Add retries in platform/ }));
    await waitFor(() => expect(location.hash).toBe("#/flow"));
    expect(localStorage.getItem("keel2.project")).toBe("platform");
  });

  it("says when nothing waits", async () => {
    inboxServer([]);
    await openInbox();
    expect(await screen.findByText("Nothing is waiting for you")).toBeInTheDocument();
  });
});

describe("the Helper's commands in the Inbox", () => {
  it("a command that waits for an OK is a card: Deny with a reason answers the Helper", async () => {
    const user = userEvent.setup();
    const state = inboxServer([{ project_id: "ludus-engine", project_name: "ludus-engine", thread_id: "h_9", flow: "Raise the limit", workflow_id: null,
      step: "permission", kind: "permission", title: "The Helper asks to run a command", detail: "rm -rf build", more: false,
      options: ["once", "always", "deny"], id: "q_7", since: ago(20),
      permission: { id: "q_7", session: "h_9", command: "rm -rf build", path: null } }]);
    server.use(http.post("/api/projects/:pid/helper/permissions/:qid", async ({ request, params }) => {
      db.calls.push({ method: "POST", path: `/api/projects/${params.pid}/helper/permissions/${params.qid}`, body: await request.json() });
      state.list = [];
      return HttpResponse.json({ id: params.qid, decision: "deny" });
    }));
    await openInbox();
    const card = (await screen.findAllByTestId("inbox-item"))[0];
    expect(within(card).getByText("may it run?")).toBeInTheDocument();
    expect(within(card).getByText("rm -rf build")).toBeInTheDocument();
    expect(within(card).getByText(/Helper · Raise the limit/)).toBeInTheDocument();
    await user.type(within(card).getByLabelText(/Why not/), "keep the build folder");
    await user.click(within(card).getByRole("button", { name: "Deny" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/helper/permissions/q_7")[0]?.body)
      .toEqual({ decision: "deny", why: "keep the build folder" }));
    expect(await screen.findByText("Refused: the Helper will not run it.")).toBeInTheDocument();
  });
});

describe("auto-approved info and the Flow page switch", () => {
  it("the Flow page's bar has the run mode switch next to Stop, and the gate card says what keel approved", async () => {
    const user = userEvent.setup();
    server.use(http.post("/api/threads/:tid/mode", async ({ request, params }) => {
      const body = await request.json();
      db.calls.push({ method: "POST", path: `/api/threads/${params.tid}/mode`, body });
      return HttpResponse.json({ ...db.flows["ludus-engine"].thread, run_mode: (body as { mode: string }).mode });
    }));
    const t = db.flows["ludus-engine"].thread!;
    db.flows["ludus-engine"].thread = { ...t, run_mode: "auto",
      gate_log: ["gate spec_gate approve: auto-approved (mode auto)", "ac AC-001 approve: auto-approved (mode auto)"] };
    render(<App />);
    const bar = await screen.findByRole("region", { name: "This flow" });
    const sel = within(bar).getByLabelText("Run mode");
    expect(sel).toHaveValue("auto");
    expect(within(bar).getByRole("button", { name: "Stop flow" })).toBeInTheDocument();
    const gate = screen.getByRole("region", { name: "Gate waits for you" });
    const note = within(gate).getByTestId("run-mode-note");
    expect(note).toHaveTextContent("keel approved 2 gates by itself in this flow");
    expect(note).toHaveTextContent("ac AC-001 approve: auto-approved (mode auto)");
    expect(note).toHaveTextContent("Stops when keel cannot decide");
    await user.selectOptions(sel, "manual");
    await waitFor(() => expect(calls("POST", "/api/threads/th_7f3a/mode")[0]?.body).toEqual({ mode: "manual" }));
  });

  it("an inbox item says the same: the run mode, what keel approved, why this one waits", async () => {
    const list = items();
    list[1] = { ...list[1], auto_approved: 1, last_auto: "ac AC-1 approve: auto-approved (mode important)" };
    list[2] = { ...list[2], run_mode: "auto" };
    inboxServer(list);
    await openInbox();
    const ask = await screen.findByRole("article", { name: "The explorer has 1 question" });
    expect(within(ask).getByTestId("run-mode-note")).toHaveTextContent("Important only keel approved 1 gate by itself in this flow");
    const dep = screen.getByRole("article", { name: "Approve new dependency" });
    expect(within(dep).getByTestId("run-mode-note")).toHaveTextContent("Auto Still stops here");
    expect(within(screen.getByRole("article", { name: "AC gate · AC-002" })).queryByTestId("run-mode-note")).toBeNull();   // manual
  });
});

describe("notification clean-up", () => {
  function cleanupServer() {
    server.use(
      http.delete("/api/notifications/:id", async ({ params }) => {
        db.calls.push({ method: "DELETE", path: `/api/notifications/${params.id}`, body: null });
        return HttpResponse.json({ ok: true });
      }),
      http.delete("/api/notifications", () => {
        db.calls.push({ method: "DELETE", path: "/api/notifications", body: null });
        return HttpResponse.json({ ok: true, count: 2 });
      }),
    );
  }

  it("marks one read, deletes one, and clears all after a confirm", async () => {
    const user = userEvent.setup();
    cleanupServer();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /Notifications, 1 unread/ }));
    const list = await screen.findByRole("list", { name: "Notifications" });
    await user.click(within(list).getByRole("button", { name: "Mark read: AC gate waits for you" }));
    expect(screen.queryByTestId("unread")).not.toBeInTheDocument();
    expect(calls("POST", "/api/notifications/n1/read")).toHaveLength(1);
    await user.click(within(list).getByRole("button", { name: "Delete: Lit-Check finished" }));
    expect(within(list).queryByText("Lit-Check finished")).not.toBeInTheDocument();
    expect(calls("DELETE", "/api/notifications/n2")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    expect(calls("DELETE", "/api/notifications")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Yes, clear all" }));
    expect(await screen.findByText("Nothing yet.")).toBeInTheDocument();
    expect(calls("DELETE", "/api/notifications")).toHaveLength(1);
  });

  it("a gate decided anywhere marks its notification decided and read", async () => {
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("unread")).toHaveTextContent("1"));
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
    act(() => FakeEventSource.emit("notification.done", { thread_id: "th_7f3a", ids: ["n1"], read: true }));
    await waitFor(() => expect(screen.queryByTestId("unread")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Notifications" }));
    const list = await screen.findByRole("list", { name: "Notifications" });
    expect(within(list).getByText("✓ decided")).toBeInTheDocument();
  });
});

describe("run modes", () => {
  it("Start a flow offers the modes with one line each and sends the one picked", async () => {
    const user = userEvent.setup();
    localStorage.setItem("keel2.project", "yegi");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Start a flow" }));
    const dlg = await screen.findByRole("dialog", { name: "Start a flow" });
    const group = within(dlg).getByRole("group", { name: "Run mode" });
    expect(within(group).getByRole("radio", { name: /Manual/ })).toBeChecked();
    expect(group).toHaveTextContent("Approves every gate keel can decide");
    expect(group).toHaveTextContent("cannot edit, write or commit anything");
    await user.type(within(dlg).getByLabelText("What to build"), "Rank next to top 10");
    await user.click(within(group).getByRole("radio", { name: /Auto/ }));
    await user.click(within(dlg).getByRole("button", { name: "Start flow" }));
    await waitFor(() => expect(calls("POST", "/api/projects/yegi/flows")[0]?.body).toMatchObject({ run_mode: "auto" }));
  });

  it("the project default comes from Settings", async () => {
    const user = userEvent.setup();
    db.overrides["ludus-engine"] = { ...db.overrides["ludus-engine"], run_mode: "important" };
    location.hash = "#/settings";
    render(<App />);
    const sel = await screen.findByLabelText("Run mode (new flows)");
    expect(sel).toHaveValue("important");
    await user.selectOptions(sel, "readonly");
    await waitFor(() => expect(calls("PUT", "/api/projects/ludus-engine/settings").at(-1)?.body).toEqual({ run_mode: "readonly" }));
  });

  it("<RunModeSwitch/> changes a running flow's mode from the next gate on", async () => {
    const user = userEvent.setup();
    server.use(http.post("/api/threads/:tid/mode", async ({ request, params }) => {
      const body = await request.json();
      db.calls.push({ method: "POST", path: `/api/threads/${params.tid}/mode`, body });
      return HttpResponse.json({ thread_id: params.tid, status: "waiting", run_mode: (body as { mode: string }).mode });
    }));
    const seen: string[] = [];
    render(<AppProvider><RunModeSwitch pid="ludus-engine" threadId="th_7f3a" mode="manual" onChange={(t) => seen.push(t.run_mode ?? "")} /></AppProvider>);
    const sel = screen.getByLabelText("Run mode");
    expect(sel).toHaveValue("manual");
    expect(screen.getByText(/Stops at every gate\. Changes count from the next gate\./)).toBeInTheDocument();
    await user.selectOptions(sel, "auto");
    await waitFor(() => expect(calls("POST", "/api/threads/th_7f3a/mode")[0]?.body).toEqual({ mode: "auto" }));
    await waitFor(() => expect(seen).toEqual(["auto"]));
    expect(await screen.findByRole("status")).toHaveTextContent("Run mode: Auto, from the next gate on.");
  });
});
