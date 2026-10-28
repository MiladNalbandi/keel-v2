// The Supervisor's Jobs tab (plugins/supervisor): its switches - keel-wide (off until you turn it on), this project's
// (else it follows keel-wide), the flow now's (else it follows the project) - the flow's mode, and the audit log
// (GET /api/projects/{pid}/supervisor/decisions). It imports only react and @keel/web-sdk.

import { useState } from "react";
import { EmptyState, get, post, useLoad } from "@keel/web-sdk";

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
};

type Switches = { keel: boolean; plugin: boolean; project: boolean; flow: boolean | null; off_by: string | null };
type Settings = { on: boolean; mode: "shadow" | "act"; effort?: string; cap_tokens: number; switches?: Switches };
type ProjectSwitch = { on: boolean; keel: boolean; project: boolean; own: boolean | null; plugin: boolean };
type FlowPatch = { on?: boolean | null; mode?: Settings["mode"] };
type FlowView = { thread: { thread_id: string; title?: string; status?: string } | null };
type SupView = { settings: Settings; used: { tokens: number } };

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}`;
export const supervisor = {
  decisions: (pid: string) => get<{ decisions: Decision[] }>(`${base(pid)}/supervisor/decisions?limit=100`),
  flow: (pid: string) => get<FlowView>(`${base(pid)}/flow`),
  settings: (pid: string, tid: string) => get<SupView>(`${base(pid)}/flows/${encodeURIComponent(tid)}/supervisor`),
  change: (pid: string, tid: string, body: FlowPatch) =>
    post<SupView>(`${base(pid)}/flows/${encodeURIComponent(tid)}/supervisor`, body),
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
    <section aria-label="Supervisor switches" className="panel">
      <p>
        <label>
          <input type="checkbox" checked={!!keel.data?.on} disabled={busy || !keel.data}
            onChange={(e) => void run(() => supervisor.setKeel(e.target.checked))} />{" "}
          On for the whole keel
        </label>{" "}
        <label>
          This project{" "}
          <select value={tri(p?.own)} disabled={busy || !p}
            onChange={(e) => void run(() => supervisor.setProject(pid, untri(e.target.value)))}>
            <option value="follow">follows keel-wide</option>
            <option value="on">on</option>
            <option value="off">off</option>
          </select>
        </label>
      </p>
      {p && (
        <p role="status">
          {p.on ? "The Supervisor runs in this project." : `The Supervisor does not run here: ${
            !p.keel ? OFF_BY.keel : !p.plugin ? OFF_BY.plugin : OFF_BY.project}.`}
        </p>
      )}
    </section>
  );
}

/** One row's result in plain words. */
export function outcomeText(d: Decision): string {
  if (d.outcome === "applied") return d.action === "send_back" || d.decision === "reject" ? "sent back" : `answered ${d.choice ?? d.decision ?? ""}`.trim();
  if (d.outcome === "suggested") return "suggested";
  if (d.outcome === "stale") return "too late (you answered)";
  return "asked you";
}

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
  return (
    <section aria-label="Supervisor for this flow" className="panel">
      <h3>{title}</h3>
      {s.error && <p role="alert">{s.error.message}</p>}
      {cur && (
        <p>
          <label>
            This flow{" "}
            <select value={tri(cur.switches?.flow)} disabled={busy} onChange={(e) => void set({ on: untri(e.target.value) })}>
              <option value="follow">follows the project</option>
              <option value="on">on</option>
              <option value="off">off</option>
            </select>
          </label>{" "}
          <label>
            Mode{" "}
            <select value={cur.mode} disabled={busy} onChange={(e) => void set({ mode: e.target.value as Settings["mode"] })}>
              <option value="shadow">shadow: only suggests</option>
              <option value="act">act: answers when sure</option>
            </select>
          </label>{" "}
          <span>
            {s.data!.used.tokens.toLocaleString()} of {cur.cap_tokens.toLocaleString()} tokens used
          </span>
        </p>
      )}
      {cur && (
        <p role="status">
          {cur.on ? "It supervises this flow." : `It does not supervise this flow: ${OFF_BY[cur.switches?.off_by ?? "keel"]}.`}
        </p>
      )}
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
    <div>
      <pre aria-label="Suggested commands">{d.commands.map((c) => `$ ${c}`).join("\n")}</pre>
      {open && state !== "sent" && (
        <button type="button" disabled={state === "busy"} onClick={() => void run()}>Run these commands</button>
      )}
      {state === "sent" && <span role="status">Sent: keel runs them, then the ladder again.</span>}
      {!["idle", "busy", "sent"].includes(state) && <span role="alert">{state}</span>}
    </div>
  );
}

export function SupervisorView({ pid }: { pid: string }) {
  const rows = useLoad(`supervisor-log:${pid}`, () => supervisor.decisions(pid));
  const flow = useLoad(`supervisor-flow:${pid}`, () => supervisor.flow(pid));
  const t = flow.data?.thread;
  const live = t && t.status !== "done" && t.status !== "stopped" && t.status !== "failed";
  const list = rows.data?.decisions ?? [];
  const [version, setVersion] = useState(0);      // a keel-wide or project switch changed: the flow's view reads again
  return (
    <div>
      <Switches pid={pid} onChanged={() => setVersion((n) => n + 1)} />
      {live && <FlowSwitch pid={pid} tid={t.thread_id} title={t.title || t.thread_id} version={version}
        onChanged={() => void rows.reload()} />}
      {rows.error && <p role="alert">{rows.error.message}</p>}
      {!rows.loading && list.length === 0 ? (
        <EmptyState title="The Supervisor has not looked at anything yet.">
          Turn it on for a flow: it looks at each gate, answers when it is sure (mode act) or only suggests (mode shadow), and
          asks you when it is not sure, when only you may answer, or when its budget is used up.
        </EmptyState>
      ) : (
        <table aria-label="Supervisor decisions">
          <thead>
            <tr><th>When</th><th>Step</th><th>Result</th><th>Sure</th><th>Why</th><th>Tokens</th></tr>
          </thead>
          <tbody>
            {list.map((d) => (
              <tr key={d.id}>
                <td>{new Date(d.at).toLocaleTimeString()}</td>
                <td>{[d.step, d.ac].filter(Boolean).join(" ")}</td>
                <td>{outcomeText(d)}</td>
                <td>{d.confidence ?? ""}</td>
                <td title={(d.evidence || []).join("\n")}>
                  {d.summary || d.why || ""}
                  <Commands d={d} live={!!live && d.thread_id === t?.thread_id} onDone={() => void rows.reload()} />
                </td>
                <td>{d.tokens.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
