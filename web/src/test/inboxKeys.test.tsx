// keel 0.28 the redesigned Inbox: a list of rows and the selected item's card, answered with keys.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { InboxItem, InboxView } from "../inboxApi";
import { db, server } from "./setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);
const ago = (s: number) => new Date(Date.now() - s * 1000).toISOString();

const items = (): InboxItem[] => [
  { project_id: "ludus-engine", project_name: "ludus-engine", thread_id: "th_7f3a", flow: "Scores for players", workflow_id: "feature",
    step: "ac_gate", kind: "gate", title: "AC gate · AC-002", detail: "## Review\nRED 4be12d9 · GREEN a81c3f0", more: false,
    options: ["approve", "reject"], id: "q-ac2", ac: "AC-002", run_mode: "manual", since: ago(600) },
  { project_id: "platform", project_name: "platform", thread_id: "th_p1", flow: "Fix the tally queue", workflow_id: "fix",
    step: "verify_green", kind: "fix", title: "AC-001 is stuck", detail: "failed 3 times", more: false, options: ["approve", "reject"],
    choices: ["retry", "skip"], id: "q-fix", since: ago(300), choice_info: [
      { name: "retry", label: "Try again", description: "one more round", why: "optional", default: true },
      { name: "skip", label: "Skip it for now", description: "move it to the end", why: "required", default: false }] },
  { project_id: "platform", project_name: "platform", thread_id: "th_p2", flow: "Add retries", workflow_id: "change",
    step: "commit", kind: "dependency", title: "Approve new dependency", detail: "pyproject.toml: httpx", more: false,
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
  await screen.findByRole("heading", { name: "Inbox" });
  return screen.findAllByTestId("inbox-row");
}

const selected = () => screen.getAllByTestId("inbox-row").find((r) => r.getAttribute("aria-current") === "true");
/** The card that shows: the selected item's (the others stay mounted but hidden). */
const shownCard = () => document.querySelector<HTMLElement>(".rdinbox-card.on [data-testid=inbox-item]");

describe("0.28 the Inbox: a list and the selected item", () => {
  it("lists every item as a row (kind, project, age, title, one line) and shows only the selected one's card", async () => {
    const user = userEvent.setup();
    inboxServer(items());
    const rows = await openInbox();
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveAttribute("aria-current", "true");
    expect(rows[0]).toHaveTextContent("gate");
    expect(rows[0]).toHaveTextContent("ludus-engine · Scores for players");
    expect(rows[0]).toHaveTextContent("AC gate · AC-002");
    expect(rows[0]).toHaveTextContent("Review");               // the detail's first line, without its Markdown marks
    expect(rows[0]).not.toHaveTextContent("##");
    expect(shownCard()).toHaveAccessibleName("AC gate · AC-002");
    await user.click(rows[2]);
    expect(selected()).toBe(rows[2]);
    expect(shownCard()).toHaveAccessibleName("Approve new dependency");
  });

  it("the filters are chips with counts", async () => {
    inboxServer(items());
    await openInbox();
    expect(within(screen.getByLabelText("Kind")).getByRole("option", { name: "needs a fix (1)" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("In project")).getByRole("option", { name: "platform (2)" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("In project")).getByRole("option", { name: "All projects (3)" })).toBeInTheDocument();
  });

  it("J and K move the selection; A approves the selected gate and the next item is selected", async () => {
    const user = userEvent.setup();
    inboxServer(items());
    const rows = await openInbox();
    await user.keyboard("j");
    expect(selected()).toBe(rows[1]);
    expect(rows[1]).toHaveFocus();
    await user.keyboard("k");
    expect(selected()).toBe(rows[0]);
    await user.keyboard("a");
    await waitFor(() => expect(calls("POST", "/api/inbox/th_7f3a/act")[0]?.body).toEqual({ decision: "approve", id: "q-ac2" }));
    await waitFor(() => expect(shownCard()).toHaveAccessibleName("AC-001 is stuck"));
    expect(shownCard()).toHaveFocus();
    expect(selected()).toHaveTextContent("AC-001 is stuck");
  });

  it("R goes to the reason box and sends nothing; keys stay quiet while you type in it", async () => {
    const user = userEvent.setup();
    inboxServer(items());
    await openInbox();
    await user.keyboard("r");
    const why = within(shownCard()!).getByLabelText("Why (needed to send back)");
    await waitFor(() => expect(why).toHaveFocus());
    await user.keyboard("a rename only");
    expect(why).toHaveValue("a rename only");
    expect(calls("POST", "/api/inbox/th_7f3a/act")).toHaveLength(0);
    expect(selected()).toHaveTextContent("AC gate · AC-002");
  });

  it("1–9 pick a named choice and A sends it", async () => {
    const user = userEvent.setup();
    inboxServer(items());
    await openInbox();
    await user.keyboard("j");
    const card = shownCard()!;
    await user.keyboard("2");
    expect(within(card).getByRole("radio", { name: /Skip it for now/ })).toHaveAttribute("aria-checked", "true");
    // a choice that needs a reason: A is refused until it has one
    (document.activeElement as HTMLElement).blur();
    await user.keyboard("a");
    expect(within(card).getByRole("alert")).toHaveTextContent("“Skip it for now” needs a reason");
    await user.type(within(card).getByLabelText("Why (needed for “Skip it for now”)"), "waits for AC-002");
    await user.click(within(card).getByRole("radio", { name: /Skip it for now/ }));
    await user.keyboard("a");
    await waitFor(() => expect(calls("POST", "/api/inbox/th_p1/act")[0]?.body).toEqual(
      { decision: "approve", id: "q-fix", why: "waits for AC-002", payload: { choice: "skip" } }));
  });

  it("O opens the selected item's flow in its project", async () => {
    const user = userEvent.setup();
    inboxServer(items());
    const rows = await openInbox();
    await user.click(rows[2]);
    await user.keyboard("o");
    await waitFor(() => expect(location.hash).toBe("#/flow"));
    expect(localStorage.getItem("keel2.project")).toBe("platform");
  });

  it("the buttons show their keys, and their names stay the words", async () => {
    inboxServer(items());
    await openInbox();
    const card = shownCard()!;
    const approve = within(card).getByRole("button", { name: "Approve AC-002" });
    expect(approve).toHaveAttribute("aria-keyshortcuts", "A");
    expect(approve.querySelector("kbd")).toHaveTextContent("A");
    expect(within(card).getByRole("button", { name: "Send back" })).toHaveAttribute("aria-keyshortcuts", "R");
    expect(within(card).getByRole("button", { name: /Open flow Scores for players/ })).toHaveAttribute("aria-keyshortcuts", "O");
    expect(screen.getByRole("note", { name: "Inbox keys" })).toHaveTextContent("Next / previous");
  });
});
