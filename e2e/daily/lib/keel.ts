// A small helper for the daily-use journeys: the throw-away keel's api, the browser sign-in, a safe gate driver.
// It depends on nothing else in e2e/ (another branch writes e2e/lib.py; this is its own).
import http from "node:http";
import { expect, type Page, type TestInfo } from "@playwright/test";

export const URL_ = (process.env.KEEL_URL || "http://127.0.0.1:18188").replace(
  /\/$/,
  "",
);
export const KEY = process.env.KEEL_KEY || "";
export const REAL = process.env.KEEL_E2E_REAL === "1";
export const SHOP = process.env.KEEL_E2E_SHOP || "";
export const NOTES = process.env.KEEL_E2E_NOTES || "";
export const KEEL2 = process.env.KEEL2_BIN || "keel2";
export const WORK = process.env.KEEL_E2E_WORK || "/tmp";

export type Reply = {
  status: number;
  headers: Record<string, string>;
  text: string;
  json: any;
};

/** A raw request: the path is sent exactly as given (fetch would tidy `;x=1` and `%61` away). */
export function raw(
  method: string,
  pathq: string,
  opts: {
    key?: string | null;
    body?: unknown;
    headers?: Record<string, string>;
  } = {},
): Promise<Reply> {
  const u = new URL(URL_);
  const data = opts.body === undefined ? undefined : JSON.stringify(opts.body);
  const key = opts.key === undefined ? process.env.KEEL_KEY || KEY : opts.key;
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        host: u.hostname,
        port: u.port,
        method,
        path: pathq,
        headers: {
          ...(key ? { Authorization: `Bearer ${key}` } : {}),
          ...(data
            ? {
                "content-type": "application/json",
                "content-length": Buffer.byteLength(data),
              }
            : {}),
          ...opts.headers,
        },
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          let json: any = null;
          try {
            json = JSON.parse(text);
          } catch {
            /* not json */
          }
          resolve({
            status: res.statusCode || 0,
            headers: res.headers as Record<string, string>,
            text,
            json,
          });
        });
      },
    );
    req.setTimeout(30_000, () =>
      req.destroy(new Error(`timeout ${method} ${pathq}`)),
    );
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

/** The api as a signed-in client: /api + path, the sign-in key as a bearer token. */
export async function api(
  method: string,
  p: string,
  body?: unknown,
  ok: number[] = [200],
): Promise<any> {
  const r = await raw(method, "/api" + p, { body });
  if (!ok.includes(r.status))
    throw new Error(
      `${method} /api${p} -> ${r.status}: ${r.text.slice(0, 400)}`,
    );
  return r.json ?? r.text;
}

/** A browser signs in with a one-time link (POST /api/web/code). */
export async function signIn(page: Page): Promise<void> {
  // the one-time link is for keel2 only: it asks for keel's internal token (native: $KEEL2_HOME/<name>/run/token)
  const r = await raw("POST", "/api/web/code", { key: null, body: {}, headers: { "X-Keel-Token": process.env.KEEL_TOKEN || "" } });
  if (r.status !== 200 || !r.json?.path)
    throw new Error(`sign-in link: ${r.status} ${r.text.slice(0, 200)}`);
  // the first-run welcome covers the whole page on a new keel: the journeys are past it (as "Skip, I know keel")
  await raw("PUT", "/api/help/welcome", { body: { seen: true, how: "skipped" } });
  await page.goto(URL_ + r.json.path);
  await page.waitForLoadState("domcontentloaded");
}

export async function projects(): Promise<
  { id: string; name: string; path?: string }[]
> {
  return api("GET", "/projects");
}
export async function projectNamed(name: string) {
  const p = (await projects()).find(
    (x) => x.name === name || (x.path || "").endsWith("/" + name),
  );
  if (!p) throw new Error(`no project named ${name}`);
  return p;
}

