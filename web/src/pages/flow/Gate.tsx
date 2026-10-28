import { copyText } from "../../clipboard";
import { useRef, useState } from "react";
import { api, errorParts, type Blocker, type Step, type ThreadState, type Workflow } from "../../api";
import { CodeBlock, FoldedText } from "../../components/Code";
import { ClarifyForm, type ClarifyAnswers } from "../../components/ClarifyForm";
import { questionsPayload, reasonsMissing } from "../../questions";
import { ChoiceForm } from "../../components/ChoiceForm";
import { Markdown, type FoldLabels } from "../../components/Markdown";
import { autoLines, RunModeNote } from "../../components/RunMode";
import { ErrorBox, Panel, Pill, Ticks } from "../../components/ui";
import { kfmt, usd } from "../../format";
import { go, useApp } from "../../state";
import { useHasPage } from "../../sdk/registry";
import { askAssistant } from "../../sdk/assistant";
import { fixRequest } from "../../askFix";

type GateLabels = {
  approve: string; reject: string; needWhy: boolean; whyLabel?: string; explain?: string;
  approved: string; rejected: string; special?: boolean;
};

/** Button labels per waiting kind, so the choice is clear without reading the code. */
export function gateLabels(w: NonNullable<ThreadState["waiting"]>, acId?: string, backName?: string): GateLabels {
  const t = w.title.toLowerCase();
  if (w.kind === "usage") {
    return {
      approve: "Continue anyway", reject: "Stop here", needWhy: false,
      explain: "A plan window of this provider is nearly used up. Continue uses it anyway (the provider may refuse when it is full), wait sleeps until it resets, the cheaper model is the one in Settings.",
      approved: "The flow goes on.", rejected: "Stopped.", special: true,
    };
  }
  if (w.kind === "budget") {
    return { approve: "Continue over the cap", reject: "Stop here", needWhy: false, approved: "The flow continues over its cap.", rejected: "Stopped.", special: true };
  }
  if (w.kind === "fix" && /dependenc/.test(t)) {
    return {
      approve: "Approve the new dependency", reject: "Refuse it", needWhy: true, whyLabel: "Why (needed to refuse)",
      explain: "The commit adds a dependency to a manifest. keel asks before new code from outside comes in.",
      approved: "Dependency approved. The commit goes on.", rejected: "Refused. The agent gets your reason and tries without it.", special: true,
    };
  }
  if (w.kind === "fix") {
    // The engine names the buttons of its newer questions (a staged secret, a readonly commit): use its words.
    return {
      approve: w.labels?.approve ?? "Approve fix", reject: w.labels?.reject ?? "Reject fix", needWhy: true,
      whyLabel: `Why (needed for “${w.labels?.reject ?? "Reject fix"}”)`,
      approved: w.labels?.approve ? "Done. The flow moves on." : "Fix approved.",
      rejected: w.labels?.reject ? "Done, with your reason." : "Fix rejected. The agent gets your reason.", special: true,
    };
  }
  if (w.kind === "clarify") {
    return {
      approve: w.labels?.approve ?? "Send my answers", reject: "Send back", needWhy: false,
      whyLabel: "Anything else the explorer should know (optional)",
      explain: "The explorer could not decide these from the code. One click each: the recommended option comes first, and your own words win over a click.",
      approved: "Answers sent. The explorer continues with them.", rejected: "Sent back.", special: true,
    };
  }
  if (w.kind === "gate" && /blocking finding/.test(t)) {
    return {
      approve: w.labels?.approve ?? "Fix them", reject: w.labels?.reject ?? "Go on anyway", needWhy: true, whyLabel: "Why (needed to go on anyway)",
      explain: "A reviewer marked these findings as blocking. The implementer can fix them, then the review runs again.",
      approved: "The implementer fixes the findings; the tests run and the review runs again.",
      rejected: "Accepted with your reason. The flow goes on.",
    };
  }
  if (w.labels?.approve || w.labels?.reject) {
    return {
      approve: w.labels.approve ?? "Approve", reject: w.labels.reject ?? "Send back", needWhy: true,
      whyLabel: /question/.test(t) ? `Your answers (needed for “${w.labels.reject ?? "Send back"}”)` : `Why (needed for “${w.labels.reject ?? "Send back"}”)`,
      approved: "Done. The flow moves on.", rejected: "Sent back with your reason.",
    };
  }
  if (w.kind === "gate" && /already passes/.test(t)) {
    return {
      approve: "Mark as already met", reject: "Send back for a stricter test", needWhy: true, whyLabel: "Why (needed to send back)",
      explain: "The new test passes without new code: an earlier criterion's code already covers this one.",
      approved: "Marked as already met. The flow goes on with the next AC.",
      rejected: "Sent back. The test author writes a stricter test with your reason.",
    };
  }
  if (/escalat/.test(t)) {
    return {
      // the engine refuses a send back from a gate without a reason: keeping the change flow needs one
      approve: "Yes, switch to a feature flow", reject: "No, keep the change flow", needWhy: true, whyLabel: "Why (needed to keep the change flow)",
      explain: "This change touches a contract, a migration, auth code, or is large. A feature flow adds a spec and an AC gate.",
      approved: "Escalated. The work continues as a feature flow.", rejected: "Kept as a change flow.", special: true,
    };
  }
  return {
    approve: `Approve${acId ? " " + acId : ""}`, reject: `Send back${backName ? " to " + backName : ""}`, needWhy: true, whyLabel: "Why (needed to send back)",
    approved: "Approved. The flow moves on.", rejected: "Sent back. The agent gets your reason in its prompt.",
  };
}

