// v0.15.0 the launcher: ⌘K on any page (Ctrl+K off a Mac, as here), ⇧⇧ in Code; one list for what waits, pull
// requests, files, code, tasks, flows, pages and actions; prefixes, scopes, ⌘K actions with a confirm for what
// changes things, and ? asking KeelBot (read only) with the answer in place.

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import {
  flat,
  groupJump,
  match,
  parseQuery,
  rank,
  type Item,
} from "../components/launcher/model";
import type { InboxItem } from "../inboxApi";
import { db, FakeEventSource, server } from "./setup";

const GATE: InboxItem = {
  project_id: "ludus-engine",
  project_name: "ludus-engine",
  thread_id: "th_1",
  flow: "Prefs sync",
  workflow_id: "feature",
  step: "plan-gate",
  kind: "gate",
  title: "Approve the plan",
  detail: "Three steps, one new table.",
  more: false,
  options: ["approve", "reject"],
  id: "q1",
  phase: "plan",
  since: "2026-10-08T09:00:00Z",
};

function inbox(items: InboxItem[] = []) {
  const acted: unknown[] = [];
  server.use(
    http.get("/api/inbox", () =>
      HttpResponse.json({
        items,
        count: items.length,
        kinds: [],
        projects: [],
      }),
    ),
    http.post("/api/inbox/:tid/act", async ({ request, params }) => {
      acted.push({ tid: params.tid, ...((await request.json()) as object) });
      return HttpResponse.json({ thread_id: params.tid, status: "running" });
    }),
  );
  return acted;
}

async function open() {
  fireEvent.keyDown(window, { key: "k", code: "KeyK", ctrlKey: true });
  return screen.findByRole("dialog", { name: "Launcher" });
}

const field = (dlg: HTMLElement) => within(dlg).getByRole("combobox");

/** The engine answers the launcher's ask chat (as the Helper tests do). */
function answer(text: string) {
  const sess = db.helper.sessions[0];
  const n = (sess.messages?.length ?? 0) + 1;
  sess.messages = [
    ...(sess.messages ?? []),
    {
      n,
      role: "helper",
      text,
      call_id: `call-${sess.id}-${n - 1}`,
      data: { status: "done", tokens_in: 900, tokens_out: 200 },
      at: new Date().toISOString(),
    },
  ];
  sess.status = "idle";
  sess.busy = false;
  act(() => {
    FakeEventSource.emit("helper.finished", {
      type: "helper.finished",
      thread_id: sess.id,
      project_id: "ludus-engine",
      call_id: `call-${sess.id}-${n - 1}`,
      step: "helper",
      at: new Date().toISOString(),
      data: { status: "done" },
    });
  });
}

describe("Launcher: what a query means", () => {
  it("reads prefixes, file:line and scopes; matches word starts first; ranks into groups with a best match", () => {
    expect(parseQuery("?why is it slow", "all")).toMatchObject({
      ask: true,
      prefix: "?",
      text: "why is it slow",
    });
    expect(parseQuery("Prefs.kt:42", "all")).toMatchObject({
      text: "Prefs.kt",
      line: 42,
      kinds: ["file"],
    });
    expect(parseQuery("@save", "all").kinds).toEqual(["symbol"]);
    expect(parseQuery("#7", "all").kinds).toEqual(["pr", "task"]);
    expect(parseQuery("x", "files").kinds).toEqual(["file"]);

    expect(match("savePageSize", "page")?.hits).toEqual([4, 5, 6, 7]); // the P of Page, not a later "page"
    expect(match("#7 Save the page size", "save page")).not.toBeNull();
    expect(match("#7 Save the page size", "save gone")).toBeNull();
    expect(match("ScoreController", "scrc")).not.toBeNull(); // letter by letter
    expect(match("Settings", "page")).toBeNull();
    expect(match("Pages", "pa")!.score).toBeGreaterThan(
      match("Map pages", "pa")!.score,
    );

    const it = (
      id: string,
      kind: Item["kind"],
      title: string,
      extra: Partial<Item> = {},
    ): Item => ({ id, kind, title, actions: [], ...extra });
    const groups = rank(
      [
        it("pr:7", "pr", "#7 Save the page size"),
        it("sym", "symbol", "savePageSize", { ranked: true, score: 200 }),
        it("page:map", "page", "Map"),
        it("act:x", "action", "Page up"),
      ],
      parseQuery("page", "all"),
      5,
    );
    expect(groups.map((g) => g.label)).toEqual([
      "Best match",
      "Pull requests",
      "Actions",
    ]);
    expect(groups[0].items[0].id).toBe("sym");
    expect(flat(groups).map((i) => i.id)).toEqual(["sym", "pr:7", "act:x"]);
    expect(groupJump(groups, 0, 1)).toBe(1);
    expect(groupJump(groups, 2, -1)).toBe(1);
  });
});

