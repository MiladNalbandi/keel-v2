// keel TUI's core: one keel, one project, its flow, the inbox, the running agents, the live stream and the KeelBot chat.
// No UI here: a front end (keel TUI's Ink app, an xal plugin, OpenTUI) reads `snapshot`, listens with on(), and runs
// commands (commands.ts). Every request goes through the web's own typed client (web/src/api.ts).

import { api, type EngineEvent, type FlowView, type Health, type HelperMessage, type HelperMode, type HelperSession, type Job,
  type JobStep, type Model, type Project } from "./sdk";
import { inboxApi, type InboxItem } from "../../../web/src/inboxApi";
import { kb } from "../../../plugins/keelbot/web/keelbotApi";
import { connect, setCommand, viaHeaders } from "./transport";
import { subscribe, type LiveState, type SseMessage, type Subscription } from "./sse";
import { Mentions } from "./mentions";
import { loadPlugins, offHere, type OffPlugin, type TuiPlugins } from "./plugins";
import { get, post, put } from "./sdk";
import type { HelperChange, HelperDone, HelperMention } from "./sdk";
import type { Profiler } from "./profile";
import { memorySettings, type SettingsStore } from "./settings";
import type { ToolCall } from "./tools";
import { handleMessage, type StreamHost } from "./stream";

export type ChatState = {
  session: HelperSession | null;
  mode: HelperMode;
  model: Model | null;
  busy: boolean;
  /** the running turn's agent call, and its live steps */
  callId: string | null;
  steps: JobStep[];
  /** the text KeelBot is writing now (helper.delta: the current block so far; "" once that block is a whole step) */
  stream: string;
  /** when the running turn started (ms), and how much text it wrote so far (for "~N tokens") */
  startedAt: number | null;
  wrote: number;
  /** 0.18.23 what KeelBot thinks in this turn so far (helper.thinking.delta), and when it last thought (ms) */
  thinking?: string;
  thoughtAt?: number | null;
  /** the running turn's tool calls, one each (helper.tool; from helper.step with an engine that does not send it) */
  tools: ToolCall[];
};

/** keel 0.18.8 a login keel can use (Connections): its name and whether its chosen way works now. */
export type Login = { id: string; label: string; ready: boolean };
/** keel 0.18.8 what keel spent today and this month (the dashboard's budget bar). */
export type Spent = { today: { tokens: number; cost_usd: number }; month: { tokens: number; cost_usd: number } };

export type Snapshot = {
  url: string;
  /** the dashboard as the person's browser sees it */
  publicUrl: string;
  health: Health | null;
  error: string | null;
  projects: Project[];
  project: Project | null;
  flow: FlowView | null;
  /** step ids that ran in the flow (its checkpoints): which arm an if / else took */
  visited: string[];
  inbox: InboxItem[];
  running: Job[];
  /** keel 0.18.8 the logins (no fake), and the spend: the top line shows them */
  logins: Login[];
  spent: Spent | null;
  /** keel 0.18.7 each running agent's flow by its id (thread id → the flow's title), so agent lines say whose they are */
  flowTitles: Record<string, string>;
  lastStep: Record<string, JobStep | undefined>;
  /** what each running agent is writing now (agent.delta), by call id */
  liveText: Record<string, string>;
  /** what keel's plugins add to keel TUI here (contributes.tui) */
  plugins: TuiPlugins;
  /** plugins keel has whose commands are off in this project (their names give a hint, not "no command") */
  pluginsOff: OffPlugin[];
  live: LiveState;
  chat: ChatState;
};

/** KeelBot asks the person's OK for a command (helper.permission). keel 0.18.18: `warning` (the command throws work
 *  away, or a flow works in this folder) and `always` (what "Allow in this chat" keeps: `git checkout *`; empty when
 *  the command is asked every time). */
export type PermissionQuestion = { id: string; command: string; title: string; path?: string | null; warning?: string; always?: string };

export type CoreEvents = {
  change: (s: Snapshot) => void;
  notice: (text: string, ev?: EngineEvent) => void;
  /** a KeelBot turn ended: its answer (markdown) or why not */
  answer: (m: { text: string; ok: boolean; message?: HelperMessage }) => void;
  /** KeelBot asks the person's OK for a command (helper.permission): once, always (this chat) or deny */
  question: (q: PermissionQuestion) => void;
  /** keel 0.18.18 a permission question ended without this window (answered elsewhere, or KeelBot's turn ended) */
  questionGone: (id: string) => void;
  /** 0.18.6 a KeelBot chat came on screen because another keel front end (the dashboard) opened it, or at the start */
  chat: (s: HelperSession, why: string) => void;
  /** 0.18.6 the person asked KeelBot in this chat from another front end (the dashboard): their words */
  remote: (text: string, by: string) => void;
  /** 0.18.28 a message typed while KeelBot answers waits ("queued", its place), then goes ("sent") */
  queued: (m: { text: string; position: number; sent: boolean }) => void;
};

