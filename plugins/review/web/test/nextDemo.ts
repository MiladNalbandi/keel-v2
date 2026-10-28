// Demo data and api handlers (msw) for the new Review view: the redesign mockup's shop project, neutral names (ana is
// "me", bo and alex review). next.test.tsx runs on them, and a preview page can serve them to look at the view.

import { http, HttpResponse } from "msw";
import type {
  AiRun,
  AiState,
  Draft,
  Finding,
  JudgeItem,
  JudgeResult,
  PrSummary,
  ReviewThread,
  ReviewView,
} from "../reviewApi";

const HOST = {
  kind: "github" as const,
  host: "github.com",
  path: "acme/shop",
  web: "https://github.com/acme/shop",
};
const ME = "ana";

type Row = [" " | "+" | "-", string];
/** A unified diff of one file from rows: context, added and removed lines, starting at line [start]. */
function diffOf(path: string, start: number, rows: Row[]): string {
  const oldN = rows.filter((r) => r[0] !== "+").length;
  const newN = rows.filter((r) => r[0] !== "-").length;
  return [
    `diff --git a/${path} b/${path}`,
    `--- a/${path}`,
    `+++ b/${path}`,
    `@@ -${start},${oldN} +${start},${newN} @@`,
    ...rows.map(([k, t]) => `${k}${t}`),
  ].join("\n");
}

const DIFFS: Record<string, string> = {
  "orders/refunds.py": diffOf("orders/refunds.py", 10, [
    [" ", "def refundable_amount(order: Order) -> Decimal:"],
    [" ", '    """What can still be refunded on this order."""'],
    [" ", "    already = sum(r.amount for r in order.refunds)"],
    ["-", "    return order.total - already"],
    ["+", "    return order.paid_total - already"],
    [" ", ""],
    [" ", ""],
    ["+", "def refund(order: Order, amount: Decimal, reason: str) -> Refund:"],
    [" ", "    if amount <= 0:"],
    [" ", '        raise ValueError("amount must be positive")'],
    ["+", "    # never give back more than the customer paid"],
    ["+", "    if amount > refundable_amount(order):"],
    ["+", "        raise RefundTooLarge(order.id)"],
    ["+", "    item = Refund(order=order, amount=amount, reason=reason)"],
    [" ", "    order.refunds.append(item)"],
    [" ", "    return item"],
  ]),
  "orders/views.py": diffOf("orders/views.py", 5, [
    [" ", ""],
    [" ", "def refund_view(request, order):"],
    ["-", '    amount = request.form["amount"]'],
    ["+", '    amount = Decimal(request.form["amount"])'],
    ["+", '    reason = request.form.get("reason", "")'],
    ["+", "    item = refund(order, amount, reason)"],
    [" ", '    return {"refund": item.id}'],
  ]),
  "tests/test_refunds.py": diffOf("tests/test_refunds.py", 8, [
    ["+", "def test_refund_never_more_than_paid(paid_order):"],
    ["+", "    with pytest.raises(RefundTooLarge):"],
    ["+", '        refund(paid_order, Decimal("120.00"), "broken")'],
    ["+", ""],
    ["+", ""],
    ["+", "def test_refund_keeps_the_reason(paid_order):"],
    ["+", '    item = refund(paid_order, Decimal("10.00"), "late delivery")'],
    ["+", '    assert item.reason == "late delivery"'],
  ]),
  "orders/export.py": diffOf("orders/export.py", 1, [
    [" ", "import csv"],
    [" ", "from io import StringIO"],
    [" ", ""],
    ["+", 'FORMULA = ("=", "+", "@")'],
    ["+", ""],
    ["+", ""],
    ["+", "def safe_cell(value):"],
    ["+", "    text = str(value)"],
    ["+", "    if text.startswith(FORMULA):"],
    ["+", '        return "\'" + text'],
    ["+", "    return text"],
    [" ", ""],
    ["-", 'COLUMNS = ["id", "status", "total", "date"]'],
    ["+", 'COLUMNS = ["id", "total", "status", "date"]'],
  ]),
};
const small = (path: string) =>
  diffOf(path, 1, [
    ["+", `# ${path.slice(path.lastIndexOf("/") + 1)}`],
    ["+", ""],
    ["+", "..."],
  ]);