/** Report a keel bug without ending the journey: steps, expected, actual. The screenshot comes with the test. */
export function bug(
  info: TestInfo,
  b: { title: string; steps: string[]; expected: string; actual: string },
): void {
  info.annotations.push({
    type: "bug",
    description: `${b.title}\nSteps:\n${b.steps.map((s, i) => `  ${i + 1}. ${s}`).join("\n")}\nExpected: ${b.expected}\nActual: ${b.actual}`,
  });
}
export function note(info: TestInfo, text: string): void {
  info.annotations.push({ type: "note", description: text });
}

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function until<T>(
  what: string,
  fn: () => Promise<T | null | undefined | false>,
  timeoutMs = 120_000,
  everyMs = 1500,
): Promise<T> {
  const end = Date.now() + timeoutMs;
  let last: unknown;
  while (Date.now() < end) {
    try {
      const v = await fn();
      if (v) return v;
    } catch (e) {
      last = e;
    }
    await sleep(everyMs);
  }
  throw new Error(
    `timed out waiting for ${what}${last ? ` (last error: ${String(last).slice(0, 200)})` : ""}`,
  );
}

// ---------------------------------------------------------------------------------------------------------------
// The SAFE gate driver. It presses only decisions the gate lists (options / choices), stops when the same gate
// (step + criterion + title) comes back more than 3 times, never approves a token cap / usage / budget question or a
// "fix" it was not asked to, and checks every resume's HTTP status. (A blind approve once looped 296 times on a real
// model: docs in the person's notes "e2e driver safety".)
// ---------------------------------------------------------------------------------------------------------------
export type Waiting = {
  step: string;
  kind: string;
  title: string;
  detail?: string;
  options?: string[];
  choices?: string[] | Record<string, string>;
  choice_info?: { name: string; default?: boolean; why?: string }[];
  questions?: {
    id: string;
    question: string;
    multiple?: boolean;
    options: { label: string; recommended?: boolean }[];
  }[];
  id?: string;
};
export type Thread = {
  thread_id: string;
  status: string;
  current?: string | null;
  ac?: string | null;
  waiting?: Waiting;
  error?: string;
  workflow_id?: string;
  title?: string;
  phase?: string;
};
export type DriveResult = {
  status: string;
  stoppedBecause:
    | "done"
    | "stop-at"
    | "repeat"
    | "cap"
    | "needs-person"
    | "limit"
    | "failed"
    | "stopped";
  gates: string[];
  thread: Thread;
};

export const threadOf = (r: any): Thread => (r && r.thread ? r.thread : r);

export async function currentThread(pid: string, tid: string): Promise<Thread> {
  return threadOf(await api("GET", `/projects/${pid}/flows/${tid}`));
}