/** keel 0.18.6 what the person looks at, shared by every keel front end (api ui/UiFocus.kt): the project and its chat. */
/** How long a project picked in this window wins over a focus from another front end that names another one. */
const PICK_HOLD_MS = 3000;

export type Focus = { project: string | null; chats: Record<string, string>; by?: string | null; client?: string | null };

export type CoreOptions = {
  url: string;
  publicUrl?: string;
  /** a project id or name; otherwise the one whose folder holds `cwd`, otherwise the first */
  project?: string;
  cwd?: string;
  fetch?: typeof fetch;
  /** no SSE stream (tests, one-shot commands) */
  noLive?: boolean;
  /** --profile: times the calls, the live events and the start */
  profiler?: Profiler;
  /** keel 0.18.63 keel TUI's settings (settings.ts): `follow` says whether this window follows the other keel windows;
   *  /follow and /settings keep the person's choice there. Without it: in memory, following. */
  settings?: SettingsStore;
};


export class KeelCore {
  snapshot: Snapshot;
  private listeners: { [K in keyof CoreEvents]: Set<CoreEvents[K]> } = { change: new Set(), notice: new Set(), answer: new Set(), question: new Set(), questionGone: new Set(), chat: new Set(), remote: new Set(), queued: new Set() };
  /** @mentions: the project's files, the code graph's symbols, the flow's criteria */
  readonly mentions = new Mentions(() => this.pid, () => (this.snapshot.flow?.thread?.acs ?? []).map((a) => ({ id: a.id, title: a.title })));
  private sub: Subscription | null = null;
  private subPid: string | null | undefined = undefined;
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  /** 0.18.6 the shared focus, and this app's own id in it (so it never follows its own change) */
  private focus: Focus | null = null;
  readonly client = `tui-${Math.random().toString(36).slice(2, 10)}`;
  /** keel TUI's settings (the person's file from main.tsx; in memory without it) */
  readonly settings: SettingsStore;
  /** keel 0.18.9 the last error the app showed: /fix hands it to KeelBot */
  lastError: string | null = null;
  private sending = false;
  /** the agents the person started (/agent new): KeelBot chats that work on their own, until each one finishes */
  private spawned = new Map<string, { pid: string; title: string }>();
  private spawnTimer: ReturnType<typeof setInterval> | null = null;
  private readonly f: typeof fetch;

  constructor(private opts: CoreOptions) {
    this.f = opts.profiler ? opts.profiler.wrap(opts.fetch ?? fetch) : opts.fetch ?? fetch;
    this.settings = opts.settings ?? memorySettings();
    const url = connect(opts.url, this.f);
    this.snapshot = {
      url, publicUrl: (opts.publicUrl || url).replace(/\/+$/, ""), health: null, error: null, projects: [], project: null, flow: null,
      visited: [], inbox: [], running: [], logins: [], spent: null, flowTitles: {}, lastStep: {}, liveText: {}, plugins: { plugins: [], problems: [] }, pluginsOff: [], live: opts.noLive ? "closed" : "connecting",
      chat: { session: null, mode: "ask", model: null, busy: false, callId: null, steps: [], tools: [], stream: "", startedAt: null, wrote: 0 },
    };
  }

  on<K extends keyof CoreEvents>(k: K, fn: CoreEvents[K]): () => void {
    (this.listeners[k] as Set<CoreEvents[K]>).add(fn);
    return () => (this.listeners[k] as Set<CoreEvents[K]>).delete(fn);
  }

  private emit<K extends keyof CoreEvents>(k: K, ...args: Parameters<CoreEvents[K]>) {
    (this.listeners[k] as Set<(...a: unknown[]) => void>).forEach((fn) => fn(...args));
  }

  private set(patch: Partial<Snapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.emit("change", this.snapshot);
  }

  private setChat(patch: Partial<ChatState>) {
    this.set({ chat: { ...this.snapshot.chat, ...patch } });
  }

  get pid(): string | null {
    return this.snapshot.project?.id ?? null;
  }

