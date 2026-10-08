// v0.14.0 the Code Review plugin, IDE style: the Review tool window (this branch, the list with filters, one pull
// request with Approve / Submit / Merge your own and its tabs: the folder tree, commits, keel's overview and findings,
// threads), each file in an editor tab (the diff with threads, pending comments and findings on their lines), and
// IntelliJ's keys (F7, ⌥⌘→, ⌘B, ⌥F7, ⌘[, ⇧⌘A).

import {
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
  actionFor,
  keyLabel,
  matches,
  readKeymap,
  saveKeymap,
  wordAt,
} from "../components/review/keymap";
import type { AiState, Draft, ReviewView } from "../reviewApi";
import { db, server } from "./setup";

const DIFF = [
  "diff --git a/src/Prefs.kt b/src/Prefs.kt",
  "--- a/src/Prefs.kt",
  "+++ b/src/Prefs.kt",
  "@@ -1,3 +1,7 @@",
  " class Prefs {",
  "+    private val cache = mutableMapOf<String, Int>()",
  "+    fun savePageSize(user: String, size: Int) {",
  "+        cache[user] = size",
  "+    }",
  "     fun pageSize(user: String) = 20",
  " }",
].join("\n");
const DIFF2 = [
  "diff --git a/src/test/PrefsTest.kt b/src/test/PrefsTest.kt",
  "--- /dev/null",
  "+++ b/src/test/PrefsTest.kt",
  "@@ -0,0 +1,2 @@",
  "+class PrefsTest {",
  '+    fun saves() { Prefs().savePageSize("a", 50) }',
].join("\n");

const HOST = {
  kind: "github" as const,
  host: "github.com",
  path: "acme/shop",
  web: "https://github.com/acme/shop",
};

function viewOf(n: number): ReviewView {
  const mine = n === 8;
  return {
    key: `pr:${n}`,
    kind: "pr",
    number: n,
    title: mine ? "My paging" : "Save the page size",
    author: mine ? "me" : "ana",
    body: "Closes ORD-88",
    base: "main",
    branch: "feat/paging",
    base_sha: "b".repeat(40),
    head_sha: "a".repeat(40),
    url: `https://github.com/acme/shop/pull/${n}`,
    state: "open",
    draft: false,
    same_repo: true,
    checks: [{ name: "build", state: "success" }],
    approved: [],
    changes_requested: [],
    files: [
      {
        path: "src/Prefs.kt",
        status: "M",
        added: 4,
        removed: 0,
        binary: false,
      },
      {
        path: "src/test/PrefsTest.kt",
        status: "A",
        added: 2,
        removed: 0,
        binary: false,
      },
    ],
    added: 6,
    removed: 0,
    commits: [
      {
        sha: "c".repeat(40),
        message: "Save the page size",
        author: "ana",
        at: "2026-10-08",
      },
    ],
    threads: mine
      ? []
      : [
          {
            id: "PRRT_1",
            path: "src/Prefs.kt",
            line: 3,
            side: "RIGHT",
            resolved: false,
            outdated: false,
            reply_to: "11",
            comments: [
              {
                id: "11",
                author: "bo",
                body: "Should this be saved?",
                at: "2026-10-08T09:00:00Z",
              },
            ],
          },
        ],
    conversation: [],
    drafts: [],
    viewed: [],
    can_post: true,
    host: HOST,
    me: "me",
    notes: [],
    mine,
    mergeable: true,
    merge_state: "clean",
  };
}

type Call = { method: string; path: string; body: unknown };

