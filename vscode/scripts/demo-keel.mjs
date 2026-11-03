#!/usr/bin/env node
// A stand-in keel with demo data only (project "shop", people ana / bo / alex), for the smoke test, screenshots and
// trying the extension without a real keel. It speaks the parts of keel's api the extension uses, streams a running
// flow's activity on /api/events, and keeps answers in memory. Nothing it does reaches a real keel.
//
//   node scripts/demo-keel.mjs [port]        (default 8099; the token is "demo-token")
import http from "node:http";

const PORT = Number(process.argv[2] ?? process.env.PORT ?? 8099);
const TOKEN = "demo-token";
const now = () => new Date().toISOString();
const ago = (min) => new Date(Date.now() - min * 60_000).toISOString();

const project = { id: "shop", name: "shop", root: "/projects/shop", branch: "main", waiting: 1, running: 1 };

const specGate = {
  id: "q-7",
  step: "spec-gate",
  kind: "gate",
  title: "Approve the spec?",
  detail:
    "## Cart totals in cents\n\nTotals are kept in **integer cents** so rounding never drifts.\n\n- AC1 `total()` returns cents\n- AC2 tax is rounded half up\n- AC3 the cart page shows `12.34`\n\nSee src/cart.ts:4 for the current code.",
  options: ["approve", "reject"],
  choices: ["approve", "change"],
  choice_info: [
    { name: "approve", label: "Approve the spec", description: "The flow writes the tests next.", why: "none", default: true },
    { name: "change", label: "Change it", description: "The spec step runs again with your note.", why: "required" },
  ],
};

const threads = {
  "t-cart": {
    thread_id: "t-cart",
    project_id: "shop",
    workflow_id: "feature",
    title: "Cart totals in cents",
    status: "waiting",
    current: "spec-gate",
    phase: "spec",
    acs: [
      { id: "AC1", layer: "API", title: "total() returns cents", status: "todo" },
      { id: "AC2", layer: "API", title: "tax is rounded half up", status: "todo" },
      { id: "AC3", layer: "E2E", title: "the cart page shows 12.34", status: "todo" },
    ],
    waiting: specGate,
    usage: { tokens_in: 18200, tokens_out: 4100, cost_usd: 0.21 },
    updated_at: ago(3),
  },
  "t-refund": {
    thread_id: "t-refund",
    project_id: "shop",
    workflow_id: "feature",
    title: "Add refunds to orders",
    status: "running",
    current: "green",
    phase: "green",
    acs: [
      { id: "AC1", layer: "API", title: "POST /orders/{id}/refund refunds the paid amount", status: "green" },
      { id: "AC2", layer: "API", title: "a refund over the paid amount is refused", status: "red" },
      { id: "AC3", layer: "UI", title: "the order page has a Refund button", status: "doing" },
    ],
    waiting: null,
    usage: { tokens_in: 210000, tokens_out: 38000, cost_usd: 1.42 },
    updated_at: ago(1),
  },
  "t-typo": {
    thread_id: "t-typo",
    project_id: "shop",
    workflow_id: "quick",
    title: "Fix the SHOPS-1234 typo on the checkout page",
    status: "done",
    phase: "done",
    acs: [{ id: "AC1", layer: "UI", title: "the button says Checkout", status: "green" }],
    usage: { tokens_in: 9000, tokens_out: 1200, cost_usd: 0.06 },
    updated_at: ago(180),
  },
};

const workflow = {
  id: "feature",
  name: "feature",
  steps: [
    { id: "spec", name: "Write the spec" },
    { id: "spec-gate", name: "Approve the spec" },
    { id: "red", name: "Write the tests" },
    { id: "green", name: "Make them pass" },
    { id: "review", name: "Review" },
  ],
};

