// Inbox (Run): everything that waits for a person, in every project — gates, the explorer's questions, failing checks,
// budget pauses, new dependencies. Each item can be answered here (the same resume as the Flow page) or opened on its
// Flow page. The list follows the app's event stream (gate events send project.changed to every tab).

import { useMemo, useState } from "react";
import { errorParts } from "../api";
import { answersOf, ClarifyForm, type ClarifyAnswers } from "../components/ClarifyForm";
import { RunModeNote } from "../components/RunMode";
import { ErrorBox, Loading, PageHead, Pill, Since } from "../components/ui";
import { inboxApi, type InboxAnswer, type InboxItem } from "../inboxApi";
import { go, useApp, useLoad } from "../state";
import "../components/inbox.css";

const KIND_LABEL: Record<string, string> = {
  gate: "gate", clarify: "questions", fix: "needs a fix", budget: "budget", usage: "plan window", dependency: "new dependency",
};
const kindLabel = (k: string) => KIND_LABEL[k] ?? k;

type Buttons = { approve: string; reject: string; needWhy: boolean; whyLabel: string };

/** The button words for one item (the engine's labels win). */
function buttonsFor(it: InboxItem): Buttons {
  const l = it.labels ?? {};
  if (it.kind === "dependency") return { approve: l.approve ?? "Allow", reject: l.reject ?? "Refuse", needWhy: false, whyLabel: "Why (optional)" };
  if (it.kind === "budget") return { approve: "Continue over the cap", reject: "Stop here", needWhy: false, whyLabel: "Note (optional)" };
  if (it.kind === "fix") return { approve: l.approve ?? "Try again", reject: l.reject ?? "Stop the flow", needWhy: false, whyLabel: "Note (optional)" };
  if (it.kind === "clarify") return { approve: l.approve ?? "Send my answers", reject: "Send back", needWhy: false, whyLabel: "Anything else the explorer should know (optional)" };
  return { approve: l.approve ?? `Approve${it.ac ? " " + it.ac : ""}`, reject: l.reject ?? "Send back", needWhy: true, whyLabel: "Why (needed to send back)" };
}