function reviewServer() {
  const calls: Call[] = [];
  const views: Record<string, ReviewView> = {
    "pr:7": viewOf(7),
    "pr:8": viewOf(8),
  };
  let ai: AiState = { overview: null, findings: null, decisions: {} };
  const rec = async (request: Request) => {
    let body: unknown = null;
    try {
      body = await request.clone().json();
    } catch {
      /* none */
    }
    const u = new URL(request.url);
    calls.push({ method: request.method, path: u.pathname + u.search, body });
    return (body ?? {}) as Record<string, unknown>;
  };
  const keyOf = (req: Request, b?: Record<string, unknown>) =>
    (b?.key as string) ?? new URL(req.url).searchParams.get("key") ?? "pr:7";
  const prs = [
    {
      number: 7,
      title: "Save the page size",
      author: "ana",
      branch: "feat/paging",
      base: "main",
      draft: false,
      updated_at: "",
      url: "",
      review_requested: true,
      mine: false,
      assigned: true,
    },
    {
      number: 8,
      title: "My paging",
      author: "me",
      branch: "feat/mine",
      base: "main",
      draft: false,
      updated_at: "",
      url: "",
      review_requested: false,
      mine: true,
      assigned: false,
    },
  ];
  const R = "/api/projects/:pid/review";
  server.use(
    http.get(`${R}/branch`, () =>
      HttpResponse.json({
        branch: "feat/paging",
        base: "main",
        ahead: 2,
        files: 2,
        added: 6,
        removed: 0,
        pr: prs[0],
        note: null,
      }),
    ),
    http.get(`${R}/prs`, ({ request }) => {
      const f = new URL(request.url).searchParams.get("filter");
      return HttpResponse.json({
        host: HOST,
        me: "me",
        counts: { review: 1, assigned: 1, mine: 1, all: 2 },
        note: null,
        prs: prs.filter(
          (p) =>
            f === "all" ||
            (f === "mine"
              ? p.mine
              : f === "assigned"
                ? p.assigned
                : p.review_requested),
        ),
      });
    }),
    http.get(`${R}/view`, ({ request }) =>
      HttpResponse.json(views[keyOf(request)]),
    ),
    http.get(`${R}/diff`, ({ request }) => {
      const p = new URL(request.url).searchParams.get("path");
      return HttpResponse.json({
        path: p,
        against: "base",
        ref: "base…#7",
        diff: p === "src/Prefs.kt" ? DIFF : DIFF2,
        binary: false,
        truncated: false,
      });
    }),
    http.get(`${R}/file`, ({ request }) =>
      HttpResponse.json({
        path: new URL(request.url).searchParams.get("path"),
        ref: "#7",
        sha: "a".repeat(40),
        text: "class Prefs {\n    private val cache = mutableMapOf<String, Int>()\n    fun savePageSize(user: String, size: Int) {\n        cache[user] = size\n    }\n}\n",
        truncated: false,
      }),
    ),
    http.get(`${R}/definition`, ({ request }) =>
      HttpResponse.json({
        symbol: new URL(request.url).searchParams.get("symbol"),
        ref: "#7",
        truncated: false,
        places: [
          {
            path: "src/Prefs.kt",
            line: 3,
            text: "fun savePageSize(user: String, size: Int) {",
            declaration: true,
            test: false,
            changed: true,
          },
        ],
      }),
    ),
    http.get(`${R}/usages`, ({ request }) =>
      HttpResponse.json({
        symbol: new URL(request.url).searchParams.get("symbol"),
        ref: "#7",
        truncated: false,
        places: [
          {
            path: "src/Prefs.kt",
            line: 3,
            text: "fun savePageSize(user: String, size: Int) {",
            declaration: true,
            test: false,
            changed: true,
          },
          {
            path: "src/test/PrefsTest.kt",
            line: 2,
            text: 'fun saves() { Prefs().savePageSize("a", 50) }',
            declaration: false,
            test: true,
            changed: true,
          },
        ],
      }),
    ),
    http.post(`${R}/drafts`, async ({ request }) => {
      const b = await rec(request);
      const k = keyOf(request, b);
      const d: Draft = {
        id: `dr_${views[k].drafts.length + 1}`,
        path: (b.path as string) ?? null,
        line: (b.line as number) ?? null,
        side: "RIGHT",
        body: b.body as string,
        finding_id: (b.finding_id as string) ?? null,
        created_at: "",
      };
      views[k] = { ...views[k], drafts: [...views[k].drafts, d] };
      return HttpResponse.json(d);
    }),
    http.post(`${R}/viewed`, async ({ request }) => {
      const b = await rec(request);
      const k = keyOf(request, b);
      views[k] = {
        ...views[k],
        viewed: b.viewed
          ? [...views[k].viewed, b.path as string]
          : views[k].viewed.filter((p) => p !== b.path),
      };
      return HttpResponse.json({ viewed: views[k].viewed });
    }),
    http.post(`${R}/submit`, async ({ request }) => {
      const b = await rec(request);
      const k = keyOf(request, b);
      const posted = views[k].drafts.length;
      views[k] = {
        ...views[k],
        drafts: [],
        ...(b.event === "APPROVE"
          ? { approved: ["me"] }
          : { changes_requested: ["me"] }),
      };
      return HttpResponse.json({
        posted,
        in_body: 0,
        event: b.event,
        url: null,
        view: views[k],
      });
    }),
    http.post(`${R}/merge`, async ({ request }) => {
      const b = await rec(request);
      const k = keyOf(request, b);
      views[k] = { ...views[k], state: "merged" };
      return HttpResponse.json({
        merged: true,
        message: "Merged",
        view: views[k],
      });
    }),
    http.post(`${R}/threads/:tid/resolve`, async ({ request }) => {
      const b = await rec(request);
      const k = keyOf(request, b);
      views[k] = {
        ...views[k],
        threads: views[k].threads.map((t) => ({ ...t, resolved: true })),
      };
      return HttpResponse.json(views[k]);
    }),
    http.get(`${R}/ai`, () => HttpResponse.json(ai)),
    http.post(`${R}/ai/:kind`, async ({ request, params }) => {
      await rec(request);
      const run = {
        id: "rr_1",
        kind: params.kind,
        status: "running",
        stage: "overview",
        head_sha: "a".repeat(40),
        stale: false,
        sessions: [{ sid: "h1", role: "overview", status: "running" }],
        result: null,
        error: null,
        created_at: "",
        updated_at: "",
      };
      ai = { ...ai, [String(params.kind)]: run } as AiState;
      return HttpResponse.json(ai);
    }),
    http.post(`${R}/ai/findings/:fid`, async ({ request, params }) => {
      const b = await rec(request);
      ai = {
        ...ai,
        decisions: {
          ...ai.decisions,
          [String(params.fid)]: {
            decision: b.decision as string,
            why: (b.why as string) ?? null,
          },
        },
      };
      return HttpResponse.json(ai);
    }),
  );
  return {
    calls,
    setAi: (a: AiState) => {
      ai = a;
    },
  };
}

