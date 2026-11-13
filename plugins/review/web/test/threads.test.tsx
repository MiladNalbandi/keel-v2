// The review threads (Threads tab): sections and counts, your messages and the reviewer's, resolved threads folded to a
// line, "new" since your last visit, keel's check with every verdict, Use as my reply (fills the box, posts nothing),
// Address it (starts the flow), the extra buttons, the narrow layout, and the thread settings.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { AppProvider } from "../../../../web/src/state";
import { server } from "../../../../web/src/test/setup";
import type {
  AiRun,
  AiState,
  FixResult,
  JudgeResult,
  ReviewThread,
  ReviewView,
  ThreadSettingsView,
} from "../reviewApi";
import * as R from "../store";
import {
  ago,
  estimate,
  HIDDEN_RUNS_CAP,
  ThreadsView,
  waitsForYou,
} from "../Threads";
import { ThreadSettings } from "../ThreadSettings";
import { checkLines, sidesOf, verdictLabel } from "../verdict";

const PID = "p1";
const KEY = "pr:7";
const LONG =
  "src/main/kotlin/com/acme/shop/tracking/repository/TrackingSubscriptionRepository.kt";

const c = (id: string, author: string, body: string, at: string) => ({
  id,
  author,
  body,
  at,
});
const thread = (id: string, extra: Partial<ReviewThread>): ReviewThread => ({
  id,
  path: "src/Prefs.kt",
  line: 3,
  side: "RIGHT",
  resolved: false,
  outdated: false,
  comments: [],
  ...extra,
});

function view(): ReviewView {
  return {
    key: KEY,
    kind: "pr",
    number: 7,
    title: "Save the page size",
    author: "me",
    body: "",
    base: "main",
    branch: "feat/paging",
    base_sha: "b".repeat(40),
    head_sha: "a".repeat(40),
    url: null,
    state: "open",
    draft: false,
    same_repo: true,
    checks: [],
    approved: [],
    changes_requested: [],
    files: [{ path: LONG, status: "M", added: 2, removed: 1, binary: false }],
    added: 2,
    removed: 1,
    commits: [],
    threads: [
      thread("T1", {
        path: LONG,
        line: 3,
        start_line: 2,
        comments: [
          c("1", "bo", "Why a pure `UPDATE` query?", "2026-10-21T09:00:00Z"),
          c(
            "2",
            "me",
            "One raw UPDATE on purpose: the check and the write are one statement.",
            "2026-10-21T10:00:00Z",
          ),
          c(
            "3",
            "bo",
            "Ok, but do we have the right indexes?",
            "2026-10-22T09:00:00Z",
          ),
        ],
      }),
      thread("T2", {
        comments: [
          c("4", "bo", "Maybe a factory?", "2026-10-21T09:00:00Z"),
          c("5", "me", "Not needed here.", "2026-10-21T11:00:00Z"),
        ],
      }),
      thread("T3", {
        line: 22,
        resolved: true,
        comments: [
          c("6", "bo", "Do we need to keep this?", "2026-10-20T09:00:00Z"),
          c("7", "me", "yes, it dedupes retries", "2026-10-20T10:00:00Z"),
        ],
      }),
      thread("T4", {
        line: 40,
        comments: [
          c(
            "8",
            "ana",
            ["one", "two", "three", "four", "five", "six"].join("\n\n"),
            "2026-10-20T09:00:00Z",
          ),
        ],
      }),
    ],
    conversation: [],
    drafts: [],
    viewed: [],
    can_post: true,
    host: {
      kind: "github",
      host: "github.com",
      path: "acme/shop",
      web: "https://github.com/acme/shop",
    },
    me: "me",
    notes: [],
    mine: true,
  };
}

const DIFF = [
  `diff --git a/${LONG} b/${LONG}`,
  `--- a/${LONG}`,
  `+++ b/${LONG}`,
  "@@ -1,3 +1,4 @@",
  " class Repo {",
  "-    fun touch() = orm.save(row)",
  '+    fun touch() = db.update("UPDATE tracking_subscription SET vat_updated_at = :at WHERE resolver_id IN (SELECT id FROM resolvers WHERE carrier = :carrier)")',
  "+    // one statement",
  " }",
].join("\n");

