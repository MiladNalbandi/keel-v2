// P4 an approval keel or a part asks itself (keel Product's "Send N tickets to Jira", any part's ApprovalHandler kind):
// not a flow, so it is answered with POST /api/approvals/{id}/decide (Approve, Deny, its kind's other decisions), and
// its Open button goes where the part says (payload.open: {page, arg, label}), never to the Flow page.

import { useState } from "react";
import { errorParts } from "../api";
import { inboxApi, type InboxItem } from "../inboxApi";
import { useHasPage } from "../sdk/registry";
import { go, useApp } from "../state";
import { Key } from "./Key";
import { ErrorBox, Pill, Since, Ticks } from "./ui";

type OpenTarget = { page?: string; arg?: string; label?: string };

const LABEL: Record<string, string> = { approve: "Approve", deny: "Deny", reject: "Deny" };
const label = (d: string) => LABEL[d] ?? d.charAt(0).toUpperCase() + d.slice(1);

export function ApprovalCard({ it, cardKey, kindLabel, onDone }: {
  it: InboxItem;
  cardKey: string;
  kindLabel: string;
  onDone: (msg: string) => Promise<void>;
}) {
  const { setProjectId } = useApp();
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const hid = `inbox-approval-${it.id}`;
  const open = ((it.payload ?? {}) as { open?: OpenTarget }).open;
  const canOpen = useHasPage(open?.page ?? "");
  const decisions = it.options.length ? it.options : ["approve", "deny"];

  const answer = async (decision: string) => {
    setBusy(true);
    setErr(null);
    try {
      await inboxApi.decide(it.id ?? "", decision, why.trim());
      await onDone(`${it.title}: ${decision === "approve" ? "approved" : decision === "deny" || decision === "reject" ? "denied" : decision}.`);
    } catch (e) {
      setErr(errorParts(e));
      setBusy(false);
    }
  };

  return (
    <article className={`inbox-item k-${it.kind}`} aria-labelledby={hid} data-testid="inbox-item" data-key={cardKey} tabIndex={-1}>
      <div className="inbox-meta">
        <Pill tone="warn">{kindLabel}</Pill>
        <span className="sub"><b>{it.project_name}</b>{it.flow ? <> · asked by {it.flow}</> : null}</span>
        {it.since && <span className="hint inbox-since">waiting <Since from={it.since} /></span>}
      </div>
      <h2 id={hid} className="inbox-title"><Ticks text={it.title} /></h2>
      {it.detail && <pre className="sub inbox-detail" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{it.detail}</pre>}
      <div className="field">
        <label htmlFor={`${hid}-why`}>Note (optional)</label>
        <input id={`${hid}-why`} type="text" value={why} aria-keyshortcuts="R" onChange={(e) => setWhy(e.target.value)} />
      </div>
      <div className="row inbox-actions">
        {decisions.map((d, n) => (
          <button key={d} className={`btn sm${n === 0 ? " primary" : ""}`} type="button" disabled={busy} aria-keyshortcuts={n === 0 ? "A" : undefined}
            onClick={() => void answer(d)}>{label(d)}{n === 0 && <> <Key k="A" /></>}</button>
        ))}
        {open?.page && canOpen && (
          <button className="btn sm ghost inbox-open" type="button" aria-keyshortcuts="O" onClick={() => { if (it.project_id) setProjectId(it.project_id); go(open.page!, open.arg); }}>
            {open.label ?? "Open"} ▸ <Key k="O" />
          </button>
        )}
      </div>
      {err && <ErrorBox error={err} />}
    </article>
  );
}