async function openTool() {
  db.plugins.review = true;
  location.hash = "#/repo";
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "Review" }));
  return screen.findByRole("region", { name: "Review" });
}

async function openPr(n = 7) {
  const side = await openTool();
  await userEvent.click(
    await within(side).findByRole("button", { name: new RegExp(`^#${n} `) }),
  );
  await within(side).findByRole("button", { name: /Back to the list/ });
  return side;
}

async function openFile(side: HTMLElement, path: string) {
  await userEvent.click(
    within(side).getByRole("button", { name: `Open the changes of ${path}` }),
  );
  return screen.findByRole("region", { name: `${path} in review #7` });
}

const FINDINGS: AiState = {
  overview: {
    id: "rr_0",
    kind: "overview",
    status: "done",
    stage: "done",
    head_sha: "a".repeat(40),
    stale: false,
    sessions: [],
    error: null,
    created_at: "",
    updated_at: "",
    result: {
      stage: "done",
      summary: "Saves the page size per user.",
      files: [{ path: "src/Prefs.kt", what: "a cache" }],
      order: ["src/Prefs.kt"],
      diagram: "Orders ─▶ Prefs ─▶ cache",
      effort: 2,
      risk: "medium",
      risk_why: "memory only",
      split: "",
      questions: ["Where is it stored?"],
    },
  },
  findings: {
    id: "rr_1",
    kind: "findings",
    status: "done",
    stage: "done",
    head_sha: "a".repeat(40),
    stale: false,
    sessions: [],
    error: null,
    created_at: "",
    updated_at: "",
    result: {
      stage: "done",
      counts: { blocking: 1, should_fix: 0, nit: 0 },
      security: "Checked input.",
      tests: "One case missing.",
      findings: [
        {
          id: "f_1",
          title: "page size is never saved",
          severity: "blocking",
          category: "correctness",
          path: "src/Prefs.kt",
          line: 4,
          side: "RIGHT",
          why: "only the cache",
          fix: "save it",
          suggestion: "        repository.save(user, size)",
          pre_existing: false,
          reviewer: "A+B",
          check: "confirmed",
          check_why: "line 4",
        },
      ],
      rejected: [],
      nits: [],
      pre_existing: [],
    },
  },
  decisions: {},
};