const run = (
  result: JudgeResult,
  extra: Partial<AiRun<JudgeResult>> = {},
): AiRun<JudgeResult> => ({
  id: "rr_1",
  kind: "judge:T1",
  status: "done",
  stage: "done",
  head_sha: "a".repeat(40),
  stale: false,
  sessions: [],
  result,
  error: null,
  created_at: "2026-10-22T10:00:00Z",
  updated_at: "2026-10-22T10:00:00Z",
  ...extra,
});

const AI = (extra: Partial<AiState> = {}): AiState => ({
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
    buttons: [],
  },
  ...extra,
});

type Call = { method: string; path: string; body: unknown };

function api(ai: AiState = AI()) {
  const calls: Call[] = [];
  let state = ai;
  const rec = async (request: Request) => {
    let body: unknown = null;
    try {
      body = await request.clone().json();
    } catch {
      /* none */
    }
    calls.push({
      method: request.method,
      path: new URL(request.url).pathname,
      body,
    });
  };
  const B = "/api/projects/:pid/review";
  server.use(
    http.get(`${B}/diff`, () =>
      HttpResponse.json({
        path: LONG,
        against: "base",
        ref: "base…#7",
        diff: DIFF,
        binary: false,
        truncated: false,
      }),
    ),
    http.post(`${B}/threads/:tid/judge`, async ({ request, params }) => {
      await rec(request);
      state = {
        ...state,
        judges: {
          ...state.judges,
          [String(params.tid)]: run(
            { stage: "check" },
            { status: "running", result: { stage: "check" } },
          ),
        },
      };
      return HttpResponse.json(state);
    }),
    http.post(`${B}/threads/:tid/address`, async ({ request, params }) => {
      await rec(request);
      state = {
        ...state,
        addressed: {
          [String(params.tid)]: {
            flow_id: "t-1",
            title: "Address review",
            branch: "feat/paging",
            workflow: "small",
            at: "",
          },
        },
      };
      return HttpResponse.json({
        flow_id: "t-1",
        title: "Address review",
        branch: "feat/paging",
        workflow: "small",
        note: "The small flow works on feat/paging. It pushes nothing.",
        state,
      });
    }),
    http.post(`${B}/threads/:tid/buttons/:bid`, async ({ request }) => {
      await rec(request);
      return HttpResponse.json(state);
    }),
    http.post(`${B}/threads/:tid/reply`, async ({ request }) => {
      await rec(request);
      return HttpResponse.json(view());
    }),
    http.post(`${B}/threads/:tid/resolve`, async ({ request }) => {
      await rec(request);
      return HttpResponse.json(view());
    }),
  );
  return { calls };
}

function Host({ ai, v = view() }: { ai: AiState; v?: ReviewView }) {
  R.useReviewStore();
  const r = R.review(PID, KEY);
  return (
    <ThreadsView
      pid={PID}
      reviewKey={KEY}
      view={v}
      ai={r.ai ?? ai}
      running={r.running}
      onOpen={() => {}}
    />
  );
}

function show(ai: AiState = AI(), v?: ReviewView) {
  return render(
    <AppProvider>
      <Host ai={ai} v={v} />
    </AppProvider>,
  );
}

const card = (label: RegExp) => screen.getByRole("article", { name: label });

afterEach(() => {
  R.resetReviews();
});