const runRow = (t, extra = {}) => ({
  thread_id: t.thread_id,
  title: t.title,
  workflow_id: t.workflow_id,
  status: t.status,
  phase: t.phase,
  current: t.current,
  waiting: t.waiting ? t.waiting.title : null,
  acs_done: t.acs.filter((a) => a.status === "green").length,
  acs_total: t.acs.length,
  tokens: (t.usage.tokens_in ?? 0) + (t.usage.tokens_out ?? 0),
  cost_usd: t.usage.cost_usd,
  updated_at: t.updated_at,
  created_at: ago(240),
  where: "worktree",
  ...extra,
});

const jobs = [
  { id: "c-plan", project_id: "shop", thread_id: "t-refund", agent: "planner", model: "opus", effort: "high", step: "plan", status: "done", started_at: ago(20), ended_at: ago(14), tokens_in: 40000, tokens_out: 6000, cost_usd: 0.31 },
  { id: "c-code", project_id: "shop", thread_id: "t-refund", agent: "coder", model: "sonnet", effort: "medium", step: "green", status: "running", started_at: ago(6), tokens_in: 52000, tokens_out: 9000, cost_usd: 0.27 },
];

const jobSteps = {
  "c-plan": [
    { n: 1, at: ago(19), kind: "read", path: "src/orders.ts" },
    { n: 2, at: ago(18), kind: "read", path: "src/money.ts" },
    { n: 3, at: ago(15), kind: "answer", text: "## Plan\n\n1. Add `refund(orderId, cents)` to src/orders.ts:12\n2. Refuse a refund over the paid amount\n3. A **Refund** button on the order page" },
  ],
  "c-code": [
    { n: 1, at: ago(6), kind: "read", path: "src/orders.ts" },
    {
      n: 2,
      at: ago(5),
      kind: "edit",
      path: "src/orders.ts",
      diff: "--- a/src/orders.ts\n+++ b/src/orders.ts\n@@ -10,3 +10,9 @@\n export function pay(o: Order) {\n   o.paid = o.total;\n }\n+\n+export function refund(o: Order, cents: number) {\n+  if (cents > o.paid) throw new Error('refund over the paid amount');\n+  o.paid -= cents;\n+  return o;\n+}\n",
    },
    { n: 3, at: ago(4), kind: "tool", tool: "bash", text: "npx vitest run src/orders.test.ts", output: " ✓ src/orders.test.ts (3)\n ✗ refuses a refund over the paid amount\n\n Tests  2 passed | 1 failed (3)\n", ok: false },
  ],
  hc1: [
    { n: 1, kind: "read", path: "src/cart.ts" },
    { n: 2, kind: "tool", tool: "grep", text: "grep -n TAX src", output: "src/cart.ts:2:export const TAX = 0.21;" },
  ],
};

const inbox = () =>
  Object.values(threads)
    .filter((t) => t.waiting && t.status === "waiting")
    .map((t) => ({
      project_id: "shop",
      project_name: "shop",
      thread_id: t.thread_id,
      flow: t.title,
      workflow_id: t.workflow_id,
      step: t.waiting.step,
      kind: t.waiting.kind,
      title: t.waiting.title,
      detail: "3 criteria",
      options: t.waiting.options,
      choices: t.waiting.choices,
      choice_info: t.waiting.choice_info,
      id: t.waiting.id,
      phase: t.phase,
      since: ago(3),
    }));

