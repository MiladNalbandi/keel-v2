// The Goals page (#/goals, #/goals/<goal>): a goal with what "done" means and its limits, its plan, its steps with what each flow says now, how
// far it is and what it cost. A person starts everything here. The Inbox's card for a goal's next step. It imports only react and
// @keel/web-sdk.

import { useState } from "react";
import {
  agoText, Async, EmptyState, errorParts, get, go, hashForScreen, kfmt, Note, PageHead, Panel, Pill, post, Since, useApp, useLoad, useRoute,
  type InboxCardProps, type Loaded, type PillTone,
} from "@keel/web-sdk";
import { usePageKey } from "./keys";

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

/** The thin bar under a goal: the share of its steps done (a picture only; the meter above says it in words). */
function Track({ p, status, big }: { p: GoalProgress; status: Goal["status"]; big?: boolean }) {
  const part = p.total ? Math.round((p.done / p.total) * 100) : 0;
  return <span className={`gl-track gl-${status}${big ? " big" : ""}`} aria-hidden="true"><span style={{ width: `${part}%` }} /></span>;
}

const used = (g: Goal, p: GoalProgress) => `${kfmt(p.tokens)}${g.cap_tokens ? ` / ${kfmt(g.cap_tokens)}` : ""} tokens · $${p.cost_usd.toFixed(2)}`;

function AddGoal({ pid, onAdded, onCancel }: { pid: string; onAdded: (id: string) => void; onCancel?: () => void }) {
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
    <section className="panel rise" aria-labelledby="gl-add-h">
      <div className="panel-head"><h2 id="gl-add-h">Add a goal</h2></div>
      <form onSubmit={(e) => void add(e)} aria-label="Add a goal" className="panel-body grid" style={{ gap: 10 }}>
        <div className="field"><label htmlFor="gl-f1">The goal</label> <input type="text" id="gl-f1" required value={f.title} onChange={set("title")} placeholder="People can export their invoices" /></div>
        <div className="field"><label htmlFor="gl-f2">Done when</label> <input type="text" id="gl-f2" required value={f.done_when} onChange={set("done_when")} placeholder="Something a person can check" /></div>
        <div className="field"><label htmlFor="gl-f3">Why (optional)</label> <input type="text" id="gl-f3" value={f.why} onChange={set("why")} /></div>
        <div className="gl-two">
          <div className="field"><label htmlFor="gl-f4">Most tokens, all flows together (optional)</label> <input type="text" id="gl-f4" inputMode="numeric" value={f.cap} onChange={set("cap")} /></div>
          <div className="field"><label htmlFor="gl-f5">Most flows (optional)</label> <input type="text" id="gl-f5" inputMode="numeric" value={f.max} onChange={set("max")} /></div>
        </div>
        {problem && <Note tone="err" role="alert">{problem}</Note>}
        <div className="rdw-bar">
          <button className="btn primary" type="submit">Add it</button>
          {onCancel && <button className="btn ghost" type="button" onClick={onCancel}>Cancel</button>}
        </div>
      </form>
    </section>
  );
}

/** The left column: the goals, each with how far it is and what it used. */
function GoalList({ list, current }: { list: Loaded<GoalRow[]>; current: string | null }) {
  return (
    <div className="rdw-list">
      <Async r={list} what="Loading the goals">
        {(rows) => (
          <>
            <h2 className="lbl rise r1">Goals · {rows.length}</h2>
            {rows.length ? (
              <ul className="rdw-items rise r2" aria-label="Goals">
                {rows.map(({ goal: g, progress: p }) => (
                  <li key={g.id}>
                    <a className="rdw-item" href={hashForScreen("goals", g.id)} aria-current={g.id === current ? "page" : undefined}>
                      <span className="rdw-item-top"><b>{g.title}</b><Pill tone={TONE[g.status] ?? "idle"}>{g.status}</Pill></span>
                      <Track p={p} status={g.status} />
                      <span className="sub"><Bar p={p} /> · {used(g, p)}</span>
                    </a>
                  </li>
                ))}
              </ul>
            ) : <EmptyState title="No goal yet" compact>Add your first one in Add a goal.</EmptyState>}
          </>
        )}
      </Async>
    </div>
  );
}

