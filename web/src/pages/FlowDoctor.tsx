// #101 part 3 Watch › Flow doctor: what crashed in keel's flows (every project), what keel repaired by itself, what
// waited for the person, and what to improve, from the engine's crash log (GET /api/flow-doctor/report). The Markdown
// copy is the same report keel_flow_doctor gives an MCP client: paste it into an issue.

import { useEffect, useState } from "react";
import { api, type Crash, type CrashKind, type DoctorReport } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBox, Loading, PageHead, Panel, Pill, Tabs, type PillTone } from "../components/ui";
import { agoText } from "../format";
import { isTyping, modalOpen } from "../keys";
import { go, useLoad } from "../state";

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

/** What keel did to repair a step, in words (the crash log's `action`). */
const ACTION: Record<string, string> = {
  wait: "waited, then ran it again",
  fresh: "ran it again in a new session",
  "git-lock": "removed a stale git lock",
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
  // C copies the report (the key on its button)
  useEffect(() => {
    if (!r || r.total === 0) return;
    const on = (e: KeyboardEvent) => {
      if (e.key !== "c" || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || e.defaultPrevented || isTyping(e.target) || modalOpen()) return;
      if (window.getSelection?.()?.toString()) return; // a selection is being copied, not the report
      e.preventDefault();
      void copy(r);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [r]);
  return (
    <div className="rdw rdw-fd">
      <PageHead
        title="Flow doctor"
        sub="What crashed in keel's flows, what keel repaired by itself, what waited for you, and what to improve. Every project."
        actions={
          <>
            <span className="rdw-seg"><Tabs value={days} onChange={(d) => setDays(d)} label="How far back"
              options={DAYS.map((d) => [d, d === "1" ? "Today" : `${d} days`] as [Days, string])} /></span>
            {r && r.total > 0 && (
              <button className="btn" type="button" aria-keyshortcuts="C" onClick={() => void copy(r)}>
                {copied ? "Copied" : "Copy the report"}{!copied && <kbd aria-hidden="true">C</kbd>}
              </button>
            )}
          </>
        }
      />
      {rep.error ? <ErrorBox error={rep.error} onRetry={() => void rep.reload()} />
        : !r ? <Loading what="Loading the flow doctor's report" />
          : r.total === 0 ? (
            <div className="panel rise r1">
              <EmptyState title={days === "1" ? "No crash today" : `No crash in the last ${days} days`}>
                When a step of a flow fails, keel names what happened here, repairs what it can by itself (a busy
                provider, a conversation too long, a stale git lock), and otherwise waits for you with what to do.
              </EmptyState>
              {cleared && <p className="hint">{cleared}</p>}
            </div>
          ) : (
            <Report r={r} onClear={() => void clear()} cleared={cleared} />
          )}
    </div>
  );
}

const where = (c: Crash) => [c.project, c.workflow, c.step].filter(Boolean).join(" › ");

function Report({ r, onClear, cleared }: { r: DoctorReport; onClear: () => void; cleared: string | null }) {
  const waits = r.recent.filter((c) => c.end === "waiting");
  const fixed = r.recent.filter((c) => c.end === "repaired" || c.end === "retried" || c.end === "repairing");
  const fixOf = (kind?: string) => r.kinds.find((k) => k.kind === kind)?.fix;
  const [all, setAll] = useState(false);
  const SHOWN = 8;
  const rows = all ? r.recent : r.recent.slice(0, SHOWN);
  return (
    <div className="fd-page">
      <section className="ss-cardp rdw-kpis rise r1" aria-label="In numbers">
        <div className="ss-kpis">
          <div className="ss-kpi"><span className="ss-meta">Crashes</span><b>{r.total}</b></div>
          <div className="ss-kpi"><span className="ss-meta">Flows hit</span><b>{r.flows}</b></div>
          <div className={`ss-kpi${r.repaired ? " t-ok" : ""}`}><span className="ss-meta">Repaired by keel</span><b>{r.repaired}</b></div>
          <div className="ss-kpi"><span className="ss-meta">After your Retry</span><b>{r.retried}</b></div>
          <div className={`ss-kpi${r.waiting ? " t-warn" : ""}`}><span className="ss-meta">Wait for you</span><b>{r.waiting}</b></div>
          <div className={`ss-kpi${r.stopped + r.failed ? " t-bad" : ""}`}><span className="ss-meta">Stopped or failed</span><b>{r.stopped + r.failed}</b></div>
        </div>
      </section>
      <div className={`rdw-fd-cols${waits.length || fixed.length ? "" : " one"}`}>
        {(waits.length > 0 || fixed.length > 0) && (
          <div className="rdw-fd-left">
            {waits.length > 0 && (
              <section className="rdw-fd-waits rise r2" aria-labelledby="fd-waits">
                <header className="rdw-sh">
                  <h2 id="fd-waits">Waits for you</h2><span className="rdw-n warn">{waits.length}</span>
                  <span className="sub">keel could not repair these by itself</span>
                </header>
                {waits.map((c, i) => (
                  <article key={`${c.thread}-${c.step}-${c.at}-${i}`} className="rdw-wait lift">
                    <p className="rdw-wait-h"><span className="rdw-dia" aria-hidden="true" /><b>{c.title ?? c.kind}</b>
                      <span className="sub">{where(c)}{c.at ? ` · ${agoText(c.at)}` : ""}</span></p>
                    {c.error && <pre className="rdw-err">{c.error.trim().split("\n")[0]}</pre>}
                    {fixOf(c.kind) && <p className="rdw-todo"><b>What to do:</b> {fixOf(c.kind)}</p>}
                    {c.thread && (
                      <div className="rdw-wait-a">
                        <button className="btn sm" type="button" onClick={() => go("flow", c.thread)}>Open the flow ›</button>
                      </div>
                    )}
                  </article>
                ))}
              </section>
            )}
            {fixed.length > 0 && (
              <section className="panel rdw-fixed rise r3" aria-labelledby="fd-fixed">
                <header className="panel-head"><h2 id="fd-fixed">Repaired and retried</h2>
                  <span className="sub">keel repairs a step at most twice, then asks you</span></header>
                <ul className="rdw-fixlist">
                  {fixed.map((c, i) => {
                    const [tone, words] = END[c.end ?? "failed"] ?? END.failed;
                    return (
                      <li key={`${c.thread}-${c.step}-${c.at}-${i}`}>
                        <span className={`rdw-fx ${c.end === "repairing" ? "run" : "ok"}`} aria-hidden="true">{c.end === "repairing" ? "" : "✓"}</span>
                        <div className="rdw-fx-t">
                          <b>{c.title ?? c.kind}</b>
                          <span className="sub">{where(c)}{c.action && ACTION[c.action] ? ` · ${ACTION[c.action]}` : ""}</span>
                        </div>
                        <Pill tone={tone}>{words}</Pill>
                        <span className="sub rdw-ago">{c.at ? agoText(c.at) : ""}</span>
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}
          </div>
        )}
        <section className="panel rdw-improve rise r2" aria-labelledby="fd-improve">
          <header className="panel-head"><h2 id="fd-improve">What to improve</h2><span className="sub">by kind, most first</span></header>
          <ol className="fd-advice rdw-kinds">
            {r.kinds.map((k, i) => <KindRow key={k.kind} k={k} first={i === 0} />)}
          </ol>
        </section>
      </div>
      <Panel title="Recent crashes" className="rdw-recent rise r4" body={false}
        extra={<div className="row"><span className="sub">one line per flow, step and kind</span>
          <button className="btn sm ghost" type="button" onClick={onClear}>Clear the log</button></div>}>
        {cleared && <p className="hint" style={{ padding: "8px 16px 0" }}>{cleared}</p>}
        <div className="ss-tablewrap">
          <table className="ss-table" aria-label="Recent crashes">
            <thead><tr><th>When</th><th>Flow and step</th><th>What happened</th><th>How it ended</th></tr></thead>
            <tbody>{rows.map((c, i) => <CrashRow key={`${c.thread}-${c.step}-${c.at}-${i}`} c={c} />)}</tbody>
          </table>
        </div>
        {r.recent.length > SHOWN && (
          <div className="rdw-more">
            <button className="btn sm ghost" type="button" aria-expanded={all} onClick={() => setAll(!all)}>
              {all ? "Show fewer" : `Show all ${r.recent.length}`}
            </button>
          </div>
        )}
      </Panel>
    </div>
  );
}

/** One kind of crash: how often, the advice, and (unfolded) what keel knows about it. The first one opens unfolded. */
function KindRow({ k, first }: { k: CrashKind; first: boolean }) {
  const [unfold, setUnfold] = useState(first);
  const [open, setOpen] = useState(false);
  const err = (k.example.error ?? "").trim();
  const id = `fd-kind-${k.kind}`;
  return (
    <li className={`rdw-kind${unfold ? " open" : ""}`}>
      <button type="button" className="rdw-kind-h" aria-expanded={unfold} aria-controls={id} onClick={() => setUnfold(!unfold)}>
        <span className="rdw-x mono">{k.count}×</span>
        <span className="rdw-kind-t"><b>{k.title}</b><span className="sub">{k.advice}</span></span>
        <svg className="rdw-chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
      </button>
      {unfold && (
        <div className="rdw-kind-b" id={id}>
          <p>{k.fix} <code className="fd-kind">{k.kind}</code></p>
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
        </div>
      )}
    </li>
  );
}

function CrashRow({ c }: { c: Crash }) {
  const [tone, words] = END[c.end ?? "failed"] ?? END.failed;
  return (
    <tr>
      <td className="sub">{c.at ? agoText(c.at) : ""}</td>
      <td>{where(c)}</td>
      <td title={c.error}>{c.title ?? c.kind}</td>
      <td><Pill tone={tone}>{words}</Pill>{c.tries ? <span className="ss-meta"> after {c.tries} repair{c.tries === 1 ? "" : "s"}</span> : null}</td>
    </tr>
  );
}