/** jsdom has no layout: make getSelection() answer the name the test "selected". */
function select(word: string) {
  Object.defineProperty(window.getSelection()!, "toString", {
    value: () => word,
    configurable: true,
  });
}

describe("the keymap", () => {
  it("names keys the IntelliJ way and matches them (Ctrl stands for ⌘ off a Mac)", () => {
    expect(keyLabel("meta+alt+arrowright")).toBe("Ctrl+Alt+→");
    expect(keyLabel("alt+f7")).toBe("Alt+F7");
    expect(
      matches(
        new KeyboardEvent("keydown", { key: "b", code: "KeyB", ctrlKey: true }),
        "meta+b",
      ),
    ).toBe(true);
    expect(
      matches(
        new KeyboardEvent("keydown", { key: "b", code: "KeyB" }),
        "meta+b",
      ),
    ).toBe(false);
    expect(
      actionFor(
        new KeyboardEvent("keydown", { key: "F7", code: "F7", altKey: true }),
      ),
    ).toBe("usages");
    expect(
      actionFor(new KeyboardEvent("keydown", { key: "F7", code: "F7" })),
    ).toBe("nextChange");
    expect(
      actionFor(
        new KeyboardEvent("keydown", {
          key: "[",
          code: "BracketLeft",
          ctrlKey: true,
        }),
      ),
    ).toBe("back");
    expect(wordAt("prefs.savePageSize(user)", 10)).toBe("savePageSize");
    expect(readKeymap()).toBe("intellij");
    saveKeymap("vscode");
    expect(
      actionFor(new KeyboardEvent("keydown", { key: "F12", code: "F12" })),
    ).toBe("declaration");
    saveKeymap("intellij");
  });
});

