import type {
  CatalogModel,
  CatalogProvider,
  Checkpoint,
  DeviceMe,
  ErrorBody,
  FileDiff,
  FlowView,
  Health,
  HelperChange,
  HelperDone,
  HelperSession,
  InboxAnswer,
  InboxView,
  JobRow,
  JobSteps,
  Model,
  PairResponse,
  PrList,
  Project,
  RefFile,
  ReviewFile,
  ReviewView,
  RunRow,
  StartFlowBody,
  ThreadState,
  TurnStarted,
  Workflow,
} from "./models";

/** The part of `fetch` the client uses (Node's global fetch, or a fake in the tests). */
export type FetchLike = (url: string, init?: RequestInit) => Promise<Response>;

export const UNREACHABLE_HINT =
  "Check that keel runs (keel2 status) and the setting keel.url.";

/**
 * What went wrong with a call. `status` 0 = keel could not be reached (no answer at all); otherwise the HTTP status
 * with keel's own `{error, hint}`.
 */
export class KeelError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly hint?: string,
  ) {
    super(message);
    this.name = "KeelError";
  }

  get unreachable(): boolean {
    return this.status === 0;
  }

  /** The token is no longer accepted (the device was removed on the keel side, or a new sign-in key was made). */
  get deviceRemoved(): boolean {
    const m = this.message.toLowerCase();
    return (
      this.status === 401 ||
      (this.status === 403 && m.includes("device") && m.includes("removed")) ||
      (this.status === 403 && m.includes("sign-in key"))
    );
  }

  /** The words for the person: keel's message and its hint. */
  say(): string {
    return [this.message, this.hint].filter(Boolean).join(" ");
  }
}

/** Any error as words for a notification. */
export function say(e: unknown): string {
  if (e instanceof KeelError) return e.say();
  if (e instanceof Error) return e.message;
  return String(e);
}

