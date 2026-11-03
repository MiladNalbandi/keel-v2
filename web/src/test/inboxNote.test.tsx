// 0.21.0 Inbox slot inbox.note: a note on a flow's own card may fill in the answer; only the person's own click sends it.
// The Flow page's slot flow.chip is tested with the Supervisor's chip (plugins/supervisor/web/test).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { registerSlot, slotItems, SLOTS } from "../sdk";
import type { InboxNoteItem, InboxNoteProps } from "../sdk";
import { server } from "./setup";

const base = {
  project_id: "ludus-engine", project_name: "ludus-engine", flow: "change", workflow_id: "change", title: "Check it", detail: "Look at it.", since: new Date().toISOString(),
  options: ["approve", "reject"], labels: {}, kind: "gate", step: "g1", thread_id: "t9", id: "q9", run_mode: "default",
};

function inbox(item: Record<string, unknown>, posted: unknown[]) {
  server.use(
    http.get("/api/inbox", () => HttpResponse.json({ items: [{ ...base, ...item }], count: 1, kinds: ["gate", "fix"], projects: [] })),
    http.post("/api/inbox/:tid/act", async ({ request }) => { posted.push(await request.json()); return HttpResponse.json({ ok: true }); }),
  );
}

function withNote(fillWith: Record<string, unknown>) {
  return registerSlot<InboxNoteItem>(SLOTS.inboxNote, {
    id: "test-note",
    component: ({ fill }: InboxNoteProps) => <button type="button" onClick={() => fill(fillWith)}>Fill it</button>,
  });
}

describe("Inbox slot inbox.note", () => {
  it("is a slot of its own, off the plugin's list unless registered", () => {
    expect(slotItems(SLOTS.inboxNote).map((x) => x.id)).not.toContain("test-note");
  });

  it("fills the reason and the send-back stays the person's click", async () => {
    const posted: unknown[] = [];
    inbox({}, posted);
    const off = withNote({ why: "the test name is wrong" });
    try {
      location.hash = "#/inbox";
      render(<App />);
      const card = await screen.findByTestId("inbox-item");
      await userEvent.click(within(card).getByRole("button", { name: "Fill it" }));
      expect(await within(card).findByDisplayValue("the test name is wrong")).toBeInTheDocument();
      expect(posted).toEqual([]);
    } finally {
      off();
    }
  });

  it("fills a named choice and its reason in the choice form", async () => {
    const posted: unknown[] = [];
    inbox({ kind: "fix", id: "q10", choices: ["skip", "retry"], choice_info: [
      { name: "skip", label: "Skip it", why: "optional", description: "go on", default: true },
      { name: "retry", label: "Try again", why: "optional", description: "again" },
    ] }, posted);
    const off = withNote({ choice: "retry", why: "one more round with the note" });
    try {
      location.hash = "#/inbox";
      render(<App />);
      const card = await screen.findByTestId("inbox-item");
      await userEvent.click(within(card).getByRole("button", { name: "Fill it" }));
      expect(await within(card).findByDisplayValue("one more round with the note")).toBeInTheDocument();
      await waitFor(() => expect(within(card).getByRole("radio", { name: /Try again/ })).toHaveAttribute("aria-checked", "true"));
      expect(posted).toEqual([]);
    } finally {
      off();
    }
  });

  it("keeps no reason for a choice that has no box: nothing goes out unseen", async () => {
    const posted: Record<string, unknown>[] = [];
    inbox({ kind: "fix", id: "q11", choices: ["go", "stop"], choice_info: [
      { name: "go", label: "Go on", why: "none", description: "go on", default: true },
      { name: "stop", label: "Stop", why: "optional", description: "stop" },
    ] }, posted);
    const off = withNote({ why: "words nobody would see" });
    try {
      location.hash = "#/inbox";
      render(<App />);
      const card = await screen.findByTestId("inbox-item");
      await userEvent.click(within(card).getByRole("button", { name: "Fill it" }));
      await userEvent.click(await within(card).findByRole("button", { name: /^Send/ }));
      await waitFor(() => expect(posted).toHaveLength(1));
      expect(posted[0].why).toBeUndefined();
    } finally {
      off();
    }
  });
});