  /** The first load: health, projects, the project to work on, then everything else; and the live stream. */
  async start(): Promise<Snapshot> {
    try {
      const health = await api.health();
      this.set({ health, error: null });
    } catch (e) {
      this.set({ health: null, error: e instanceof Error ? e.message : String(e) });
      return this.snapshot;
    }
    await this.loadProjects();
    await Promise.all([this.refresh(), this.loadPlugins(), this.loadLogins()]);
    if (this.pid) this.shareFocus({ project: this.pid });
    await this.openFocusChat("the chat open in keel");
    this.follow();
    return this.snapshot;
  }

  // ---- 0.18.6 the shared focus: the dashboard and keel TUI on the same project and KeelBot chat ----
  // keel 0.18.63 unless this window keeps its own (follow off: /follow, /settings): it then reads no focus, follows no
  // other window and shares nothing, at the start too (the project comes from --project, the folder, else the first)

  /** Does this window follow the other keel windows (and they it)? On unless the person switched it off. */
  get follows(): boolean {
    return this.settings.get().follow !== false;
  }

  /** Follow the other keel windows, or keep this window's own project and chat; kept for the next start. Nothing moves
   *  now: the next project or chat picked here or elsewhere is shared again (the focus is read, so it is not stale). */
  setFollow(on: boolean) {
    this.settings.set({ follow: on });
    if (on) void this.readFocus();
    else this.focus = null;
  }

  private async readFocus(): Promise<Focus | null> {
    try {
      this.focus = await get<Focus>("/ui/focus");
    } catch {
      this.focus = null;          // an older keel has none: each front end on its own, as before
    }
    return this.focus;
  }

  /** The project the person picked here last, and when (followFocus holds it for a moment). */
  private picked: { id: string; at: number } | null = null;

  private shareFocus(patch: { project?: string; chat?: string; chat_project?: string; close?: boolean }) {
    if (!this.follows) return;
    void put("/ui/focus", { ...patch, by: "tui", client: this.client }).catch(() => undefined);
  }

  /** The chat the shared focus has open on this project, on screen (its last messages come with the chat event). */
  private async openFocusChat(why: string) {
    const pid = this.pid;
    const sid = pid && this.follows ? this.focus?.chats?.[pid] : undefined;
    if (!pid || !sid || this.snapshot.chat.session?.id === sid) return;
    try {
      const s = await this.openChat(sid, true);
      this.emit("chat", s, why);
    } catch {
      /* a chat that is gone: a new one starts with the next question */
    }
  }

  /** Another front end changed the focus: follow it (another project, or another chat on this one). */
  private async followFocus(f: Focus) {
    if (!this.follows) return;        // 0.18.63 this window keeps its own project and chat
    this.focus = f;
    const who = f.by === "web" ? "the dashboard" : f.by || "another keel window";
    // 0.18.14 a focus that still names the project the person just left (the dashboard shared a chat before keel had
    // this window's pick) does not pull the window back: the person had to pick twice
    const justPicked = this.picked && this.picked.id === this.pid && Date.now() - this.picked.at < PICK_HOLD_MS;
    if (f.project && f.project !== this.pid && !justPicked) {
      if (await this.useProject(f.project, true)) this.emit("notice", `following ${who}: project ${f.project}`);
      return;
    }
    if (this.snapshot.chat.busy) return;
    await this.openFocusChat(`following ${who}`);
  }

  /** What keel's plugins add to keel TUI for this project. */
  async loadPlugins() {
    const [here, all] = await Promise.all([loadPlugins(this.pid), this.pid ? loadPlugins(null) : Promise.resolve(null)]);
    this.set({ plugins: here, pluginsOff: all ? offHere(all.plugins, here.plugins) : [] });
  }

  stop() {
    this.sub?.close();
    this.sub = null;
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    if (this.pollTimer) clearInterval(this.pollTimer);
    if (this.spawnTimer) clearInterval(this.spawnTimer);
    if (this.loginTimer) clearInterval(this.loginTimer);
  }

  async loadProjects() {
    const projects = await api.projects();
    const cur = this.snapshot.project;
    const want = this.opts.project;
    let project: Project | null = null;
    if (cur) project = projects.find((p) => p.id === cur.id) ?? null;
    if (!project && want) project = projects.find((p) => p.id === want || p.name === want) ?? null;
    // 0.18.6 the folder keel TUI starts in picks the project; else the one the dashboard (or another window) is on
    if (!project && this.opts.cwd) {
      const cwd = this.opts.cwd.replace(/\/+$/, "");
      const inside = projects.filter((p) => p.root && (cwd === p.root || cwd.startsWith(p.root.replace(/\/+$/, "") + "/")));
      project = inside.sort((a, b) => b.root.length - a.root.length)[0] ?? null;
    }
    if (this.follows) {
      if (!project && (await this.readFocus())?.project) project = projects.find((p) => p.id === this.focus!.project) ?? null;
      else if (!this.focus) await this.readFocus();
    }
    this.set({ projects, project: project ?? projects[0] ?? null });
  }

