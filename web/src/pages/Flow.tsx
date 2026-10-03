// Flow (Run): the graph of the running thread, the gate card that waits for you, checkpoints with Rewind,
// acceptance criteria, the budget meter and the live events. The init workflow gets its own layout.

import { Fragment, useMemo, useState } from "react";
import {
  api, errorParts, type Checkpoint, type EngineEvent, type FlowView, type Job, type Memory, type ThreadState, type Workflow,
} from "../api";
import { eventLine } from "../components/events";
import { Graph, GraphLegend } from "../components/Graph";
import { StartFlowDrawer } from "../components/StartFlow";
import { Async, ErrorBox, GoButton, PageHead, Panel, Pill, Prov, type PillTone } from "../components/ui";
import { clock, kfmt, usd } from "../format";
import { useApp, useLoad } from "../state";

const AC_TONE: Record<string, PillTone> = { done: "ok", green: "ok", red: "bad", todo: "idle" };

export function FlowPage({ pid }: { pid: string }) {
  const flow = useLoad(`flow:${pid}`, () => api.flow(pid));
  const { project } = useApp();
  const [start, setStart] = useState(false);
  return (
    <>
      <Async r={flow} what="Loading the flow">
        {(f) => !f.thread || !f.workflow ? (
          <>
            <PageHead title={project?.name ?? pid} sub="No flow is running in this project." />
            <div className="panel"><div className="panel-body empty grid" style={{ gap: 10, justifyItems: "center" }}>
              <b>Nothing running here yet</b>
              <span className="sub">Start a feature, change, fix or hunt. You will see the estimate before it starts.</span>
              <button className="btn primary" type="button" id="startFlow2" onClick={() => setStart(true)}>Start a flow</button>
            </div></div>
          </>
        ) : isInit(f.workflow) ? (
          <InitFlow pid={pid} f={f as Required<FlowView> & { thread: ThreadState; workflow: Workflow }} onStart={() => setStart(true)} reload={flow.reload} />
        ) : (
          <ThreadView pid={pid} thread={f.thread} workflow={f.workflow} keelState={f.keel_state} reload={flow.reload} onStart={() => setStart(true)} />
        )}
      </Async>
      {start && <StartFlowDrawer onClose={() => setStart(false)} />}
    </>
  );
}

const isInit = (w: Workflow) => w.id === "init" || w.based_on === "keel/init" || w.based_on === "init";

function useThreadBits(pid: string, thread: ThreadState, workflow: Workflow) {
  const history = useLoad(`hist:${thread.thread_id}`, () => api.history(thread.thread_id));
  const n = thread.acs.length || 3;
  const est = useLoad(`est:${pid}:${workflow.id}:${n}`, () => api.estimate(pid, workflow.id, n), { live: false });
  const jobs = useLoad(`jobs-run:${pid}`, () => api.jobs({ project: pid, status: "running" }));
  const job = jobs.data?.find((j) => j.thread_id === thread.thread_id) ?? null;
  return { history, est, job, jobs };
}

function Header({ thread, workflow, onStart, extra }: { thread: ThreadState; workflow: Workflow; onStart: () => void; extra?: React.ReactNode }) {
  const { project, toast } = useApp();
  const [busy, setBusy] = useState(false);
  const live = thread.status === "running" || thread.status === "waiting";
  return (
    <PageHead
      title={`${project?.name ?? thread.project_id} · ${workflow.name}`}
      sub={<>{thread.title && <>{thread.title} · </>}Branch <span className="mono">{project?.branch ?? "—"}</span> · thread <span className="mono">{thread.thread_id}</span> · v{workflow.version}</>}
      actions={<>
        {extra}
        {live ? (
          <button className="btn" type="button" disabled={busy} onClick={async () => {
            setBusy(true);
            try {
              await api.stopThread(thread.thread_id);
              toast("Stop sent; the thread keeps its last checkpoint.");
            } catch (e) {
              toast(errorParts(e).message);
            } finally {
              setBusy(false);
            }
          }}>Stop flow</button>
        ) : <button className="btn primary" type="button" onClick={onStart}>Start a flow</button>}
      </>}
    />
  );
}