/** "192.168.1.20:8088" → http://192.168.1.20:8088/; keeps https; drops a trailing /api and any path. null: not an address. */
export function normalizeBase(raw: string): string | null {
  let s = (raw ?? "").trim();
  if (!s) return null;
  if (!s.includes("://")) s = "http://" + s;
  let u: URL;
  try {
    u = new URL(s);
  } catch {
    return null;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  if (!u.hostname) return null;
  return `${u.protocol}//${u.host}/`;
}

export const enc = (s: string): string => encodeURIComponent(s);

export interface KeelApiOptions {
  baseUrl: string;
  /** The sign-in (an API key from keel2 auth key, or a paired device's kd_ token), sent as Bearer. */
  token: () => string | undefined;
  fetch?: FetchLike;
  /** Told when keel says the sign-in is gone (removed device, new key): the extension asks to connect again. */
  onRemoved?: () => void;
  timeoutMs?: number;
}

/**
 * A small typed client for the keel api (a port of the JetBrains plugin's KeelApi, itself the Android app's client).
 * Every call sends `Authorization: Bearer <token>` (except pairing and health) and `X-Keel-Via: vscode`.
 */
export class KeelApi {
  readonly base: string;
  private readonly fetchFn: FetchLike;
  private readonly timeoutMs: number;

  constructor(private readonly opts: KeelApiOptions) {
    const b = normalizeBase(opts.baseUrl);
    if (!b)
      throw new KeelError(
        0,
        `That is not a keel address: ${opts.baseUrl}`,
        "Use http://<ip>:<port> or https://<name>.",
      );
    this.base = b;
    this.fetchFn = opts.fetch ?? ((url, init) => fetch(url, init));
    this.timeoutMs = opts.timeoutMs ?? 30_000;
  }

  // ---------------------------------------------------------------- pairing, health
  pair(code: string, name: string): Promise<PairResponse> {
    return this.call(
      "POST",
      "/api/devices/pair",
      { code: code.trim(), name, platform: "vscode" },
      false,
    );
  }

  me(): Promise<DeviceMe> {
    return this.call("GET", "/api/devices/me");
  }

  health(): Promise<Health> {
    return this.call("GET", "/api/health", undefined, false);
  }

  // ---------------------------------------------------------------- projects, inbox
  projects(): Promise<Project[]> {
    return this.call("GET", "/api/projects");
  }

  inbox(): Promise<InboxView> {
    return this.call("GET", "/api/inbox");
  }

  /** Answer the question a flow waits at. 409: the flow now asks something else (reload). */
  answer(threadId: string, answer: InboxAnswer): Promise<ThreadState> {
    return this.call(
      "POST",
      `/api/inbox/${enc(threadId)}/act`,
      stripNulls(answer),
    );
  }

  /** A command that waits for the person's OK (kind permission): once | always | deny. */
  decideApproval(id: string, decision: string, why = ""): Promise<unknown> {
    return this.call("POST", `/api/approvals/${enc(id)}/decide`, {
      decision,
      why,
    });
  }

  // ---------------------------------------------------------------- flows
  runs(pid: string, limit = 30): Promise<RunRow[]> {
    return this.call("GET", `/api/projects/${enc(pid)}/runs?limit=${limit}`);
  }

  flowOf(pid: string, tid: string): Promise<FlowView> {
    return this.call("GET", `/api/projects/${enc(pid)}/flows/${enc(tid)}`);
  }

  history(tid: string): Promise<Checkpoint[]> {
    return this.call("GET", `/api/threads/${enc(tid)}/history`);
  }

  stop(tid: string): Promise<ThreadState> {
    return this.call("POST", `/api/threads/${enc(tid)}/stop`, {});
  }

  /** Resume a stopped flow from a saved step (the web's Resume: a rewind to the newest checkpoint). */
  rewind(tid: string, checkpointId: string): Promise<ThreadState> {
    return this.call("POST", `/api/threads/${enc(tid)}/rewind`, {
      checkpoint_id: checkpointId,
    });
  }

  workflows(pid: string): Promise<Workflow[]> {
    return this.call("GET", `/api/projects/${enc(pid)}/workflows`);
  }

  startFlow(pid: string, body: StartFlowBody): Promise<ThreadState> {
    return this.call(
      "POST",
      `/api/projects/${enc(pid)}/flows`,
      stripNulls(body),
    );
  }

  /** 0.18.57 a file as a branch, review ref or commit has it, without checking it out (the Code plugin's api) */
  fileAt(pid: string, path: string, ref: string): Promise<RefFile> {
    return this.call("GET", `/api/projects/${enc(pid)}/repo/file-at?path=${enc(path)}&ref=${enc(ref)}`);
  }

  // ---------------------------------------------------------------- the review plugin
  private r(pid: string): string {
    return `/api/projects/${enc(pid)}/review`;
  }

  /** filter: review | assigned | mine | all */
  prs(pid: string, filter: string): Promise<PrList> {
    return this.call("GET", `${this.r(pid)}/prs?filter=${enc(filter)}`);
  }

  review(pid: string, key: string, refresh = false): Promise<ReviewView> {
    return this.call(
      "GET",
      `${this.r(pid)}/view?key=${enc(key)}${refresh ? "&refresh=true" : ""}`,
    );
  }

  diff(pid: string, key: string, path: string): Promise<FileDiff> {
    return this.call(
      "GET",
      `${this.r(pid)}/diff?key=${enc(key)}&path=${enc(path)}`,
    );
  }

  /** 0.21.1 the whole file as the review's head (or base) commit has it */
  reviewFile(pid: string, key: string, path: string, side: "head" | "base"): Promise<ReviewFile> {
    return this.call(
      "GET",
      `${this.r(pid)}/file?key=${enc(key)}&path=${enc(path)}&side=${side}`,
    );
  }

  reply(
    pid: string,
    key: string,
    threadId: string,
    body: string,
  ): Promise<ReviewView> {
    return this.call("POST", `${this.r(pid)}/threads/${enc(threadId)}/reply`, {
      key,
      body,
    });
  }

  resolve(
    pid: string,
    key: string,
    threadId: string,
    resolved: boolean,
  ): Promise<ReviewView> {
    return this.call(
      "POST",
      `${this.r(pid)}/threads/${enc(threadId)}/resolve`,
      { key, resolved },
    );
  }

  // ---------------------------------------------------------------- models
  /** The model catalog the web's ModelPicker reads (GET /api/providers/models). */
  async providerModels(): Promise<CatalogProvider[]> {
    return parseCatalog(
      await this.call<unknown>("GET", "/api/providers/models"),
    );
  }

  // ---------------------------------------------------------------- KeelBot
  private h(pid: string): string {
    return `/api/projects/${enc(pid)}/helper`;
  }

  chats(pid: string): Promise<HelperSession[]> {
    return this.call("GET", `${this.h(pid)}/sessions`);
  }

  chat(pid: string, sid: string): Promise<HelperSession> {
    return this.call("GET", `${this.h(pid)}/sessions/${enc(sid)}`);
  }

  newChat(pid: string, mode: string): Promise<HelperSession> {
    return this.call("POST", `${this.h(pid)}/sessions`, { mode });
  }

  setChatMode(pid: string, sid: string, mode: string): Promise<HelperSession> {
    return this.call("PATCH", `${this.h(pid)}/sessions/${enc(sid)}`, { mode });
  }

  setChatModel(pid: string, sid: string, model: Model): Promise<HelperSession> {
    return this.call("PATCH", `${this.h(pid)}/sessions/${enc(sid)}`, {
      model: stripNulls(model),
    });
  }

  send(pid: string, sid: string, text: string): Promise<TurnStarted> {
    return this.call("POST", `${this.h(pid)}/sessions/${enc(sid)}/turn`, {
      text,
    });
  }

  stopChat(pid: string, sid: string): Promise<HelperSession> {
    return this.call("POST", `${this.h(pid)}/sessions/${enc(sid)}/stop`, {});
  }

  changes(pid: string, sid: string): Promise<HelperChange[]> {
    return this.call("GET", `${this.h(pid)}/sessions/${enc(sid)}/changes`);
  }

  undo(pid: string, sid: string, path?: string): Promise<HelperChange[]> {
    return this.call(
      "POST",
      `${this.h(pid)}/sessions/${enc(sid)}/undo`,
      path ? { path } : {},
    );
  }

  /** Keep: keel commits KeelBot's files after the checks. */
  keep(pid: string, sid: string): Promise<HelperDone> {
    return this.call("POST", `${this.h(pid)}/sessions/${enc(sid)}/done`, {});
  }

  jobSteps(callId: string, after = 0): Promise<JobSteps> {
    return this.call(
      "GET",
      `/api/jobs/${enc(callId)}/steps${after > 0 ? `?after=${after}` : ""}`,
    );
  }

  /** The project's recent agent calls (newest first). */
  jobs(pid: string, limit = 50): Promise<JobRow[]> {
    return this.call("GET", `/api/jobs?project=${enc(pid)}&limit=${limit}`);
  }

  // ---------------------------------------------------------------- live stream
  /** The SSE url: this project's events plus the notifications of every project. */
  eventsUrl(pid: string | undefined): string {
    const q = pid ? `project=${enc(pid)}&notify=all` : "project=*";
    return `${this.base}api/events?${q}`;
  }

  /** The headers of the live stream (the same sign-in as every call). */
  streamHeaders(): Record<string, string> {
    const h: Record<string, string> = {
      Accept: "text/event-stream",
      "X-Keel-Via": "vscode",
    };
    const t = this.opts.token();
    if (t) h.Authorization = `Bearer ${t}`;
    return h;
  }

  // ---------------------------------------------------------------- transport
  async call<T>(
    method: string,
    path: string,
    body?: unknown,
    auth = true,
  ): Promise<T> {
    const url = this.base.replace(/\/$/, "") + path;
    const headers: Record<string, string> = {
      Accept: "application/json",
      "X-Keel-Via": "vscode",
    };
    if (auth) {
      const t = this.opts.token();
      if (t) headers.Authorization = `Bearer ${t}`;
    }
    let payload: string | undefined;
    if (body !== undefined) payload = JSON.stringify(body);
    else if (method !== "GET" && method !== "HEAD") payload = "{}";
    if (payload !== undefined)
      headers["Content-Type"] = "application/json; charset=utf-8";
    let res: Response;
    try {
      res = await this.fetchFn(url, {
        method,
        headers,
        body: payload,
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch {
      throw new KeelError(
        0,
        `Cannot reach keel at ${new URL(this.base).host}.`,
        UNREACHABLE_HINT,
      );
    }
    let text: string;
    try {
      text = await res.text();
    } catch {
      throw new KeelError(
        0,
        `keel at ${new URL(this.base).host} stopped answering.`,
        UNREACHABLE_HINT,
      );
    }
    if (!res.ok) {
      let err: ErrorBody | undefined;
      try {
        err = JSON.parse(text) as ErrorBody;
      } catch {
        err = undefined;
      }
      const ex = new KeelError(
        res.status,
        err?.error || `keel answered ${res.status}.`,
        err?.hint ?? undefined,
      );
      if (auth && ex.deviceRemoved) this.opts.onRemoved?.();
      throw ex;
    }
    const type = res.headers.get("Content-Type") ?? "";
    if (
      text.trim() &&
      !type.includes("json") &&
      text.trimStart().startsWith("<")
    ) {
      throw new KeelError(
        res.status,
        "keel sent a web page instead of data.",
        "Check the address: it must be keel's api, not another site.",
      );
    }
    if (!text.trim()) return {} as T;
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new KeelError(
        res.status,
        "keel sent an answer the extension cannot read.",
        "Is this a keel v2 address? Update the extension or keel.",
      );
    }
  }
}

/** A body without its null / undefined fields (keel reads a missing field as "not set"). */
export function stripNulls<T extends object>(o: T): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(o))
    if (v !== null && v !== undefined) out[k] = v;
  return out as T;
}

const PROVIDER_ORDER = [
  "claude",
  "codex",
  "copilot",
  "vertex",
  "local",
  "fake",
];

/**
 * The model catalog as the web's normalizeCatalog reads it: `{provider: {label, modes: {mode: [{id, label, efforts?}]},
 * efforts}}`, or the older `{provider: [{id, label}]}`. Anything malformed is dropped.
 */
export function parseCatalog(raw: unknown): CatalogProvider[] {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  const models = (v: unknown): CatalogModel[] =>
    (Array.isArray(v) ? v : []).flatMap((m) => {
      if (!m || typeof m !== "object") return [];
      const o = m as Record<string, unknown>;
      const id = typeof o.id === "string" ? o.id.trim() : "";
      if (!id) return [];
      const label =
        typeof o.label === "string" && o.label.trim() ? o.label.trim() : id;
      const efforts = Array.isArray(o.efforts)
        ? o.efforts.filter((e): e is string => typeof e === "string")
        : undefined;
      return [{ id, label, efforts }];
    });
  const out: CatalogProvider[] = [];
  for (const [id, v] of Object.entries(raw as Record<string, unknown>)) {
    if (Array.isArray(v)) {
      const ms = models(v);
      if (ms.length)
        out.push({ id, label: id, modes: { api: ms }, efforts: [] });
    } else if (v && typeof v === "object") {
      const o = v as Record<string, unknown>;
      const modes: Record<string, CatalogModel[]> = {};
      if (o.modes && typeof o.modes === "object") {
        for (const [mode, l] of Object.entries(
          o.modes as Record<string, unknown>,
        )) {
          const ms = models(l);
          if (ms.length) modes[mode] = ms;
        }
      }
      if (!Object.keys(modes).length) continue;
      const label = typeof o.label === "string" && o.label ? o.label : id;
      const efforts = Array.isArray(o.efforts)
        ? o.efforts.filter((e): e is string => typeof e === "string")
        : [];
      out.push({ id, label, modes, efforts });
    }
  }
  const rank = (id: string) => {
    const i = PROVIDER_ORDER.indexOf(id);
    return i < 0 ? 99 : i;
  };
  return out.sort((a, b) => rank(a.id) - rank(b.id));
}

/** The efforts of one model: its own list, else its provider's. */
export function effortsOf(
  p: CatalogProvider,
  model: string,
  mode: string,
): string[] {
  return p.modes[mode]?.find((m) => m.id === model)?.efforts ?? p.efforts;
}