  /** Switch to another project (id or name); false when keel has none by that name. */
  async useProject(name: string, remote = false): Promise<boolean> {
    const projects = this.snapshot.projects.length ? this.snapshot.projects : await api.projects();
    const p = projects.find((x) => x.id === name || x.name === name);
    if (!p) return false;
    this.set({ project: p, flow: null, visited: [], chat: { ...this.snapshot.chat, session: null, callId: null, steps: [], tools: [], busy: false } });
    this.mentions.reset();
    // shared at once, not after the project's state is read: the other front ends follow sooner, and the window where a
    // focus naming the old project can arrive is short (followFocus holds this pick meanwhile)
    if (!remote) {
      this.picked = { id: p.id, at: Date.now() };
      this.shareFocus({ project: p.id });
    }
    await Promise.all([this.refresh(), this.loadPlugins()]);
    await this.openFocusChat(remote ? "the chat open there" : "the chat open in keel");
    this.follow();
    return true;
  }

  /** keel 0.18.8 the logins keel can use and whether each works now (read again every minute: a login can expire). */
  private loginTimer: ReturnType<typeof setInterval> | null = null;
  async loadLogins() {
    const read = async () => {
      try {
        const c = await api.connections();
        const logins = c.providers.filter((x) => x.id !== "fake")
          .map((x) => ({ id: x.id, label: x.label, ready: !!x.modes.find((m) => m.id === x.selected)?.ready }));
        this.set({ logins });
      } catch {
        /* an older keel or a short outage: the top line leaves them out */
      }
    };
    await read();
    if (!this.loginTimer && !this.opts.noLive) this.loginTimer = setInterval(() => void read(), 60_000);
  }

  /** The project's flow, its checkpoints, the inbox, the running agents. */
  async refresh(): Promise<Snapshot> {
    const pid = this.pid;
    const [flow, inbox, running, projects, spent] = await Promise.all([
      pid ? api.flow(pid).catch(() => null) : Promise.resolve(null),
      inboxApi.list().then((v) => v.items).catch(() => [] as InboxItem[]),
      api.jobs({ status: "running", limit: 50 }).catch(() => [] as Job[]),
      api.projects().catch(() => this.snapshot.projects),
      pid ? api.budgetNow(pid).then((b) => ({ today: b.today, month: b.month })).catch(() => this.snapshot.spent) : Promise.resolve(this.snapshot.spent),
    ]);
    let visited: string[] = [];
    if (flow?.thread) {
      const hist = await api.history(flow.thread.thread_id).catch(() => []);
      visited = hist.map((c) => c.step);
    }
    const project = pid ? projects.find((p) => p.id === pid) ?? this.snapshot.project : this.snapshot.project;
    const lastStep = { ...this.snapshot.lastStep };
    for (const k of Object.keys(lastStep)) if (!running.some((j) => j.id === k)) delete lastStep[k];
    const liveText = { ...this.snapshot.liveText };
    for (const k of Object.keys(liveText)) if (!running.some((j) => j.id === k)) delete liveText[k];
    // the flows the running agents work for: their titles (one runs list per project that has an agent at work)
    const flowTitles = { ...this.snapshot.flowTitles };
    const unknown = [...new Set(running.filter((j) => j.thread_id && !flowTitles[j.thread_id]).map((j) => j.project_id))];
    await Promise.all(unknown.map((p) => api.runs(p, undefined, 30).then((rows) => rows.forEach((r) => (flowTitles[r.thread_id] = r.title)))
      .catch(() => undefined)));
    this.set({ flow, inbox, running, flowTitles, projects, project, visited, lastStep, liveText, spent });
    return this.snapshot;
  }

