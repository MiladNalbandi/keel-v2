// The Supervisor's Jobs tab (plugins/supervisor): its switches - keel-wide (off until you turn it on), this project's
// (else it follows keel-wide), the flow now's (else it follows the project) - the flow's mode, and the audit log
// (GET /api/projects/{pid}/supervisor/decisions). It imports only react and @keel/web-sdk.

import { useState } from "react";
import { Drawer, EmptyState, get, hashForScreen, Pill, post, useLoad, type PillTone } from "@keel/web-sdk";

export type Decision = {
  id: string;
  thread_id: string;
  step: string | null;
  ac: string | null;
  kind: string | null;
  action: string;
  decision: string | null;
  choice: string | null;
  why: string | null;
  confidence: string | null;
  evidence: string[];
  /** 0.18.64 an own_fix verdict's commands (a failing setup rung): "Run these commands" sends them as your answer */
  commands?: string[];
  code: string | null;
  summary: string | null;
  tokens: number;
  outcome: string;
  at: string;
  /** 0.21.0 a small fix: what it changed, also when it put it back */
  patch?: string | null;
};

type Switches = { keel: boolean; plugin: boolean; project: boolean; flow: boolean | null; off_by: string | null };
type Settings = { on: boolean; mode: "shadow" | "retry" | "act" | "fix"; effort?: string; cap_tokens: number; switches?: Switches };
type ProjectSwitch = { on: boolean; keel: boolean; project: boolean; own: boolean | null; plugin: boolean };
type FlowPatch = { on?: boolean | null; mode?: Settings["mode"] };
type FlowView = { thread: { thread_id: string; title?: string; status?: string } | null };
type SupView = { settings: Settings; used: { tokens: number } };