export async function driveFlow(
  pid: string,
  tid: string,
  opts: {
    stopAtStep?: RegExp;
    stopAtTitle?: RegExp;
    maxDecisions?: number;
    waitMs?: number;
    log?: (s: string) => void;
  } = {},
): Promise<DriveResult> {
  const seen = new Map<string, number>();
  const gates: string[] = [];
  const max = opts.maxDecisions ?? 25;
  const end = Date.now() + (opts.waitMs ?? 900_000);
  let decisions = 0;
  const log = opts.log || (() => undefined);
  let th = await currentThread(pid, tid);
  while (Date.now() < end) {
    th = await currentThread(pid, tid);
    if (th.status === "done" || th.status === "completed")
      return { status: th.status, stoppedBecause: "done", gates, thread: th };
    if (th.status === "failed")
      return { status: th.status, stoppedBecause: "failed", gates, thread: th };
    if (th.status === "stopped")
      return {
        status: th.status,
        stoppedBecause: "stopped",
        gates,
        thread: th,
      };
    const w = th.waiting;
    if (th.status !== "waiting" || !w) {
      await sleep(2000);
      continue;
    }

    const key = `${w.step}|${th.ac ?? ""}|${w.title}`;
    const n = (seen.get(key) || 0) + 1;
    seen.set(key, n);
    gates.push(`${w.kind}: ${w.step} - ${w.title}`);
    log(`gate #${n} ${key}`);
    if (opts.stopAtStep?.test(w.step) || opts.stopAtTitle?.test(w.title))
      return {
        status: th.status,
        stoppedBecause: "stop-at",
        gates,
        thread: th,
      };
    if (n > 3)
      return { status: th.status, stoppedBecause: "repeat", gates, thread: th };
    if (
      /token cap reached|cap reached/i.test(w.title) ||
      ["budget", "usage"].includes(w.kind)
    )
      return { status: th.status, stoppedBecause: "cap", gates, thread: th };
    if (w.kind === "fix")
      return {
        status: th.status,
        stoppedBecause: "needs-person",
        gates,
        thread: th,
      };
    if (++decisions > max)
      return { status: th.status, stoppedBecause: "limit", gates, thread: th };

    const options = w.options ?? [];
    const names = Array.isArray(w.choices)
      ? w.choices
      : w.choices
        ? Object.keys(w.choices)
        : [];
    const body: {
      decision: string;
      payload?: Record<string, unknown>;
      why?: string;
    } = { decision: "approve" };
    if (w.kind === "clarify" && w.questions?.length) {
      // answer with an offered option only: the recommended one, else the first
      const answers: Record<string, unknown> = {};
      for (const q of w.questions) {
        const pick = q.options.find((o) => o.recommended) ?? q.options[0];
        if (!pick)
          return {
            status: th.status,
            stoppedBecause: "needs-person",
            gates,
            thread: th,
          };
        answers[q.id] = q.multiple ? [pick.label] : pick.label;
      }
      body.payload = { answers };
    } else if (names.length) {
      const info =
        w.choice_info?.find((c) => c.default) ??
        w.choice_info?.find((c) => c.why !== "required" && c.name !== "reject");
      const choice = info?.name ?? names.find((c) => c !== "reject");
      if (!choice)
        return {
          status: th.status,
          stoppedBecause: "needs-person",
          gates,
          thread: th,
        };
      body.payload = { choice };
    } else if (!options.includes("approve")) {
      return {
        status: th.status,
        stoppedBecause: "needs-person",
        gates,
        thread: th,
      };
    }
    const r = await raw("POST", `/api/threads/${tid}/resume`, { body });
    // 409 "running, not waiting": somebody else (the UI, the TUI) answered this gate a moment ago. Read the flow again
    // (this still counts against the repeat limit above); every other refusal stops the driver.
    if (r.status === 409 && /not waiting/i.test(r.text)) {
      await sleep(2500);
      continue;
    }
    if (r.status < 200 || r.status >= 300)
      throw new Error(
        `resume refused (${r.status}) at ${key}: ${r.text.slice(0, 300)}`,
      );
    await sleep(1500);
  }
  return { status: th.status, stoppedBecause: "limit", gates, thread: th };
}

/** Start a flow through the api (the UI journeys start theirs by clicking). The fake model is allowed unless --real. */
export async function startFlow(
  pid: string,
  workflow: string,
  title: string,
  extra: Record<string, unknown> = {},
): Promise<string> {
  const res = await api("POST", `/projects/${pid}/flows`, {
    workflow_id: workflow,
    title,
    allow_fake: !REAL,
    allow_dirty: true,
    ...extra,
  });
  const tid = res.thread_id || res.thread?.thread_id;
  if (!tid)
    throw new Error("no thread id in " + JSON.stringify(res).slice(0, 300));
  return tid;
}

/** The project's wait for the fake model, e2e style: the cheaper model is the fake one. */
export async function useFakeModel(): Promise<void> {
  if (REAL) return;
  await api(
    "PUT",
    "/settings/general",
    { cheaper_model: { provider: "fake", mode: "api", model: "fake" } },
    [200, 204],
  );
}

export async function expectNoConsoleErrors(errors: string[]): Promise<void> {
  const real = errors.filter(
    (e) => !/favicon|ResizeObserver|net::ERR_ABORTED/i.test(e),
  );
  expect(real, "console errors on the page").toEqual([]);
}

export function watchConsole(page: Page): string[] {
  const errs: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errs.push(m.text());
  });
  page.on("pageerror", (e) => errs.push("pageerror: " + e.message));
  return errs;
}