function ThreadView({ pid, thread, workflow, keelState, reload, onStart }: {
  pid: string; thread: ThreadState; workflow: Workflow; keelState: Record<string, unknown> | null; reload: () => Promise<void>; onStart: () => void;
}) {
  const { history, est, job } = useThreadBits(pid, thread, workflow);
  const tokens = useMemo(() => perStep(workflow, est.data?.per_step), [workflow, est.data]);
  return (
    <>
      <Header thread={thread} workflow={workflow} onStart={onStart} />
      <div className="grid" style={{ gap: 16 }}>
        <Panel title="Graph" extra={<GraphLegend />}>
          <div className="graph-wrap">
            <Graph steps={workflow.steps} current={thread.current} status={thread.status} tokens={thread.status === "done" ? undefined : tokens} />
          </div>
        </Panel>
        <StatusCard pid={pid} thread={thread} workflow={workflow} job={job} onDone={reload} />
        <BudgetMeter thread={thread} estimate={est.data?.tokens ?? null} />
        <div className="grid g2">
          <CheckpointsPanel thread={thread} history={history.data} error={history.error} onRewound={reload} />
          <div className="grid" style={{ alignContent: "start" }}>
            <AcsPanel thread={thread} />
            <KeelStatePanel state={keelState} />
            <EventsPanel threadId={thread.thread_id} />
          </div>
        </div>
      </div>
    </>
  );
}

function perStep(w: Workflow, per?: { step: string; tokens: number }[]) {
  if (!per) return undefined;
  const m: Record<string, number> = {};
  per.forEach((p) => {
    const s = w.steps.find((x) => x.id === p.step || x.name === p.step);
    if (s) m[s.id] = (m[s.id] ?? 0) + p.tokens;
  });
  return m;
}

/** The ◆ card when the thread waits, the running card while an agent works, or how it ended. */
export function StatusCard({ pid, thread, workflow, job, onDone }: {
  pid: string; thread: ThreadState; workflow: Workflow; job: Job | null; onDone: () => Promise<void>;
}) {
  const step = workflow.steps.find((s) => s.id === thread.current);
  if (thread.status === "waiting" && thread.waiting) return <GateCard thread={thread} workflow={workflow} onDone={onDone} />;
  if (thread.status === "running") {
    return (
      <div className="running-card">
        <b><Pill tone="run">running</Pill> node <span className="mono">{step?.name ?? thread.current ?? thread.phase}</span>{thread.ac ? ` — ${thread.ac}` : ""}</b>
        <span className="sub">
          {job ? <>{job.agent} · <Prov p={job.provider} m={job.model} /> · {job.steps_count} steps · keel calls {job.mcp_calls}</>
            : step?.agent ? <>{step.agent}{step.tools?.length ? ` · tools: ${step.tools.join(", ")}` : ""}</>
              : step?.kind === "code" ? "plain code, no LLM" : "working"}
        </span>
        <div className="row">
          <GoButton to="live" arg={job?.id}>Watch live</GoButton>
          <GoButton to="jobs" arg={job?.id} className="btn sm ghost">Open job</GoButton>
        </div>
      </div>
    );
  }
  void pid;
  const tone: PillTone = thread.status === "done" ? "ok" : thread.status === "failed" ? "bad" : "idle";
  return (
    <div className={thread.status === "failed" ? "errbox" : "running-card"} style={thread.status === "done" ? { borderColor: "var(--ok)", background: "var(--ok-soft)" } : undefined}>
      <b><Pill tone={tone}>{thread.status}</Pill> {thread.status === "done" ? "The flow is done." : thread.status === "failed" ? "The flow failed." : "The flow was stopped."}</b>
      {thread.error && <span className="sub">{thread.error}</span>}
      {thread.status !== "done" && <span className="hint">Rewind to a checkpoint below to try again from there.</span>}
    </div>
  );
}

