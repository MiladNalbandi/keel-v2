// The Record page (#/record, #/record/<flow>): the project's flows, and one flow's record in five parts, with its export.
// It reads GET /api/projects/{pid}/record/... and writes nothing. It imports only react and @keel/web-sdk.

import {
  Async,
  EmptyState,
  get,
  getText,
  go,
  hashForScreen,
  PageHead,
  Panel,
  Pill,
  useLoad,
  useRoute,
  type FlowPanelProps,
  type PillTone,
} from "@keel/web-sdk";

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

function ExportButtons({ pid, tid }: { pid: string; tid: string }) {
  return (
    <span>
      <button type="button" className="btn sm" onClick={() => void download(pid, tid, "md")}>Export Markdown</button>{" "}
      <button type="button" className="btn sm ghost" onClick={() => void download(pid, tid, "json")}>Export JSON</button>
    </span>
  );
}

function FlowList({ pid }: { pid: string }) {
  const rows = useLoad(`record:${pid}`, () => record.flows(pid));
  return (
    <>
      <PageHead title="Record" sub="One page and one file per flow: who asked, what ran, how each gate was answered, the commits and the cost." />
      <Async r={rows} what="Loading the flows">
        {(list) => !list.length ? (
          <div className="panel"><EmptyState title="No flow yet">A flow's record shows here as soon as it starts.</EmptyState></div>
        ) : (
          <div className="panel ss-tablewrap">
            <table className="ss-table" aria-label="Flows">
              <thead>
                <tr><th>Flow</th><th>Workflow</th><th>Status</th><th>Started</th><th>Gates</th><th>Commits</th><th>Tokens</th><th>Cost</th></tr>
              </thead>
              <tbody>
                {list.map((r) => (
                  <tr key={r.thread_id}>
                    <td><a href={hashForScreen("record", r.thread_id)}>{r.title || r.thread_id}</a></td>
                    <td className="mono">{r.workflow_id ?? ""}</td>
                    <td><Pill tone={TONE[r.status] ?? "idle"}>{r.status}</Pill></td>
                    <td className="mono">{when(r.started_at)}</td>
                    <td>{r.gates}</td>
                    <td>{r.commits}</td>
                    <td>{r.tokens.toLocaleString()}</td>
                    <td>{usd(r.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Async>
    </>
  );
}

export function RecordView({ r }: { r: FlowRecord }) {
  const commits = r.applied.filter((a) => a.kind === "commit");
  const more = r.applied.filter((a) => a.kind !== "commit");
  return (
    <div className="grid" style={{ gap: 12 }}>
      <Panel title="Who asked">
        <div>
          <p><b>By:</b> {r.asked.by}</p>
          <p><b>Through:</b> {r.asked.via}</p>
          <p><b>Workflow:</b> <span className="mono">{r.asked.workflow_id ?? "unknown"}</span> · <b>Started:</b> {when(r.asked.started_at)}</p>
          {r.asked.task && <p><b>Task:</b> <span className="mono">{r.asked.task}</span></p>}
          {r.asked.parent && <p><b>From the flow:</b> <a href={hashForScreen("record", r.asked.parent)}>{r.asked.parent}</a></p>}
          {r.asked.request ? <blockquote style={{ whiteSpace: "pre-wrap" }}>{r.asked.request}</blockquote>
            : <p className="sub">keel kept no request text for this flow.</p>}
        </div>
      </Panel>
      <Panel title={`What ran (${r.ran.length})`} body={false}>
        {r.ran.length ? (
          <div className="ss-tablewrap">
            <table className="ss-table" aria-label="What ran">
              <thead><tr><th>Started</th><th>Step</th><th>Agent</th><th>Model</th><th>Status</th><th>Tokens</th><th>Cost</th></tr></thead>
              <tbody>
                {r.ran.map((x) => (
                  <tr key={x.id}>
                    <td className="mono">{when(x.started_at)}</td>
                    <td>{x.step ?? ""}{x.ac ? ` · ${x.ac}` : ""}</td>
                    <td className="mono">{x.agent ?? ""}</td>
                    <td className="mono">{x.model ?? ""}</td>
                    <td>{x.status}</td>
                    <td>{(x.tokens_in + x.tokens_out).toLocaleString()}</td>
                    <td>{usd(x.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div className="empty">Nothing ran.</div>}
      </Panel>
      <Panel title={`Gate answers (${r.gates.length})`}>
        {r.waiting && <p><Pill tone="warn">waiting</Pill> {r.waiting}</p>}
        {r.gates.length ? (
          <ul aria-label="Gate answers">
            {r.gates.map((g, i) => (
              <li key={i}>
                <span className="mono">{when(g.at)}</span> <b>{[g.gate ?? g.step, g.ac, g.item].filter(Boolean).join(" · ")}</b>:{" "}
                {g.decision}{g.choice ? ` [${g.choice}]` : ""}, by <b>{g.who}</b>{g.why ? `. ${g.why}` : ""}
              </li>
            ))}
          </ul>
        ) : <div className="empty">No gate was answered.</div>}
      </Panel>
      <Panel title={`Commits (${commits.length})`}>
        {commits.length ? (
          <ul aria-label="Commits">
            {commits.map((c) => (
              <li key={c.ref}>
                <span className="mono">{c.ref.slice(0, 12)}</span> {c.subject}
                {c.files.length ? <span className="sub"> ({c.files.join(", ")})</span> : null}
              </li>
            ))}
          </ul>
        ) : <div className="empty">No commit.</div>}
        {more.length > 0 && <p className="sub">Also: {more.map((a) => `${a.kind} ${a.ref}`).join("; ")}</p>}
      </Panel>
      <Panel title="Cost">
        <p>
          <b>{(r.cost.tokens_in + r.cost.tokens_out).toLocaleString()}</b> tokens (in {r.cost.tokens_in.toLocaleString()}, out{" "}
          {r.cost.tokens_out.toLocaleString()}, cached {r.cost.tokens_cached.toLocaleString()}), <b>{usd(r.cost.usd)}</b>, {r.cost.runs} runs.{" "}
          <span className="sub">Plan logins show no cost per call, so the real bill may differ.</span>
        </p>
      </Panel>
      {r.other.length > 0 && (
        <Panel title={`Also on this flow (${r.other.length})`}>
          <ul aria-label="Also on this flow">
            {r.other.map((o, i) => <li key={i}><span className="mono">{when(o.at)}</span> {o.text}</li>)}
          </ul>
        </Panel>
      )}
      <Panel title="What this record is not">
        <ul>{r.limits.map((l) => <li key={l}>{l}</li>)}</ul>
      </Panel>
    </div>
  );
}

function OneFlow({ pid, tid }: { pid: string; tid: string }) {
  const r = useLoad(`record:${pid}:${tid}`, () => record.flow(pid, tid));
  return (
    <>
      <PageHead title={r.data?.asked.title || "Record of a flow"}
        sub={r.data ? <><Pill tone={TONE[r.data.status] ?? "idle"}>{r.data.status}</Pill> {r.data.branch ? <span className="mono">{r.data.branch}</span> : null}</> : undefined}
        actions={<span><a className="btn sm ghost" href={hashForScreen("record")}>All flows</a> <ExportButtons pid={pid} tid={tid} /></span>} />
      <Async r={r} what="Loading the record">{(data) => <RecordView r={data} />}</Async>
    </>
  );
}

export function RecordPage({ pid }: { pid: string }) {
  const { arg } = useRoute();
  return arg ? <OneFlow key={arg} pid={pid} tid={arg} /> : <FlowList pid={pid} />;
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