export type Note = {
  summary: string;
  code: string | null;
  reason: string;
  confidence: string | null;
  suggestion: { decision: string; choice: string | null; answers: Record<string, string | string[]>; why: string } | null;
  patch: string | null;
};
export type ChipState = "off" | "watching" | "looking" | "acted" | "escalated" | "paused-budget" | "paused-repeats";
export type FlowStatus = { state: ChipState; on: boolean; off_by: string | null; mode: string; note: Note | null };
export type Defaults = { on: boolean; mode: Settings["mode"]; stall_minutes: number; modes: Settings["mode"][]; stall_min: number; stall_max: number };

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}`;
export const supervisor = {
  decisions: (pid: string) => get<{ decisions: Decision[] }>(`${base(pid)}/supervisor/decisions?limit=100`),
  flow: (pid: string) => get<FlowView>(`${base(pid)}/flow`),
  settings: (pid: string, tid: string) => get<SupView>(`${base(pid)}/flows/${encodeURIComponent(tid)}/supervisor`),
  change: (pid: string, tid: string, body: FlowPatch) =>
    post<SupView>(`${base(pid)}/flows/${encodeURIComponent(tid)}/supervisor`, body),
  status: (pid: string, tid: string) => get<FlowStatus>(`${base(pid)}/flows/${encodeURIComponent(tid)}/supervisor/status`),
  defaults: () => get<Defaults>("/supervisor/defaults"),
  setDefaults: (body: Partial<Pick<Defaults, "on" | "mode" | "stall_minutes">>) => post<Defaults>("/supervisor/defaults", body),
  keel: () => get<{ on: boolean }>("/supervisor/switch"),
  setKeel: (on: boolean) => post<{ on: boolean }>("/supervisor/switch", { on }),
  project: (pid: string) => get<ProjectSwitch>(`${base(pid)}/supervisor/switch`),
  setProject: (pid: string, on: boolean | null) => post<ProjectSwitch>(`${base(pid)}/supervisor/switch`, { on }),
  /** your answer to the waiting gate: own_fix with the Supervisor's commands */
  runCommands: (tid: string, commands: string[]) => post<unknown>(`/threads/${encodeURIComponent(tid)}/resume`, {
    decision: "approve", why: "the Supervisor's commands", payload: { choice: "own_fix", commands } }),
};

const tri = (v: boolean | null | undefined) => (v === true ? "on" : v === false ? "off" : "follow");
const untri = (v: string) => (v === "on" ? true : v === "off" ? false : null);
const OFF_BY: Record<string, string> = {
  keel: "it is off keel-wide",
  plugin: "the plugin is off for this project (Settings › Plugins)",
  project: "it is off for this project",
  flow: "it is off for this flow",
};

/** A small segmented choice (follows / on / off, a mode): real buttons in a named group. */
function Seg<T extends string>({ label, value, options, disabled, onPick }: { label: string; value: T; options: [T, string][];
  disabled?: boolean; onPick: (v: T) => void }) {
  return (
    <div className="rdp-seg" role="group" aria-label={label}>
      {options.map(([k, l]) => (
        <button key={k} type="button" aria-pressed={value === k} disabled={disabled} onClick={() => value !== k && onPick(k)}>{l}</button>
      ))}
    </div>
  );
}

/** The keel-wide and project switches: they change keel's settings (General, and this project's own). */
function Switches({ pid, onChanged }: { pid: string; onChanged: () => void }) {
  const keel = useLoad("supervisor-keel", () => supervisor.keel());
  const proj = useLoad(`supervisor-project:${pid}`, () => supervisor.project(pid));
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      await Promise.all([keel.reload(), proj.reload()]);
      onChanged();
    } finally {
      setBusy(false);
    }
  };
  const p = proj.data;
  return (
    <section aria-label="Supervisor switches" className="rdp-card">
      <div className="rdp-card-h"><h3>Where it runs</h3></div>
      <div className="rdp-card-b">
        <div className="rdp-line">
          <span className="rdp-two"><b>On for the whole keel</b><span className="sub">Off: nothing listens and no model is called.</span></span>
          <input type="checkbox" className="rdp-switch" aria-label="On for the whole keel" checked={!!keel.data?.on} disabled={busy || !keel.data}
            onChange={(e) => void run(() => supervisor.setKeel(e.target.checked))} />
        </div>
        <div className="rdp-line">
          <span className="rdp-two"><b>This project · <span className="mono">{pid}</span></b><span className="sub">Else it follows keel-wide.</span></span>
          <Seg label="This project" value={tri(p?.own)} disabled={busy || !p}
            options={[["follow", "follows keel-wide"], ["on", "on"], ["off", "off"]]}
            onPick={(v) => void run(() => supervisor.setProject(pid, untri(v)))} />
        </div>
        {p && (
          <p role="status" className={`rdp-note ${p.on ? "ok" : "warn"}`}>
            {p.on ? "The Supervisor runs in this project." : `The Supervisor does not run here: ${
              !p.keel ? OFF_BY.keel : !p.plugin ? OFF_BY.plugin : OFF_BY.project}.`}
          </p>
        )}
      </div>
    </section>
  );
}

/** One row's result in plain words. */
export function outcomeText(d: Decision): string {
  if (d.outcome === "applied" && d.action === "fix") return "made a small fix";
  if (d.outcome === "applied") return d.action === "send_back" || d.decision === "reject" ? "sent back" : `answered ${d.choice ?? d.decision ?? ""}`.trim();
  if (d.outcome === "suggested") return "suggested";
  if (d.outcome === "diagnosed") return "wrote a diagnosis";
  if (d.outcome === "skipped") return "did not look";
  if (d.outcome === "cancelled") return "was turned off";
  if (d.outcome === "stale") return "too late (you answered)";
  return "asked you";
}

const MODE_OPTIONS: [Settings["mode"], string][] = [["shadow", "shadow"], ["retry", "retry"], ["act", "act"], ["fix", "fix"]];
const MODE_TEXT: Record<Settings["mode"], string> = {
  shadow: "shadow: only suggests",
  retry: "retry: only tries a stuck step again, with its note",
  act: "act: answers when sure",
  fix: "fix: also makes small fixes on the flow's own branch",
};

function FlowSwitch({ pid, tid, title, version, onChanged }: { pid: string; tid: string; title: string; version: number;
  onChanged: () => void }) {
  const s = useLoad(`supervisor:${pid}:${tid}:${version}`, () => supervisor.settings(pid, tid));
  const [busy, setBusy] = useState(false);
  const set = async (body: FlowPatch) => {
    setBusy(true);
    try {
      s.setData(await supervisor.change(pid, tid, body));
      onChanged();
    } finally {
      setBusy(false);
    }
  };
  const cur = s.data?.settings;
  const used = s.data?.used.tokens ?? 0;
  return (
    <section aria-label="Supervisor for this flow" className="rdp-card">
      <div className="rdp-card-h"><h3>The flow now</h3></div>
      <div className="rdp-card-b">
        <a className="rdp-flow" href={hashForScreen("flow", tid)}>{title} ›</a>
        {s.error && <p role="alert" className="rdp-note bad">{s.error.message}</p>}
        {cur && (
          <>
            <div className="rdp-line">
              <span className="sub rdp-key">This flow</span>
              <Seg label="This flow" value={tri(cur.switches?.flow)} disabled={busy}
                options={[["follow", "follows the project"], ["on", "on"], ["off", "off"]]}
                onPick={(v) => void set({ on: untri(v) })} />
            </div>
            <div className="rdp-line top">
              <span className="sub rdp-key">Mode</span>
              <span className="rdp-two">
                <Seg label="Mode" value={cur.mode} disabled={busy} options={MODE_OPTIONS} onPick={(v) => void set({ mode: v })} />
                <span className="sub">{MODE_TEXT[cur.mode]}</span>
              </span>
            </div>
            {cur.mode === "fix" && (
              <p className="rdp-note warn" role="note">
                In fix mode it may change code by itself: at most 3 files and 40 lines, on the flow's own branch. It never commits, pushes or merges.
              </p>
            )}
            <div className="rdp-used">
              <span className="sub">{used.toLocaleString()} of {cur.cap_tokens.toLocaleString()} tokens used</span>
              <progress className="rdp-prog" value={Math.min(used, cur.cap_tokens)} max={cur.cap_tokens || 1} aria-label="Tokens used" />
            </div>
            <p role="status" className="sub">
              {cur.on ? "It supervises this flow." : `It does not supervise this flow: ${OFF_BY[cur.switches?.off_by ?? "keel"]}.`}
            </p>
          </>
        )}
      </div>
    </section>
  );
}

/** A suggested own_fix: its commands, and a button that sends them as your answer (keel runs them where the rungs run). */
function Commands({ d, live, onDone }: { d: Decision; live: boolean; onDone: () => void }) {
  const [state, setState] = useState<"idle" | "busy" | "sent" | string>("idle");
  if (!d.commands?.length) return null;
  const run = async () => {
    setState("busy");
    try {
      await supervisor.runCommands(d.thread_id, d.commands!);
      setState("sent");
      onDone();
    } catch (e) {
      setState(e instanceof Error ? e.message : String(e));
    }
  };
  const open = live && d.outcome !== "applied" && d.choice === "own_fix";
  return (
    <div className="rdp-cmds">
      <pre className="rdp-term" aria-label="Suggested commands">{d.commands.map((c) => `$ ${c}`).join("\n")}</pre>
      {open && state !== "sent" && (
        <button type="button" className="btn sm primary" disabled={state === "busy"} onClick={() => void run()}>Run these commands</button>
      )}
      {state === "sent" && <span role="status" className="rdp-okt">Sent: keel runs them, then the ladder again.</span>}
      {!["idle", "busy", "sent"].includes(state) && <span role="alert" className="rdp-err">{state}</span>}
    </div>
  );
}

type Filter = "all" | "answered" | "called" | "suggested";
const FILTERS: [Filter, string][] = [["all", "All"], ["answered", "Answered"], ["called", "Called you"], ["suggested", "Suggested"]];
const inFilter = (d: Decision, f: Filter) =>
  f === "all" ||
  (f === "answered" ? d.outcome === "applied" : f === "called" ? d.outcome === "escalated" : d.outcome === "suggested" || d.outcome === "diagnosed");

/** A result's colour: answered or fixed green, asked you amber, suggested blue, the rest grey. */
function outcomeTone(d: Decision): PillTone {
  if (d.outcome === "applied") return "ok";
  if (d.outcome === "escalated") return "warn";
  if (d.outcome === "suggested" || d.outcome === "diagnosed") return "run";
  return "idle";
}

/** The numbers over the log: what it looked at today, answered by itself, and called you about. */
export function supervisorStats(list: Decision[], now = new Date()) {
  const today = list.filter((d) => new Date(d.at).toDateString() === now.toDateString());
  return {
    looked: today.length,
    applied: today.filter((d) => d.outcome === "applied").length,
    called: today.filter((d) => d.outcome === "escalated").length,
  };
}

export function SupervisorView({ pid }: { pid: string }) {
  const rows = useLoad(`supervisor-log:${pid}`, () => supervisor.decisions(pid));
  const flow = useLoad(`supervisor-flow:${pid}`, () => supervisor.flow(pid));
  const t = flow.data?.thread;
  const live = t && t.status !== "done" && t.status !== "stopped" && t.status !== "failed";
  const list = rows.data?.decisions ?? [];
  const [version, setVersion] = useState(0);      // a keel-wide or project switch changed: the flow's view reads again
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<Decision | null>(null);
  const words = q.trim().toLowerCase();
  const shown = list.filter((d) => inFilter(d, filter) &&
    (!words || [d.step, d.ac, d.summary, d.why, outcomeText(d)].filter(Boolean).join(" ").toLowerCase().includes(words)));
  const st = supervisorStats(list);
  return (
    <div className="rdp sup">
      <div className="rdp-head">
        <p className="sub">
          A strong model watches your flows. It answers their gates from the project's own context, and calls you when it is
          unsure, stuck or out of budget.
        </p>
      </div>
      <div className="rdp-grid2 rdp-rise">
        <Switches pid={pid} onChanged={() => setVersion((n) => n + 1)} />
        {live ? (
          <FlowSwitch pid={pid} tid={t.thread_id} title={t.title || t.thread_id} version={version}
            onChanged={() => void rows.reload()} />
        ) : (
          <section className="rdp-card" aria-label="No flow now">
            <div className="rdp-card-h"><h3>The flow now</h3></div>
            <div className="rdp-card-b"><p className="sub">No flow runs or waits in this project now. Its switch and mode show here while one does.</p></div>
          </section>
        )}
      </div>
      {list.length > 0 && (
        <div className="rdp-stats rdp-rise">
          <div className="rdp-card rdp-stat"><span className="sub">Looked today</span><b>{st.looked}</b><span className="sub">gates, failures and quiet flows</span></div>
          <div className="rdp-card rdp-stat"><span className="sub">Answered by itself</span><b className="t-ok">{st.applied}</b><span className="sub">only when sure, in mode act or fix</span></div>
          <div className="rdp-card rdp-stat"><span className="sub">Called you</span><b className={st.called ? "t-warn" : undefined}>{st.called}</b><span className="sub">not sure, only you may answer, or out of budget</span></div>
        </div>
      )}
      {rows.error && <p role="alert" className="rdp-note bad">{rows.error.message}</p>}
      {!rows.loading && list.length === 0 ? (
        <EmptyState title="The Supervisor has not looked at anything yet.">
          Turn it on for a flow: it looks at each gate, answers when it is sure (mode act) or only suggests (mode shadow), and
          asks you when it is not sure, when only you may answer, or when its budget is used up.
        </EmptyState>
      ) : (
        <>
          <div className="rdp-bar">
            <div className="rdp-chips" role="group" aria-label="Show">
              {FILTERS.map(([k, l]) => (
                <button key={k} type="button" className="rdp-fchip" aria-pressed={filter === k} onClick={() => setFilter(k)}>
                  {l}
                  {k === "called" && list.some((d) => d.outcome === "escalated") && (
                    <span className="rdp-badge">{list.filter((d) => d.outcome === "escalated").length}</span>
                  )}
                </button>
              ))}
            </div>
            <span className="grow" />
            <input type="search" className="rdp-inp" aria-label="Search the decisions" placeholder="Step, result or why"
              value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div className="rdp-card rdp-table-card table-wrap">
            <table aria-label="Supervisor decisions" className="rdp-table">
              <thead>
                <tr><th>When</th><th>Step</th><th>Result</th><th>Sure</th><th>Why</th><th className="num">Tokens</th></tr>
              </thead>
              <tbody>
                {shown.map((d) => (
                  <tr key={d.id} className={sel?.id === d.id ? "on" : undefined}>
                    <td className="mono sub">{new Date(d.at).toLocaleTimeString()}</td>
                    <td className="mono">{[d.step, d.ac].filter(Boolean).join(" ")}</td>
                    <td><Pill tone={outcomeTone(d)}>{outcomeText(d)}</Pill></td>
                    <td className="sub">{d.confidence ?? ""}</td>
                    <td title={(d.evidence || []).join("\n")}>
                      <button type="button" className="rdp-linkbtn" onClick={() => setSel(d)}>{d.summary || d.why || "(no words)"}</button>
                      <Commands d={d} live={!!live && d.thread_id === t?.thread_id} onDone={() => void rows.reload()} />
                    </td>
                    <td className="mono sub num">{d.tokens.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {shown.length === 0 && (
              <div className="rdp-empty"><b>No decision matches.</b><span>Clear the search, or pick All.</span></div>
            )}
          </div>
        </>
      )}
      {sel && <OneDecision d={sel} onClose={() => setSel(null)} />}
    </div>
  );
}

/** One decision in a drawer: what it saw, why, what backs it, its small fix and its commands. */
function OneDecision({ d, onClose }: { d: Decision; onClose: () => void }) {
  const step = [d.step, d.ac].filter(Boolean).join(" ") || "a step";
  return (
    <Drawer title="One decision" onClose={onClose}>
      <div className="rdp rdp-one">
        <h3>{step} · {outcomeText(d)}</h3>
        <div className="rdp-chips">
          <Pill tone={outcomeTone(d)}>{outcomeText(d)}</Pill>
          {d.confidence && <span className="rdp-tag">sure: {d.confidence}</span>}
          <span className="rdp-tag mono">{d.tokens.toLocaleString()} tokens</span>
        </div>
        <p className="sub">
          {new Date(d.at).toLocaleString()} · <a href={hashForScreen("flow", d.thread_id)}>{d.thread_id} ›</a>
        </p>
        {d.summary && <div><span className="rdp-lbl">What it saw</span><p>{d.summary}</p></div>}
        {d.why && <div><span className="rdp-lbl">Why</span><p>{d.why}</p></div>}
        {d.code && <p className="rdp-note warn">Why it did not answer: {d.code}.</p>}
        {d.evidence.length > 0 && (
          <div>
            <span className="rdp-lbl">What backs it</span>
            <ul className="rdp-ev">{d.evidence.map((e) => <li key={e} className="mono">{e}</li>)}</ul>
          </div>
        )}
        {d.patch && <div><span className="rdp-lbl">The small fix</span><pre className="rdp-term">{d.patch}</pre></div>}
      </div>
    </Drawer>
  );
}
