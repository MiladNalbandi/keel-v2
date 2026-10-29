// v0.14.0 the Code Review plugin, IDE style: the Review tool window (this branch, the list with filters, one pull
// request with Approve / Submit / Merge your own and its tabs: the folder tree, commits, keel's overview and findings,
// threads), each file in an editor tab (the diff with threads, pending comments and findings on their lines), and
// IntelliJ's keys (F7, ⌥⌘→, ⌘B, ⌥F7, ⌘[, ⇧⌘A). Moved from web/src/test with the plugin: it runs inside keel's web,
// as the full image has it (web/src/test/setup.ts runs every plugin's setup()).

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
import { describe, expect, it, vi } from "vitest";
import {
  registerSlot,
  slotItems,
  SLOTS,
  type CodeActivityItem,
} from "@keel/web-sdk";
// keel's own app and test harness: this test runs the Review window inside keel's web, as keel loads it
import { App } from "../../../../web/src/App";
import {
  actionFor,
  keyLabel,
  matches,
  readKeymap,
  saveKeymap,
  wordAt,
} from "../keymap";
import type { AiState, Draft, ReviewView } from "../reviewApi";
import { db, server } from "../../../../web/src/test/setup";

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
      approved: ["ana"],
      changes_requested: [],
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
    // 0.18.57 the overview's diagrams (test/diagrams.test.tsx tests the tabs)
    http.get(`${R}/diagrams`, () =>
      HttpResponse.json({ database: { shown: false, count: 0 }, system: { nodes: [], edges: [], groups: [], externals: [] } }),
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
        side: (b.side as Draft["side"]) ?? "RIGHT",
        body: b.body as string,
        finding_id: (b.finding_id as string) ?? null,
        created_at: "",
        start_line: (b.start_line as number) ?? null,
        start_side: (b.start_side as Draft["side"]) ?? null,
      };
      views[k] = { ...views[k], drafts: [...views[k].drafts, d] };
      return HttpResponse.json(d);
    }),
    http.post(`${R}/drafts/move`, async ({ request }) => {
      const b = await rec(request);
      const from = b.from as string;
      const to = b.to as string;
      views[to] = {
        ...views[to],
        drafts: [...views[to].drafts, ...views[from].drafts],
      };
      views[from] = { ...views[from], drafts: [] };
      return HttpResponse.json(views[to]);
    }),
    http.put(`${R}/drafts/:id`, async ({ request, params }) => {
      const b = await rec(request);
      const k = Object.keys(views).find((x) =>
        views[x].drafts.some((d) => d.id === params.id),
      )!;
      const d = {
        ...views[k].drafts.find((x) => x.id === params.id)!,
        body: b.body as string,
      };
      views[k] = {
        ...views[k],
        drafts: views[k].drafts.map((x) => (x.id === d.id ? d : x)),
      };
      return HttpResponse.json(d);
    }),
    http.delete(`${R}/drafts/:id`, async ({ request, params }) => {
      await rec(request);
      for (const k of Object.keys(views))
        views[k] = {
          ...views[k],
          drafts: views[k].drafts.filter((x) => x.id !== params.id),
        };
      return HttpResponse.json({ ok: true });
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
    http.post(`${R}/ai/findings/:fid/fix`, async ({ request, params }) => {
      await rec(request);
      ai = { ...ai, fixes: { ...(ai.fixes ?? {}), [String(params.fid)]: { flow_id: "t-fix-9", workflow: "small" } } } as AiState;
      return HttpResponse.json({ flow_id: "t-fix-9", title: "Fix", branch: "feat/x", workflow: "small", note: "", state: ai });
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
    views,
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
      not_checked: [
        {
          id: "f_2",
          title: "callers may pass null",
          severity: "should_fix",
          category: "correctness",
          path: "src/Prefs.kt",
          line: 2,
          side: "RIGHT",
          why: "I could not check the callers",
          fix: "",
          pre_existing: false,
          reviewer: "A",
          check: "not checked",
          check_why: "its reviewer could not see the code it is about",
        },
      ],
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

describe("Code › Review: the CI box", () => {
  it("0.18.70 looks again every 30 s while a check runs, and stops when they end", async () => {
    reviewServer();
    let n = 0;
    let state = "in_progress";
    server.use(
      http.get("/api/projects/:pid/review/view", () => {
        n += 1;
        return HttpResponse.json({ ...viewOf(7), checks: [{ name: "build", state }] });
      }),
    );
    const timers: { fn: () => void; ms: number }[] = [];
    const real = window.setInterval.bind(window);
    const spy = vi.spyOn(window, "setInterval").mockImplementation(((fn: () => void, ms?: number) => {
      timers.push({ fn, ms: ms ?? 0 });
      return real(() => {}, 1_000_000);
    }) as typeof window.setInterval);
    try {
      const side = await openPr(7);
      expect(await within(side).findByText("CI 0/1")).toBeInTheDocument();
      const poll = timers.filter((t) => t.ms === 30_000);
      expect(poll.length).toBeGreaterThan(0);
      const before = n;
      state = "success";
      poll[poll.length - 1].fn();
      await waitFor(() => expect(n).toBeGreaterThan(before));
      expect(await within(side).findByText("CI 1/1")).toBeInTheDocument();
    } finally {
      spy.mockRestore();
    }
  });
});

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
    // 0.18.60 who approved your own pull request, in the list
    expect(
      await within(list).findByRole("button", { name: "#8 My paging" }),
    ).toHaveTextContent("approved by ana");
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
      within(dlg).getByRole("button", { name: "Submit and request changes" }),
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
    // the file's tab shows first, its diff once that loads
    expect(
      await within(next).findByRole("region", {
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
    // keel 0.18.57: what nobody could prove is listed apart, never as a finding
    expect(
      within(side).getByText(/Not checked: nobody could prove or disprove/),
    ).toBeInTheDocument();
    // 0.18.74 Fix it here too, as on the Problems tab: a flow fixes it, and the card links to it
    await userEvent.click(within(side).getAllByRole("button", { name: /^Fix it/ })[0]);
    await waitFor(() => expect(s.calls.some((c) => c.path.endsWith("/fix"))).toBe(true));
    expect(await within(side).findByRole("link", { name: "Open flow ▸" })).toHaveAttribute("href", "#/flow/t-fix-9");

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

  it("opens a review's link that came before the plugin's web part had loaded", async () => {
    reviewServer();
    db.plugins.review = true;
    // keel loads a plugin's web part at start; the Code page may open first
    const view = slotItems<CodeActivityItem>(SLOTS.codeActivity).find(
      (a) => a.id === "review",
    )!;
    registerSlot(SLOTS.codeActivity, view)();
    try {
      // a page load with the link (jsdom tells about a new hash with a hashchange; a page load does not)
      await new Promise<void>((done) => {
        const on = (e: HashChangeEvent) => {
          if (!e.newURL.endsWith("#/repo/@review/pr:7")) return;
          window.removeEventListener("hashchange", on);
          done();
        };
        window.addEventListener("hashchange", on);
        location.hash = "#/repo/@review/pr:7";
      });
      render(<App />);
      expect(
        await screen.findByRole("button", { name: "Explorer" }),
      ).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Review" })).toBeNull();
      // the part has loaded: the link opens the review in its tool window
      act(() => {
        registerSlot(SLOTS.codeActivity, view);
      });
      expect(
        await screen.findByRole("region", { name: "Review" }),
      ).toBeVisible();
      expect(sessionStorage.getItem("keel2.review.sel.ludus-engine")).toBe(
        "pr:7",
      );
    } finally {
      registerSlot(SLOTS.codeActivity, view);
    }
  });
});

describe("Code › Review: comments on several lines and the Pending tab", () => {
  it("picks several lines with shift-click and with a drag, in the diff and in the whole file, and shows a thread over its range", async () => {
    const s = reviewServer();
    s.views["pr:7"] = {
      ...s.views["pr:7"],
      threads: s.views["pr:7"].threads.map((t) => ({
        ...t,
        start_line: 2,
        start_side: "RIGHT" as const,
      })),
    };
    const side = await openPr(7);
    const tab = await openFile(side, "src/Prefs.kt");
    // the thread on lines 2–3 sits under line 3 and marks both rows
    expect(await within(tab).findByText("lines 2–3")).toBeInTheDocument();
    expect(tab.querySelectorAll(".rng").length).toBe(2);

    // click line 2, shift-click line 4: one comment on lines 2–4
    await userEvent.click(
      within(tab).getByRole("button", { name: "Comment on line 2" }),
    );
    fireEvent.click(
      within(tab).getByRole("button", { name: "Comment on line 4" }),
      { shiftKey: true },
    );
    const box = await within(tab).findByRole("textbox", {
      name: "Comment on src/Prefs.kt:2–4",
    });
    expect(tab.querySelectorAll(".sel").length).toBe(3);
    await userEvent.type(box, "Move these three lines to a store");
    await userEvent.click(
      within(tab).getByRole("button", { name: "Add comment" }),
    );
    await waitFor(() =>
      expect(
        s.calls.find((c) => c.path.endsWith("/drafts"))?.body,
      ).toMatchObject({
        key: "pr:7",
        path: "src/Prefs.kt",
        start_line: 2,
        start_side: "RIGHT",
        line: 4,
        side: "RIGHT",
      }),
    );
    expect(await within(tab).findByText("lines 2–4")).toBeInTheDocument();

    // drag over the numbers from line 3 to line 5
    fireEvent.mouseDown(
      within(tab).getByRole("button", { name: "Comment on line 3" }),
    );
    fireEvent.mouseOver(
      within(tab).getByRole("button", { name: "Comment on line 5" }),
    );
    fireEvent.mouseUp(
      within(tab).getByRole("button", { name: "Comment on line 5" }),
    );
    expect(
      await within(tab).findByRole("textbox", {
        name: "Comment on src/Prefs.kt:3–5",
      }),
    ).toBeInTheDocument();
    await userEvent.click(within(tab).getByRole("button", { name: "Cancel" }));

    // the whole file: its line numbers take a range too
    await userEvent.click(
      within(tab).getByRole("button", { name: "Whole file" }),
    );
    await userEvent.click(
      await within(tab).findByRole("button", { name: "Comment on line 1" }),
    );
    fireEvent.click(
      within(tab).getByRole("button", { name: "Comment on line 3" }),
      { shiftKey: true },
    );
    await userEvent.type(
      await within(tab).findByRole("textbox", {
        name: "Comment on src/Prefs.kt:1–3",
      }),
      "The class does two things",
    );
    await userEvent.click(
      within(tab).getByRole("button", { name: "Add comment" }),
    );
    await waitFor(() =>
      expect(
        s.calls.filter((c) => c.path.endsWith("/drafts")).at(-1)?.body,
      ).toMatchObject({ start_line: 1, line: 3, side: "RIGHT" }),
    );
  });

  it("lists every pending comment in the Pending tab, edits and deletes them, and sends them with Approve as one review", async () => {
    const s = reviewServer();
    s.views["pr:7"] = {
      ...s.views["pr:7"],
      drafts: [
        {
          id: "dr_a",
          path: "src/Prefs.kt",
          line: 4,
          side: "RIGHT",
          start_line: 2,
          start_side: "RIGHT",
          body: "Move these to a store",
          created_at: "",
        },
        {
          id: "dr_b",
          path: null,
          line: null,
          side: "RIGHT",
          body: "Nice tests",
          created_at: "",
        },
      ],
      threads: [
        ...s.views["pr:7"].threads,
        {
          id: "PRRT_9",
          path: "src/Prefs.kt",
          line: 5,
          side: "RIGHT",
          resolved: false,
          outdated: false,
          pending: true,
          reply_to: "91",
          comments: [
            { id: "91", author: "me", body: "Started on github.com", at: "" },
          ],
        },
      ],
    };
    const side = await openPr(7);
    // the pending comments are one click away, from the tab and from the Changes footer
    expect(
      within(side).getByRole("button", { name: "Submit review (3)" }),
    ).toBeInTheDocument();
    await userEvent.click(within(side).getByRole("tab", { name: /Pending/ }));
    const list = within(side).getByRole("region", { name: "Pending comments" });
    expect(within(list).getByText("Lines 2–4")).toBeInTheDocument();
    expect(within(list).getByText("Move these to a store")).toBeInTheDocument();
    expect(within(list).getByText("Nice tests")).toBeInTheDocument();
    expect(
      within(list).getByText("In your pending review on GitHub"),
    ).toBeInTheDocument();
    expect(within(list).getByText("Started on github.com")).toBeInTheDocument();

    // edit one, delete another
    await userEvent.click(within(list).getAllByRole("button", { name: "Edit" })[0]);
    const edit = within(list).getByRole("textbox", {
      name: "Edit the pending comment",
    });
    await userEvent.clear(edit);
    await userEvent.type(edit, "Move these to the repository");
    await userEvent.click(within(list).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(
        s.calls.find((c) => c.method === "PUT" && c.path.endsWith("/drafts/dr_a"))
          ?.body,
      ).toEqual({ body: "Move these to the repository" }),
    );
    await userEvent.click(within(list).getAllByRole("button", { name: "Delete" })[1]);
    await waitFor(() =>
      expect(
        s.calls.some(
          (c) => c.method === "DELETE" && c.path.endsWith("/drafts/dr_b"),
        ),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(within(list).queryByText("Nice tests")).not.toBeInTheDocument(),
    );

    // a comment on the whole change
    await userEvent.type(
      within(list).getByRole("textbox", { name: "A comment on the whole change" }),
      "Thanks!",
    );
    await userEvent.click(
      within(list).getByRole("button", { name: "Add pending comment" }),
    );
    await waitFor(() =>
      expect(
        s.calls.filter((c) => c.path.endsWith("/drafts")).at(-1)?.body,
      ).toMatchObject({ key: "pr:7", path: null, line: null, body: "Thanks!" }),
    );

    // submit them all with Approve, as one review
    const form = within(side).getByRole("form", { name: "Submit your review" });
    expect(form).toHaveTextContent(
      "1 line comment and 1 general comment, and 1 from your pending review on GitHub, go with it.",
    );
    await userEvent.click(within(form).getByRole("radio", { name: /Approve/ }));
    await userEvent.type(
      within(form).getByRole("textbox", { name: "Your review" }),
      "Good, two notes.",
    );
    await userEvent.click(
      within(form).getByRole("button", { name: "Submit and approve" }),
    );
    await waitFor(() =>
      expect(s.calls.find((c) => c.path.endsWith("/submit"))?.body).toEqual({
        key: "pr:7",
        event: "APPROVE",
        body: "Good, two notes.",
      }),
    );
    expect(await within(side).findByText("approved by me")).toBeInTheDocument();
  });

  it("says why your own pull request cannot be approved", async () => {
    reviewServer();
    const side = await openTool();
    await userEvent.click(await within(side).findByRole("tab", { name: /Mine/ }));
    await userEvent.click(
      await within(side).findByRole("button", { name: "#8 My paging" }),
    );
    await userEvent.click(
      await within(side).findByRole("tab", { name: /Pending/ }),
    );
    const form = within(side).getByRole("form", { name: "Submit your review" });
    expect(form).toHaveTextContent("This is your own pull request");
    expect(within(form).getByRole("radio", { name: /Approve/ })).toBeDisabled();
    expect(within(form).getByRole("radio", { name: /Comment/ })).toBeChecked();
  });

  it("0.18.65 a branch review warns when the local base went its own way, and Compare with picks the base", async () => {
    const s = reviewServer();
    const remote: ReviewView = {
      ...s.views["pr:7"],
      key: "branch:feat/paging",
      kind: "branch",
      number: null,
      can_post: false,
      threads: [],
      base: "github/main",
      base_warning:
        "Your local master is 3 commits behind and 2 ahead of github/main: this review compares against github/main.",
      base_options: [
        { id: "remote", ref: "refs/remotes/github/main", label: "github/main" },
        { id: "local", ref: "refs/heads/master", label: "master" },
      ],
      base_choice: "remote",
    };
    s.views["branch:feat/paging"] = remote;
    const local: ReviewView = {
      ...remote,
      base: "master",
      base_sha: "c".repeat(40),
      base_choice: "local",
      files: [
        ...remote.files,
        { path: "landed/m1.txt", status: "A", added: 1, removed: 0, from: null, binary: false },
      ],
      base_warning:
        "Your local master is 3 commits behind and 2 ahead of github/main: this review compares against your local master, so it may show other changes' files as this branch's.",
    };
    const picked: unknown[] = [];
    server.use(
      http.get("/api/projects/:pid/review/branch", () =>
        HttpResponse.json({
          branch: "feat/paging", base: "github/main", ahead: 1, files: 2, added: 6, removed: 0, pr: null, note: null,
          base_warning: remote.base_warning,
        }),
      ),
      http.put("/api/projects/:pid/review/base", async ({ request }) => {
        const b = (await request.json()) as { base: string };
        picked.push(b);
        return HttpResponse.json(b.base === "local" ? local : remote);
      }),
    );
    const side = await openTool();
    expect(await within(side).findByTestId("branch-base-warning")).toHaveTextContent(
      "Your local master is 3 commits behind and 2 ahead of github/main: this review compares against github/main.",
    );
    await userEvent.click(await within(side).findByRole("button", { name: "Review this branch" }));
    const warning = await within(side).findByTestId("review-base-warning");
    expect(warning).toHaveTextContent("this review compares against github/main.");
    const pick = within(side).getByRole("combobox", { name: "Compare with" });
    expect(pick).toHaveValue("remote");
    await userEvent.selectOptions(pick, "local");
    await waitFor(() => expect(picked).toEqual([{ key: "branch:feat/paging", base: "local" }]));
    await waitFor(() =>
      expect(within(side).getByTestId("review-base-warning")).toHaveTextContent(
        "compares against your local master, so it may show other changes' files",
      ),
    );
    expect(within(side).getByRole("combobox", { name: "Compare with" })).toHaveValue("local");
  });

  it("moves a branch review's pending comments to its pull request, where they can be sent", async () => {
    const s = reviewServer();
    s.views["branch:feat/paging"] = {
      ...s.views["pr:7"],
      key: "branch:feat/paging",
      kind: "branch",
      number: null,
      can_post: false,
      threads: [],
      branch_pr: 7,
      drafts: [
        {
          id: "dr_x",
          path: "src/Prefs.kt",
          line: 4,
          side: "RIGHT",
          body: "Written on the branch",
          created_at: "",
        },
      ],
    };
    const side = await openTool();
    await userEvent.click(
      await within(side).findByRole("button", { name: "Commits only" }),
    );
    await userEvent.click(
      await within(side).findByRole("button", { name: "Pending (1)" }),
    );
    expect(within(side).getByText("Written on the branch")).toBeInTheDocument();
    await userEvent.click(
      within(side).getByRole("button", { name: "Move to #7 and review it" }),
    );
    await waitFor(() =>
      expect(
        s.calls.find((c) => c.path.endsWith("/drafts/move"))?.body,
      ).toEqual({ from: "branch:feat/paging", to: "pr:7" }),
    );
    expect(
      await within(side).findByRole("button", { name: "Submit review (1)" }),
    ).toBeInTheDocument();
    expect(
      within(side).getByRole("form", { name: "Submit your review" }),
    ).toBeInTheDocument();
  });
});

describe("the Review view's width", () => {
  it("has its own width on the Code page's splitter: remembered, and Source control and the others keep theirs", async () => {
    localStorage.removeItem("keel2.review.sideW");
    await openTool();
    const split = () =>
      screen.getByRole("separator", { name: "Resize the side bar" });
    expect(split()).toHaveAttribute("aria-valuenow", "440");
    expect(split()).toHaveAttribute("aria-valuemin", "280");
    fireEvent.keyDown(split(), { key: "ArrowRight" });
    expect(split()).toHaveAttribute("aria-valuenow", "456");
    expect(localStorage.getItem("keel2.review.sideW")).toBe("456");
    expect(document.querySelector(".ide")).toHaveClass("side-own");
    await userEvent.click(
      screen.getByRole("button", { name: "Source control" }),
    );
    expect(split()).toHaveAttribute("aria-valuenow", "380");
    await userEvent.click(screen.getByRole("button", { name: "Explorer" }));
    expect(split()).toHaveAttribute("aria-valuenow", "280");
    expect(document.querySelector(".ide")).not.toHaveClass("side-own");
    await userEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(split()).toHaveAttribute("aria-valuenow", "456");
    fireEvent.doubleClick(split());
    expect(split()).toHaveAttribute("aria-valuenow", "440");
  });

  it("reads the width an earlier 0.18.38 build kept (a bare number)", async () => {
    localStorage.setItem("keel2.review.sideW", "512");
    await openTool();
    expect(
      screen.getByRole("separator", { name: "Resize the side bar" }),
    ).toHaveAttribute("aria-valuenow", "512");
  });
});

describe("the Review view after you leave the Code page and come back", () => {
  const PID = "ludus-engine";
  // another page (Flow) unmounts the Code page; the menu brings it back
  async function leaveAndComeBack() {
    act(() => {
      location.hash = "#/flow";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Review" })).toBeNull(),
    );
    act(() => {
      location.hash = "#/repo";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    return screen.findByRole("region", { name: "Review" });
  }

  it("keeps the open review, its tab, the thread filter and the reply you were writing", async () => {
    reviewServer();
    let side = await openPr(7);
    await userEvent.click(within(side).getByRole("tab", { name: /^Threads/ }));
    await userEvent.click(
      await within(side).findByRole("button", { name: /^Open · / }),
    );
    await userEvent.click(within(side).getByRole("button", { name: "Reply" }));
    await userEvent.type(
      within(side).getByRole("textbox", { name: "Your reply" }),
      "On purpose",
    );

    side = await leaveAndComeBack();
    expect(
      await within(side).findByRole("button", { name: /Back to the list/ }),
    ).toBeInTheDocument();
    expect(within(side).getByRole("tab", { name: /^Threads/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      within(side).getByRole("button", { name: /^Open · / }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      within(side).getByRole("textbox", { name: "Your reply" }),
    ).toHaveValue("On purpose");

    // Cancel drops the draft: it is not back the next time
    await userEvent.click(within(side).getByRole("button", { name: "Cancel" }));
    side = await leaveAndComeBack();
    await within(side).findByRole("button", { name: /Back to the list/ });
    expect(
      within(side).queryByRole("textbox", { name: "Your reply" }),
    ).toBeNull();
    expect(
      Object.keys(localStorage).filter((k) =>
        k.startsWith(`keel2.review.draft.${PID}.`),
      ),
    ).toEqual([]);
  });

  it("keeps the list's filter", async () => {
    reviewServer();
    let side = await openTool();
    await userEvent.click(
      await within(side).findByRole("tab", { name: /^Mine/ }),
    );
    side = await leaveAndComeBack();
    expect(
      await within(side).findByRole("tab", { name: /^Mine/ }),
    ).toHaveAttribute("aria-selected", "true");
  });

  it("goes back to the list quietly when the remembered review is not there any more", async () => {
    const s = reviewServer();
    server.use(
      http.get("/api/projects/:pid/review/view", ({ request }) => {
        const k = new URL(request.url).searchParams.get("key");
        return k === "pr:99"
          ? HttpResponse.json(
              { error: "GitHub did not find pull request 99." },
              { status: 404 },
            )
          : HttpResponse.json(s.views[k ?? "pr:7"]);
      }),
    );
    db.plugins.review = true;
    sessionStorage.setItem(`keel2.repo.activity.${PID}`, '"review"');
    sessionStorage.setItem(`keel2.review.sel.${PID}`, "pr:99");
    sessionStorage.setItem(`keel2.review.tab.${PID}.pr:99`, '"threads"');
    location.hash = "#/repo";
    render(<App />);
    const side = await screen.findByRole("region", { name: "Review" });
    expect(
      await within(side).findByRole("button", { name: "Review this branch" }),
    ).toBeInTheDocument();
    expect(within(side).queryByRole("alert")).toBeNull();
    expect(sessionStorage.getItem(`keel2.review.sel.${PID}`)).toBeNull();
  });

  it("still shows the error when a review you opened yourself is not found", async () => {
    const s = reviewServer();
    server.use(
      http.get("/api/projects/:pid/review/view", () =>
        HttpResponse.json(
          { error: "GitHub did not find pull request 7." },
          { status: 404 },
        ),
      ),
    );
    void s;
    const side = await openTool();
    await userEvent.click(
      await within(side).findByRole("button", { name: /^#7 / }),
    );
    expect(
      await within(side).findByText(/did not find pull request 7/),
    ).toBeInTheDocument();
  });
});

describe("threads in the code (the file tab)", () => {
  const PID = "ludus-engine";
  const judged: AiState = {
    overview: null,
    findings: null,
    decisions: {},
    config: {
      judge: {
        mode: "quick",
        workflow: "review",
        allow_harder: true,
        token_cap: 25000,
        agent: "pr-reviewer",
      },
      address_workflow: "small",
      buttons: [
        {
          id: "explain-simply",
          label: "Explain simply",
          mode: "ask",
          agent: "explorer",
        },
      ],
    },
    judges: {
      PRRT_1: {
        id: "rr_1",
        kind: "judge:PRRT_1",
        status: "done",
        stage: "done",
        head_sha: "a".repeat(40),
        stale: false,
        sessions: [],
        result: {
          stage: "done",
          items: [{ claim: "saving", verdict: "unclear", why: "no caller yet" }],
          reply: "It is saved on purpose.",
        },
        error: null,
        created_at: "",
        updated_at: "",
      },
    },
  };
  const head = (tab: HTMLElement) =>
    within(tab).getByRole("button", { name: /Should this be saved\?/ });
  const go = (hash: string) =>
    act(() => {
      location.hash = hash;
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });

  it("draws the Threads panel's box under the line: messages, keel's check, the buttons", async () => {
    const s = reviewServer();
    s.setAi(judged);
    const side = await openPr(7);
    const tab = await openFile(side, "src/Prefs.kt");
    expect(await within(tab).findByText("Should this be saved?")).toBeInTheDocument();
    const box = within(tab).getByRole("article", {
      name: "Thread on src/Prefs.kt line 3",
    });
    expect(box).toHaveClass("inline");
    // the code is right there: no excerpt and no file header
    expect(within(box).queryByLabelText(/The code of/)).toBeNull();
    expect(within(box).queryByRole("button", { name: /^Open src/ })).toBeNull();
    expect(
      within(box).getByRole("region", { name: "keel checked this thread" }),
    ).toHaveTextContent("UNCLEAR");
    for (const name of [
      "Reply",
      "✦ Is the reviewer right?",
      "✦ Address it",
      "Explain simply",
      "Resolve",
    ])
      expect(within(box).getByRole("button", { name })).toBeInTheDocument();
    // the head says who, what and that it waits for you
    expect(head(tab)).toHaveAttribute("aria-expanded", "true");
    expect(head(tab)).toHaveTextContent("bo");
    expect(head(tab)).toHaveTextContent("0 replies");
    expect(head(tab)).toHaveTextContent("Waiting for you");
  });

  it("folds by click and by keyboard, Close all / Open all, and keeps the choice after you leave and come back", async () => {
    reviewServer();
    const side = await openPr(7);
    let tab = await openFile(side, "src/Prefs.kt");
    await within(tab).findByText("Should this be saved?");
    await userEvent.click(head(tab));
    expect(head(tab)).toHaveAttribute("aria-expanded", "false");
    expect(within(tab).queryByText("Should this be saved?")).toBeNull();
    head(tab).focus();
    await userEvent.keyboard("{Enter}");
    expect(head(tab)).toHaveAttribute("aria-expanded", "true");
    await userEvent.keyboard(" ");
    expect(head(tab)).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(within(tab).getByRole("button", { name: "Open all" }));
    expect(head(tab)).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(within(tab).getByRole("button", { name: "Close all" }));
    expect(head(tab)).toHaveAttribute("aria-expanded", "false");
    expect(
      JSON.parse(sessionStorage.getItem(`keel2.review.inline.${PID}.pr:7`)!),
    ).toEqual({ PRRT_1: false });

    go("#/flow");
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: /in review #7/ })).toBeNull(),
    );
    go("#/repo");
    tab = await screen.findByRole("region", { name: "src/Prefs.kt in review #7" });
    expect(await within(tab).findByRole("button", { name: /Should this be saved\?/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("starts a resolved thread folded", async () => {
    const s = reviewServer();
    s.views["pr:7"] = {
      ...s.views["pr:7"],
      threads: s.views["pr:7"].threads.map((t) => ({ ...t, resolved: true })),
    };
    const side = await openPr(7);
    const tab = await openFile(side, "src/Prefs.kt");
    const h = await within(tab).findByRole("button", { name: /Should this be saved\?/ });
    expect(h).toHaveAttribute("aria-expanded", "false");
    expect(h).toHaveTextContent("Resolved");
    expect(h).not.toHaveTextContent("Waiting for you");
  });

  it("shares Dismiss and the reply draft with the Threads panel, without a reload", async () => {
    const s = reviewServer();
    s.setAi(judged);
    const side = await openPr(7);
    const tab = await openFile(side, "src/Prefs.kt");
    await userEvent.click(within(side).getByRole("tab", { name: /^Threads/ }));
    const check = (el: HTMLElement) =>
      within(el).queryByRole("region", { name: "keel checked this thread" });
    await waitFor(() => expect(check(side)).not.toBeNull());
    expect(check(tab)).not.toBeNull();

    // dismissed in the code: gone in the panel too
    await userEvent.click(
      within(tab).getByRole("button", { name: "Dismiss keel's check" }),
    );
    expect(check(tab)).toBeNull();
    expect(check(side)).toBeNull();
    // asked again in the panel: back in both, with no new check
    await userEvent.click(
      within(side).getByRole("button", { name: /Is the reviewer right/ }),
    );
    expect(check(side)).not.toBeNull();
    expect(check(tab)).not.toBeNull();
    // dismissed in the panel: gone in the code
    await userEvent.click(within(check(side)!).getByRole("button", { name: "Dismiss" }));
    expect(check(tab)).toBeNull();
    expect(s.calls.filter((c) => c.path.includes("/judge"))).toEqual([]);

    // a reply started in the code shows in the panel's box
    await userEvent.click(within(tab).getByRole("button", { name: "Reply" }));
    await userEvent.type(
      within(tab).getByRole("textbox", { name: "Your reply" }),
      "Saved on purpose",
    );
    expect(
      await within(side).findByRole("textbox", { name: "Your reply" }),
    ).toHaveValue("Saved on purpose");
  });
});