// ---------------------------------------------------------------- review
const files = [
  { path: "src/cart.ts", status: "modified", added: 3, removed: 2 },
  { path: "src/money.ts", status: "added", added: 6, removed: 0 },
  { path: "src/legacy-totals.ts", status: "removed", added: 0, removed: 4 },
];
const diffs = {
  "src/cart.ts":
    "diff --git a/src/cart.ts b/src/cart.ts\n--- a/src/cart.ts\n+++ b/src/cart.ts\n@@ -1,8 +1,8 @@\n import { Item } from './item';\n-export const TAX = 0.2;\n+export const TAX = 0.21;\n \n+// totals in integer cents\n export function total(items: Item[]): number {\n-  let sum = 0;\n   return items.reduce((s, i) => s + i.cents, 0);\n }\n \n@@ -12,3 +13,4 @@ export function withTax(cents: number): number {\n   return Math.round(cents * (1 + TAX));\n }\n+export const CART_TICKET = 'SHOPS-1234';\n",
  "src/money.ts":
    "diff --git a/src/money.ts b/src/money.ts\nnew file mode 100644\n--- /dev/null\n+++ b/src/money.ts\n@@ -0,0 +1,6 @@\n+/** 1234 → \"12.34\" */\n+export function format(cents: number): string {\n+  const sign = cents < 0 ? '-' : '';\n+  const v = Math.abs(cents);\n+  return `${sign}${Math.floor(v / 100)}.${String(v % 100).padStart(2, '0')}`;\n+}\n",
  "src/legacy-totals.ts":
    "diff --git a/src/legacy-totals.ts b/src/legacy-totals.ts\ndeleted file mode 100644\n--- a/src/legacy-totals.ts\n+++ /dev/null\n@@ -1,4 +0,0 @@\n-// totals in floats (replaced by cents)\n-export function legacyTotal(prices: number[]) {\n-  return prices.reduce((a, b) => a + b, 0);\n-}\n",
};
// 0.21.1 the whole files of Old | New: cart.ts as the head (the fixture) and the base have it
const wholeFiles = {
  "head:src/cart.ts":
    "import { Item } from './item';\nexport const TAX = 0.21;\n\n// totals in integer cents\nexport function total(items: Item[]): number {\n  return items.reduce((s, i) => s + i.cents, 0);\n}\n",
  "base:src/cart.ts":
    "import { Item } from './item';\nexport const TAX = 0.2;\n\nexport function total(items: Item[]): number {\n  let sum = 0;\n  return items.reduce((s, i) => s + i.cents, 0);\n}\n",
};
let reviewThreads = [
  {
    id: "RT_1",
    path: "src/cart.ts",
    line: 2,
    side: "RIGHT",
    resolved: false,
    comments: [
      { id: "c1", author: "ana", body: "Why **0.21**? The SHOPS-1234 ticket says 20 %.", at: ago(50) },
      { id: "c2", author: "bo", body: "The new rate starts next month; see the ticket's last comment.", at: ago(40) },
      { id: "c3", author: "ana", body: "Then please read it from config instead of a constant.", at: ago(30) },
    ],
  },
  { id: "RT_2", path: "src/money.ts", line: 5, side: "RIGHT", resolved: true, comments: [{ id: "c4", author: "alex", body: "Nice: `padStart` keeps 12.05 right.", at: ago(25) }] },
  { id: "RT_3", path: "src/cart.ts", line: 7, side: "RIGHT", outdated: true, resolved: false, comments: [{ id: "c5", author: "alex", body: "This `let sum` is unused.", at: ago(90) }] },
];
const reviewView = () => ({
  key: "pr:7",
  kind: "pr",
  number: 7,
  title: "Cart: totals in integer cents",
  author: "bo",
  body: "Moves totals to cents (SHOPS-1234).",
  base: "main",
  branch: "feat/cents",
  url: "https://github.com/example/shop/pull/7",
  state: "open",
  checks: [
    { name: "test", state: "success" },
    { name: "lint", state: "success" },
  ],
  files,
  added: 9,
  removed: 6,
  threads: reviewThreads,
  me: "ana",
  can_post: true,
});

// ---------------------------------------------------------------- KeelBot
const chat = {
  id: "h1",
  project: "shop",
  mode: "ask",
  title: "How are cart totals computed?",
  model: { provider: "claude", mode: "subscription", model: "sonnet", effort: "medium" },
  status: "idle",
  tokens_in: 12000,
  tokens_out: 900,
  cost_usd: 0.04,
  updated_at: ago(8),
  messages: [
    { n: 1, role: "user", text: "How are cart totals computed?", at: ago(9) },
    {
      n: 2,
      role: "assistant",
      call_id: "hc1",
      text: "Totals are summed in **integer cents** in src/cart.ts:5, then tax is added in `withTax()` at src/cart.ts:13.\n\n- `TAX` is a constant (0.21)\n- rounding is `Math.round`, so half up for positive amounts\n\n```ts\nreturn Math.round(cents * (1 + TAX));\n```",
      at: ago(8),
    },
  ],
};

