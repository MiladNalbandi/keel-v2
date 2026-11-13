// #101 part 3 Watch › Flow doctor: what crashed in keel's flows (every project), what keel repaired by itself, what
// waited for the person, and what to improve, from the engine's crash log (GET /api/flow-doctor/report). The Markdown
// copy is the same report keel_flow_doctor gives an MCP client: paste it into an issue.

import { useState } from "react";
import { api, type Crash, type CrashKind, type DoctorReport } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBox, Loading, PageHead, Panel, Pill, Tabs, type PillTone } from "../components/ui";
import { agoText } from "../format";
import { useLoad } from "../state";

const DAYS = ["1", "7", "30"] as const;
type Days = (typeof DAYS)[number];

const END: Record<string, [PillTone, string]> = {
  repaired: ["ok", "repaired by keel"],
  retried: ["ok", "went on after your Retry"],
  waiting: ["warn", "waits for you"],
  repairing: ["run", "keel is repairing it"],
  stopped: ["idle", "stopped"],
  failed: ["bad", "failed"],
};

export function FlowDoctorPage() {
  const [days, setDays] = useState<Days>("7");
  const rep = useLoad(`flow-doctor:${days}`, () => api.doctorReport(Number(days)));
  const [copied, setCopied] = useState(false);
  const [cleared, setCleared] = useState<string | null>(null);

  async function copy(r: DoctorReport) {
    try {
      await navigator.clipboard.writeText(r.markdown);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  async function clear() {
    if (!window.confirm("Remove every line of the crash log? The report starts empty.")) return;
    const r = await api.clearCrashes();
    setCleared(`${r.removed} line${r.removed === 1 ? "" : "s"} removed.`);
    void rep.reload();
  }

  const r = rep.data;
  return (
    <>
      <PageHead
        title="Flow doctor"
        sub="What crashed in keel's flows, what keel repaired by itself, what waited for you, and what to improve. Every project."
        actions={
          <>
            <Tabs value={days} onChange={(d) => setDays(d)} label="How far back"
              options={DAYS.map((d) => [d, d === "1" ? "Today" : `${d} days`] as [Days, string])} />
            {r && r.total > 0 && (
              <button className="btn sm" type="button" onClick={() => void copy(r)}>{copied ? "Copied" : "Copy the report"}</button>
            )}
          </>
        }
      />
      {rep.error ? <ErrorBox error={rep.error} onRetry={() => void rep.reload()} />
        : !r ? <Loading what="Loading the flow doctor's report" />
          : r.total === 0 ? (
            <div className="panel">
              <EmptyState title={days === "1" ? "No crash today" : `No crash in the last ${days} days`}>
                When a step of a flow fails, keel names what happened here, repairs what it can by itself (a busy
                provider, a conversation too long, a stale git lock), and otherwise waits for you with what to do.
              </EmptyState>
              {cleared && <p className="hint">{cleared}</p>}
            </div>
          ) : (
            <Report r={r} onClear={() => void clear()} cleared={cleared} />
          )}
    </>
  );
}

function Report({ r, onClear, cleared }: { r: DoctorReport; onClear: () => void; cleared: string | null }) {
  return (
    <div className="fd-page">
      <section className="ss-cardp" aria-label="In numbers">
        <div className="ss-kpis">
          <div className="ss-kpi"><span className="ss-meta">Crashes</span><b>{r.total}</b></div>
          <div className="ss-kpi"><span className="ss-meta">Flows hit</span><b>{r.flows}</b></div>
          <div className="ss-kpi"><span className="ss-meta">Repaired by keel</span><b>{r.repaired}</b></div>
          <div className="ss-kpi"><span className="ss-meta">After your Retry</span><b>{r.retried}</b></div>
          <div className="ss-kpi"><span className="ss-meta">Wait for you</span><b>{r.waiting}</b></div>
          <div className="ss-kpi"><span className="ss-meta">Stopped or failed</span><b>{r.stopped + r.failed}</b></div>
        </div>
      </section>
      <Panel title="What to improve">
        <ol className="fd-advice">
          {r.kinds.map((k) => <li key={k.kind}><b>{k.title}</b> ({k.count}×): {k.advice}</li>)}
        </ol>
      </Panel>
      {r.kinds.map((k) => <KindPanel key={k.kind} k={k} />)}
      <Panel title="Recent crashes" extra={<button className="btn sm ghost" type="button" onClick={onClear}>Clear the log</button>}>
        {cleared && <p className="hint">{cleared}</p>}
        <div className="ss-tablewrap">
          <table className="ss-table" aria-label="Recent crashes">
            <thead><tr><th>When</th><th>Flow and step</th><th>What happened</th><th>How it ended</th></tr></thead>
            <tbody>{r.recent.map((c, i) => <CrashRow key={`${c.thread}-${c.step}-${c.at}-${i}`} c={c} />)}</tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function KindPanel({ k }: { k: CrashKind }) {
  const [open, setOpen] = useState(false);
  const err = (k.example.error ?? "").trim();
  return (
    <Panel title={`${k.title} · ${k.count}×`} extra={<code className="fd-kind">{k.kind}</code>}>
      <p>{k.fix}</p>
      <ul className="fd-facts">
        <li>Repaired by keel: {k.repaired} · after your Retry: {k.retried} · waiting: {k.waiting} · stopped: {k.stopped} · failed: {k.failed}</li>
        {k.last_at && <li>Last: {agoText(k.last_at)}</li>}
        {k.steps.length > 0 && <li>Steps: {k.steps.join(", ")}</li>}
        {k.providers.length > 0 && <li>Providers: {k.providers.join(", ")}</li>}
        {k.where.length > 0 && <li>In keel's code: {k.where.map((w) => <code key={w}>{w} </code>)}</li>}
      </ul>
      {err && (
        <>
          <button className="btn sm ghost" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "Hide the example" : "Show an example"}
          </button>
          {open && <pre className="fd-error">{err}</pre>}
        </>
      )}
    </Panel>
  );
}

function CrashRow({ c }: { c: Crash }) {
  const [tone, words] = END[c.end ?? "failed"] ?? END.failed;
  return (
    <tr>
      <td>{c.at ? agoText(c.at) : ""}</td>
      <td>{[c.project, c.workflow, c.step].filter(Boolean).join(" › ")}</td>
      <td title={c.error}>{c.title ?? c.kind}</td>
      <td><Pill tone={tone}>{words}</Pill>{c.tries ? <span className="ss-meta"> after {c.tries} repair{c.tries === 1 ? "" : "s"}</span> : null}</td>
    </tr>
  );
}