describe("the review threads", () => {
  it("counts and groups the threads: waiting for you, open, resolved, all", async () => {
    api();
    show();
    expect(
      screen.getByRole("button", { name: "Waiting for you · 2" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open · 3" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Resolved · 1" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All · 4" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByRole("heading", { name: "Waiting for your answer · 2" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Open · 1" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Resolved · 1" }),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Waiting for you · 2" }),
    );
    expect(
      screen.queryByRole("heading", { name: "Open · 1" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /Resolved/ }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Resolved · 1" }));
    expect(
      screen.queryByRole("heading", { name: /Waiting/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Resolved · 1" }),
    ).toBeInTheDocument();
    // the rule
    const v = view();
    expect(v.threads.map((t) => waitsForYou(t, "me"))).toEqual([
      true,
      false,
      false,
      true,
    ]);
  });

  it("marks your own messages and names the reviewer; a long path keeps its file name; the code lines scroll in their box", async () => {
    api();
    show();
    const t1 = card(/Thread on src\/main\/kotlin.* line 2–3/);
    const mine = t1.querySelectorAll("[data-mine]");
    expect(mine).toHaveLength(1);
    expect(within(mine[0] as HTMLElement).getByText("You")).toBeInTheDocument();
    expect(within(t1).getAllByText(/reviewer ·/)).toHaveLength(2);
    expect(
      within(t1).getByText("UPDATE", { selector: "code" }),
    ).toBeInTheDocument(); // inline code
    const path = within(t1).getByRole("button", {
      name: `Open ${LONG} at line 3`,
    });
    expect(path).toHaveAttribute("title", LONG);
    expect(
      within(path).getByText("TrackingSubscriptionRepository.kt").tagName,
    ).toBe("B");
    const code = await within(t1)
      .findByRole("region", { name: /The code of .* at line 2–3/ })
      .catch(() => null);
    const pre =
      code ??
      (await waitFor(() => t1.querySelector("pre.rv-tcode") as HTMLElement));
    expect(pre).toHaveAttribute("tabindex", "0");
    expect(pre.textContent).toContain("UPDATE tracking_subscription");
    expect(pre.textContent).toContain("// one statement");
  });

  it("folds a resolved thread to one line and opens it", async () => {
    api();
    show();
    const row = screen.getByRole("button", { name: /src\/Prefs\.kt · L22/ });
    expect(row).toHaveAttribute("aria-expanded", "false");
    expect(row).toHaveTextContent(
      '"Do we need to keep this?" → You: yes, it dedupes retries',
    );
    expect(row).toHaveTextContent("1 reply");
    expect(
      screen.queryByRole("article", { name: /line 22/ }),
    ).not.toBeInTheDocument();
    await userEvent.click(row);
    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(
      within(card(/line 22/)).getByRole("button", { name: "Open again" }),
    ).toBeInTheDocument();
  });

  it("tags what came since your last visit as new, and folds a long message", async () => {
    api();
    localStorage.setItem(
      `keel2.review.seen.${PID}.${KEY}`,
      "2026-10-21T12:00:00Z",
    );
    show();
    expect(screen.getAllByText("new")).toHaveLength(1);
    expect(within(card(/line 2–3/)).getByText("new")).toBeInTheDocument();
    // this visit counts from now on
    expect(
      localStorage.getItem(`keel2.review.seen.${PID}.${KEY}`)! >
        "2026-10-22T09:00:00Z",
    ).toBe(true);
    const more = within(card(/line 40/)).getByRole("button", {
      name: "Show more",
    });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(more);
    expect(
      within(card(/line 40/)).getByRole("button", { name: "Show less" }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("shows keel's check with every verdict, its cost, and Use as my reply fills the box and posts nothing", async () => {
    const { calls } = api();
    show(
      AI({
        judges: {
          T1: run({
            stage: "done",
            tokens: 5200,
            model: "claude · haiku",
            effort: "low",
            context: { code_lines: 61, hunk_lines: 8, files: 1 },
            checked_at: new Date(Date.now() - 10 * 60000).toISOString(),
            items: [
              {
                claim: "indexes",
                verdict: "reviewer_right",
                why: "no index on (resolver_id) — src/a.sql:3",
                fix: "add one",
              },
              {
                claim: "row updated twice",
                verdict: "author_right",
                why: "the WHERE keeps the newer ETA",
              },
              {
                claim: "subquery per row",
                verdict: "partly",
                why: "not correlated",
              },
              {
                claim: "locking",
                verdict: "unclear",
                why: "needs the isolation level",
              },
            ],
            reply: "Good point on the index: I add one in the migration.",
          }),
        },
      }),
    );
    const box = within(card(/line 2–3/)).getByRole("region", {
      name: "keel checked this thread",
    });
    expect(box).toHaveAttribute("aria-live", "polite");
    // who is right, by role and name: bo opened the thread, you wrote the change
    for (const label of [
      "Reviewer (bo) is right",
      "Author (you) is right",
      "Reviewer (bo) is partly right",
      "Unclear: keel could not decide",
    ])
      expect(within(box).getByText(label)).toBeInTheDocument();
    expect(
      within(box).getByText(
        /checked 10 min ago · ~5k tokens · read 1 file · claude · haiku · low/,
      ),
    ).toBeInTheDocument();
    expect(
      within(box).getByRole("button", { name: "Address it (a small flow)" }),
    ).toBeInTheDocument();
    expect(
      within(box).getByRole("button", { name: "Think harder" }),
    ).toBeInTheDocument();
    await userEvent.click(
      within(box).getByRole("button", { name: "Use as my reply" }),
    );
    expect(
      within(card(/line 2–3/)).getByRole("textbox", { name: "Your reply" }),
    ).toHaveValue("Good point on the index: I add one in the migration.");
    expect(calls.filter((x) => x.method === "POST")).toEqual([]); // nothing posted
    // the same words from the reviewer's side, and from neither side
    const t = view().threads[0];
    expect(
      verdictLabel("reviewer_right", sidesOf({ me: "bo", author: "me" }, t)),
    ).toBe("Reviewer (you) is right");
    expect(
      verdictLabel("author_right", sidesOf({ me: "alex", author: "me" }, t)),
    ).toBe("Author (me) is right");
    // and in the text a follow-up question to KeelBot carries
    expect(
      checkLines(
        [{ claim: "indexes", verdict: "reviewer_right", why: "no index" }],
        sidesOf(view(), t),
      ),
    ).toBe("- Reviewer (bo) is right: indexes — no index");
  });

  it("the same point in two threads: Check all counts it once, each answer links the other, one reply answers both", async () => {
    const { calls } = api();
    // bo (line 3) and ana (line 40) make the same point: the view says so
    const v = { ...view(), thread_groups: [["T2", "T4"]] };
    const first = show(AI(), v);
    // three open threads keel has not checked, two points
    expect(
      screen.getByRole("button", { name: "✦ Check all open (2)" }),
    ).toBeInTheDocument();
    first.unmount();

    const reply = "One factory is enough here: I keep it.";
    const same = (id: string, line: number, author: string) =>
      run(
        {
          stage: "done",
          items: [
            { claim: "a factory", verdict: "author_right", why: "one caller" },
          ],
          reply,
          same_as: [{ thread_id: id, path: "src/Prefs.kt", line, author }],
        },
        { kind: `judge:${id}` },
      );
    show(AI({ judges: { T2: same("T4", 40, "ana"), T4: same("T2", 3, "bo") } }), v);
    const t2 = card(/Thread on src\/Prefs\.kt line 3$/);
    const t4 = card(/line 40/);
    const scrolled: Element[] = [];
    const before = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = function () {
      scrolled.push(this as Element);
    };
    try {
      await userEvent.click(
        within(t2).getByRole("button", {
          name: "Same point as the thread on line 40 (ana)",
        }),
      );
    } finally {
      HTMLElement.prototype.scrollIntoView = before;
    }
    expect(scrolled).toEqual([t4]);
    expect(
      within(t4).getByRole("button", {
        name: "Same point as the thread on line 3 (bo)",
      }),
    ).toBeInTheDocument();
    // one reply for both: each box has it, nothing is posted
    await userEvent.click(
      within(t2).getByRole("button", { name: "Use as my reply on both" }),
    );
    expect(within(t2).getByRole("textbox", { name: "Your reply" })).toHaveValue(
      reply,
    );
    expect(within(t4).getByRole("textbox", { name: "Your reply" })).toHaveValue(
      reply,
    );
    expect(calls.filter((x) => x.method === "POST")).toEqual([]);
  });

  it("Fixed → Resolve: a fix keel saw in the new commit offers Resolve, one it did not says what is open, none shows nothing", async () => {
    const { calls } = api();
    const fix = (fixed: boolean, extra: Partial<AiRun<FixResult>> = {}): AiRun<FixResult> => ({
      ...run({ stage: "done" }),
      kind: "fixcheck:x",
      result: {
        stage: "done",
        fixed,
        sha: "abc1234",
        why: "src/Prefs.kt:3 reads the limit now",
        still_open: fixed ? "" : "the max is still hard-coded",
      },
      ...extra,
    });
    // T1: fixed; T2: not fixed; T4: fixed, but the head moved on since (a new check comes); T3 resolved; none for the rest
    show(AI({ fixed: { T1: fix(true), T2: fix(false), T4: fix(true, { stale: true }) } }));
    const fixed = within(card(/line 2–3/)).getByRole("group", {
      name: "keel checked the fix",
    });
    expect(within(fixed).getByText("Fixed in abc1234")).toBeInTheDocument();
    const open = within(
      card(/Thread on src\/Prefs\.kt line 3$/),
    ).getByRole("group", { name: "keel checked the fix" });
    expect(
      within(open).getByText("Still open: the max is still hard-coded"),
    ).toBeInTheDocument();
    expect(within(open).queryByRole("button", { name: "Resolve" })).toBeNull();
    expect(
      within(card(/line 40/)).queryByRole("group", {
        name: "keel checked the fix",
      }),
    ).toBeNull();
    // keel resolved nothing; the person's click calls Resolve
    expect(calls.filter((x) => x.method === "POST")).toEqual([]);
    await userEvent.click(within(fixed).getByRole("button", { name: "Resolve" }));
    await waitFor(() =>
      expect(
        calls.find((x) => x.path.endsWith("/threads/T1/resolve"))?.body,
      ).toEqual({ key: KEY, resolved: true }),
    );
  });

  it("asks keel to check a thread (with its estimate first) and says when it fails", async () => {
    const { calls } = api();
    show();
    const t1 = card(/line 2–3/);
    const [lo] = estimate(view().threads[0]);
    const btn = within(t1).getByRole("button", {
      name: /✦ Is the reviewer right\?/,
    });
    expect(btn).toHaveTextContent(`~${Math.round(lo / 1000)}k`);
    await userEvent.click(btn);
    await waitFor(() =>
      expect(
        calls.find((x) => x.path.endsWith("/threads/T1/judge"))?.body,
      ).toEqual({ key: KEY }),
    );
    expect(
      await within(t1).findByText("keel is checking the code…"),
    ).toBeInTheDocument();

    server.use(
      http.post("/api/projects/:pid/review/threads/T2/judge", () =>
        HttpResponse.json(
          { error: "Stopped at the cap: this check needs about 30k tokens" },
          { status: 409 },
        ),
      ),
    );
    await userEvent.click(
      within(card(/Thread on src\/Prefs\.kt line 3$/)).getByRole("button", {
        name: /Is the reviewer right/,
      }),
    );
    expect(await screen.findByText(/Stopped at the cap/)).toBeInTheDocument();
    expect(ago(new Date(Date.now() - 3 * 3600000).toISOString())).toBe(
      "3 h ago",
    );
  });

  it("Address it asks first, then starts the flow and links to it", async () => {
    const { calls } = api();
    show();
    const t1 = card(/line 2–3/);
    await userEvent.click(
      within(t1).getByRole("button", { name: "✦ Address it" }),
    );
    const confirm = within(t1).getByRole("group", {
      name: "Confirm Address it",
    });
    expect(confirm).toHaveTextContent("pushes and posts nothing");
    expect(calls).toEqual([]);
    await userEvent.click(
      within(confirm).getByRole("button", { name: "Start the flow" }),
    );
    await waitFor(() =>
      expect(
        calls.find((x) => x.path.endsWith("/threads/T1/address"))?.body,
      ).toMatchObject({ key: KEY }),
    );
    expect(
      await within(t1).findByRole("link", { name: "Open the flow ›" }),
    ).toHaveAttribute("href", "#/flow/t-1");
    expect(within(t1).getByText(/It pushes nothing/)).toBeInTheDocument();
  });

  it("Address it asks \"What should the fix be?\" when the thread has no reply of yours, the reviewer's words above it", async () => {
    // keel 0.18.57 a review comment's intent: without your reply the flow guessed (a private method for "a factory?")
    const { calls } = api();
    show();
    // T1 has your reply: nothing more to ask
    const t1 = card(/line 2–3/);
    await userEvent.click(within(t1).getByRole("button", { name: "✦ Address it" }));
    expect(
      within(within(t1).getByRole("group", { name: "Confirm Address it" })).queryByRole("textbox", { name: "What should the fix be?" }),
    ).toBeNull();
    // T4: only the reviewer wrote
    const t4 = card(/line 40/);
    await userEvent.click(within(t4).getByRole("button", { name: "✦ Address it" }));
    const confirm = within(t4).getByRole("group", { name: "Confirm Address it" });
    const fix = within(confirm).getByRole("textbox", { name: "What should the fix be?" });
    expect(fix).toHaveValue("> ana: one\n> \n> two\n> \n> three\n> \n> four\n> \n> five\n> \n> six\n\n");
    expect(confirm).toHaveTextContent("You have not replied in this thread");
    await userEvent.type(fix, "A reusable factory class next to the others.");
    await userEvent.click(within(confirm).getByRole("button", { name: "Start the flow" }));
    await waitFor(() =>
      expect(calls.find((x) => x.path.endsWith("/threads/T4/address"))?.body).toMatchObject({
        key: KEY,
        fix: expect.stringMatching(/^> ana: one\n[\s\S]*\n\nA reusable factory class next to the others\.$/),
      }),
    );
  });

  it("Address it runs the address flow by default and lets you pick another flow", async () => {
    const ai = AI({
      config: {
        judge: {
          mode: "quick",
          workflow: "review",
          allow_harder: true,
          token_cap: 25000,
          agent: "pr-reviewer",
        },
        address_workflow: "address",
        address_flows: [
          { id: "address", name: "address (keel)" },
          { id: "feature", name: "feature (keel)" },
        ],
        buttons: [],
      },
    });
    const { calls } = api(ai);
    show(ai);
    const t1 = card(/line 2–3/);
    await userEvent.click(
      within(t1).getByRole("button", { name: "✦ Address it" }),
    );
    const confirm = within(t1).getByRole("group", {
      name: "Confirm Address it",
    });
    const pick = within(confirm).getByRole("combobox", {
      name: "Flow for Address it",
    });
    expect(pick).toHaveValue("address");
    expect(confirm).toHaveTextContent("starts the address flow");
    await userEvent.selectOptions(pick, "feature");
    expect(confirm).toHaveTextContent("starts the feature flow");
    await userEvent.click(
      within(confirm).getByRole("button", { name: "Start the flow" }),
    );
    await waitFor(() =>
      expect(
        calls.find((x) => x.path.endsWith("/threads/T1/address"))?.body,
      ).toMatchObject({ key: KEY, workflow: "feature" }),
    );
  });

  it("draws the extra buttons from the settings and runs one", async () => {
    const { calls } = api();
    show(
      AI({
        config: {
          judge: {
            mode: "quick",
            workflow: "review",
            allow_harder: false,
            token_cap: 25000,
            agent: "pr-reviewer",
          },
          address_workflow: "fix",
          buttons: [
            {
              id: "explain-simply",
              label: "Explain simply",
              mode: "ask",
              agent: "explorer",
            },
          ],
        },
      }),
    );
    const t1 = card(/line 2–3/);
    await userEvent.click(
      within(t1).getByRole("button", { name: "Explain simply" }),
    );
    await waitFor(() =>
      expect(
        calls.some((x) =>
          x.path.endsWith("/threads/T1/buttons/explain-simply"),
        ),
      ).toBe(true),
    );
  });

  it("stacks the actions when the panel is narrow", () => {
    api();
    const RO = globalThis.ResizeObserver;
    class Narrow {
      constructor(private cb: ResizeObserverCallback) {}
      observe() {
        this.cb(
          [{ contentRect: { width: 320 } } as ResizeObserverEntry],
          this as unknown as ResizeObserver,
        );
      }
      disconnect() {}
      unobserve() {}
    }
    globalThis.ResizeObserver = Narrow as unknown as typeof ResizeObserver;
    try {
      show();
      expect(screen.getByTestId("rv-threads")).toHaveClass("narrow");
    } finally {
      globalThis.ResizeObserver = RO;
    }
  });
});

describe("Settings › Code Review threads", () => {
  function settingsApi(judge = "pr-reviewer") {
    const puts: unknown[] = [];
    let v: ThreadSettingsView = {
      defaults: {
        judge_mode: "quick",
        judge_agent: "pr-reviewer",
        judge_model: null,
        judge_effort: "low",
        judge_token_cap: 25000,
        judge_allow_harder: true,
        judge_workflow: "review",
        address_workflow: "small",
        thread_buttons: [],
      },
      general: {},
      overrides: {},
      effective: {
        judge_mode: "quick",
        judge_agent: judge,
        judge_model: null,
        judge_effort: "low",
        judge_token_cap: 25000,
        judge_allow_harder: true,
        judge_workflow: "review",
        address_workflow: "small",
        thread_buttons: [],
      },
      agents: ["explorer", "pr-reviewer"],
      workflows: [
        { id: "small", name: "small" },
        { id: "fix", name: "fix" },
        { id: "review", name: "review" },
      ],
      models: [
        {
          role: "cheaper",
          model: { provider: "claude", mode: "subscription", model: "haiku" },
        },
      ],
      limits: {
        buttons: 6,
        label: 30,
        min_cap: 200,
        max_cap: 1000000,
        placeholders: ["diff", "file", "lines", "pr", "thread"],
      },
    };
    server.use(
      http.get("/api/projects/:pid/review/settings", () =>
        HttpResponse.json(v),
      ),
      http.put("/api/projects/:pid/review/settings", async ({ request }) => {
        const b = (await request.json()) as Record<string, unknown>;
        puts.push(b);
        const btns = b.thread_buttons as { label: string }[] | undefined;
        if (btns?.some((x) => !x.label.trim()))
          return HttpResponse.json(
            { error: "Button 1 needs a label" },
            { status: 400 },
          );
        v = {
          ...v,
          overrides: { ...v.overrides, ...b },
          effective: { ...v.effective, ...b },
        };
        return HttpResponse.json(v);
      }),
    );
    return { puts };
  }

  it("saves a project's choice, and adds, orders and checks the extra buttons", async () => {
    const { puts } = settingsApi();
    render(<ThreadSettings pid={PID} scope="project" />);
    const mode = await screen.findByLabelText("How keel checks");
    await userEvent.selectOptions(mode, "workflow");
    await waitFor(() => expect(puts[0]).toEqual({ judge_mode: "workflow" }));
    expect(
      await screen.findByText("changed for this project"),
    ).toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByLabelText("Workflow", { selector: "#rvs-address_workflow" }),
      "fix",
    );
    await waitFor(() => expect(puts[1]).toEqual({ address_workflow: "fix" }));

    await userEvent.click(screen.getByRole("button", { name: "Add a button" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Save the buttons" }),
    );
    expect(
      await screen.findByText("Button 1 needs a label"),
    ).toBeInTheDocument(); // the api's check, shown
    await userEvent.type(
      screen.getByRole("textbox", { name: "Label of button 1" }),
      "Explain",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add a button" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Label of button 2" }),
      "Fix it",
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Mode of button 2" }),
      "workflow",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Move button 2 up" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Save the buttons" }),
    );
    await waitFor(() =>
      expect(
        (
          puts.at(-1) as {
            thread_buttons: {
              label: string;
              mode: string;
              workflow?: string | null;
            }[];
          }
        ).thread_buttons.map((b) => `${b.label}|${b.mode}|${b.workflow ?? ""}`),
      ).toEqual(["Fix it|workflow|small", "Explain|ask|"]),
    );
    await act(async () => {});
  });

  it("keeps an agent saved under its old name as picked, and says which agent it is now", async () => {
    settingsApi("ticket-reviewer");
    render(<ThreadSettings pid={PID} scope="project" />);
    const agent = (await screen.findByLabelText("Agent")) as HTMLSelectElement;
    expect(agent.value).toBe("ticket-reviewer");
    expect(
      screen.getByRole("option", { name: "ticket-reviewer (now pr-reviewer)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "pr-reviewer" }),
    ).toBeInTheDocument();
  });
});

describe("dismissing keel's answer under a thread", () => {
  const HIDDEN = `keel2.review.judgeHidden.${PID}.${KEY}`;
  const checked = (id = "rr_1") =>
    AI({
      judges: {
        T1: run(
          {
            stage: "done",
            items: [{ claim: "indexes", verdict: "unclear", why: "maybe" }],
            reply: "Let me look.",
          },
          { id },
        ),
      },
    });
  const checkBox = () =>
    within(card(/line 2–3/)).queryByRole("region", {
      name: "keel checked this thread",
    });

  it("Dismiss hides the check, it stays hidden after a remount, and asking again shows the saved one with no new check", async () => {
    const { calls } = api(checked());
    const first = show(checked());
    await userEvent.click(
      within(checkBox()!).getByRole("button", { name: "Dismiss" }),
    );
    expect(checkBox()).toBeNull();
    expect(JSON.parse(localStorage.getItem(HIDDEN)!)).toEqual(["T1|rr_1"]);

    first.unmount();
    show(checked());
    expect(checkBox()).toBeNull();
    await userEvent.click(
      within(card(/line 2–3/)).getByRole("button", {
        name: /Is the reviewer right/,
      }),
    );
    expect(checkBox()).not.toBeNull();
    expect(within(checkBox()!).getByText("Unclear: keel could not decide")).toBeInTheDocument();
    expect(calls.filter((x) => x.path.endsWith("/judge"))).toEqual([]);
    expect(JSON.parse(localStorage.getItem(HIDDEN)!)).toEqual([]);
  });

  it("the ✕ hides it too, and a new check (another run) shows again", async () => {
    api(checked());
    const first = show(checked());
    await userEvent.click(
      within(card(/line 2–3/)).getByRole("button", {
        name: "Dismiss keel's check",
      }),
    );
    expect(checkBox()).toBeNull();
    first.unmount();
    show(checked("rr_2"));
    expect(checkBox()).not.toBeNull();
  });

  it("dismisses an extra button's answer, and the button shows the saved answer again without a new run", async () => {
    const withButton = AI({
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
      buttons: {
        "explain-simply|T1": run(
          { stage: "done", text: "It keeps the size per user." },
          { id: "rb_1", kind: "button" },
        ),
      },
    });
    const { calls } = api(withButton);
    const first = show(withButton);
    const t1 = () => card(/line 2–3/);
    expect(
      within(t1()).getByText("It keeps the size per user."),
    ).toBeInTheDocument();
    await userEvent.click(
      within(t1()).getByRole("button", { name: "Dismiss Explain simply" }),
    );
    expect(within(t1()).queryByText("It keeps the size per user.")).toBeNull();
    first.unmount();
    show(withButton);
    expect(within(t1()).queryByText("It keeps the size per user.")).toBeNull();
    await userEvent.click(
      within(t1()).getByRole("button", { name: "Explain simply" }),
    );
    expect(
      within(t1()).getByText("It keeps the size per user."),
    ).toBeInTheDocument();
    expect(calls.filter((x) => x.path.includes("/buttons/"))).toEqual([]);
  });

  it("remembers at most the cap of dismissed answers per review", async () => {
    localStorage.setItem(
      HIDDEN,
      JSON.stringify(
        Array.from({ length: HIDDEN_RUNS_CAP }, (_, i) => `old|r${i}`),
      ),
    );
    api(checked());
    show(checked());
    await userEvent.click(
      within(checkBox()!).getByRole("button", { name: "Dismiss" }),
    );
    const kept = JSON.parse(localStorage.getItem(HIDDEN)!) as string[];
    expect(kept).toHaveLength(HIDDEN_RUNS_CAP);
    expect(kept.at(-1)).toBe("T1|rr_1");
    expect(kept).not.toContain("old|r0");
  });
});