const at = (minAgo: number) =>
  new Date(Date.now() - minAgo * 60000).toISOString();
const comment = (id: string, author: string, body: string, minAgo: number) => ({
  id,
  author,
  body,
  at: at(minAgo),
});
const thread = (
  id: string,
  path: string,
  line: number,
  c: ReturnType<typeof comment>[],
  resolved = false,
): ReviewThread => ({
  id,
  path,
  line,
  side: "RIGHT",
  resolved,
  outdated: false,
  comments: c,
});

export const PRS: PrSummary[] = [
  {
    number: 42,
    title: "Add refunds to orders",
    author: ME,
    branch: "feature/SHOPS-1234-refunds",
    base: "main",
    draft: false,
    updated_at: at(20),
    url: "",
    review_requested: false,
    mine: true,
    assigned: false,
  },
  {
    number: 45,
    title: "Export orders as CSV",
    author: "bo",
    branch: "feature/SHOPS-1240-csv",
    base: "main",
    draft: false,
    updated_at: at(35),
    url: "",
    review_requested: true,
    mine: false,
    assigned: false,
  },
  {
    number: 44,
    title: "Order list crashes on an empty page",
    author: "alex",
    branch: "fix/SHOPS-1262-empty-page",
    base: "main",
    draft: true,
    updated_at: at(120),
    url: "",
    review_requested: true,
    mine: false,
    assigned: false,
  },
  {
    number: 41,
    title: "Upgrade the test runner",
    author: "alex",
    branch: "chore/test-runner",
    base: "main",
    draft: false,
    updated_at: at(1500),
    url: "",
    review_requested: false,
    mine: false,
    assigned: true,
  },
];

