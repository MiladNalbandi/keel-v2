// "What this step does": the engine's explanation of one workflow step (POST /api/projects/{pid}/workflows/explain-step)
// in a drawer — Task (the agent's real prompt, or each code action in words, or a gate's answers), Rules (what the phase
// lets an agent touch), Next (where it goes, by step name) and Last run (with a thread: what it really did).
// keel 0.18.66 the Flow page's step panel (pages/flow/StepPanel.tsx) builds its tabs from the same pieces: the step in
// plain words (stepWhat, StepChoices, stepMeta), the rules as three boxes (RuleBoxes) with the full table one click away,
// and the refused shell commands one per line (RefusedList).

import { useEffect, useState, type ReactNode } from "react";
import { api, errorParts, type ExplainRequest, type StepExplanation, type StepRoute, type Workflow } from "../api";
import { kfmt } from "../format";
import { Markdown } from "./Markdown";
import { PreBox } from "./PreBox";
import { listify } from "./prose";
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

export function Task({ x }: { x: StepExplanation }) {
  if (x.agent) {
    const a = x.agent;
    const now = a.model.now;
    return (
      <Section title="Task">
        {/* a definition list: the labels keep their width, a long value wraps under itself (never over its label) */}
        <dl className="xkv" data-testid="explain-facts">
          <dt>Agent</dt><dd>{a.id}{a.custom ? " (custom)" : ""}</dd>
          <dt>Model</dt>
          <dd>{now ? `${now.provider} ${now.model ?? ""}${now.effort ? ` · ${now.effort}` : ""}` : a.model.step === "default" ? `the agent's model${a.model.agent_file ? ` (file: ${a.model.agent_file}${a.model.effort ? `, ${a.model.effort}` : ""})` : ""}` : a.model.step}</dd>
          {a.max_turns ? <><dt>Turns at most</dt><dd>{a.max_turns}</dd></> : null}
          {a.knowledge ? <><dt>Knowledge</dt><dd>{a.knowledge.sections.join(", ") || "none"}{a.knowledge.code_graph ? " · code graph" : ""}{a.knowledge.memory ? " · memory" : ""}</dd></> : null}
        </dl>
        {a.about && <p className="sub">{a.about}</p>}
        {a.instructions && <><span className="lab">What this step asks</span>
          <div className="md xasks" data-testid="explain-asks"><Markdown text={listify(a.instructions)} breaks /></div></>}
        {a.markers.length > 0 && (
          <ul className="xlist">{a.markers.map((m) => <li key={m.name}><span className="mono">{m.name}</span>: {m.text}</li>)}</ul>
        )}
        {a.collect && <p className="sub">{a.collect.text}</p>}
        <details className="xprompt">
          <summary>The prompt keel sends{a.placeholders ? " (with «placeholders»: no flow running)" : ""}</summary>
          <PreBox text={a.prompt} label="The prompt keel sends" testId="explain-prompt" />
          {a.prompt_notes?.map((n, i) => <p key={i} className="hint">{n}</p>)}
        </details>
        {a.system && (
          <details className="xprompt">
            <summary>The agent's role (system prompt)</summary>
            <PreBox text={a.system} label="The agent's role" />
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
        {/* keel 0.18.66 the engine's line already says "Waits for you." (it was there twice) */}
        <p className="sub">{x.gate.costs || "Waits for you."}</p>
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

/** The phase's rules file by file: one row per kind of file and what an agent may do with it. */
export function RulesTable({ x }: { x: StepExplanation }) {
  return (
    <div className="table-wrap"><table className="xrules">
      <thead><tr><th>Files</th><th>An agent may</th></tr></thead>
      <tbody>{x.rules.buckets.map((b) => (
        <tr key={b.bucket} data-bucket={b.bucket} data-may={b.may}>
          <td><span className="mono">{b.bucket}</span> <span className="sub">{b.what}</span></td>
          <td><Pill tone={MAY[b.may]?.[1] as "ok" | "warn" | "bad"} title={b.note ?? b.label}>{MAY[b.may]?.[0] ?? b.may}</Pill> <span className="sub">{b.label}{b.note ? ` — ${b.note}` : ""}</span></td>
        </tr>
      ))}</tbody>
    </table></div>
  );
}

export const PhaseLine = ({ x }: { x: StepExplanation }) => (
  <p className="sub">Phase <span className="mono">{x.phase}</span>{x.phase_inherited ? " (from the step before)" : ""}: {x.phase_meaning}</p>
);

export const CommitLine = ({ x }: { x: StepExplanation }) => x.rules.commit ? (
  <p className="sub">A commit in this phase is a <b>{x.rules.commit.type}</b> commit ({x.rules.commit.about}) by {x.rules.commit.author}: <span className="mono">{x.rules.commit.message}</span>.</p>
) : null;

/** keel 0.18.66 the same rules in three boxes: what an agent may change, may only read, and never touches. A file kind
 *  with a limit says it ("database migrations (create new files only)"). */
export function RuleBoxes({ x }: { x: StepExplanation }) {
  const words = (may: string[]) => x.rules.buckets.filter((b) => may.includes(b.may))
    .map((b) => (b.may === "new-only" || b.may === "delete-only" ? `${b.what} (${b.label})` : b.what));
  const boxes: [string, string, string, string[]][] = [
    ["edit", "ok", "✓ agents may edit", words(["edit", "new-only", "delete-only"])],
    ["read", "bad", "✕ read only", words(["read-only"])],
    ["never", "never", "✕ never", words(["no-access"])],
  ];
  return (
    <div className="sp-rules" data-testid="rule-boxes">
      {boxes.map(([id, tone, title, list]) => (
        <div key={id} className={`sp-rule is-${tone}`} data-box={id}>
          <b>{title}</b>
          <span>{list.length ? list.join(" · ") : "nothing"}</span>
        </div>
      ))}
    </div>
  );
}

/** One refused shell command as the command and why: the engine writes `"git push --force" (it skips checks …)` or
 *  `git commit: the engine's commit step makes every commit` (runtime/explain.py phase_rules). */
export function refusedOf(line: string): { cmd: string; why: string; code: boolean } {
  const quoted = /^"(.+?)"\s*\((.+)\)$/.exec(line);
  if (quoted) return { cmd: quoted[1], why: quoted[2], code: true };
  const colon = line.indexOf(": ");
  if (colon > 0) return { cmd: line.slice(0, colon), why: line.slice(colon + 2), code: /^[\w.-]+( [\w.-]+)*$/.test(line.slice(0, colon)) };
  return { cmd: line, why: "", code: false };
}

/** keel 0.18.66 the shell commands keel refuses in this step, one line each with why ("the same" under its twin). */
export function RefusedList({ x }: { x: StepExplanation }) {
  const rows = x.rules.shell_refused.map(refusedOf);
  return (
    <ul className="sp-cmds" aria-label="Shell commands keel refuses">
      {rows.map((r, i) => (
        <li key={i}>
          {r.code ? <code>{r.cmd}</code> : <span className="sp-cmd-t">{r.cmd}</span>}
          {r.why && <span className="sub">{i > 0 && rows[i - 1].why === r.why ? "the same" : r.why}</span>}
        </li>
      ))}
    </ul>
  );
}

/** keel 0.18.66 what the step does, in one or two plain sentences. */
export function stepWhat(x: StepExplanation): string {
  if (x.agent?.about) return x.agent.about;
  if (x.code) return x.code.text || x.code.actions[0]?.summary || "Plain code: it runs without a model.";
  if (x.branch) return `It checks: ${x.branch.condition}.`;
  if (x.gate) {
    const when = x.gate.notes.find((n) => n.startsWith("Pauses only when"));
    return when ? `It waits for your answer, but only when ${when.slice("Pauses only when ".length).replace(/;.*$/, "")}. Otherwise keel goes on without asking.`
      : "It waits for your answer before the flow goes on.";
  }
  return "";
}

/** keel 0.18.66 the step's few facts on one line: waits for you or not, its tokens, its phase. */
export function stepMeta(x: StepExplanation): string[] {
  const used = (x.last_runs?.runs ?? []).reduce((a, r) => a + (r.tokens?.step ?? 0), 0);
  const tokens = x.agent ? (used ? `${kfmt(used)} tokens so far` : "uses tokens") : "no tokens";
  const kind = x.gate ? "Waits for you" : x.code ? "Plain code" : x.branch ? "A check" : x.agent ? `Agent ${x.agent.id}` : KIND[x.kind] ?? x.kind;
  return [kind, tokens, `phase ${x.phase}`];
}

/** keel 0.18.66 the step's choices in one small box: a gate's answers (and what a plain approve picks), a branch's
 *  question, or where any other step goes next. */
export function StepChoices({ x }: { x: StepExplanation }) {
  const plain = x.gate?.notes.find((n) => n.startsWith("A plain approve picks "));
  const rows = x.gate ? x.gate.answers : x.branch ? x.branch.routes : x.next;
  if (!rows.length && !plain) return null;
  return (
    <div className="sp-box" data-testid="step-choices">
      <span className="sp-box-t">{x.gate ? "Your choices here" : x.branch ? `It asks: ${x.branch.condition}` : "Where it goes next"}</span>
      <dl className="sp-choices">
        {rows.map((r, i) => <div key={i}><dt>{r.label}</dt><dd>{r.text || r.to_name || "—"}</dd></div>)}
      </dl>
      {plain && <span className="sp-box-t">A plain approve picks <b>{plain.slice("A plain approve picks ".length).replace(/[.,].*$/, "")}</b>.</span>}
    </div>
  );
}

function Rules({ x }: { x: StepExplanation }) {
  const r = x.rules;
  return (
    <Section title="Rules">
      <PhaseLine x={x} />
      <RulesTable x={x} />
      {r.lane_note && <p className="hint">{r.lane_note}</p>}
      {r.shell_refused.length > 0 && <>
        <span className="lab">Shell commands keel refuses</span>
        <ul className="xlist">{r.shell_refused.map((s, i) => <li key={i}>{s}</li>)}</ul>
      </>}
      <CommitLine x={x} />
    </Section>
  );
}

export function LastRun({ x }: { x: StepExplanation }) {
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
          {(r.answer || r.output) && <PreBox text={r.answer || r.output || ""} label={r.answer ? "What the agent answered" : "What the step printed"} />}
        </div>
      ))}
      {l.count > l.runs.length && <p className="hint">{l.count} runs in all; the newest {l.runs.length} are shown.</p>}
    </Section>
  );
}

/** The explanation itself (also used inside the builder's step panel). */
export function StepInfoBody({ x }: { x: StepExplanation }) {
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

export function useStepInfo(pid: string, req: ExplainRequest | null) {
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

/** The request for one step: a saved workflow (its id), a draft (the object), and/or a thread (its state and last runs). */
export function infoRequest(stepId: string, workflow?: Workflow | string | null, threadId?: string | null): ExplainRequest {
  return {
    step_id: stepId,
    ...(typeof workflow === "string" ? { workflow_id: workflow } : workflow ? { workflow } : {}),
    ...(threadId ? { thread_id: threadId } : {}),
  };
}

/** "What this step does" in a drawer. `workflow`: a saved workflow's id or a draft — pass the one the page draws, so the
 * drawer and the graph never disagree (a thread started on an older template has fewer steps); `threadId` adds the
 * thread's state and what the step did there. With `threadId` alone the engine uses the thread's own workflow. */
export function StepInfoDrawer({ pid, workflow, stepId, threadId, onClose }: {
  pid: string; workflow?: Workflow | string | null; stepId: string; threadId?: string | null; onClose: () => void;
}) {
  const { x, error } = useStepInfo(pid, infoRequest(stepId, workflow, threadId));
  const name = typeof workflow === "object" && workflow ? workflow.steps.find((s) => s.id === stepId)?.name : undefined;
  return (
    <Drawer title={`What ${x?.name ?? name ?? stepId} does`} onClose={onClose} className="step-drawer">
      {error ? <ErrorBox error={error} /> : !x ? <Loading what="Reading the step" /> : <StepInfoBody x={x} />}
    </Drawer>
  );
}
