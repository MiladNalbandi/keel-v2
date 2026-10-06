// The workspace Doctor: explains the uncommitted files that stop a flow from starting, proposes a plan
// (commit / stash / add to .gitignore / keep) per group, lets you change it, and runs it with plain git.

import { useEffect, useState } from "react";
import { api, errorParts, type Diagnosis, type DoctorAction, type DoctorApplied, type PlanItem } from "../api";
import { ErrorBox } from "./ui";

const ACTIONS: [DoctorAction, string][] = [
  ["commit", "Commit"],
  ["stash", "Stash (keep it safe, bring back later)"],
  ["exclude", "Hide on this computer only (.git/info/exclude)"],
  ["ignore", "Add to the project's .gitignore"],
  ["keep", "Leave it"],
];
const KIND: Record<string, string> = { tooling: "setup", docs: "docs", code: "code", local: "local", secret: "secret" };

export function WorkspaceDoctor({ pid, onClean }: { pid: string; onClean?: () => void }) {
  const [d, setD] = useState<Diagnosis | null>(null);
  const [plan, setPlan] = useState<PlanItem[]>([]);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [busy, setBusy] = useState<"look" | "apply" | null>("look");
  const [done, setDone] = useState<DoctorApplied | null>(null);

  const look = () => {
    setBusy("look");
    setErr(null);
    setDone(null);
    api.doctorWorkspace(pid).then((x) => {
      setD(x);
      setPlan(x.plan);
    }, (e) => setErr(errorParts(e))).finally(() => setBusy(null));
  };
  useEffect(look, [pid]);

  const change = (id: string, patch: Partial<PlanItem>) => setPlan((p) => p.map((x) => (x.id === id ? { ...x, ...patch } : x)));

  const apply = async () => {
    setBusy("apply");
    setErr(null);
    try {
      const res = await api.applyDoctor(pid, plan.map((x) => ({ action: x.action, files: x.files, message: x.message, patterns: x.patterns, title: x.title })));
      setDone(res);
      if (res.clean) onClean?.();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(null);
    }
  };

  if (busy === "look") return <div className="doctor"><span className="sub loading">The Doctor is looking at the files…</span></div>;
  return (
    <div className="doctor" aria-live="polite">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>Doctor</b>
        {d && <span className="tag">{d.by === "rules" ? "built-in rules" : d.by}{d.tokens_in ? ` · ${Math.round((d.tokens_in + d.tokens_out) / 100) / 10}k tokens` : ""}</span>}
      </div>
      {d && <p style={{ margin: 0 }}>{d.summary}</p>}
      {d?.note && <span className="hint">{d.note}</span>}
      {err && <ErrorBox error={err} onRetry={look} />}

      {done ? (
        <div className={done.clean ? "wbar ok" : "wbar warn"}>
          <div className="grid" style={{ gap: 4 }}>
            {done.results.map((r, i) => <span key={i}>{r.ok ? "✓" : "✕"} {r.detail}</span>)}
            <b>{done.clean ? "The working tree is clean. You can start the flow." : `Still uncommitted: ${done.remaining.join(", ")}`}</b>
          </div>
          {!done.clean && <button className="btn sm" type="button" onClick={look}>Ask again</button>}
        </div>
      ) : (
        <>
          {plan.map((x) => (
            <div key={x.id} className={`dgroup a-${x.action}`}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <b>{x.title}</b>
                <select aria-label={`What to do with ${x.title}`} value={x.action} onChange={(e) => change(x.id, { action: e.target.value as DoctorAction })}>
                  {ACTIONS.filter(([a]) => !(a === "commit" && x.files.some((f) => d?.files.find((df) => df.path === f)?.secret))
                    && !((a === "ignore" || a === "exclude") && x.files.some((f) => d?.files.find((df) => df.path === f)?.tracked)))
                    .map(([a, l]) => <option key={a} value={a}>{l}</option>)}
                </select>
              </div>
              <span className="sub">{x.why}</span>
              <div className="row" style={{ gap: 4 }}>
                {x.files.map((f) => {
                  const df = d?.files.find((y) => y.path === f);
                  return <span key={f} className={`tag ${df?.secret ? "star" : ""}`} title={df ? `${df.status} · ${KIND[df.kind] ?? df.kind}` : ""}>{f}</span>;
                })}
              </div>
              {x.action === "commit" && (
                <input type="text" aria-label={`Commit message for ${x.title}`} value={x.message ?? ""} onChange={(e) => change(x.id, { message: e.target.value })}
                  placeholder="commit message" />
              )}
              {(x.action === "ignore" || x.action === "exclude") && x.patterns && <span className="hint">{x.action === "exclude" ? ".git/info/exclude" : ".gitignore"}: <span className="mono">{x.patterns.join("  ")}</span></span>}
            </div>
          ))}
          {plan.length > 0 && (
            <div className="row">
              <button className="btn primary" type="button" onClick={apply} disabled={busy === "apply" || plan.some((x) => x.action === "commit" && !x.message?.trim())}>
                {busy === "apply" ? "Working…" : "Apply this plan"}
              </button>
              <span className="hint">Plain git: commit, stash, .git/info/exclude or .gitignore. Nothing is deleted.</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}