function Limits({ pid, g, onSaved, onCancel }: { pid: string; g: Goal; onSaved: () => Promise<unknown>; onCancel: () => void }) {
  const [f, setF] = useState({ cap: g.cap_tokens ? String(g.cap_tokens) : "", max: g.max_flows ? String(g.max_flows) : "" });
  const [problem, setProblem] = useState<string | null>(null);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setProblem(null);
    try {
      await goalsApi.limits(pid, g.id, { cap_tokens: num(f.cap), max_flows: num(f.max) });
      await onSaved();
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    }
  };
  return (
    <form className="gl-box grid" style={{ gap: 10 }} aria-label="Change the limits" onSubmit={(e) => void save(e)}>
      <div className="gl-two">
        <div className="field"><label htmlFor="gl-l1">Most tokens, all flows together</label> <input type="text" id="gl-l1" inputMode="numeric" value={f.cap} onChange={(e) => setF({ ...f, cap: e.target.value })} /></div>
        <div className="field"><label htmlFor="gl-l2">Most flows</label> <input type="text" id="gl-l2" inputMode="numeric" value={f.max} onChange={(e) => setF({ ...f, max: e.target.value })} /></div>
      </div>
      <span className="sub">Empty means no limit. Only you change the limits: the Goals tools for agents cannot.</span>
      {problem && <Note tone="err" role="alert">{problem}</Note>}
      <div className="rdw-bar"><button className="btn sm primary" type="submit">Save the limits</button><button className="btn sm ghost" type="button" onClick={onCancel}>Cancel</button></div>
    </form>
  );
}