function view(n: number): ReviewView {
  const p = PRS.find((x) => x.number === n)!;
  const base = {
    key: `pr:${n}`,
    kind: "pr" as const,
    number: n,
    title: p.title,
    author: p.author,
    body: "",
    base: p.base,
    branch: p.branch,
    base_sha: "b".repeat(40),
    head_sha: "a".repeat(40),
    url: `https://github.com/acme/shop/pull/${n}`,
    state: "open",
    draft: p.draft,
    same_repo: true,
    approved: [] as string[],
    changes_requested: [] as string[],
    conversation: [],
    drafts: [] as Draft[],
    viewed: [] as string[],
    can_post: true,
    host: HOST,
    me: ME,
    notes: [],
    mine: p.mine,
    mergeable: true,
    merge_state: "clean",
  };
  if (n === 42)
    return {
      ...base,
      checks: [
        { name: "build", state: "success" },
        { name: "tests", state: "success" },
      ],
      files: [
        {
          path: "orders/refunds.py",
          status: "M",
          added: 24,
          removed: 3,
          binary: false,
        },
        {
          path: "orders/views.py",
          status: "M",
          added: 3,
          removed: 1,
          binary: false,
        },
        {
          path: "tests/test_refunds.py",
          status: "A",
          added: 15,
          removed: 0,
          binary: false,
        },
        {
          path: "orders/models.py",
          status: "M",
          added: 6,
          removed: 0,
          binary: false,
        },
        {
          path: "migrations/0007_refund.py",
          status: "A",
          added: 28,
          removed: 0,
          binary: false,
        },
      ],
      added: 184,
      removed: 22,
      commits: [
        {
          sha: "a41c9e2".padEnd(40, "0"),
          message: "AC-2: refuse more than was paid",
          author: "keel",
          at: at(40),
        },
        {
          sha: "7d02b11".padEnd(40, "0"),
          message: "AC-1: refund a paid order",
          author: "keel",
          at: at(60),
        },
        {
          sha: "c9e0f54".padEnd(40, "0"),
          message: "Spec: add refunds to orders",
          author: "keel",
          at: at(120),
        },
        {
          sha: "5b1e7a0".padEnd(40, "0"),
          message: "Add the refund table",
          author: ME,
          at: at(180),
        },
      ],
      threads: [
        thread("th1", "orders/refunds.py", 13, [
          comment(
            "c1",
            "bo",
            "Should this also take off the coupon? I think paid_total still has the coupon in it.",
            120,
          ),
        ]),
        thread("th2", "orders/refunds.py", 20, [
          comment(
            "c2",
            "alex",
            "The amount comes from a form. Is it always a Decimal here, or can a string reach this check?",
            60,
          ),
        ]),
        thread("th3", "orders/views.py", 7, [
          comment(
            "c3",
            "bo",
            "Decimal() raises on bad input. Please return a 400 instead of a crash.",
            50,
          ),
        ]),
        thread(
          "th4",
          "tests/test_refunds.py",
          13,
          [
            comment("c4", "alex", "Nice clear test names.", 1500),
            comment("c5", ME, "Thanks!", 1400),
          ],
          true,
        ),
      ],
    };
  if (n === 45)
    return {
      ...base,
      checks: [{ name: "build", state: "in_progress" }],
      files: [
        {
          path: "orders/export.py",
          status: "M",
          added: 41,
          removed: 6,
          binary: false,
        },
        {
          path: "orders/views.py",
          status: "M",
          added: 12,
          removed: 0,
          binary: false,
        },
        {
          path: "tests/test_export.py",
          status: "A",
          added: 38,
          removed: 0,
          binary: false,
        },
      ],
      added: 96,
      removed: 8,
      commits: [
        {
          sha: "e11f0a3".padEnd(40, "0"),
          message: "Export button and CSV writer",
          author: "bo",
          at: at(60),
        },
        {
          sha: "0c9d2b4".padEnd(40, "0"),
          message: "Safe cells for spreadsheets",
          author: "bo",
          at: at(40),
        },
      ],
      threads: [
        thread("th5", "orders/export.py", 13, [
          comment("c6", "alex", "Why did total move before status?", 30),
        ]),
      ],
    };
  return {
    ...base,
    checks: [{ name: "build", state: "success" }],
    files: [
      {
        path: "orders/views.py",
        status: "M",
        added: 6,
        removed: 3,
        binary: false,
      },
    ],
    added: 14,
    removed: 3,
    commits: [
      {
        sha: "9a8b7c6".padEnd(40, "0"),
        message: "Fix: empty page in the order list",
        author: p.author,
        at: at(120),
      },
    ],
    threads: [],
  };
}

const run = <R>(
  kind: string,
  result: R,
  status: AiRun<R>["status"] = "done",
): AiRun<R> => ({
  id: `run_${kind}`,
  kind,
  status,
  stage: status === "done" ? "done" : "review",
  head_sha: "a".repeat(40),
  stale: false,
  sessions: [],
  result: status === "done" ? result : null,
  error: null,
  created_at: at(10),
  updated_at: at(5),
});
const judged = (items: JudgeItem[], reply: string): AiRun<JudgeResult> =>
  run("judge", { stage: "done", items, reply, tokens: 3800, model: "quick" });

/** keel's check when you ask for it (th3, th5 have none yet). */
const LATER: Record<string, [JudgeItem[], string]> = {
  th3: [
    [
      {
        claim: "Decimal() raises on bad input",
        verdict: "reviewer_right",
        why: 'InvalidOperation is not caught, so "abc" gives a 500.',
      },
      {
        claim: "It should be a 400",
        verdict: "unclear",
        why: "The app answers form errors with 422 and form_error() (forms.py). Ask bo which one the team wants.",
      },
    ],
    "Agreed it must not crash. The app uses 422 with form_error() for form errors; OK to use that here?",
  ],
  th5: [
    [
      {
        claim: "The order change is a mistake",
        verdict: "unclear",
        why: "The ticket does not name a column order. But the nightly import reads columns by position, so it matters.",
      },
    ],
    "",
  ],
};