function GateCard({ thread, workflow, onDone }: { thread: ThreadState; workflow: Workflow; onDone: () => Promise<void> }) {
  const { toast, reloadProjects } = useApp();
  const w = thread.waiting!;
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const step = workflow.steps.find((s) => s.id === w.step) ?? workflow.steps.find((s) => s.id === thread.current);
  const back = step?.back ? workflow.steps.find((s) => s.id === step.back) : undefined;
  const ac = thread.acs.find((a) => a.id === thread.ac);
  const labels = w.kind === "budget"
    ? { approve: "Continue over the cap", reject: "Stop here", needWhy: false }
    : w.kind === "fix"
      ? { approve: "Approve fix", reject: "Reject fix", needWhy: true }
      : { approve: `Approve${ac ? " " + ac.id : ""}`, reject: `Send back${back ? " to " + back.name : ""}`, needWhy: true };

  const decide = async (decision: "approve" | "reject") => {
    if (decision === "reject" && labels.needWhy && !why.trim()) {
      document.getElementById("why")?.focus();
      toast("Write why first, so the agent knows what to change.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await api.resume(thread.thread_id, decision, why.trim() || undefined);
      toast(decision === "approve" ? "Approved. The flow moves on." : labels.needWhy ? "Sent back. The agent gets your reason in its prompt." : "Stopped.");
      setWhy("");
      await Promise.all([onDone(), reloadProjects()]);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="interrupt" role="region" aria-label="Gate waits for you">
      <h3><Pill tone="warn">◆ waits for you</Pill> {w.title}{ac ? ` — ${ac.id} [${ac.layer}] ${ac.title}` : ""}</h3>
      {w.detail && <div className="facts" style={{ whiteSpace: "pre-wrap" }}>{w.detail}</div>}
      {labels.needWhy && (
        <div className="field">
          <label htmlFor="why">Why (needed to send back)</label>
          <textarea id="why" value={why} onChange={(e) => setWhy(e.target.value)} placeholder="e.g. also check the error message text in the test" />
        </div>
      )}
      <div className="row">
        {w.options.includes("approve") && <button className="btn warn" type="button" id="approve" disabled={busy} onClick={() => decide("approve")}>{labels.approve}</button>}
        {w.options.includes("reject") && <button className="btn" type="button" id="sendback" disabled={busy} onClick={() => decide("reject")}>{labels.reject}</button>}
        <span className="hint">Resumes the thread with <span className="mono">Command(resume={"{decision, why}"})</span>.</span>
      </div>
      {err && <ErrorBox error={err} />}
    </div>
  );
}

export function BudgetMeter({ thread, estimate, title = "Budget for this flow" }: { thread: ThreadState; estimate: number | null; title?: string }) {
  const u = thread.usage;
  const used = u.tokens_in + u.tokens_out;
  const cap = u.cap_tokens || Math.max(used, estimate ?? 0, 1);
  const pct = (n: number) => `${Math.min(100, (n / cap) * 100)}%`;
  return (
    <Panel title={title} extra={<span className="hint">cap {kfmt(u.cap_tokens)}</span>} body="grid">
      <div className="grid" style={{ gap: 8 }}>
        <div className="meter2" role="meter" aria-label="Tokens used of the cap" aria-valuemin={0} aria-valuemax={cap} aria-valuenow={used}>
          <i className="used" style={{ width: pct(used), background: used > cap * 0.8 ? "var(--warn)" : undefined }} />
          {estimate !== null && <i className="est" style={{ left: pct(estimate) }} title="estimate" />}
        </div>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span className="num"><b>{kfmt(used)}</b> used</span>
          <span className="num sub">estimate {estimate === null ? "…" : kfmt(estimate)}</span>
          <span className="num sub">cap {kfmt(u.cap_tokens)}</span>
        </div>
        <span className="hint">Spent so far: {usd(u.cost_usd)} on API, {u.premium_requests} Copilot premium requests.</span>
      </div>
    </Panel>
  );
}

function CheckpointsPanel({ thread, history, error, onRewound }: {
  thread: ThreadState; history: Checkpoint[] | null; error: { message: string; hint?: string } | null; onRewound: () => Promise<void>;
}) {
  const { toast } = useApp();
  const [busy, setBusy] = useState<string | null>(null);
  const rewind = async (cp: Checkpoint) => {
    setBusy(cp.id);
    try {
      await api.rewind(thread.thread_id, cp.id);
      toast(`Rewound to checkpoint #${cp.n}. A new branch starts here; the old one stays in history.`);
      await onRewound();
    } catch (e) {
      const p = errorParts(e);
      toast(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <Panel title="Checkpoints" extra={<span className="hint">Every step is saved. Rewind starts a new branch from that point.</span>}>
      {error ? <ErrorBox error={error} /> : !history ? <div className="empty loading">Loading…</div> : !history.length ? <div className="empty">No checkpoint yet.</div> : (
        <ul className="timeline">
          {history.map((c, i) => (
            <li key={c.id} className={i === 0 ? "head" : ""}>
              <span className="cp">#{c.n}</span>
              <span className="t">{clock(c.at)}</span>
              <span><span className="mono">{c.step}</span> <span className="sub">{c.note}</span></span>
              {i === 0 ? <span className="tag">now</span> : (
                <button className="btn sm ghost" type="button" disabled={busy !== null} onClick={() => rewind(c)}>{busy === c.id ? "Rewinding…" : "Rewind here"}</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function AcsPanel({ thread }: { thread: ThreadState }) {
  return (
    <Panel title="Acceptance criteria">
      {!thread.acs.length ? <span className="sub">The spec step writes them. They show here after it.</span> : (
        <div className="acs">
          {thread.acs.map((a) => (
            <div key={a.id} className="ac">
              <div className="row" style={{ justifyContent: "space-between" }}><b>{a.id}</b><Pill tone={AC_TONE[a.status] ?? "idle"}>{a.status}</Pill></div>
              <span className="sub">[{a.layer}] {a.title}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function KeelStatePanel({ state }: { state: Record<string, unknown> | null }) {
  if (!state) return null;
  const keys = ["flow", "phase", "lane", "ac", "branch"].filter((k) => state[k] !== undefined && state[k] !== null && typeof state[k] !== "object");
  if (!keys.length) return null;
  return (
    <Panel title="keel v1 state" extra={<span className="hint mono">.keel/state.json</span>}>
      <div className="kv">{keys.map((k) => <Fragment key={k}><span>{k}</span><b className="mono">{String(state[k])}</b></Fragment>)}</div>
    </Panel>
  );
}

export function EventsPanel({ threadId }: { threadId: string }) {
  const { recent } = useApp();
  const mine = recent.filter((e: EngineEvent) => e.thread_id === threadId && e.type !== "agent.step").slice(-12).reverse();
  return (
    <Panel title="Events">
      <div className="events">
        {!mine.length ? <span className="sub">Events show here as they happen.</span> : mine.map((e, i) => {
          const l = eventLine(e);
          return <div key={i}><span className="t mono sub">{clock(e.at, false)}</span><span className="k">{l.k}</span><span>{l.text}</span></div>;
        })}
      </div>
    </Panel>
  );
}

// ---------- init flow: ladder ‖ knowledge build ----------

type Rung = { label: string; cmd: string; status: "ok" | "fix" | "todo" | "skip"; note: string };

function rungsFrom(state: Record<string, unknown> | null): Rung[] {
  if (!state) return [];
  const raw = (state.ladder ?? (state.setup as Record<string, unknown> | undefined)?.rungs ?? null) as unknown;
  const list: Record<string, unknown>[] = Array.isArray(raw) ? raw : raw && typeof raw === "object" ? Object.values(raw as object) : [];
  return list.map((r) => {
    const s = String(r.status ?? (r.ok === true ? "ok" : r.ok === false ? "fail" : "todo"));
    const status: Rung["status"] = /ok|pass/.test(s) ? "ok" : /fail|fix/.test(s) ? "fix" : /skip/.test(s) ? "skip" : "todo";
    return { label: String(r.label ?? r.id ?? "rung"), cmd: String(r.cmd ?? r.command ?? ""), status, note: String(r.note ?? r.why ?? (r.attempts ? `attempt ${r.attempts}` : "")) };
  });
}

const RUNG_PILL: Record<Rung["status"], [PillTone, string]> = { ok: ["ok", "pass"], fix: ["warn", "fixing"], todo: ["idle", "waiting"], skip: ["idle", "skipped"] };
const KB_PILL: Record<string, [PillTone, string]> = { written: ["ok", "written"], stale: ["warn", "stale"], missing: ["idle", "not written"], writing: ["run", "writing"] };

function InitFlow({ pid, f, onStart, reload }: { pid: string; f: { thread: ThreadState; workflow: Workflow; keel_state: Record<string, unknown> | null }; onStart: () => void; reload: () => Promise<void> }) {
  const { thread, workflow } = f;
  const { est, job, jobs } = useThreadBits(pid, thread, workflow);
  const memory = useLoad<Memory>(`mem:${pid}`, () => api.memory(pid));
  const rungs = rungsFrom(f.keel_state);
  const passed = rungs.filter((r) => r.status === "ok").length;
  const kb = memory.data?.knowledge ?? [];
  const writing = (jobs.data ?? []).filter((j) => j.thread_id === thread.thread_id && /librarian/.test(j.agent));
  const written = kb.filter((k) => k.status === "written").length;
  return (
    <>
      <Header thread={thread} workflow={workflow} onStart={onStart} extra={<GoButton to="wiki" className="btn">Open wiki</GoButton>} />
      <Panel title="Graph" extra={<GraphLegend />} body="grid">
        <div className="grid" style={{ gap: 14 }}>
          <div className="graph-wrap"><Graph steps={workflow.steps} current={thread.current} status={thread.status} /></div>
          <div className="lanes">
            <div className="lane">
              <div className="lane-h"><b>Ladder</b><span className="sub num">{rungs.length ? `${passed} / ${rungs.length} rungs` : "not started"}</span></div>
              <div className="meter m-ok"><i style={{ width: `${rungs.length ? (passed / rungs.length) * 100 : 0}%` }} /></div>
              <span className="sub">plain code · each rung must pass before the next</span>
            </div>
            <div className="lane">
              <div className="lane-h"><b>Knowledge build</b><span className="sub num">{written} / {kb.length || "?"} sections</span></div>
              <div className="meter m-ok"><i style={{ width: `${kb.length ? (written / kb.length) * 100 : 0}%`, background: "var(--run)" }} /></div>
              <span className="sub">{writing.length ? `librarian × ${writing.length} in parallel` : "librarians"} · writes the wiki</span>
            </div>
          </div>
        </div>
      </Panel>
      <div className="grid g2" style={{ marginTop: 16 }}>
        <Panel title="Ladder" extra={<span className="hint">the exact command that worked is saved for each rung</span>} body={false}>
          {!rungs.length ? <div className="panel-body empty">Rungs show here when the ladder runs.</div> : (
            <div className="table-wrap"><table>
              <thead><tr><th>#</th><th>Rung</th><th>Command</th><th>Status</th></tr></thead>
              <tbody>{rungs.map((r, i) => (
                <tr key={i}>
                  <td className="num sub">{i + 1}</td>
                  <td><b>{r.label}</b>{r.status === "fix" && r.note && <div className="sub">{r.note}</div>}</td>
                  <td className="mono sub">{r.cmd}</td>
                  <td><Pill tone={RUNG_PILL[r.status][0]}>{RUNG_PILL[r.status][1]}</Pill></td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
        </Panel>
        <div className="grid" style={{ alignContent: "start" }}>
          <StatusCard pid={pid} thread={thread} workflow={workflow} job={job} onDone={reload} />
          <Panel title="Knowledge base → wiki" body="grid">
            <div className="grid" style={{ gap: 8 }}>
              {memory.error ? <ErrorBox error={memory.error} /> : !kb.length ? <span className="sub">No section yet.</span> : kb.map((k) => {
                const live = writing.some((j) => j.agent.includes(k.id) || j.step.includes(k.id));
                const [tone, lab] = live ? KB_PILL.writing : KB_PILL[k.status] ?? ["idle", k.status];
                return <div key={k.id} className="row" style={{ justifyContent: "space-between" }}><span><b>{k.id}</b> <span className="sub">{k.words} words · {k.cites} citations</span></span><Pill tone={tone}>{lab}</Pill></div>;
              })}
              <GoButton to="wiki">Read what is written so far</GoButton>
            </div>
          </Panel>
          <BudgetMeter thread={thread} estimate={est.data?.tokens ?? null} title="Budget for init" />
          <EventsPanel threadId={thread.thread_id} />
        </div>
      </div>
    </>
  );
}