const GATE_LABEL: Record<string, string> = { release: "release", coverage: "coverage", deps: "dependencies", knowledge: "knowledge", secrets: "secrets" };

export function BeforeShipPanel({ blockers }: { blockers?: Blocker[] }) {
  return (
    <Panel title="Before ship" extra={<span className="hint">checked before the push and after every commit</span>}>
      {!blockers ? <span className="sub">Not checked yet. keel checks it before the push.</span> : !blockers.length ? (
        <div className="row"><Pill tone="ok">ready</Pill><span className="sub">Nothing blocks shipping.</span></div>
      ) : (
        <div className="blockers" aria-label="What blocks shipping">
          {blockers.map((b, i) => (
            <div key={i} className="blocker">
              <div className="row"><Pill tone={b.gate === "secrets" ? "bad" : "warn"}>{GATE_LABEL[b.gate] ?? b.gate}</Pill><span>{b.why}</span></div>
              {b.fix && <span className="sub">Fix: {b.fix}</span>}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

/** keel 0.18.11 what an approve leads to, in plain words: the next criterion, the next step, or the end. */
function afterApprove(workflow: Workflow, thread: ThreadState, step?: Step): string {
  if (!step) return "the flow goes on";
  if (step.per_ac) {
    const next = thread.acs.find((a) => a.id !== thread.ac && !["done", "already-met"].includes(a.status));
    if (next) return `keel starts the next criterion: ${next.id}${next.title ? ` “${next.title}”` : ""}`;
  }
  const at = workflow.steps.findIndex((s) => s.id === step.id);
  const to = step.then && step.then !== "continue" ? workflow.steps.find((s) => s.id === step.then)
    : workflow.steps.slice(at + 1).find((s) => !(step.per_ac && s.per_ac));
  return to ? `the flow goes on with “${to.name}”` : "the flow is done";
}

/** The flow's id as a small copy button (it is for MCP and the terminal app, not for reading). */
export function CopyId({ id }: { id: string }) {
  const { toast } = useApp();
  return (
    <button type="button" className="linkbtn sub fl-tid" title={`Flow id ${id}: copy it for keel2 or MCP`} aria-label={`Copy the flow id ${id}`}
      onClick={() => { void copyText(id).then((ok) => toast(ok ? "Flow id copied." : id)); }}>
      copy flow id
    </button>
  );
}

export function GateCard({ thread, workflow, onDone }: { thread: ThreadState; workflow: Workflow; onDone: () => Promise<void> }) {
  const { toast, reloadProjects } = useApp();
  const w = thread.waiting!;
  const [why, setWhy] = useState("");
  const [picked, setPicked] = useState<ClarifyAnswers>({});
  const [typed, setTyped] = useState<ClarifyAnswers>({});
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const step = workflow.steps.find((s) => s.id === w.step.replace(/__fix$/, "")) ?? workflow.steps.find((s) => s.id === thread.current);
  const back = step?.back ? workflow.steps.find((s) => s.id === step.back) : undefined;
  const ac = thread.acs.find((a) => a.id === thread.ac);
  const labels = gateLabels(w, ac?.id, back?.name);
  const keelbot = useHasPage("helper");

  const showAc = w.kind === "gate" && !labels.special;
  // keel 0.18.13 the engine says what each named choice means: one card each (ChoiceForm), its own reason box
  const info = (w.kind === "gate" || w.kind === "fix" || w.kind === "usage") && w.choice_info?.length ? w.choice_info : null;
  // keel 0.18.11 a plain approve / send back gate says in plain words what you approve and what each button does
  const plain = w.kind === "gate" && !labels.special && !w.choices?.length && w.options.includes("approve") && w.options.includes("reject");
  const after = plain ? afterApprove(workflow, thread, step) : "";
  const sentBack = back
    ? `${back.agent ? `the ${back.agent} does “${back.name}” again` : `“${back.name}” runs again`}${ac && step?.per_ac ? ` for ${ac.id}` : ""}, with your reason`
    : "the step before runs again, with your reason";
  const used = thread.usage.tokens_in + thread.usage.tokens_out + Math.floor((thread.usage.tokens_cached ?? 0) / 10);
  const onKey = (e: React.KeyboardEvent) => {
    const target = e.target as HTMLElement;
    const tag = target.tagName;
    // a held key repeats; typing in a box (also a contenteditable one) is only typing
    if (e.repeat || target.isContentEditable || target.closest?.('[contenteditable]:not([contenteditable="false"])')) return;
    if (busy || inFlight.current || e.metaKey || e.ctrlKey || e.altKey || tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT") return;
    if (plain && e.key.toLowerCase() === "a") { e.preventDefault(); void decide("approve"); }
    if (w.options.includes("reject") && labels.whyLabel && e.key.toLowerCase() === "r") { e.preventDefault(); document.getElementById("why")?.focus(); }
  };

  const decide = async (decision: "approve" | "reject", extra?: Record<string, unknown>, said = why) => {
    // a gate's exit named "reject" sends back too: the engine wants the reason for it as well
    if ((decision === "reject" && labels.needWhy || (extra?.choice === "reject" && !info)) && !said.trim()) {
      document.getElementById("why")?.focus();
      toast("Write why first, so the agent knows what to change.");
      return;
    }
    // B9 a ticked step of a skip menu needs its reason before the answer goes
    const missing = decision === "approve" && w.questions?.length ? reasonsMissing(w.questions, picked, typed) : "";
    if (missing) {
      toast(missing);
      return;
    }
    // a ref, not `busy`: the state is stale until the next render, so a second key in the same frame would post again
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setErr(null);
    try {
      // keel 0.18.13 a gate may ask questions next to its choices (hunt lenses, a skip menu): both go back together
      const answers = w.questions?.length ? { ...(extra ?? {}), ...questionsPayload(w.questions, picked, typed) } : extra;
      await api.resume(thread.thread_id, decision, said.trim() || undefined, answers);
      setPicked({});
      setTyped({});
      toast(decision === "approve" ? labels.approved : labels.rejected);
      setWhy("");
      await Promise.all([onDone(), reloadProjects()]);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  return (
    <div className="interrupt" role="region" aria-label="Gate waits for you" onKeyDown={onKey}>
      {/* 0.18.64 one span for the title: in the flex row each text and `code` piece of it was its own column */}
      <h3><Pill tone="warn">◆ waits for you</Pill> <span className="gate-title"><Ticks text={w.title} />{showAc && ac ? <> — {ac.id} [{ac.layer}] <Ticks text={ac.title} /></> : null}</span></h3>
      <RunModeNote mode={thread.run_mode} kind={w.kind === "fix" && /dependenc/i.test(w.title) ? "dependency" : w.kind} title={w.title}
        auto={{ count: autoLines(thread.gate_log).length, last: autoLines(thread.gate_log).at(-1) }} />
      {labels.explain && <p className="sub" style={{ margin: 0 }}>{labels.explain}</p>}
      {plain && (
        <dl className="gc-lines" aria-label="What you decide">
          <div><dt>You approve</dt><dd>{showAc && ac ? <><b>{ac.id}</b> “<Ticks text={ac.title} />”</> : <b>{step?.name ?? w.title}</b>}</dd></div>
          <div><dt>So far</dt><dd>{kfmt(used)} tokens · {usd(thread.usage.cost_usd)} for this flow</dd></div>
          <div><dt>After approve</dt><dd>{after}</dd></div>
          <div><dt>After send back</dt><dd>{sentBack}</dd></div>
        </dl>
      )}
      {w.kind === "clarify" && w.questions
        ? <ClarifyForm questions={w.questions} picked={picked} typed={typed}
            onPick={(id, value) => setPicked((p) => ({ ...p, [id]: value }))} onType={(id, text) => setTyped((p) => ({ ...p, [id]: text }))} />
        : w.detail && (plain ? <div className="gc-proof"><span className="gc-k">Proof</span><GateDetail text={w.detail} /></div> : <GateDetail text={w.detail} />)}
      {w.kind !== "clarify" && w.questions?.length ? (
        <ClarifyForm questions={w.questions} picked={picked} typed={typed}
          onPick={(id, value) => setPicked((p) => ({ ...p, [id]: value }))} onType={(id, text) => setTyped((p) => ({ ...p, [id]: text }))} />
      ) : null}
      {labels.whyLabel && !info && (
        <div className="field">
          <label htmlFor="why">{labels.whyLabel}</label>
          <textarea id="why" value={why} onChange={(e) => setWhy(e.target.value)} placeholder="e.g. also check the error message text in the test" />
        </div>
      )}
      {info ? (
        <ChoiceForm key={w.id ?? w.title} id={`gate-${thread.thread_id}`} choices={info} busy={busy} onSend={(choice, said) => decide("approve", { choice }, said)} />
      ) : w.kind === "usage" && w.choices ? (
        <div className="row">
          {w.choices.includes("continue") && <button className="btn warn" type="button" id="approve" disabled={busy} onClick={() => decide("approve", { choice: "continue" })}>Continue anyway</button>}
          {w.choices.includes("wait") && <button className="btn" type="button" disabled={busy} onClick={() => decide("approve", { choice: "wait" })}>Wait for the reset</button>}
          {w.choices.includes("cheaper") && <button className="btn" type="button" disabled={busy} onClick={() => decide("approve", { choice: "cheaper" })}>Use the cheaper model</button>}
          {w.choices.includes("stop") && <button className="btn" type="button" id="sendback" disabled={busy} onClick={() => decide("reject")}>Stop here</button>}
        </div>
      ) : (w.kind === "gate" || w.kind === "fix") && w.choices?.length ? (
        <div className="row" aria-label="Choose one">
          {w.choices.map((c, n) => (
            <button key={c} className={n === 0 ? "btn warn" : "btn"} type="button" id={n === 0 ? "approve" : undefined} disabled={busy}
              onClick={() => decide("approve", { choice: c })}>{c}</button>))}
          {w.options.includes("reject") && <button className="btn" type="button" id="sendback" disabled={busy} onClick={() => decide("reject")}>{labels.reject}</button>}
        </div>
      ) : <div className="row">
        {w.options.includes("approve") && (
          <button className="btn warn" type="button" id="approve" disabled={busy} onClick={() => decide("approve")} aria-keyshortcuts={plain ? "A" : undefined}>
            {labels.approve}{plain && <kbd className="gc-kbd" aria-hidden="true">A</kbd>}
          </button>
        )}
        {w.options.includes("reject") && (
          <button className="btn" type="button" id="sendback" disabled={busy} onClick={() => decide("reject")}
            title={labels.whyLabel ? "R puts you in the reason box" : undefined}>
            {labels.reject}{plain && labels.whyLabel && <kbd className="gc-kbd" aria-hidden="true">R</kbd>}
          </button>
        )}
      </div>}
      {err && <ErrorBox error={err} />}
      {err && keelbot && (
        <div className="row">
          <button type="button" className="btn sm" onClick={() => { askAssistant(fixRequest(`answering the gate “${w.title}” of the flow “${thread.title}”`, err)); go("helper"); }}
            title="KeelBot reads the error and the project, says why it failed and proposes a fix you press">Ask KeelBot to fix it</button>
        </div>
      )}
    </div>
  );
}


/** Lines shown before a gate's code or diff block folds (more than elsewhere: the diff is what you decide on). */
export const GATE_FOLD = 30;
const GATE_FOLD_LABELS: FoldLabels = {
  code: (n) => `Show all ${n} lines`,
  diff: (n) => `Show the full diff, ${n} lines`,
};

/** The first line that starts with `$ ` outside a fenced block (a `$ ` line inside a fence is code), or -1. */
export function commandLine(lines: string[]): number {
  let open: string | null = null;
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (open) {
      const t = l.trim();
      if (t.length >= open.length && t === open[0].repeat(t.length)) open = null;
      continue;
    }
    const f = l.match(/^\s*(```+|~~~+)/);
    if (f) { open = f[1]; continue; }
    if (l.startsWith("$ ")) return i;
  }
  return -1;
}

/** A pause's detail: the explanation as Markdown (headings, tables, ```diff fences as diffs), then a `$ command` with
 *  its output folded (like agent steps). */
export function GateDetail({ text }: { text: string }) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const at = commandLine(lines);
  const prose = (at < 0 ? lines : lines.slice(0, at)).join("\n").trim();
  const cmd = at < 0 ? null : lines[at].slice(2);
  const out = at < 0 ? "" : lines.slice(at + 1).join("\n").replace(/\s+$/, "");
  return (
    <div className="gate-detail" data-testid="gate-detail">
      {prose && <div className="facts md gate-md"><Markdown text={prose} breaks fold={GATE_FOLD} foldLabels={GATE_FOLD_LABELS} /></div>}
      {cmd !== null && (
        <div className="cmd" data-testid="gate-cmd">
          <span className="prompt" aria-hidden="true">$</span>
          <CodeBlock text={cmd} lang="bash" gutter={false} className="cmd-code" />
        </div>
      )}
      {out && <FoldedText text={out} className="cmd-out" />}
    </div>
  );
}