function One({ pid, id }: { pid: string; id: string }) {
  const view = useLoad(`goals:${pid}:${id}`, () => goalsApi.one(pid, id));
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editLimits, setEditLimits] = useState(false);
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
  const plan = () => void run(() => goalsApi.plan(pid, id));
  usePageKey("p", () => { if (!busy) plan(); }, view.data?.goal.status === "new");
  return (
    <Async r={view} what="Loading the goal">
      {({ goal: g, steps, progress: p, draft, limits }) => {
        const open = g.status !== "done" && g.status !== "stopped";
        const next = steps.find((s) => s.state === "todo");
        return (
          <div className="rdw-detail">
            <section className="panel rise" aria-labelledby="gl-title">
              <div className="panel-body grid" style={{ gap: 12 }}>
                <div className="rdw-meta">
                  <Pill tone={TONE[g.status] ?? "idle"}>{g.status}</Pill>
                  <span>added {agoText(g.created_at)}</span>
                  <span className="grow" />
                  <a className="btn sm ghost" href={hashForScreen("goals")}>All goals</a>
                </div>
                <h2 id="gl-title" className="gl-title">{g.title}</h2>
                <dl className="gl-kv">
                  <dt>Done when</dt><dd>{g.done_when}</dd>
                  {g.why && <><dt>Why</dt><dd>{g.why}</dd></>}
                </dl>
                <div className="grid" style={{ gap: 6 }}>
                  <div className="rdw-bar"><b>How far</b> <Bar p={p} /></div>
                  <Track p={p} status={g.status} big />
                </div>
                <div className="rdw-bar gl-stats">
                  <span><span className="sub">tokens</span><b className="mono">{kfmt(p.tokens)}{g.cap_tokens ? ` of ${kfmt(g.cap_tokens)}` : ""}</b></span>
                  <span><span className="sub">cost</span><b className="mono">${p.cost_usd.toFixed(2)}</b></span>
                  <span><span className="sub">flows</span><b className="mono">{p.flows}{g.max_flows ? ` of ${g.max_flows}` : ""}</b></span>
                  <span className="grow" />
                  {open && !editLimits && <button className="btn sm" type="button" onClick={() => setEditLimits(true)}>Change limits</button>}
                </div>
                {editLimits && <Limits pid={pid} g={g} onCancel={() => setEditLimits(false)} onSaved={async () => { setEditLimits(false); await view.reload(); }} />}
                {p.blocked && open && <Note tone="warn">{p.blocked}</Note>}
                {problem && <Note tone="err" role="alert">{problem}</Note>}
              </div>
            </section>
            {(g.status === "new" || g.status === "planning") && (
              <Panel title="The plan" className="rise r1">
                {g.status === "new" ? (
                  <div className="grid" style={{ gap: 10 }}>
                    <p style={{ margin: 0 }}>An agent reads this project and splits the goal into steps. The goal's words go to your model company, and it reads this project's repo. Nothing changes and nothing starts.</p>
                    <div><button className="btn primary" type="button" disabled={busy} onClick={plan} aria-keyshortcuts="P">Plan it <kbd aria-hidden="true">P</kbd></button></div>
                  </div>
                ) : (
                  <div className="grid" style={{ gap: 10 }}>
                    {draft ? (
                      <ol aria-label="Proposed steps" className="gl-steps">{draft.map((s, i) => <li key={i}><span className="gl-n" aria-hidden="true">{i + 1}</span><span><b>{s.title}</b> <span className="mono gl-wf">{s.workflow}</span></span></li>)}</ol>
                    ) : <p className="sub rdw-bar" style={{ margin: 0 }}><span className="gl-dot" aria-hidden="true" />The agent is planning.</p>}
                    {g.plan_thread && <div><a className="btn primary" href={hashForScreen("flow", g.plan_thread)}>{draft ? "Read and approve the plan" : "Open the planning flow"}</a></div>}
                  </div>
                )}
              </Panel>
            )}
            {steps.length > 0 && (
              <Panel title={`Steps · ${steps.length}`} extra={<span className="sub">Each step is a flow you start.</span>} className="rise r1">
                <ol aria-label="Steps" className="gl-steps">
                  {steps.map((s) => (
                    <li key={s.id} aria-label={s.title} className={s.state === "todo" ? "" : `gl-s-${s.state}`}>
                      <span className="gl-n" aria-hidden="true">{s.n}</span>
                      <div className="grid" style={{ gap: 4, minWidth: 0, flex: 1 }}>
                        <div className="rdw-bar" style={{ gap: 8 }}>
                          <b>{s.title}</b> <span className="mono gl-wf">{s.workflow}</span> <Pill tone={TONE[s.state] ?? "idle"}>{s.state}</Pill>
                          <span className="grow" />
                          {s.thread_id && <span className="sub mono">{kfmt(s.tokens)} tokens, ${s.cost_usd.toFixed(2)}</span>}
                          {s.thread_id && <a href={hashForScreen("flow", s.thread_id)}>Open its flow</a>}
                        </div>
                        <div className="sub" style={{ whiteSpace: "pre-wrap" }}>{s.request}</div>
                        {s.state === "todo" && open && (
                          <div className="rdw-bar" style={{ marginTop: 2 }}>
                            <button className={s.id === next?.id ? "btn sm primary" : "btn sm"} type="button" disabled={busy || !!p.blocked} onClick={() => void run(() => goalsApi.start(pid, id, s.id))}>Start this step</button>
                            <button className="btn sm ghost" type="button" disabled={busy} onClick={() => void run(() => goalsApi.skip(pid, id, s.id))}>Leave it out</button>
                          </div>
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              </Panel>
            )}
            <div className="gl-pair rise r2">
              {open && (
                <Panel title="End it">
                  <p className="sub" style={{ marginTop: 0 }}>keel counts steps; only you know if the goal is reached.</p>
                  <div className="rdw-bar">
                    <button className="btn" type="button" disabled={busy} onClick={() => void run(() => goalsApi.status(pid, id, "done"))}>It is done</button>
                    <button className="btn ghost" type="button" disabled={busy} onClick={() => void run(() => goalsApi.status(pid, id, "stopped"))}>Stop the goal</button>
                  </div>
                </Panel>
              )}
              <Panel title="What to know"><ul className="gl-know">{limits.map((l) => <li key={l}>{l}</li>)}</ul></Panel>
            </div>
          </div>
        );
      }}
    </Async>
  );
}

/** #/goals and #/goals/<goal>: the goals on the left, the open one on the right (one column on a phone). */
export function GoalsPage({ pid }: { pid: string }) {
  const { arg } = useRoute();
  const list = useLoad(`goals:${pid}`, () => goalsApi.list(pid));
  // the add form fills the right side while no goal is open; on a goal it opens above it with New goal (or N)
  const [adding, setAdding] = useState(false);
  const add = () => {
    setAdding(true);
    window.setTimeout(() => document.getElementById("gl-f1")?.focus(), 0);
  };
  usePageKey("n", add);
  return (
    <div className="rdw rdw-goals">
      <PageHead
        title="Goals"
        sub="One goal with its limits. An agent splits it into steps, you approve the plan, and each step is a flow you start."
        actions={<button className="btn primary" type="button" onClick={add} aria-keyshortcuts="N">New goal <kbd aria-hidden="true">N</kbd></button>}
      />
      <div className="rdw-cols">
        <GoalList list={list} current={arg ?? null} />
        <div className="rdw-detail">
          {(adding || !arg) && <AddGoal pid={pid} onCancel={arg ? () => setAdding(false) : undefined} onAdded={(id) => { setAdding(false); void list.reload(); go("goals", id); }} />}
          {arg && <One key={arg} pid={pid} id={arg} />}
        </div>
      </div>
    </div>
  );
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