// ---------------------------------------------------------------- the live stream
const clients = new Set();
function emit(ev) {
  const data = JSON.stringify({ at: now(), project_id: "shop", ...ev });
  for (const res of clients) res.write(`event: engine\ndata: ${data}\n\n`);
}

let tick = 0;
const script = [
  () => emit({ type: "agent.step", thread_id: "t-refund", call_id: "c-code", step: "green", data: { n: 10 + tick, kind: "read", path: "src/orders.test.ts", agent: "coder" } }),
  () =>
    emit({
      type: "agent.step",
      thread_id: "t-refund",
      call_id: "c-code",
      step: "green",
      data: {
        n: 10 + tick,
        kind: "edit",
        agent: "coder",
        path: "src/orders.ts",
        diff: "@@ -12,3 +12,4 @@\n export function refund(o: Order, cents: number) {\n-  if (cents > o.paid) throw new Error('refund over the paid amount');\n+  if (cents <= 0) throw new Error('a refund is more than 0');\n+  if (cents > o.paid) throw new Error('refund over the paid amount');\n",
      },
    }),
  () => emit({ type: "tool.ran", thread_id: "t-refund", call_id: "c-code", step: "green", data: { agent: "coder", cmd: "npx vitest run src/orders.test.ts", code: 0, output: " ✓ src/orders.test.ts (4)\n\n Tests  4 passed (4)\n" } }),
  () => emit({ type: "agent.usage", thread_id: "t-refund", call_id: "c-code", data: { tokens_in: 52000 + tick * 900, tokens_out: 9000 + tick * 200, cost_usd: 0.27 + tick * 0.01 } }),
  () => emit({ type: "guard.refused", thread_id: "t-refund", call_id: "c-code", data: { agent: "coder", reason: "git push is not allowed: keel never pushes", tool: "bash" } }),
  () => emit({ type: "tool.ran", thread_id: "t-refund", call_id: "c-code", data: { agent: "coder", cmd: "npm run lint", code: 1, output: "src/orders.ts\n  14:7  error  'o' is never reassigned  prefer-const\n\n✖ 1 problem (1 error, 0 warnings)" } }),
];
setInterval(() => {
  script[tick % script.length]();
  tick++;
}, 2500);
setInterval(() => {
  for (const res of clients) res.write(": ping\n\n");
}, 20_000);

