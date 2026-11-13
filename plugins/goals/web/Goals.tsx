// The Goals page (#/goals, #/goals/<goal>): a goal with what "done" means and its limits, its plan, its steps with what each flow says now, how
// far it is and what it cost. A person starts everything here. The Inbox's card for a goal's next step. It imports only react and
// @keel/web-sdk.

import { useState } from "react";
import {
  Async, EmptyState, errorParts, get, go, hashForScreen, kfmt, Note, PageHead, Panel, Pill, post, Since, useApp, useLoad, useRoute,
  type InboxCardProps, type PillTone,
} from "@keel/web-sdk";

export type Goal = { id: string; project_id: string; title: string; why: string | null; done_when: string; cap_tokens: number | null; max_flows: number | null;
  status: "new" | "planning" | "active" | "done" | "stopped"; plan_thread: string | null; plan_ready: boolean; created_at: string; updated_at: string };
export type GoalStep = { id: string; n: number; title: string; request: string; workflow: string; skipped: boolean; thread_id: string | null; state: string; tokens: number; cost_usd: number };
export type GoalProgress = { done: number; total: number; tokens: number; cost_usd: number; flows: number; blocked: string | null };
export type GoalView = { goal: Goal; steps: GoalStep[]; progress: GoalProgress; draft: { title: string; request: string; workflow: string }[] | null; limits: string[] };
export type GoalRow = { goal: Goal; progress: GoalProgress };

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/goals`;
export const goalsApi = {
  list: (pid: string) => get<GoalRow[]>(base(pid)),
  add: (pid: string, b: Record<string, unknown>) => post<Goal>(base(pid), b),
  one: (pid: string, id: string) => get<GoalView>(`${base(pid)}/${encodeURIComponent(id)}`),
  plan: (pid: string, id: string) => post<Goal>(`${base(pid)}/${encodeURIComponent(id)}/plan`, {}),
  limits: (pid: string, id: string, b: Record<string, unknown>) => post<Goal>(`${base(pid)}/${encodeURIComponent(id)}/limits`, b),
  start: (pid: string, id: string, sid: string, allowDirty = false) => post<GoalView>(`${base(pid)}/${encodeURIComponent(id)}/steps/${encodeURIComponent(sid)}/start`, { allow_dirty: allowDirty }),
  skip: (pid: string, id: string, sid: string) => post<GoalView>(`${base(pid)}/${encodeURIComponent(id)}/steps/${encodeURIComponent(sid)}/skip`),
  status: (pid: string, id: string, status: string) => post<Goal>(`${base(pid)}/${encodeURIComponent(id)}/status/${status}`),
};

const TONE: Record<string, PillTone> = { new: "idle", planning: "run", active: "run", done: "ok", stopped: "idle", todo: "idle", skipped: "idle", running: "run", waiting: "warn", failed: "bad" };
const num = (v: string) => (v.trim() ? Number(v) : null);

function Bar({ p }: { p: GoalProgress }) {
  const part = p.total ? Math.round((p.done / p.total) * 100) : 0;
  return <span role="meter" aria-label="Steps done" aria-valuemin={0} aria-valuemax={p.total} aria-valuenow={p.done}>{p.done} of {p.total} steps{p.total ? ` (${part}%)` : ""}</span>;
}

function AddGoal({ pid, onAdded }: { pid: string; onAdded: (id: string) => void }) {
  const [f, setF] = useState({ title: "", why: "", done_when: "", cap: "", max: "" });
  const [problem, setProblem] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setProblem(null);
    try {
      onAdded((await goalsApi.add(pid, { title: f.title, why: f.why, done_when: f.done_when, cap_tokens: num(f.cap), max_flows: num(f.max) })).id);
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    }
  };
  return (
    <form onSubmit={(e) => void add(e)} aria-label="Add a goal" className="grid" style={{ gap: 8 }}>
      <div className="field"><label htmlFor="gl-f1">The goal</label> <input type="text" id="gl-f1" required value={f.title} onChange={set("title")} placeholder="People can export their invoices" /></div>
      <div className="field"><label htmlFor="gl-f2">Done when</label> <input type="text" id="gl-f2" required value={f.done_when} onChange={set("done_when")} placeholder="Something a person can check" /></div>
      <div className="field"><label htmlFor="gl-f3">Why (optional)</label> <input type="text" id="gl-f3" value={f.why} onChange={set("why")} /></div>
      <div className="row">
        <div className="field"><label htmlFor="gl-f4">Most tokens, all flows together (optional)</label> <input type="text" id="gl-f4" inputMode="numeric" value={f.cap} onChange={set("cap")} /></div>
        <div className="field"><label htmlFor="gl-f5">Most flows (optional)</label> <input type="text" id="gl-f5" inputMode="numeric" value={f.max} onChange={set("max")} /></div>
      </div>
      {problem && <Note tone="err" role="alert">{problem}</Note>}
      <div><button className="btn primary" type="submit">Add it</button></div>
    </form>
  );
}

function List({ pid }: { pid: string }) {
  const list = useLoad(`goals:${pid}`, () => goalsApi.list(pid));
  return (
    <>
      <PageHead title="Goals" sub="One goal with its limits. An agent splits it into steps, you approve the plan, and each step is a flow you start." />
      <Async r={list} what="Loading the goals">
        {(rows) => (
          <div className="grid" style={{ gap: 12 }}>
            <Panel title={`Goals (${rows.length})`} body={false}>
              {rows.length ? (
                <div className="ss-tablewrap">
                  <table className="ss-table" aria-label="Goals">
                    <thead><tr><th>Goal</th><th>Status</th><th>How far</th><th>Tokens</th><th>Cost</th></tr></thead>
                    <tbody>
                      {rows.map(({ goal: g, progress: p }) => (
                        <tr key={g.id}>
                          <td><a href={hashForScreen("goals", g.id)}>{g.title}</a></td>
                          <td><Pill tone={TONE[g.status] ?? "idle"}>{g.status}</Pill></td>
                          <td><Bar p={p} /></td>
                          <td>{kfmt(p.tokens)}{g.cap_tokens ? ` / ${kfmt(g.cap_tokens)}` : ""}</td>
                          <td>${p.cost_usd.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <EmptyState title="No goal yet">Add your first one below.</EmptyState>}
            </Panel>
            <Panel title="Add a goal"><AddGoal pid={pid} onAdded={(id) => go("goals", id)} /></Panel>
          </div>
        )}
      </Async>
    </>
  );
}

function One({ pid, id }: { pid: string; id: string }) {
  const view = useLoad(`goals:${pid}:${id}`, () => goalsApi.one(pid, id));
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setProblem(null);
    try {
      await fn();
      await view.reload();
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHead title={view.data?.goal.title ?? "A goal"} actions={<a className="btn sm ghost" href={hashForScreen("goals")}>All goals</a>} />
      <Async r={view} what="Loading the goal">
        {({ goal: g, steps, progress: p, draft, limits }) => {
          const open = g.status !== "done" && g.status !== "stopped";
          const next = steps.find((s) => s.state === "todo");
          return (
            <div className="grid" style={{ gap: 12 }}>
              <Panel title="The goal" extra={<Pill tone={TONE[g.status] ?? "idle"}>{g.status}</Pill>}>
                <p><b>Done when:</b> {g.done_when}</p>
                {g.why && <p><b>Why:</b> {g.why}</p>}
                <p><b>How far:</b> <Bar p={p} /> · <b>Used:</b> {kfmt(p.tokens)} tokens{g.cap_tokens ? ` of ${kfmt(g.cap_tokens)}` : ""}, ${p.cost_usd.toFixed(2)}, {p.flows} flows{g.max_flows ? ` of ${g.max_flows}` : ""}</p>
                {p.blocked && open && <Note tone="warn">{p.blocked}</Note>}
                {problem && <Note tone="err" role="alert">{problem}</Note>}
              </Panel>
              {(g.status === "new" || g.status === "planning") && (
                <Panel title="The plan">
                  {g.status === "new" ? (
                    <>
                      <p>An agent reads this project and splits the goal into steps. The goal's words go to your model company, and it reads this project's repo. Nothing changes and nothing starts.</p>
                      <button className="btn primary" type="button" disabled={busy} onClick={() => void run(() => goalsApi.plan(pid, id))}>Plan it</button>
                    </>
                  ) : (
                    <>
                      {draft ? (
                        <ol aria-label="Proposed steps">{draft.map((s, i) => <li key={i}>{s.title} <span className="sub">({s.workflow})</span></li>)}</ol>
                      ) : <p className="sub">The agent is planning.</p>}
                      {g.plan_thread && <a className="btn primary" href={hashForScreen("flow", g.plan_thread)}>{draft ? "Read and approve the plan" : "Open the planning flow"}</a>}
                    </>
                  )}
                </Panel>
              )}
              {steps.length > 0 && (
                <Panel title={`Steps (${steps.length})`}>
                  <ol aria-label="Steps" style={{ paddingLeft: 20 }}>
                    {steps.map((s) => (
                      <li key={s.id} aria-label={s.title} style={{ padding: "6px 0" }}>
                        <b>{s.title}</b> <span className="mono sub">{s.workflow}</span> <Pill tone={TONE[s.state] ?? "idle"}>{s.state}</Pill>{" "}
                        {s.thread_id && <a href={hashForScreen("flow", s.thread_id)}>Open its flow</a>}{" "}
                        {s.thread_id && <span className="sub">{kfmt(s.tokens)} tokens, ${s.cost_usd.toFixed(2)}</span>}
                        <div className="sub" style={{ whiteSpace: "pre-wrap" }}>{s.request}</div>
                        {s.state === "todo" && open && (
                          <div className="row" style={{ marginTop: 4 }}>
                            <button className={s.id === next?.id ? "btn sm primary" : "btn sm"} type="button" disabled={busy || !!p.blocked} onClick={() => void run(() => goalsApi.start(pid, id, s.id))}>Start this step</button>
                            <button className="btn sm ghost" type="button" disabled={busy} onClick={() => void run(() => goalsApi.skip(pid, id, s.id))}>Leave it out</button>
                          </div>
                        )}
                      </li>
                    ))}
                  </ol>
                </Panel>
              )}
              {open && (
                <Panel title="End it">
                  <p className="sub">keel counts steps; only you know if the goal is reached.</p>
                  <div className="row">
                    <button className="btn" type="button" disabled={busy} onClick={() => void run(() => goalsApi.status(pid, id, "done"))}>It is done</button>
                    <button className="btn ghost" type="button" disabled={busy} onClick={() => void run(() => goalsApi.status(pid, id, "stopped"))}>Stop the goal</button>
                  </div>
                </Panel>
              )}
              <Panel title="What to know"><ul>{limits.map((l) => <li key={l}>{l}</li>)}</ul></Panel>
            </div>
          );
        }}
      </Async>
    </>
  );
}

export function GoalsPage({ pid }: { pid: string }) {
  const { arg } = useRoute();
  return arg ? <One key={arg} pid={pid} id={arg} /> : <List pid={pid} />;
}

/** The Inbox: a goal whose next step can start. It opens the goal, where the person starts it and sees its limits. */
export function GoalInboxCard({ item: it, cardKey, kindLabel }: InboxCardProps) {
  const { setProjectId } = useApp();
  const hid = `inbox-goal-${it.id}`;
  const open = () => {
    setProjectId(it.project_id);
    go("goals", it.id ?? undefined);
  };
  return (
    <article className="inbox-item" aria-labelledby={hid} data-testid="inbox-item" data-key={cardKey} tabIndex={-1}>
      <div className="inbox-meta">
        <Pill tone="run">{kindLabel}</Pill>
        <span className="sub"><b>{it.project_name}</b></span>
        {it.since && <span className="hint inbox-since">waiting <Since from={it.since} /></span>}
      </div>
      <h2 id={hid} className="inbox-title">{it.title}</h2>
      <p className="sub" style={{ margin: 0 }}>{it.detail}{it.more ? "…" : ""}</p>
      <div className="row"><button className="btn sm primary" type="button" onClick={open}>Open the goal</button></div>
    </article>
  );
}