describe("Launcher: the keyboard way to everything", () => {
  it("opens with ⌘K on any page: what waits for you first; typing finds a file and ↩ opens it in Code at the line", async () => {
    inbox([GATE]);
    render(<App />);
    const dlg = await open();
    const waits = await within(dlg).findByRole("group", {
      name: "Waiting for you",
    });
    expect(
      within(waits).getByRole("option", { name: /Approve the plan/ }),
    ).toHaveAttribute("aria-selected", "true");
    expect(
      within(dlg).getByRole("group", { name: "Quick actions" }),
    ).toHaveTextContent("Ask KeelBot…");

    await userEvent.type(field(dlg), "ScoreRepository.kt:12");
    const files = await within(dlg).findByRole("group", { name: "Files" });
    expect(
      within(files).getByRole("option", { name: /ScoreRepository\.kt/ }),
    ).toHaveTextContent("line 12");
    await userEvent.keyboard("{Enter}");
    await waitFor(() =>
      expect(location.hash).toBe("#/repo/api/ScoreRepository.kt:12"),
    );
    expect(
      screen.queryByRole("dialog", { name: "Launcher" }),
    ).not.toBeInTheDocument();

    // ⌘K again closes it; the recent file is remembered
    const again = await open();
    expect(
      await within(again).findByRole("group", { name: "Recent" }),
    ).toHaveTextContent("ScoreRepository.kt");
    fireEvent.keyDown(window, { key: "k", code: "KeyK", ctrlKey: true });
    expect(
      screen.queryByRole("dialog", { name: "Launcher" }),
    ).not.toBeInTheDocument();
  });

  it("narrows with a prefix and with ⇥ scopes; finds tasks by their Jira key; esc goes back one step at a time", async () => {
    inbox();
    render(<App />);
    const dlg = await open();
    await userEvent.type(field(dlg), ">theme");
    expect(
      within(dlg).getByRole("button", { name: /Actions/ }),
    ).toHaveTextContent(">");
    expect(
      await within(dlg).findByRole("option", {
        name: /Switch to the dark theme/,
      }),
    ).toBeInTheDocument();
    expect(
      within(dlg).queryByRole("group", { name: "Files" }),
    ).not.toBeInTheDocument();
    await userEvent.clear(field(dlg));
    await userEvent.keyboard("{Backspace}"); // ⌫ on the empty field drops the prefix
    expect(
      within(dlg).queryByRole("button", { name: /Actions/ }),
    ).not.toBeInTheDocument();

    await userEvent.type(field(dlg), "ABC-1");
    expect(
      await within(dlg).findByRole("option", {
        name: /ABC-1 Rank players weekly/,
      }),
    ).toBeInTheDocument();
    await userEvent.clear(field(dlg));

    await userEvent.keyboard("{Tab}");
    expect(within(dlg).getByRole("tab", { name: "Files" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      await within(dlg).findByRole("option", { name: /README\.md/ }),
    ).toBeInTheDocument();
    await userEvent.keyboard("{Escape}"); // the scope back to All
    expect(within(dlg).getByRole("tab", { name: "All" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await userEvent.keyboard("{Escape}");
    expect(
      screen.queryByRole("dialog", { name: "Launcher" }),
    ).not.toBeInTheDocument();
  });

  it("⌘K on a result shows its actions; one that changes things asks first (approve a gate)", async () => {
    const acted = inbox([GATE]);
    render(<App />);
    const dlg = await open();
    await within(dlg).findByRole("option", { name: /Approve the plan/ });
    fireEvent.keyDown(field(dlg), { key: "k", code: "KeyK", ctrlKey: true });
    const panel = await within(dlg).findByRole("dialog", {
      name: "Actions for Approve the plan",
    });
    expect(
      within(panel)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual([
      expect.stringContaining("Open the flow"),
      expect.stringContaining("Open in the Inbox"),
      expect.stringContaining("Approve"),
      expect.stringContaining("Ask KeelBot about it"),
    ]);
    const search = within(panel).getByRole("textbox", {
      name: "Search actions",
    });
    await waitFor(() => expect(search).toHaveFocus());
    await userEvent.type(search, "appr");
    await userEvent.keyboard("{Enter}");
    expect(panel).toHaveTextContent(
      "Approve “Approve the plan” in Prefs sync? The flow goes on.",
    );
    expect(acted).toEqual([]);
    await userEvent.keyboard("{Enter}");
    await waitFor(() =>
      expect(acted).toEqual([{ tid: "th_1", decision: "approve", id: "q1" }]),
    );
  });

  it("? asks KeelBot right there, read only; a file link in the answer opens the file", async () => {
    inbox();
    render(<App />);
    const dlg = await open();
    await userEvent.type(field(dlg), "?why is a negative score refused");
    expect(
      within(dlg).getByRole("button", { name: /Ask keel/ }),
    ).toBeInTheDocument();
    expect(dlg).toHaveTextContent("It only reads: it changes nothing.");
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(db.helper.sessions[0]?.mode).toBe("ask"));
    expect(db.helper.sessions[0].messages?.[0].text).toBe(
      "why is a negative score refused",
    );
    answer("The controller refuses it at `api/ScoreController.kt:7`.");
    const link = await within(dlg).findByText("api/ScoreController.kt:7");
    expect(
      within(dlg).getByRole("button", { name: /Continue in KeelBot/ }),
    ).toBeInTheDocument();
    await userEvent.click(link);
    await waitFor(() =>
      expect(location.hash).toBe("#/repo/api/ScoreController.kt:7"),
    );
  });

  it("⌘↩ on a result asks KeelBot about it; ⌘↩ on the answer continues the chat on KeelBot's page", async () => {
    inbox();
    render(<App />);
    const dlg = await open();
    await userEvent.type(field(dlg), "ScoreController");
    await within(dlg).findByRole("option", { name: /ScoreController\.kt/ });
    fireEvent.keyDown(field(dlg), { key: "Enter", ctrlKey: true });
    await waitFor(() =>
      expect(field(dlg)).toHaveValue(
        "Explain api/ScoreController.kt: what it does and how it is used.",
      ),
    );
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(db.helper.sessions.length).toBe(1));
    answer("It saves a score.");
    expect(
      await within(dlg).findByText("It saves a score."),
    ).toBeInTheDocument();
    fireEvent.keyDown(field(dlg), { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(location.hash).toBe("#/helper"));
    expect(localStorage.getItem("keel2.helper.ludus-engine.session")).toBe(
      db.helper.sessions[0].id,
    );
  });

  it("lists pull requests from the Code Review plugin; ↩ opens that review in Code's Review tool window", async () => {
    inbox();
    db.plugins.review = true;
    const R = "/api/projects/:pid/review";
    server.use(
      http.get(`${R}/prs`, () =>
        HttpResponse.json({
          host: {
            kind: "github",
            host: "github.com",
            path: "acme/shop",
            web: "https://github.com/acme/shop",
          },
          me: "me",
          note: null,
          counts: {},
          prs: [
            {
              number: 7,
              title: "Save the page size",
              author: "ana",
              branch: "feat/paging",
              base: "main",
              draft: false,
              updated_at: "",
              url: "https://github.com/acme/shop/pull/7",
              review_requested: true,
              mine: false,
              assigned: false,
            },
          ],
        }),
      ),
      http.get(`${R}/branch`, () =>
        HttpResponse.json({
          branch: "feat/scores",
          base: "main",
          ahead: 0,
          files: 0,
          added: 0,
          removed: 0,
          pr: null,
          note: null,
        }),
      ),
      http.get(`${R}/*`, () =>
        HttpResponse.json({ error: "Not in this test" }, { status: 404 }),
      ),
    );
    render(<App />);
    const dlg = await open();
    const waits = await within(dlg).findByRole("group", {
      name: "Waiting for you",
    });
    expect(
      within(waits).getByRole("option", { name: /#7 Save the page size/ }),
    ).toHaveTextContent("you review");
    await userEvent.type(field(dlg), "#7");
    expect(
      await within(dlg).findByRole("option", { name: /#7 Save the page size/ }),
    ).toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    // the link (#/repo/@review/pr:7) opens the review; then the address follows Code's open tab again
    await waitFor(() =>
      expect(sessionStorage.getItem("keel2.review.sel.ludus-engine")).toBe("pr:7"),
    );
    expect(await screen.findByRole("region", { name: "Review" })).toBeVisible();
    expect(sessionStorage.getItem("keel2.review.sel.ludus-engine")).toBe(
      "pr:7",
    );
  });

  it("⇧⇧ in Code opens it too (Search Everywhere)", async () => {
    inbox();
    location.hash = "#/repo";
    render(<App />);
    await screen.findByRole("tree", { name: /files/i });
    fireEvent.keyDown(window, { key: "Shift" });
    fireEvent.keyDown(window, { key: "Shift" });
    expect(
      await screen.findByRole("dialog", { name: "Launcher" }),
    ).toBeInTheDocument();
  });
});