describe("Code › Review, IDE style", () => {
  it("lists pull requests by to review, assigned, mine and all, and opens one with its folder tree; a file opens as an editor tab", async () => {
    reviewServer();
    const side = await openTool();
    expect(
      within(side).getByRole("region", { name: "This branch" }),
    ).toHaveTextContent("2 commits · 2 files");
    const list = within(side).getByRole("region", { name: "Pull requests" });
    expect(
      await within(list).findByRole("button", {
        name: "#7 Save the page size",
      }),
    ).toHaveTextContent("review requested");
    await userEvent.click(within(list).getByRole("tab", { name: /Assigned/ }));
    expect(
      await within(list).findByRole("button", {
        name: "#7 Save the page size",
      }),
    ).toHaveTextContent("assigned");
    await userEvent.click(within(list).getByRole("tab", { name: /Mine/ }));
    await userEvent.click(
      await within(list).findByRole("button", { name: "#8 My paging" }),
    );
    expect(
      await within(side).findByRole("button", { name: "Merge…" }),
    ).toBeInTheDocument();
    expect(
      within(side).queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument(); // not on your own
    await userEvent.click(
      within(side).getByRole("button", { name: /Back to the list/ }),
    );
    await userEvent.click(
      await within(side).findByRole("tab", { name: /To review/ }),
    );
    await userEvent.click(
      await within(side).findByRole("button", {
        name: "#7 Save the page size",
      }),
    );
    expect(await within(side).findByText("CI 1/1")).toBeInTheDocument();
    expect(
      within(side).getByRole("button", { name: "Approve" }),
    ).toBeInTheDocument();
    expect(
      within(side).queryByRole("button", { name: "Merge…" }),
    ).not.toBeInTheDocument(); // only on your own
    // the folder tree: src with its two files (src/test is its own folder), viewed counts per folder
    const tree = within(side).getByRole("tree", { name: "Changed files" });
    expect(
      within(tree).getByRole("treeitem", { name: /^src / }),
    ).toHaveTextContent("0/2");
    expect(
      within(tree).getByRole("treeitem", { name: /^test/ }),
    ).toHaveTextContent("0/1");
    await userEvent.click(
      within(tree).getByRole("checkbox", {
        name: "Viewed src/test/PrefsTest.kt",
      }),
    );
    await waitFor(() =>
      expect(
        within(tree).getByRole("treeitem", { name: /^test/ }),
      ).toHaveTextContent("1/1"),
    );
    const tab = await openFile(side, "src/Prefs.kt");
    expect(
      within(tab).getByRole("region", { name: "Changes in src/Prefs.kt" }),
    ).toBeInTheDocument();
    expect(
      await within(tab).findByText("Should this be saved?"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /Prefs\.kt \(7\)/ }),
    ).toBeInTheDocument();
  });

  it("comments on a line in the file tab, resolves a thread, approves with one click and submits the review", async () => {
    const s = reviewServer();
    const side = await openPr(7);
    const tab = await openFile(side, "src/Prefs.kt");
    await userEvent.click(
      await within(tab).findByRole("button", { name: "Comment on line 4" }),
    );
    await userEvent.type(
      within(tab).getByRole("textbox", { name: "Comment on src/Prefs.kt:4" }),
      "issue (blocking): save it in the repository",
    );
    await userEvent.click(
      within(tab).getByRole("button", { name: "Add comment" }),
    );
    await waitFor(() =>
      expect(
        s.calls.find((c) => c.path.endsWith("/drafts"))?.body,
      ).toMatchObject({
        key: "pr:7",
        path: "src/Prefs.kt",
        line: 4,
        side: "RIGHT",
      }),
    );
    expect(
      await within(tab).findByText(
        "issue (blocking): save it in the repository",
      ),
    ).toBeInTheDocument();
    expect(
      within(side).getByRole("button", { name: "Submit review (1)" }),
    ).toBeInTheDocument();
    await userEvent.click(within(tab).getByRole("button", { name: "Resolve" }));
    await waitFor(() =>
      expect(
        s.calls.some((c) => c.path.includes("/threads/PRRT_1/resolve")),
      ).toBe(true),
    );

    await userEvent.click(
      within(side).getByRole("button", { name: "Submit review (1)" }),
    );
    const dlg = await screen.findByRole("dialog", {
      name: "Submit your review of #7",
    });
    await userEvent.type(
      within(dlg).getByRole("textbox", { name: "Your review" }),
      "One blocker.",
    );
    await userEvent.click(
      within(dlg).getByRole("radio", { name: /Request changes/ }),
    );
    await userEvent.click(
      within(dlg).getByRole("button", { name: "Submit review" }),
    );
    await waitFor(() =>
      expect(s.calls.find((c) => c.path.endsWith("/submit"))?.body).toEqual({
        key: "pr:7",
        event: "REQUEST_CHANGES",
        body: "One blocker.",
      }),
    );
    expect(
      await within(side).findByText("changes requested by me"),
    ).toBeInTheDocument();

    // Approve with nothing pending: one click and a confirm
    await userEvent.click(
      within(side).getByRole("button", { name: "Approve" }),
    );
    await userEvent.click(
      within(
        within(side).getByRole("group", { name: "Confirm approve" }),
      ).getByRole("button", { name: "Approve" }),
    );
    await waitFor(() =>
      expect(
        s.calls.filter((c) => c.path.endsWith("/submit")).at(-1)?.body,
      ).toEqual({ key: "pr:7", event: "APPROVE", body: "" }),
    );
    expect(await within(side).findByText("approved by me")).toBeInTheDocument();
  });

  it("merges your own pull request after you pick how", async () => {
    const s = reviewServer();
    const side = await openTool();
    await userEvent.click(within(side).getByRole("tab", { name: /Mine/ }));
    await userEvent.click(
      await within(side).findByRole("button", { name: "#8 My paging" }),
    );
    await userEvent.click(
      await within(side).findByRole("button", { name: "Merge…" }),
    );
    const dlg = await screen.findByRole("dialog", { name: "Merge #8" });
    await userEvent.click(
      within(dlg).getByRole("radio", { name: "Squash and merge" }),
    );
    expect(
      within(dlg).getByRole("checkbox", {
        name: /Delete the branch feat\/paging/,
      }),
    ).toBeChecked();
    await userEvent.click(
      within(dlg).getByRole("button", { name: "Merge into main" }),
    );
    await waitFor(() =>
      expect(s.calls.find((c) => c.path.endsWith("/merge"))?.body).toEqual({
        key: "pr:8",
        method: "squash",
        delete_branch: true,
      }),
    );
    expect(await within(side).findByText("merged")).toBeInTheDocument();
    expect(
      within(side).queryByRole("button", { name: "Merge…" }),
    ).not.toBeInTheDocument();
  });

  it("moves with IntelliJ's keys: next change, next file, go to declaration, find usages, back, Find action", async () => {
    reviewServer();
    const side = await openPr(7);
    const tab = await openFile(side, "src/Prefs.kt");
    await within(tab).findByRole("region", { name: "Changes in src/Prefs.kt" });
    fireEvent.keyDown(window, { key: "F7", code: "F7" });
    await waitFor(() =>
      expect(tab.querySelector(".rv-dv")?.getAttribute("data-current")).toBe(
        "RIGHT:2",
      ),
    );
    fireEvent.keyDown(window, {
      key: "ArrowRight",
      code: "ArrowRight",
      ctrlKey: true,
      altKey: true,
    });
    const next = await screen.findByRole("region", {
      name: "src/test/PrefsTest.kt in review #7",
    });
    expect(
      within(next).getByRole("region", {
        name: "Changes in src/test/PrefsTest.kt",
      }),
    ).toBeInTheDocument();
    // ⌘B on a selected name: one declaration → the whole file opens at it
    select("savePageSize");
    fireEvent.keyDown(window, { key: "b", code: "KeyB", ctrlKey: true });
    expect(
      await screen.findByRole("region", {
        name: "src/Prefs.kt in the reviewed commit",
      }),
    ).toBeInTheDocument();
    // ⌥F7: usages, with the declaration and the test
    fireEvent.keyDown(window, { key: "F7", code: "F7", altKey: true });
    const pop = await screen.findByRole("dialog", {
      name: "Usages of savePageSize",
    });
    expect(
      within(pop)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual([
      expect.stringContaining("src/Prefs.kt"),
      expect.stringContaining("src/test/PrefsTest.kt"),
    ]);
    fireEvent.keyDown(within(pop).getByRole("listbox"), { key: "ArrowDown" });
    fireEvent.keyDown(within(pop).getByRole("listbox"), { key: "Enter" });
    expect(
      await screen.findByRole("region", {
        name: "Changes in src/test/PrefsTest.kt",
      }),
    ).toBeInTheDocument();
    // ⌘[ back to the declaration
    fireEvent.keyDown(window, { key: "[", code: "BracketLeft", ctrlKey: true });
    expect(
      await screen.findByRole("region", {
        name: "src/Prefs.kt in the reviewed commit",
      }),
    ).toBeInTheDocument();
    // ⇧⌘A: every action with its key, and the keymap switch
    fireEvent.keyDown(window, {
      key: "A",
      code: "KeyA",
      ctrlKey: true,
      shiftKey: true,
    });
    const fa = await screen.findByRole("dialog", { name: "Find action" });
    await userEvent.type(
      within(fa).getByRole("textbox", { name: "Find an action" }),
      "usages",
    );
    expect(
      within(fa).getByRole("button", { name: /Find usages/ }),
    ).toHaveTextContent("Alt+F7");
    await userEvent.click(within(fa).getByRole("button", { name: "VS Code" }));
    expect(
      within(fa).getByRole("button", { name: /Find usages/ }),
    ).toHaveTextContent("Shift+F12");
    await userEvent.click(
      within(fa).getByRole("button", { name: "IntelliJ (macOS)" }),
    );
  });

  it("keel explains the change, and a checked finding on its line becomes a pending comment", async () => {
    const s = reviewServer();
    let side = await openPr(7);
    await userEvent.click(within(side).getByRole("tab", { name: /Overview/ }));
    await userEvent.click(
      within(side).getByRole("button", { name: "Explain this change" }),
    );
    await waitFor(() =>
      expect(s.calls.some((c) => c.path.endsWith("/ai/overview"))).toBe(true),
    );
    expect(
      await within(side).findByText("keel reads the change…"),
    ).toBeInTheDocument();

    s.setAi(FINDINGS);
    await userEvent.click(
      within(side).getByRole("button", { name: /Back to the list/ }),
    );
    await userEvent.click(
      await within(side).findByRole("button", {
        name: "#7 Save the page size",
      }),
    );
    side = screen.getByRole("region", { name: "Review" });
    await userEvent.click(
      await within(side).findByRole("tab", { name: /Overview/ }),
    );
    expect(
      await within(side).findByText("Saves the page size per user."),
    ).toBeInTheDocument();
    expect(within(side).getByText("risk medium")).toBeInTheDocument();
    expect(
      within(side).getByText("Orders ─▶ Prefs ─▶ cache"),
    ).toBeInTheDocument();
    await userEvent.click(within(side).getByRole("tab", { name: /Findings/ }));
    expect(
      within(side).getByText("1 blocking · 0 should fix"),
    ).toBeInTheDocument();

    await userEvent.click(within(side).getByRole("tab", { name: /Changes/ }));
    const tab = await openFile(side, "src/Prefs.kt");
    const inline = await within(tab).findByText(
      (_, el) =>
        el?.getAttribute("data-finding") === "f_1" &&
        el.classList.contains("rv-ai"),
    );
    await userEvent.click(
      within(inline).getByRole("button", { name: "Add as comment" }),
    );
    await waitFor(() =>
      expect(
        s.calls.find((c) => c.path.endsWith("/drafts"))?.body,
      ).toMatchObject({ path: "src/Prefs.kt", line: 4, finding_id: "f_1" }),
    );
    const draft = s.calls.find((c) => c.path.endsWith("/drafts"))!.body as {
      body: string;
    };
    expect(draft.body).toContain(
      "**issue (blocking):** page size is never saved",
    );
    expect(draft.body).toContain(
      "```suggestion\n        repository.save(user, size)\n```",
    );
    await waitFor(() =>
      expect(
        s.calls.find((c) => c.path.endsWith("/ai/findings/f_1"))?.body,
      ).toMatchObject({ decision: "commented" }),
    );
  });
});