  /** Refresh soon (many events arrive together: one refresh for them, like the web's bump). */
  bump(ms = 300) {
    if (this.refreshTimer) return;
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      void this.refresh().catch(() => undefined);
    }, ms);
  }

  // ------------------------------------------------------------------ the live stream

  /** One stream: this project's events plus the notifications of every project (as the web's one stream per tab). */
  follow() {
    if (this.opts.noLive) return;
    if (this.sub && this.subPid === this.pid) return;
    this.sub?.close();
    this.subPid = this.pid;
    const url = api.eventsUrl(this.pid, true);      // the transport puts keel's address in front
    this.sub = subscribe(url, (m) => this.onMessage(m), {
      fetch: this.f, headers: viaHeaders, onState: (s) => { if (s !== "closed") this.set({ live: s }); },
    });
  }

  /** One message of the stream (public for tests and other front ends that bring their own stream). */
  onMessage(m: SseMessage) {
    handleMessage(this.streamHost(), m);
  }

  private streamHost(): StreamHost {
    const k = this;
    return {
      get client() { return k.client; },
      get pid() { return k.pid; },
      get snapshot() { return k.snapshot; },
      get sending() { return k.sending; },
      get profiler() { return k.opts.profiler; },
      followFocus: (f) => k.followFocus(f),
      loadProjects: () => k.loadProjects(),
      bump: () => k.bump(),
      set: (p) => k.set(p),
      setChat: (p) => k.setChat(p),
      emit: (e, ...args) => k.emit(e, ...args),
      dropRunning: (id) => k.dropRunning(id),
      startPolling: () => {
        if (k.pollTimer) clearInterval(k.pollTimer);
        k.pollTimer = setInterval(() => void k.pollTurn(), 3000);
      },
      finishTurn: (s) => k.finishTurn(s),
    };
  }

  // ------------------------------------------------------------------ KeelBot

  /** KeelBot's mode for the next message. 0.18.19 the open chat goes on in it where it can (the engine keeps the
   *  chat and tells KeelBot of the change): Ask, Edit and Fix share the project folder, a side session moves between
   *  Side and Ask in its own worktree. Into or out of a worktree, or with no chat open, the next message starts a new
   *  chat. Answers true when the open chat goes on. */
  async setChatMode(mode: HelperMode): Promise<boolean> {
    const pid = this.pid;
    const s = this.snapshot.chat.session;
    if (pid && s && s.mode === mode) return true;
    if (pid && s && sameChatMode(s, mode)) {
      this.setChat({ mode });               // the status line changes at once
      try {
        const next = await kb.patch(pid, s.id, { mode });
        if (this.snapshot.chat.session?.id === s.id) this.setChat({ mode: next.mode, session: { ...this.snapshot.chat.session, ...next } });
        return true;
      } catch {
        // the engine said no (a flow moved on): a new chat in that mode, as before
      }
    }
    this.setChat({ mode, session: null, callId: null, steps: [], tools: [] });
    return false;
  }

  async setChatModel(model: Model) {
    const pid = this.pid;
    const s = this.snapshot.chat.session;
    if (pid && s) {
      const next = await kb.patch(pid, s.id, { model });
      this.setChat({ model, session: next });
    } else this.setChat({ model });
  }

  /** Earlier chats of this project, newest first. */
  async chats(): Promise<HelperSession[]> {
    if (!this.pid) return [];
    const all = await kb.sessions(this.pid);
    return [...all].sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0));
  }

  /** Open an earlier chat: its messages come back with it. */
  async openChat(sid: string, remote = false): Promise<HelperSession> {
    if (!this.pid) throw new Error("no project");
    const s = await kb.session(this.pid, sid);
    const busy = !!s.busy || s.status === "running";
    this.setChat({ session: s, mode: s.mode, model: s.model, callId: null, steps: [], tools: [], stream: "", busy, startedAt: busy ? Date.now() : null, wrote: 0, thinking: "", thoughtAt: null });
    // a chat at work (an agent from /agent new): follow it from here; its answer comes as for the person's own turn
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = busy ? setInterval(() => void this.pollTurn(), 3000) : null;
    if (!remote) this.shareFocus({ chat: s.id, chat_project: this.pid! });
    return s;
  }

  /** Start an agent: a KeelBot chat of its own (side: its own worktree and branch, so it never touches this folder; ask:
   *  it only reads) that works on the task while the person goes on. A notice says when it is finished. */
  async spawnAgent(task: string, mode: HelperMode = "side"): Promise<HelperSession> {
    const pid = this.pid;
    if (!pid) throw new Error("no project: /project picks one");
    const model = this.snapshot.chat.model;
    setCommand("agent");
    try {
      const s = await kb.create(pid, { mode, ...(model ? { model } : {}), title: task.slice(0, 60) });
      await kb.turn(pid, s.id, { text: task, ...(model ? { model } : {}) });
      this.spawned.set(s.id, { pid, title: s.title || task.slice(0, 60) });
      if (!this.spawnTimer) this.spawnTimer = setInterval(() => void this.watchSpawned(), 4000);
      return s;
    } finally {
      setCommand("");
    }
  }

  /** The agents the person started that are still at work (their chat ids). */
  spawnedAtWork(): string[] {
    return [...this.spawned.keys()];
  }

  private async watchSpawned() {
    for (const [sid, a] of [...this.spawned]) {
      try {
        const s = await kb.session(a.pid, sid);
        if (s.busy || s.status === "running") continue;
        this.spawned.delete(sid);
        // the chat on screen says it itself (its answer); the others with a notice
        if (this.snapshot.chat.session?.id !== sid) this.emit("notice", `agent "${a.title}" ${s.status === "failed" ? "failed" : "is finished"} · /agent opens it`);
      } catch {
        /* look again next time */
      }
    }
    if (!this.spawned.size && this.spawnTimer) {
      clearInterval(this.spawnTimer);
      this.spawnTimer = null;
    }
  }

  newChat() {
    this.setChat({ session: null, callId: null, steps: [], tools: [], busy: false });
    if (this.pid) this.shareFocus({ close: true, chat_project: this.pid });
  }

  /** Ask KeelBot: the answer streams as `chat.steps`, and arrives with the "answer" event. `mentions`: what the person
   *  pointed at with @ (files, symbols, criteria). */
  async send(text: string, mentions: HelperMention[] = [], images: string[] = []): Promise<HelperSession | null> {
    const pid = this.pid;
    if (!pid) throw new Error("no project: /project picks one");
    // 0.18.28 typed while KeelBot answers: no "still answering" refusal; it waits and goes after this answer (Esc or
    // Ctrl+C stops the answer and sends it now)
    if (this.snapshot.chat.busy) {
      this.queue.push({ text, mentions, images });
      this.emit("queued", { text, position: this.queue.length, sent: false });
      return null;
    }
    setCommand("KeelBot");                                   // the timeline: "via keel TUI · KeelBot"
    try {
      return await this.sendTurn(pid, text, mentions, images);
    } finally {
      setCommand("");
    }
  }

  // ---- Fix and Side: the files KeelBot changed, Keep (the checks, then keel's commit), Undo ----

  private chatOf(): { pid: string; sid: string } {
    const pid = this.pid;
    const s = this.snapshot.chat.session;
    if (!pid || !s) throw new Error("No KeelBot chat yet: /mode fix, then ask it to change something.");
    return { pid, sid: s.id };
  }

  /** Every file KeelBot changed in this chat (not kept or undone yet); `turn`: only the files that answer changed. */
  async changes(turn?: number): Promise<HelperChange[]> {
    const { pid, sid } = this.chatOf();
    return kb.changes(pid, sid, turn);
  }

  /** Summarise the open chat's stored history into a fresh engine session: nothing in `helper_messages` is lost. */
  async compact(): Promise<{ before: number; after: number }> {
    const { pid, sid } = this.chatOf();
    return post<{ before: number; after: number }>(
      `/projects/${encodeURIComponent(pid)}/helper/sessions/${encodeURIComponent(sid)}/compact`);
  }

  /** P1-3 Write the open chat as Markdown into the project's .keel/chats/<sid>.md; nothing stored changes. */
  async exportChat(): Promise<{ path: string; messages: number }> {
    const { pid, sid } = this.chatOf();
    return post<{ path: string; messages: number }>(
      `/projects/${encodeURIComponent(pid)}/helper/sessions/${encodeURIComponent(sid)}/export`);
  }

  /** 0.18.18 The person's earlier prompts in this project's KeelBot chats, newest first, each once: keel keeps them
   *  (the stored user messages), so ↑ / ↓ find them after a restart and on another computer. [] with no project. */
  async prompts(): Promise<string[]> {
    const pid = this.pid;
    if (!pid) return [];
    return get<string[]>(`/projects/${encodeURIComponent(pid)}/helper/prompts?limit=50`);
  }

  /** 0.18.18 The last `!command`'s output that goes with the person's next question to KeelBot (once), as @mentions
   *  do; null when there is none (a refused command keeps none). */
  shell: ShellRun | null = null;

  /** 0.18.18 keel TUI's `!command`: keel runs it in the chat's folder through its guard (a command that changes
   *  something waits for the person's OK on a permission card, as KeelBot's own do; Ask mode runs only what changes
   *  nothing). Opens a chat first when none is open. Its output is kept for the next question. */
  async runShell(command: string): Promise<ShellRun & { refused: string | null }> {
    const pid = this.pid;
    if (!pid) throw new Error("no project: /project picks one");
    setCommand(`!${command.split(/\s+/)[0]}`);
    try {
      const s = await this.ensureChat(pid, `!${command}`);
      const r = await post<ShellRun & { refused: string | null }>(
        `/projects/${encodeURIComponent(pid)}/helper/sessions/${encodeURIComponent(s.id)}/run`, { command });
      this.shell = r.refused ? null : { command: r.command, exit: r.exit, output: r.output };
      return r;
    } finally {
      setCommand("");
    }
  }

  /** P1-3 Name the open chat: the session's existing PATCH {title}; the engine keeps it (a first turn no longer names it). */
  async rename(title: string): Promise<HelperSession> {
    const { pid, sid } = this.chatOf();
    const next = await kb.patch(pid, sid, { title });
    this.setChat({ session: { ...this.snapshot.chat.session!, ...next } });
    return next;
  }

  /** Keep one file, some, or (no paths) all: the dashboard's Done, with keel 0.18.3's `paths`. */
  async keep(paths?: string[], message = ""): Promise<HelperDone> {
    const { pid, sid } = this.chatOf();
    return post<HelperDone>(`/projects/${encodeURIComponent(pid)}/helper/sessions/${encodeURIComponent(sid)}/done`,
      { ...(message.trim() ? { message: message.trim() } : {}), ...(paths?.length ? { paths } : {}) });
  }

  /** 0.18.19 Undo KeelBot's last answer in the open chat: the files it changed go back, its messages are marked
   *  reverted, and the question comes back ({n, text, files, messages}). Each call takes one more answer back. */
  async undoTurn(): Promise<TurnUndo> {
    const { pid, sid } = this.chatOf();
    return post<TurnUndo>(`/projects/${encodeURIComponent(pid)}/helper/sessions/${encodeURIComponent(sid)}/undo-turn`);
  }

  /** 0.18.19 Redo the answer undoTurn took back, while no new message was sent. */
  async redoTurn(): Promise<TurnUndo> {
    const { pid, sid } = this.chatOf();
    return post<TurnUndo>(`/projects/${encodeURIComponent(pid)}/helper/sessions/${encodeURIComponent(sid)}/redo-turn`);
  }

  async undo(path?: string): Promise<HelperChange[]> {
    const { pid, sid } = this.chatOf();
    return kb.undo(pid, sid, path);
  }

  /** The open chat, or a new one in the chosen mode and model (and the shared focus says so). */
  private async ensureChat(pid: string, title: string): Promise<HelperSession> {
    let s = this.snapshot.chat.session;
    if (!s) {
      const { mode, model } = this.snapshot.chat;
      s = await kb.create(pid, { mode, ...(model ? { model } : {}), title: title.slice(0, 60) });
      this.setChat({ session: s });
      this.shareFocus({ chat: s.id, chat_project: pid });
    }
    return s;
  }

  /** 0.18.28 the messages typed while KeelBot answered, in order */
  queue: { text: string; mentions: HelperMention[]; images: string[] }[] = [];

  /** 0.18.28 the next queued message, once an answer ended */
  private sendQueued() {
    const next = this.queue.shift();
    if (!next) return;
    this.emit("queued", { text: next.text, position: 0, sent: true });
    void this.send(next.text, next.mentions, next.images).catch((e) =>
      this.emit("answer", { text: e instanceof Error ? e.message : String(e), ok: false }));
  }

  /** 0.18.18 what the engine said of the last turn's images: the ones sent (full paths), the others with why not */
  turnImages: { images: string[]; skipped: { path: string; why: string }[] } = { images: [], skipped: [] };

  private async sendTurn(pid: string, text: string, mentions: HelperMention[] = [], images: string[] = []): Promise<HelperSession> {
    const s = await this.ensureChat(pid, text);
    // its helper.started may arrive before the turn's answer: it is this app's own, not one from the dashboard
    this.sending = true;
    let t;
    try {
      // 0.18.18 the last `!command`'s output goes with this question, once
      const body = { text, ...(mentions.length ? { mentions } : {}), ...(this.snapshot.chat.model ? { model: this.snapshot.chat.model } : {}),
        ...(this.shell ? { shell: this.shell } : {}), ...(images.length ? { images } : {}) };
      t = await kb.turn(pid, s.id, body);
      if ((t as { queued?: boolean }).queued) {
        // another front end's answer runs in this chat: keel queued it there
        this.emit("queued", { text, position: Number((t as { position?: number }).position ?? 1), sent: false });
        return s;
      }
      const ti = t as typeof t & { images?: string[]; images_skipped?: { path: string; why: string }[] };
      this.turnImages = { images: ti.images ?? [], skipped: ti.images_skipped ?? [] };
      this.shell = null;
    } finally {
      this.sending = false;
    }
    this.setChat({ busy: true, callId: t.call_id, steps: [], tools: [], stream: "", startedAt: Date.now(), wrote: 0, thinking: "", thoughtAt: null });
    // a missed helper.finished never leaves the chat "working": look every 3 s
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = setInterval(() => void this.pollTurn(), 3000);
    return s;
  }

  private async pollTurn() {
    const { session, busy } = this.snapshot.chat;
    if (!busy || !session || !this.pid) return;
    try {
      const s = await kb.session(this.pid, session.id);
      if (!s.busy && s.status !== "running") await this.finishTurn(s);
    } catch {
      /* try again next time */
    }
  }

  private finishing = false;

  private async finishTurn(loaded?: HelperSession) {
    if (this.finishing || !this.snapshot.chat.busy) return;
    this.finishing = true;
    try {
      if (this.pollTimer) clearInterval(this.pollTimer);
      this.pollTimer = null;
      const sid = this.snapshot.chat.session?.id;
      if (!sid || !this.pid) return;
      // the answer is stored a moment after helper.finished: wait for a message after the person's
      let s = loaded ?? (await kb.session(this.pid, sid));
      for (let i = 0; i < 10 && lastIsUser(s); i++) {
        await new Promise((r) => setTimeout(r, 300));
        s = await kb.session(this.pid, sid);
      }
      const msgs = s.messages ?? [];
      const last = msgs[msgs.length - 1];
      // the turn's call is done: no running agent any more, also when its helper.finished never came
      const callId = this.snapshot.chat.callId ?? last?.call_id;
      if (callId) this.dropRunning(callId);
      this.bump();
      this.setChat({ session: s, busy: false, callId: null, stream: "", startedAt: null });
      if (last && last.role !== "user") this.emit("answer", { text: last.text, ok: last.role === "helper", message: last });
      else this.emit("answer", { text: s.error || "KeelBot did not answer.", ok: false });
    } finally {
      this.finishing = false;
    }
    this.sendQueued();
  }

  private dropRunning(callId: string) {
    const running = this.snapshot.running;
    if (running.some((j) => j.id === callId)) this.set({ running: running.filter((j) => j.id !== callId) });
  }

  async stopChat() {
    const s = this.snapshot.chat.session;
    if (this.pid && s) await kb.stop(this.pid, s.id).catch(() => undefined);
  }

  /** The MCP servers KeelBot gets in this project's chats (the project's settings name them, Control › MCP has them on),
   *  each with its status (ok: it answered its last test; error: it failed) and its tools; null when keel does not say
   *  (an older keel, or no project). */
  async keelbotServers(): Promise<KeelbotServer[] | null> {
    const pid = this.pid;
    if (!pid) return null;
    try {
      const [all, settings] = await Promise.all([api.mcpServers(), api.projectSettings(pid).catch(() => null)]);
      const named = settings?.effective?.mcp;
      return all.filter((m) => m.enabled && (!Array.isArray(named) || named.includes(m.name)))
        .map((m) => ({ name: m.name, label: m.label || m.name, status: m.status, tools: m.tools ?? [] }));
    } catch {
      return null;
    }
  }

  /** KeelBot's own slash commands (/gate, /where, /explain …): typed in the TUI, they go to KeelBot as they are. */
  async keelbotCommands(): Promise<{ name: string; description: string }[]> {
    if (!this.pid) return [];
    return kb.commands(this.pid).catch(() => []);
  }
}

/** An MCP server KeelBot can use: its status as keel last saw it (ok, off, error) and its tools. */
export type KeelbotServer = { name: string; label: string; status: string; tools: string[] };

/** 0.18.18 a `!command` keel ran for the person: its exit (null: it did not run) and what it printed */
export type ShellRun = { command: string; exit: number | null; output: string };

const lastIsUser = (s: HelperSession) => {
  const m = s.messages ?? [];
  return !m.length || m[m.length - 1].role === "user";
};

/** 0.18.19 Can the open chat go on in `mode`? A side session (its own branch) only as Side or Ask; any other chat in
 *  every mode but Side (a side session needs its own worktree). */
export function sameChatMode(s: Pick<HelperSession, "mode" | "branch">, mode: HelperMode): boolean {
  const side = s.mode === "side" || !!s.branch;
  return side ? mode === "side" || mode === "ask" : mode !== "side";
}

/** 0.18.19 What undo-turn and redo-turn answer: the turn's user message (n, its text), the files put back, its messages. */
export type TurnUndo = { n: number; text: string; files: string[]; messages: number[] };