// ---------------------------------------------------------------- http
function send(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

async function readBody(req) {
  let s = "";
  for await (const c of req) s += c;
  try {
    return s ? JSON.parse(s) : {};
  } catch {
    return {};
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const p = url.pathname;
  const m = req.method;
  if (p === "/api/health") return send(res, 200, { ok: true, engine: true, version: "demo", fake: true });
  if (p === "/api/devices/pair" && m === "POST") {
    const b = await readBody(req);
    if (b.code !== "DEMO-1") return send(res, 403, { error: "This pairing code is not valid", hint: "The demo code is DEMO-1." });
    return send(res, 200, { token: TOKEN, device: { id: "d1", name: b.name, platform: b.platform }, keel: { name: "demo keel", version: "demo" } });
  }
  if (req.headers.authorization !== `Bearer ${TOKEN}`) return send(res, 401, { error: "Sign in to keel", hint: "The demo token is demo-token." });

  if (p === "/api/events") {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    res.write(": hello\n\n");
    clients.add(res);
    req.on("close", () => clients.delete(res));
    return;
  }
  if (p === "/api/devices/me") return send(res, 200, { device: { id: "d1", name: "VS Code", platform: "vscode" } });
  if (p === "/api/projects") return send(res, 200, [project]);
  if (p === "/api/inbox") {
    const items = inbox();
    return send(res, 200, { items, count: items.length });
  }
  let r;
  if ((r = /^\/api\/inbox\/([^/]+)\/act$/.exec(p)) && m === "POST") {
    const t = threads[decodeURIComponent(r[1])];
    const b = await readBody(req);
    if (!t?.waiting) return send(res, 409, { error: "This flow does not wait for an answer now" });
    if (b.id && b.id !== t.waiting.id) return send(res, 409, { error: "This flow now asks something else" });
    const choice = b.payload?.choice ?? b.decision;
    emit({ type: "gate.decided", thread_id: t.thread_id, step: t.waiting.step, data: { gate: t.waiting.step, choice, by: "vscode", why: b.why } });
    t.waiting = null;
    t.status = "running";
    t.phase = choice === "change" ? "spec" : "red";
    t.updated_at = now();
    return send(res, 200, t);
  }
  if ((r = /^\/api\/approvals\/([^/]+)\/decide$/.exec(p))) return send(res, 200, { ok: true });
  if (p === "/api/projects/shop/runs") return send(res, 200, Object.values(threads).map((t) => runRow(t, t.thread_id === "t-refund" ? { branch: "keel/add-refunds" } : {})));
  if ((r = /^\/api\/projects\/shop\/flows\/([^/]+)$/.exec(p))) {
    const t = threads[decodeURIComponent(r[1])];
    return t ? send(res, 200, { thread: t, workflow }) : send(res, 404, { error: "No such flow" });
  }
  if (p === "/api/projects/shop/flows" && m === "POST") {
    const b = await readBody(req);
    const id = `t-${Object.keys(threads).length + 1}`;
    threads[id] = { thread_id: id, project_id: "shop", workflow_id: b.workflow_id, title: b.title, status: "running", phase: "spec", acs: [], usage: {}, updated_at: now() };
    return send(res, 200, threads[id]);
  }
  if ((r = /^\/api\/threads\/([^/]+)\/stop$/.exec(p))) {
    const t = threads[decodeURIComponent(r[1])];
    if (t) {
      t.status = "stopped";
      t.waiting = null;
    }
    return send(res, 200, t ?? {});
  }
  if ((r = /^\/api\/threads\/([^/]+)\/history$/.exec(p))) return send(res, 200, [{ id: "cp1", n: 1, step: "spec" }]);
  if ((r = /^\/api\/threads\/([^/]+)\/rewind$/.exec(p))) {
    const t = threads[decodeURIComponent(r[1])];
    if (t) t.status = "running";
    return send(res, 200, t ?? {});
  }
  if (p === "/api/projects/shop/workflows")
    return send(res, 200, [workflow, { id: "fix", name: "fix", steps: [] }, { id: "quick", name: "quick", steps: [] }]);
  if (p === "/api/providers/models")
    return send(res, 200, { claude: { label: "Claude", modes: { subscription: [{ id: "sonnet", label: "Sonnet" }, { id: "opus", label: "Opus" }] }, efforts: ["low", "medium", "high"] } });
  if (p === "/api/jobs") return send(res, 200, jobs);
  if ((r = /^\/api\/jobs\/([^/]+)\/steps$/.exec(p))) return send(res, 200, { steps: jobSteps[decodeURIComponent(r[1])] ?? [], running: r[1] === "c-code" });
  // review
  if (p === "/api/projects/shop/review/prs")
    return send(res, 200, {
      me: "ana",
      prs: [
        { number: 7, title: "Cart: totals in integer cents", author: "bo", branch: "feat/cents", base: "main", updated_at: ago(30), review_requested: true },
        { number: 9, title: "Checkout: show the SHOPS-1234 notice", author: "alex", branch: "feat/notice", base: "main", updated_at: ago(300), draft: true },
      ],
      counts: { review: 2 },
    });
  if (p === "/api/projects/shop/review/view") {
    const key = url.searchParams.get("key");
    if (key === "pr:7") return send(res, 200, reviewView());
    return send(res, 200, { key, number: Number(key?.slice(3)), title: "Checkout: show the SHOPS-1234 notice", base: "main", branch: "feat/notice", files: [], threads: [], me: "ana" });
  }
  if (p === "/api/projects/shop/review/file") {
    const side = url.searchParams.get("side") === "base" ? "base" : "head";
    const path = url.searchParams.get("path");
    const text = wholeFiles[`${side}:${path}`];
    if (text == null) return send(res, 404, { error: `${path} is not in ${side === "base" ? "main" : "#7"}` });
    return send(res, 200, { path, ref: side === "base" ? "main" : "#7", sha: side === "base" ? "b".repeat(40) : "a".repeat(40), text, truncated: false });
  }
  if (p === "/api/projects/shop/review/diff") {
    const path = url.searchParams.get("path");
    return send(res, 200, { path, diff: diffs[path] ?? "" });
  }
  if ((r = /^\/api\/projects\/shop\/review\/threads\/([^/]+)\/(reply|resolve)$/.exec(p)) && m === "POST") {
    const b = await readBody(req);
    const id = decodeURIComponent(r[1]);
    reviewThreads = reviewThreads.map((t) => {
      if (t.id !== id) return t;
      if (r[2] === "reply") return { ...t, comments: [...t.comments, { id: `c${Date.now()}`, author: "ana", body: b.body, at: now() }] };
      return { ...t, resolved: !!b.resolved };
    });
    return send(res, 200, reviewView());
  }
  // KeelBot
  if (p === "/api/projects/shop/helper/sessions" && m === "GET") return send(res, 200, [{ ...chat, messages: undefined }]);
  if (p === "/api/projects/shop/helper/sessions" && m === "POST") return send(res, 200, chat);
  if ((r = /^\/api\/projects\/shop\/helper\/sessions\/([^/]+)$/.exec(p))) {
    if (m === "PATCH") {
      const b = await readBody(req);
      if (b.mode) chat.mode = b.mode;
      if (b.model) chat.model = b.model;
    }
    return send(res, 200, chat);
  }
  if ((r = /^\/api\/projects\/shop\/helper\/sessions\/([^/]+)\/turn$/.exec(p)) && m === "POST") {
    const b = await readBody(req);
    const n = chat.messages.length + 1;
    chat.messages.push({ n, role: "user", text: b.text, at: now() });
    chat.status = "running";
    const callId = `hc${n}`;
    const answer = "Looking at src/cart.ts:13: `withTax()` rounds with **Math.round**, so 0.5 cents goes up.";
    let i = 0;
    const timer = setInterval(() => {
      i += 12;
      emit({ type: "helper.delta", thread_id: "h1", call_id: callId, data: { text: answer.slice(0, i) } });
      if (i >= answer.length) {
        clearInterval(timer);
        chat.messages.push({ n: n + 1, role: "assistant", call_id: callId, text: answer, at: now() });
        chat.status = "idle";
        emit({ type: "helper.finished", thread_id: "h1", call_id: callId, data: {} });
      }
    }, 150);
    return send(res, 200, { session: "h1", call_id: callId, n });
  }
  if ((r = /^\/api\/projects\/shop\/helper\/sessions\/([^/]+)\/stop$/.exec(p))) {
    chat.status = "idle";
    return send(res, 200, chat);
  }
  if ((r = /^\/api\/projects\/shop\/helper\/sessions\/([^/]+)\/changes$/.exec(p))) return send(res, 200, []);
  if ((r = /^\/api\/projects\/shop\/helper\/sessions\/([^/]+)\/(undo|done)$/.exec(p))) return send(res, 200, r[2] === "undo" ? [] : { ok: true, sha: "0123abcd", message: "demo" });
  send(res, 404, { error: `The demo keel has no ${m} ${p}` });
});

server.listen(PORT, "127.0.0.1", () => console.log(`demo keel on http://127.0.0.1:${PORT} (token: ${TOKEN})`));