const finding = (
  id: string,
  severity: Finding["severity"],
  title: string,
  path: string,
  line: number,
  why: string,
  fix: string,
  pre = false,
): Finding => ({
  id,
  title,
  severity,
  category: "correctness",
  path,
  line,
  side: "RIGHT",
  why,
  fix,
  pre_existing: pre,
  reviewer: "A+B",
  check: "confirmed",
});

const FINDINGS: Record<number, Finding[]> = {
  42: [
    finding(
      "f9",
      "should_fix",
      "No test sends a bad amount",
      "orders/views.py",
      7,
      'A form value like "abc" makes Decimal() raise. The same point as alex\'s comment.',
      'one test that posts "abc".',
    ),
  ],
  45: [
    finding(
      "f1",
      "blocking",
      'A cell that starts with "-" is not made safe',
      "orders/export.py",
      4,
      'FORMULA has = + @ but not -. A spreadsheet runs "-2+3" as a formula. The ticket asks for all four.',
      'add "-" to FORMULA and a test.',
    ),
    finding(
      "f2",
      "should_fix",
      "Column order changed",
      "orders/export.py",
      13,
      '"total" moved before "status". The nightly import reads the columns by position.',
      "keep the old order, or tell the import owners.",
    ),
    finding(
      "f3",
      "should_fix",
      "The orders query has no limit",
      "orders/queries.py",
      30,
      "This was there before the change. A big shop may time out. Not for this PR.",
      "",
      true,
    ),
  ],
};

function aiOf(n: number, found: boolean): AiState {
  const judges: Record<string, AiRun<JudgeResult>> = n === 42
    ? {
        th1: judged(
          [
            {
              claim: "paid_total still has the coupon in it",
              verdict: "author_right",
              why: "paid_total is set from the payment, after the coupon (checkout.py line 41). Taking it off again would refund too little.",
            },
          ],
          "paid_total is what the card was charged, so the coupon is already out of it (checkout.py line 41). Leaving it as is.",
        ),
        th2: judged(
          [
            {
              claim: "A string can reach this check",
              verdict: "reviewer_right",
              why: "Before this branch, views.py passed the raw form value. It is converted now (views.py line 7), but no test sends a bad value.",
              fix: 'add a test that posts "abc" and expects a clear error.',
            },
          ],
          "Good catch. views.py converts it now; I am adding a test for a bad value.",
        ),
      }
    : {};
  return {
    overview: run("overview", {
      stage: "done",
      summary:
        n === 42
          ? "A shop owner can refund a paid order, fully or in part. A refund is never more than what was paid, and the reason is kept with it."
          : "Adds an Export button on the order list. Cells that start with = + - @ are made safe so a spreadsheet does not run them as formulas.",
      files: [],
      order:
        n === 42
          ? ["orders/refunds.py", "tests/test_refunds.py"]
          : ["orders/export.py"],
      diagram: "",
      effort: 2,
      risk: "low",
      risk_why: "",
      split: "",
      questions: [],
    }),
    findings: found
      ? run("findings", {
          stage: "done",
          findings: (FINDINGS[n] ?? []).filter((f) => !f.pre_existing),
          pre_existing: (FINDINGS[n] ?? []).filter((f) => f.pre_existing),
          security: "nothing reported",
          tests: n === 42 ? "one case missing" : "cover the main path",
        })
      : null,
    decisions: {},
    judges,
    addressed: {},
    config: {
      judge: {
        mode: "quick",
        workflow: "review",
        allow_harder: true,
        token_cap: 25000,
        agent: "pr-reviewer",
      },
      address_workflow: "small",
      address_flows: [{ id: "small", name: "small" }],
      buttons: [],
      // keel 0.18.65 Settings › Review: three reviewers, and a fix flow picked for each finding
      find: {
        text: "Three keel reviewers read the change (A: correctness and security, B: tests, the description and design, " +
          "C: money). Then a fresh run checks every serious claim against the code. Only checked findings are shown here; nothing is posted.",
        reviewers: [
          { letter: "A", name: "correctness and security" },
          { letter: "B", name: "tests, the description and design" },
          { letter: "C", name: "money" },
        ],
        fix: { flow: "ask", one_flow_per_file: false },
      },
    },
    fixes: {},
  };
}