function InboxCard({ it, onDone }: { it: InboxItem; onDone: (msg: string) => Promise<void> }) {
  const { setProjectId } = useApp();
  const b = buttonsFor(it);
  const [why, setWhy] = useState("");
  const [askWhy, setAskWhy] = useState(false);
  const [picked, setPicked] = useState<ClarifyAnswers>({});
  const [typed, setTyped] = useState<ClarifyAnswers>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const hid = `inbox-${it.thread_id}`;
  const whyId = `${hid}-why`;

  const act = async (decision: "approve" | "reject", payload?: Record<string, unknown>, msg?: string) => {
    if (decision === "reject" && b.needWhy && !why.trim()) {
      setAskWhy(true);
      window.setTimeout(() => document.getElementById(whyId)?.focus(), 0);
      return;
    }
    setBusy(true);
    setErr(null);
    const body: InboxAnswer = { decision, id: it.id ?? undefined, ...(why.trim() ? { why: why.trim() } : {}) };
    if (it.kind === "clarify" && it.questions?.length) body.payload = { answers: answersOf(it.questions, picked, typed) };
    else if (payload) body.payload = payload;
    try {
      await inboxApi.act(it.thread_id, body);
      await onDone(msg ?? (decision === "approve" ? `${it.project_name}: ${b.approve}. The flow goes on.` : `${it.project_name}: ${b.reject}.`));
    } catch (e) {
      setErr(errorParts(e));
      setBusy(false);
    }
  };

  const openFlow = () => {
    setProjectId(it.project_id);
    go("flow");
  };

  const choices = it.choices ?? [];
  return (
    <article className={`inbox-item k-${it.kind}`} aria-labelledby={hid} data-testid="inbox-item">
      <div className="inbox-meta">
        <Pill tone={it.kind === "fix" || it.kind === "budget" || it.kind === "usage" ? "bad" : "warn"}>{kindLabel(it.kind)}</Pill>
        <span className="sub"><b>{it.project_name}</b> · {it.flow}{it.workflow_id ? <> · <span className="mono">{it.workflow_id}</span></> : null}</span>
        {it.since && <span className="hint inbox-since">waiting <Since from={it.since} /></span>}
      </div>
      <h2 id={hid} className="inbox-title">{it.title}</h2>
      <RunModeNote mode={it.run_mode} kind={it.kind} title={it.title} auto={{ count: it.auto_approved ?? 0, last: it.last_auto }} />
      {it.kind === "clarify" && it.questions?.length ? (
        <ClarifyForm questions={it.questions} picked={picked} typed={typed}
          onPick={(id, label) => setPicked((p) => ({ ...p, [id]: label }))} onType={(id, text) => setTyped((p) => ({ ...p, [id]: text }))} />
      ) : it.detail ? (
        <pre className="inbox-detail">{it.detail}</pre>
      ) : null}
      {it.more && <span className="hint">The rest is on the Flow page.</span>}
      {(askWhy || !b.needWhy) && (
        <div className="field">
          <label htmlFor={whyId}>{b.whyLabel}</label>
          <textarea id={whyId} rows={2} value={why} onChange={(e) => setWhy(e.target.value)} />
        </div>
      )}
      <div className="row inbox-actions">
        {it.kind === "usage" && choices.length ? (
          <>
            {choices.includes("continue") && <button className="btn warn sm" type="button" disabled={busy} onClick={() => act("approve", { choice: "continue" })}>Continue anyway</button>}
            {choices.includes("wait") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("approve", { choice: "wait" })}>Wait for the reset</button>}
            {choices.includes("cheaper") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("approve", { choice: "cheaper" })}>Use the cheaper model</button>}
            {choices.includes("stop") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("reject")}>Stop here</button>}
          </>
        ) : it.kind === "gate" && choices.length ? (
          <>
            {choices.map((c, n) => (
              <button key={c} className={`btn sm${n === 0 ? " warn" : ""}`} type="button" disabled={busy}
                onClick={() => act("approve", { choice: c }, `${it.project_name}: ${c}.`)}>{c}</button>
            ))}
            {it.options.includes("reject") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("reject")}>{b.reject}</button>}
          </>
        ) : (
          <>
            {it.options.includes("approve") && <button className="btn warn sm" type="button" disabled={busy} onClick={() => act("approve")}>{b.approve}</button>}
            {it.options.includes("reject") && <button className="btn sm" type="button" disabled={busy} onClick={() => act("reject")}>{b.reject}</button>}
          </>
        )}
        <button className="btn sm ghost inbox-open" type="button" onClick={openFlow} aria-label={`Open flow ${it.flow} in ${it.project_name}`}>Open flow ▸</button>
      </div>
      {err && <ErrorBox error={err} />}
    </article>
  );
}

export function InboxPage() {
  const { toast, reloadProjects } = useApp();
  const r = useLoad("inbox", () => inboxApi.list());
  const [project, setProject] = useState("");
  const [kind, setKind] = useState("");
  const items = useMemo(
    () => (r.data?.items ?? []).filter((it) => (!project || it.project_id === project) && (!kind || it.kind === kind)),
    [r.data, project, kind],
  );
  const total = r.data?.count ?? 0;

  const done = async (msg: string) => {
    toast(msg);
    await Promise.all([r.reload(), reloadProjects()]);
  };

  return (
    <>
      <PageHead title="Inbox" sub="Everything that waits for you, in every project. Answer it here or open its flow." />
      <div className="row inbox-filters" role="group" aria-label="Filters">
        <label className="row" htmlFor="inbox-project"><span className="sub">In project</span>
          <select id="inbox-project" value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">All projects</option>
            {(r.data?.projects ?? []).map((p) => <option key={p.id} value={p.id}>{p.name} ({p.count})</option>)}
          </select>
        </label>
        <label className="row" htmlFor="inbox-kind"><span className="sub">Kind</span>
          <select id="inbox-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">All kinds</option>
            {(r.data?.kinds ?? []).map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}
          </select>
        </label>
        {r.data && <span className="hint" aria-live="polite">{items.length === total ? `${total} waiting` : `${items.length} of ${total} waiting`}</span>}
      </div>
      {r.error && !r.data ? <ErrorBox error={r.error} onRetry={() => void r.reload()} />
        : !r.data ? <Loading what="Loading the inbox" />
          : !items.length ? (
            <div className="empty inbox-empty">
              <b>Nothing is waiting for you</b>
              <span className="sub">{total ? "Nothing matches these filters." : "When a flow stops at a gate or needs a decision, it shows up here."}</span>
            </div>
          ) : (
            <div className="inbox-list">
              {items.map((it) => <InboxCard key={`${it.thread_id}:${it.id ?? it.step}`} it={it} onDone={done} />)}
            </div>
          )}
    </>
  );
}
