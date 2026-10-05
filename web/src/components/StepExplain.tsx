// "What this step does": the engine's explanation of one workflow step (POST /api/projects/{pid}/workflows/explain-step)
// in a drawer — Task (the agent's real prompt, or each code action in words, or a gate's answers), Rules (what the phase
// lets an agent touch), Next (where it goes, by step name) and Last run (with a thread: what it really did).

import { useEffect, useState, type ReactNode } from "react";
import { api, errorParts, type ExplainRequest, type StepExplanation, type StepRoute } from "../api";
import { kfmt } from "../format";
import { Drawer, ErrorBox, Loading, Pill } from "./ui";
import { KIND } from "./workflow";

const MAY: Record<string, [string, string]> = {
  edit: ["✓", "ok"], "new-only": ["new only", "warn"], "delete-only": ["delete lines only", "warn"], "read-only": ["✗", "bad"],
  "no-access": ["✗ never", "bad"],
};

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="xsec" aria-label={title}>
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function Routes({ routes }: { routes: StepRoute[] }) {
  return (
    <ul className="xlist">
      {routes.map((r, i) => (
        <li key={i}><b>{r.label}</b> → {r.text || r.to_name || "—"}</li>
      ))}
    </ul>
  );
}

function Task({ x }: { x: StepExplanation }) {
  if (x.agent) {
    const a = x.agent;
    const now = a.model.now;
    return (
      <Section title="Task">
        <div className="kv">
          <span>Agent</span><b>{a.id}{a.custom ? " (custom)" : ""}</b>
          <span>Model</span>
          <b>{now ? `${now.provider} ${now.model ?? ""}` : a.model.step === "default" ? `the agent's model${a.model.agent_file ? ` (file: ${a.model.agent_file}${a.model.effort ? `, ${a.model.effort}` : ""})` : ""}` : a.model.step}</b>
          {a.max_turns ? <><span>Turns at most</span><b>{a.max_turns}</b></> : null}
          {a.knowledge ? <><span>Knowledge</span><b>{a.knowledge.sections.join(", ") || "none"}{a.knowledge.code_graph ? " · code graph" : ""}{a.knowledge.memory ? " · memory" : ""}</b></> : null}
        </div>
        {a.about && <p className="sub">{a.about}</p>}
        {a.instructions && <><span className="lab">What this step asks</span><p className="xpre-line">{a.instructions}</p></>}
        {a.markers.length > 0 && (
          <ul className="xlist">{a.markers.map((m) => <li key={m.name}><span className="mono">{m.name}</span>: {m.text}</li>)}</ul>
        )}
        {a.collect && <p className="sub">{a.collect.text}</p>}
        <details className="xprompt">
          <summary>The prompt keel sends{a.placeholders ? " (with «placeholders»: no flow running)" : ""}</summary>
          <pre className="head" data-testid="explain-prompt">{a.prompt}</pre>
          {a.prompt_notes?.map((n, i) => <p key={i} className="hint">{n}</p>)}
        </details>
        {a.system && (
          <details className="xprompt">
            <summary>The agent's role (system prompt)</summary>
            <pre className="head">{a.system}</pre>
          </details>
        )}
        <p className="hint">How the model is picked: {a.model.rule}</p>
      </Section>
    );
  }
  if (x.code) {
    return (
      <Section title="Task">
        {x.code.chain && <p className="sub">Plain code, no LLM, no tokens: <span className="mono">{x.code.chain}</span></p>}
        {x.code.text && <p className="sub">{x.code.text}</p>}
        {x.code.actions.map((d) => (
          <div key={d.name} className="xaction">
            <b className="mono">{d.name}</b> — {d.summary}
            {d.after && <span className="sub"> ({d.after})</span>}
            {d.steps.length > 0 && <ol className="xlist">{d.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>}
            {d.for_this_step && <p className="hint">{d.for_this_step}</p>}
          </div>
        ))}
        {x.code.soft && <p className="hint">{x.code.soft}</p>}
        {x.code.rounds && <p className="hint">{x.code.rounds}</p>}
      </Section>
    );
  }
  if (x.gate) {
    return (
      <Section title="Task">
        <p className="sub">Waits for you. {x.gate.costs}</p>
        <Routes routes={x.gate.answers} />
        {x.gate.notes.map((n, i) => <p key={i} className="hint">{n}</p>)}
      </Section>
    );
  }
  if (x.branch) {
    return (
      <Section title="Task">
        <p>Asks: <b>{x.branch.condition}</b></p>
        {x.branch.rounds && <p className="hint">{x.branch.rounds}</p>}
      </Section>
    );
  }
  return null;
}

function Rules({ x }: { x: StepExplanation }) {
  const r = x.rules;
  return (
    <Section title="Rules">
      <p className="sub">Phase <span className="mono">{x.phase}</span>{x.phase_inherited ? " (from the step before)" : ""}: {x.phase_meaning}</p>
      <div className="table-wrap"><table className="xrules">
        <thead><tr><th>Files</th><th>An agent may</th></tr></thead>
        <tbody>{r.buckets.map((b) => (
          <tr key={b.bucket} data-bucket={b.bucket} data-may={b.may}>
            <td><span className="mono">{b.bucket}</span> <span className="sub">{b.what}</span></td>
            <td><Pill tone={MAY[b.may]?.[1] as "ok" | "warn" | "bad"} title={b.note ?? b.label}>{MAY[b.may]?.[0] ?? b.may}</Pill> <span className="sub">{b.label}{b.note ? ` — ${b.note}` : ""}</span></td>
          </tr>
        ))}</tbody>
      </table></div>
      {r.lane_note && <p className="hint">{r.lane_note}</p>}
      {r.shell_refused.length > 0 && <>
        <span className="lab">Shell commands keel refuses</span>
        <ul className="xlist">{r.shell_refused.map((s, i) => <li key={i}>{s}</li>)}</ul>
      </>}
      {r.commit && <p className="sub">A commit in this phase is a <b>{r.commit.type}</b> commit ({r.commit.about}) by {r.commit.author}: <span className="mono">{r.commit.message}</span>.</p>}
    </Section>
  );
}

function LastRun({ x }: { x: StepExplanation }) {
  const l = x.last_runs;
  if (!l) return null;
  return (
    <Section title="Last run">
      {l.now && <p><Pill tone="warn">{l.now}</Pill></p>}
      {!l.runs.length && !l.now && <p className="sub">This step has not run in this flow yet.</p>}
      {l.runs.map((r) => (
        <div key={r.checkpoint} className="xrun" data-testid="explain-run">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span>{r.ac ? <b>{r.ac} · </b> : null}{r.item ? <b>{r.item} · </b> : null}<Pill tone={r.ok ? "ok" : "bad"}>{r.ok ? "ok" : "failed"}</Pill></span>
            <span className="sub">{new Date(r.at).toLocaleString()}</span>
          </div>
          {r.note && <p className="sub">{r.note}</p>}
          {r.commit && <p>Commit <span className="mono">{r.commit.sha.slice(0, 7)}</span> {r.commit.subject}</p>}
          {r.tokens && <p className="sub">{kfmt(r.tokens.in)} in · {kfmt(r.tokens.out)} out</p>}
          {r.decided && <p className="sub">{r.decided.join(" · ")}</p>}
          {r.went_to && <p className="sub">Then: {r.went_to}</p>}
          {(r.answer || r.output) && <pre className="head">{r.answer || r.output}</pre>}
        </div>
      ))}
      {l.count > l.runs.length && <p className="hint">{l.count} runs in all; the newest {l.runs.length} are shown.</p>}
    </Section>
  );
}

/** The explanation itself (also used inside the builder's step panel). */
export function StepExplainBody({ x }: { x: StepExplanation }) {
  return (
    <div className="grid xbody" style={{ gap: 16 }}>
      <p className="sub" style={{ margin: 0 }}>
        {KIND[x.kind] ?? x.kind}{x.lock ? " · keel rule" : ""}{x.loop?.text ? ` · ${x.loop.text}` : ""}
      </p>
      {x.loop?.fan_out && <p className="sub" style={{ margin: 0 }}>{x.loop.fan_out.text}</p>}
      {x.included_from && <p className="hint" style={{ margin: 0 }}>{x.included_from.text}</p>}
      {x.runs_only_when && <p className="hint" style={{ margin: 0 }}>Runs only when {x.runs_only_when}.</p>}
      {x.skippable && <p className="hint" style={{ margin: 0 }}>Skippable ({x.skippable.band}): {x.skippable.text}.</p>}
      <Task x={x} />
      <Rules x={x} />
      <Section title="Next"><Routes routes={x.next} /></Section>
      <LastRun x={x} />
    </div>
  );
}

export function useStepExplain(pid: string, req: ExplainRequest | null) {
  const [x, setX] = useState<StepExplanation | null>(null);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);
  const key = req ? JSON.stringify(req) : "";
  useEffect(() => {
    if (!req) return;
    let gone = false;
    setX(null);
    setError(null);
    api.explainStep(pid, req).then((v) => { if (!gone) setX(v); }, (e) => { if (!gone) setError(errorParts(e)); });
    return () => { gone = true; };
  }, [pid, key]);   // key = the request's content: a new object for the same step does not load again
  return { x, error };
}

export function StepExplainDrawer({ pid, req, name, onClose }: { pid: string; req: ExplainRequest; name?: string; onClose: () => void }) {
  const { x, error } = useStepExplain(pid, req);
  return (
    <Drawer title={`What ${x?.name ?? name ?? req.step_id} does`} onClose={onClose}>
      {error ? <ErrorBox error={error} /> : !x ? <Loading what="Reading the step" /> : <StepExplainBody x={x} />}
    </Drawer>
  );
}