export type Call = {
  method: string;
  path: string;
  body: Record<string, unknown>;
};

/** The api of the Code Review plugin over the demo data. [found]: the pull requests keel looked for problems in. */
export function nextDemo(opts: { found?: number[] } = {}) {
  const calls: Call[] = [];
  const views: Record<string, ReviewView> = Object.fromEntries(
    PRS.map((p) => [`pr:${p.number}`, view(p.number)]),
  );
  const ai: Record<string, AiState> = Object.fromEntries(
    PRS.map((p) => [
      `pr:${p.number}`,
      aiOf(p.number, (opts.found ?? [42]).includes(p.number)),
    ]),
  );
  const rec = async (request: Request) => {
    let body: Record<string, unknown> = {};
    try {
      body = (await request.clone().json()) as Record<string, unknown>;
    } catch {
      /* none */
    }
    const u = new URL(request.url);
    calls.push({ method: request.method, path: u.pathname + u.search, body });
    return body;
  };
  const keyOf = (req: Request, b?: Record<string, unknown>) =>
    (b?.key as string) ?? new URL(req.url).searchParams.get("key") ?? "pr:42";
  const judge = (k: string, tid: string) => {
    const later = LATER[tid];
    const cur = ai[k].judges?.[tid];
    if (later)
      ai[k] = {
        ...ai[k],
        judges: { ...ai[k].judges, [tid]: judged(later[0], later[1]) },
      };
    else if (cur)
      ai[k] = {
        ...ai[k],
        judges: { ...ai[k].judges, [tid]: { ...cur, id: `${cur.id}x` } },
      };
  };
  const R = "/api/projects/:pid/review";
  const handlers = [
    http.get(`${R}/branch`, () =>
      HttpResponse.json({
        branch: "feature/SHOPS-1234-refunds",
        base: "main",
        ahead: 4,
        files: 5,
        added: 184,
        removed: 22,
        pr: PRS[0],
        note: null,
      }),
    ),
    http.get(`${R}/prs`, ({ request }) => {
      const f = new URL(request.url).searchParams.get("filter");
      const pick = (p: PrSummary) =>
        f === "all" ||
        (f === "mine"
          ? p.mine
          : f === "assigned"
            ? !!p.assigned
            : p.review_requested);
      return HttpResponse.json({
        host: HOST,
        me: ME,
        counts: {
          all: PRS.length,
          review: PRS.filter((p) => p.review_requested).length,
          mine: PRS.filter((p) => p.mine).length,
          assigned: PRS.filter((p) => p.assigned).length,
        },
        note: null,
        prs: PRS.filter(pick),
      });
    }),
    http.get(`${R}/view`, ({ request }) =>
      HttpResponse.json(views[keyOf(request)]),
    ),
    http.get(`${R}/diff`, ({ request }) => {
      const p = new URL(request.url).searchParams.get("path")!;
      return HttpResponse.json({
        path: p,
        against: "base",
        ref: "main…#42",
        diff: DIFFS[p] ?? small(p),
        binary: false,
        truncated: false,
      });
    }),
    http.get(`${R}/ai`, ({ request }) => HttpResponse.json(ai[keyOf(request)])),
    http.post(`${R}/ai/findings/:fid/fix`, async ({ request, params }) => {
      const b = await rec(request);
      const k = keyOf(request, b);
      const fid = String(params.fid);
      const done = { flow_id: "t-fix-1", title: "Fix review finding", branch: "feat/export", workflow: String(b.workflow ?? "small"), at: "now" };
      ai[k] = { ...ai[k], fixes: { ...ai[k].fixes, [fid]: done } };
      return HttpResponse.json({ ...done, note: "", state: ai[k] });
    }),
    http.post(`${R}/ai/findings/:fid`, async ({ request, params }) => {
      const b = await rec(request);
      const k = keyOf(request, b);
      ai[k] = {
        ...ai[k],
        decisions: {
          ...ai[k].decisions,
          [String(params.fid)]: {
            decision: b.decision as string,
            why: (b.why as string) ?? null,
          },
        },
      };
      return HttpResponse.json(ai[k]);
    }),
    http.post(`${R}/ai/:kind`, async ({ request, params }) => {
      const b = await rec(request);
      const k = keyOf(request, b);
      const n = Number(k.slice(3));
      if (params.kind === "findings")
        ai[k] = { ...ai[k], findings: aiOf(n, true).findings };
      return HttpResponse.json(ai[k]);
    }),
    http.post(`${R}/threads/judge`, async ({ request }) => {
      const b = await rec(request);
      const k = keyOf(request, b);
      views[k].threads
        .filter((t) => !t.resolved && !ai[k].judges?.[t.id])
        .forEach((t) => judge(k, t.id));
      return HttpResponse.json(ai[k]);
    }),
    http.post(`${R}/threads/:tid/judge`, async ({ request, params }) => {
      const b = await rec(request);
      const k = keyOf(request, b);
      judge(k, String(params.tid));
      return HttpResponse.json(ai[k]);
    }),
    http.post(`${R}/threads/:tid/address`, async ({ request, params }) => {
      const b = await rec(request);
      const k = keyOf(request, b);
      const v = views[k];
      ai[k] = {
        ...ai[k],
        addressed: {
          ...ai[k].addressed,
          [String(params.tid)]: {
            flow_id: "fl_1",
            title: "Address the comment",
            branch: v.branch,
            workflow: "small",
            at: at(0),
          },
        },
      };
      return HttpResponse.json({
        flow_id: "fl_1",
        title: "Address the comment",
        branch: v.branch,
        workflow: "small",
        note: `A small flow works on ${v.branch}.`,
        state: ai[k],
      });
    }),
    http.post(`${R}/threads/:tid/reply`, async ({ request, params }) => {
      const b = await rec(request);
      const k = keyOf(request, b);
      views[k] = {
        ...views[k],
        threads: views[k].threads.map((t) =>
          t.id === params.tid
            ? {
                ...t,
                comments: [
                  ...t.comments,
                  comment(`r${t.comments.length}`, ME, b.body as string, 0),
                ],
              }
            : t,
        ),
      };
      return HttpResponse.json(views[k]);
    }),
    http.post(`${R}/threads/:tid/resolve`, async ({ request, params }) => {
      const b = await rec(request);
      const k = keyOf(request, b);
      views[k] = {
        ...views[k],
        threads: views[k].threads.map((t) =>
          t.id === params.tid ? { ...t, resolved: !!b.resolved } : t,
        ),
      };
      return HttpResponse.json(views[k]);
    }),
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
        created_at: at(0),
      };
      views[k] = { ...views[k], drafts: [...views[k].drafts, d] };
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
      const posted = views[k].drafts.filter((d) => d.line != null).length;
      views[k] = {
        ...views[k],
        drafts: [],
        ...(b.event === "APPROVE"
          ? { approved: [ME] }
          : b.event === "REQUEST_CHANGES"
            ? { changes_requested: [ME] }
            : {}),
      };
      return HttpResponse.json({
        posted,
        in_body: 0,
        event: b.event,
        url: null,
        view: views[k],
      });
    }),
  ];
  return { calls, views, ai, handlers };
}
