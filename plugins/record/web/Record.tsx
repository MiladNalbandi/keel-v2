// The Record page (#/record, #/record/<flow>): the project's flows, and one flow's record in five parts, with its export.
// It reads GET /api/projects/{pid}/record/... and writes nothing. It imports only react and @keel/web-sdk.

import { useEffect, useId, useState, type ReactNode } from "react";
import {
  Async,
  EmptyState,
  get,
  getText,
  go,
  hashForScreen,
  isTyping,
  modalOpen,
  PageHead,
  Pill,
  useLoad,
  useRoute,
  type FlowPanelProps,
  type PillTone,
} from "@keel/web-sdk";
import { CSS } from "./recordCss";

export type RecordRow = {
  thread_id: string; title: string; workflow_id: string | null; status: string; started_at: string; updated_at: string;
  tokens: number; cost_usd: number; gates: number; commits: number;
};
export type FlowRecord = {
  thread_id: string; project_id: string; status: string; updated_at: string; branch: string | null; error: string | null;
  waiting: string | null;
  asked: { by: string; via: string; title: string; workflow_id: string | null; request: string | null; started_at: string;
    parent: string | null; source: string | null; task: string | null };
  ran: { id: string; agent: string | null; step: string | null; phase: string | null; ac: string | null; provider: string | null;
    model: string | null; status: string; started_at: string; ended_at: string | null; tokens_in: number; tokens_out: number;
    tokens_cached: number; cost_usd: number; result: string | null }[];
  gates: { at: string; step: string | null; gate: string | null; decision: string | null; choice: string | null; why: string | null;
    who: string; ac: string | null; item: string | null }[];
  applied: { at: string; kind: string; ref: string; subject: string | null; files: string[] }[];
  other: { at: string; type: string; text: string }[];
  cost: { tokens_in: number; tokens_out: number; tokens_cached: number; usd: number; runs: number };
  limits: string[];
};

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/record/flows`;
export const record = {
  flows: (pid: string) => get<RecordRow[]>(`${base(pid)}?limit=100`),
  flow: (pid: string, tid: string) => get<FlowRecord>(`${base(pid)}/${encodeURIComponent(tid)}`),
  file: (pid: string, tid: string, format: "md" | "json") => getText(`${base(pid)}/${encodeURIComponent(tid)}/export?format=${format}`),
};

const TONE: Record<string, PillTone> = { done: "ok", failed: "bad", stopped: "idle", waiting: "warn", running: "run" };
const usd = (v: number) => `$${v.toFixed(v > 0 && v < 0.01 ? 4 : 2)}`;
const when = (iso: string) => iso.replace("T", " ").replace(/\.\d+Z?$|Z$/, "");

/** Gives the person the export as a file. */
async function download(pid: string, tid: string, format: "md" | "json") {
  const text = await record.file(pid, tid, format);
  const url = URL.createObjectURL(new Blob([text], { type: format === "json" ? "application/json" : "text/markdown" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `keel-record-${tid.slice(0, 12)}.${format}`;
  a.click();
  URL.revokeObjectURL(url);
}

function ExportButtons({ pid, tid, keys = false }: { pid: string; tid: string; keys?: boolean }) {
  return (
    <span className="rec-exports">
      <button type="button" className="btn sm" aria-keyshortcuts={keys ? "E" : undefined} onClick={() => void download(pid, tid, "md")}>
        <DownIcon /> Export Markdown{keys && <kbd aria-hidden="true">E</kbd>}
      </button>{" "}
      <button type="button" className="btn sm ghost" onClick={() => void download(pid, tid, "json")}>Export JSON</button>
    </span>
  );
}

const DownIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />
  </svg>
);

/** "2026-11-15 10:08:12" → "10:08" today, else the date. */
const short = (iso: string) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return when(iso);
  const today = new Date().toDateString() === d.toDateString();
  return today ? `today ${d.toTimeString().slice(0, 5)}` : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
};
const hhmm = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? when(iso) : d.toTimeString().slice(0, 5);
};

const FILTERS = ["waiting", "running", "done", "failed", "stopped"] as const;
const LABEL: Record<string, string> = { waiting: "Waiting", running: "Running", done: "Done", failed: "Failed", stopped: "Stopped" };

/** The project's flows, newest first: a search, the status filters, and one link per flow. */
function FlowList({ list, sel }: { list: RecordRow[]; sel: string | null }) {
  const [q, setQ] = useState("");
  const [f, setF] = useState("");
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const shown = list.filter((r) => (!f || r.status === f)
    && words.every((w) => `${r.title} ${r.workflow_id ?? ""} ${r.status}`.toLowerCase().includes(w)));
  const has = FILTERS.filter((x) => x !== "failed" || list.some((r) => r.status === "failed"));
  return (
    <div className="rec-side">
      <input type="search" className="rec-q" aria-label="Find a flow" placeholder="Find a flow by title, workflow or status" value={q}
        onChange={(e) => setQ(e.target.value)} />
      <div className="rec-chips" role="group" aria-label="Status">
        <button type="button" className="rec-chip" aria-pressed={!f} onClick={() => setF("")}>All</button>
        {has.map((x) => <button key={x} type="button" className="rec-chip" aria-pressed={f === x} onClick={() => setF(f === x ? "" : x)}>{LABEL[x]}</button>)}
      </div>
      {shown.length ? (
        <ul className="rec-list" aria-label="Flows">
          {shown.map((r) => (
            <li key={r.thread_id}>
              <a className="rec-item" href={hashForScreen("record", r.thread_id)} aria-current={sel === r.thread_id ? "page" : undefined}
                aria-labelledby={`rec-t-${r.thread_id}`} aria-describedby={`rec-m-${r.thread_id}`}>
                <span className={`rec-dot t-${r.status}`} aria-hidden="true" />
                <span className="rec-it">
                  <b className="rec-title" id={`rec-t-${r.thread_id}`}>{r.title || r.thread_id}</b>
                  <span className="rec-meta" id={`rec-m-${r.thread_id}`}><Pill tone={TONE[r.status] ?? "idle"}>{r.status}</Pill>
                    {r.workflow_id && <span className="mono">{r.workflow_id}</span>}<span>{short(r.started_at)}</span></span>
                  <span className="rec-nums mono">{r.gates} gates · {r.commits} commits · {r.tokens.toLocaleString()} tokens · <span>{usd(r.cost_usd)}</span></span>
                </span>
              </a>
            </li>
          ))}
        </ul>
      ) : <p className="sub rec-none">No flow matches.</p>}
    </div>
  );
}

function Card({ title, n, children, className = "" }: { title: string; n?: number; children: ReactNode; className?: string }) {
  const id = useId();
  return (
    <section className={`rec-card ${className}`} aria-labelledby={id}>
      <header className="rec-card-h"><h3 id={id}>{title}</h3>{n !== undefined && <span className="rec-n">{n}</span>}</header>
      <div className="rec-card-b">{children}</div>
    </section>
  );
}

export function RecordView({ r }: { r: FlowRecord }) {
  const commits = r.applied.filter((a) => a.kind === "commit");
  const more = r.applied.filter((a) => a.kind !== "commit");
  return (
    <div className="rec-view">
      <Card title="Who asked">
        <dl className="rec-kv">
          <dt>By</dt><dd>{r.asked.by}</dd>
          <dt>Through</dt><dd>{r.asked.via}</dd>
          <dt>Workflow</dt><dd className="mono">{r.asked.workflow_id ?? "unknown"}</dd>
          <dt>Started</dt><dd className="mono">{when(r.asked.started_at)}</dd>
          {r.asked.task && <><dt>Task</dt><dd className="mono">{r.asked.task}</dd></>}
          {r.asked.parent && <><dt>From the flow</dt><dd><a href={hashForScreen("record", r.asked.parent)}>{r.asked.parent}</a></dd></>}
        </dl>
        {r.asked.request ? <blockquote className="rec-quote">{r.asked.request}</blockquote>
          : <p className="sub">keel kept no request text for this flow.</p>}
      </Card>
      <Card title="What ran" n={r.ran.length} className="rec-flush">
        {r.ran.length ? (
          <div className="ss-tablewrap">
            <table className="ss-table rec-table" aria-label="What ran">
              <thead><tr><th>Started</th><th>Step</th><th>Agent</th><th>Model</th><th>Status</th><th>Tokens</th><th>Cost</th></tr></thead>
              <tbody>
                {r.ran.map((x) => (
                  <tr key={x.id}>
                    <td className="mono sub" title={when(x.started_at)}>{hhmm(x.started_at)}</td>
                    <td>{x.step ?? ""}{x.ac ? ` · ${x.ac}` : ""}</td>
                    <td className="mono">{x.agent ?? ""}</td>
                    <td className="mono">{x.model ?? ""}</td>
                    <td><Pill tone={TONE[x.status] ?? "idle"}>{x.status}</Pill></td>
                    <td className="mono">{(x.tokens_in + x.tokens_out).toLocaleString()}</td>
                    <td className="mono">{usd(x.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div className="empty">Nothing ran.</div>}
      </Card>
      <div className="rec-two">
        <Card title="Gate answers" n={r.gates.length}>
          {r.waiting && <p className="rec-waiting"><Pill tone="warn">waiting</Pill> {r.waiting}</p>}
          {r.gates.length ? (
            <ul className="rec-lines" aria-label="Gate answers">
              {r.gates.map((g, i) => (
                <li key={i}>
                  <span className="mono sub" title={when(g.at)}>{hhmm(g.at)}</span> <b>{[g.gate ?? g.step, g.ac, g.item].filter(Boolean).join(" · ")}</b>
                  <br />{g.decision}{g.choice ? ` [${g.choice}]` : ""}, by <b>{g.who}</b>{g.why ? <span className="sub">. {g.why}</span> : ""}
                </li>
              ))}
            </ul>
          ) : <div className="empty">No gate was answered.</div>}
        </Card>
        <Card title="Commits" n={commits.length}>
          {commits.length ? (
            <ul className="rec-lines" aria-label="Commits">
              {commits.map((c) => (
                <li key={c.ref}>
                  <span className="mono rec-sha">{c.ref.slice(0, 12)}</span> {c.subject}
                  {c.files.length ? <span className="sub mono rec-files"> ({c.files.join(", ")})</span> : null}
                </li>
              ))}
            </ul>
          ) : <div className="empty">No commit.</div>}
          {more.length > 0 && <p className="sub">Also: {more.map((a) => `${a.kind} ${a.ref}`).join("; ")}</p>}
        </Card>
      </div>
      <Card title="Cost">
        <div className="rec-cost">
          <span className="rec-stat"><span className="sub">Tokens</span><b className="mono">{(r.cost.tokens_in + r.cost.tokens_out).toLocaleString()}</b></span>
          <span className="rec-stat"><span className="sub">In / out / cached</span>
            <span className="mono">{r.cost.tokens_in.toLocaleString()} / {r.cost.tokens_out.toLocaleString()} / {r.cost.tokens_cached.toLocaleString()}</span></span>
          <span className="rec-stat"><span className="sub">Cost</span><b className="mono">{usd(r.cost.usd)}</b></span>
          <span className="rec-stat"><span className="sub">Runs</span><span className="mono">{r.cost.runs}</span></span>
          <span className="sub rec-note">At keel's API prices; the provider's bill may differ a little.</span>
        </div>
      </Card>
      <div className="rec-two">
        {r.other.length > 0 && (
          <Card title="Also on this flow" n={r.other.length}>
            <ul className="rec-lines" aria-label="Also on this flow">
              {r.other.map((o, i) => <li key={i}><span className="mono sub" title={when(o.at)}>{hhmm(o.at)}</span> {o.text}</li>)}
            </ul>
          </Card>
        )}
        <Card title="What this record is not">
          <ul className="rec-bul">{r.limits.map((l) => <li key={l}>{l}</li>)}</ul>
        </Card>
      </div>
    </div>
  );
}

function OneFlow({ pid, tid }: { pid: string; tid: string }) {
  const r = useLoad(`record:${pid}:${tid}`, () => record.flow(pid, tid));
  return (
    <div className="rec-main" aria-live="polite">
      <a className="btn sm ghost rec-back" href={hashForScreen("record")}>‹ All flows</a>
      <Async r={r} what="Loading the record">{(data) => (
        <>
          <header className="rec-head">
            <div className="rec-head-t">
              <h2>{data.asked.title || "Record of a flow"}</h2>
              <p className="rec-meta">
                <Pill tone={TONE[data.status] ?? "idle"}>{data.status}</Pill>
                {data.branch ? <span className="mono">⎇ {data.branch}</span> : null}
                <span className="sub">updated {short(data.updated_at)}</span>
              </p>
            </div>
            <button type="button" className="btn sm" onClick={() => go("flow", tid)}>Open the flow ›</button>
          </header>
          {data.error && <p className="rec-error" role="note">{data.error}</p>}
          <RecordView r={data} />
        </>
      )}</Async>
    </div>
  );
}

export function RecordPage({ pid }: { pid: string }) {
  const { arg } = useRoute();
  const rows = useLoad(`record:${pid}`, () => record.flows(pid));
  // E takes the open flow's record as Markdown (the key on its button)
  useEffect(() => {
    if (!arg) return;
    const on = (e: KeyboardEvent) => {
      if (e.key !== "e" || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || e.defaultPrevented || isTyping(e.target) || modalOpen()) return;
      e.preventDefault();
      void download(pid, arg, "md");
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [pid, arg]);
  return (
    <div className={`rdw rec${arg ? " has-flow" : ""}`}>
      <style>{CSS}</style>
      <PageHead title="Record" sub="One page and one file per flow: who asked, what ran, how each gate was answered, the commits and the cost."
        actions={arg ? <ExportButtons pid={pid} tid={arg} keys /> : undefined} />
      {!arg && rows.data && !rows.data.length ? (
        <div className="panel"><EmptyState title="No flow yet">A flow's record shows here as soon as it starts.</EmptyState></div>
      ) : (
        <div className="rec-split">
          <div className="rec-side-w">
            <Async r={rows} what="Loading the flows">{(list) => <FlowList list={list} sel={arg ?? null} />}</Async>
          </div>
          {arg ? <OneFlow key={arg} pid={pid} tid={arg} /> : (
            <div className="rec-main rec-pick">
              <p><b>Pick a flow</b> on the left to read its record: who asked, what ran, each gate's answer, the commits and the cost.</p>
              <p className="sub">Each record exports as Markdown or JSON.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** A small panel under a flow's steps: open its record, or take the file. */
export function RecordFlowPanel({ pid, thread }: FlowPanelProps) {
  return (
    <section className="panel" aria-label="Record of this flow">
      <div className="panel-body">
        <b>Record of this flow.</b> Who asked, what ran, each gate's answer, the commits and the cost.{" "}
        <button type="button" className="btn sm" onClick={() => go("record", thread.thread_id)}>Open the record</button>{" "}
        <ExportButtons pid={pid} tid={thread.thread_id} />
      </div>
    </section>
  );
}
