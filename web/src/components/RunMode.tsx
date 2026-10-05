// v0.4.1 run modes: how much of a flow keel decides by itself. The picker for "Start a flow" and Settings, and
// <RunModeSwitch/> for the Flow page (change it during a run; it counts from the next gate on).

import { useEffect, useId, useState } from "react";
import { errorParts, type RunMode, type ThreadState } from "../api";
import { inboxApi } from "../inboxApi";
import { useApp } from "../state";
import "./inbox.css";

/** [mode, label, one line that says what it does] — the same words as the engine's runtime/run_mode.py ABOUT. */
export const RUN_MODES: [RunMode, string, string][] = [
  ["manual", "Manual", "Stops at every gate."],
  ["important", "Important only", "Approves a criterion's AC gate by itself when its checks pass and its review is clean. Stops at the spec, contract, final review, PR gate and every problem."],
  ["auto", "Auto", "Approves every gate keel can decide and logs each one. Still stops for money, new dependencies, secrets, a check that keeps failing and anything only you can answer. Never opens a PR or pushes."],
  ["readonly", "Read-only", "Stops at every gate. Agents may read and run checks, but cannot edit, write or commit anything."],
];

export const runModeLabel = (m?: string | null) => RUN_MODES.find(([k]) => k === m)?.[1] ?? "Manual";
export const runModeAbout = (m?: string | null) => RUN_MODES.find(([k]) => k === m)?.[2] ?? RUN_MODES[0][2];

/** The gate-log lines the run mode wrote ("… approve: auto-approved (mode auto)"). */
export const autoLines = (log?: string[] | null) => (log ?? []).filter((l) => l.includes("auto-approved (mode "));

const SAFETY_KINDS = new Set(["fix", "budget", "usage", "dependency"]);

/** Why this pause waits for a person in this run mode (null in manual, where every gate waits). */
export function whyItWaits(mode: string | null | undefined, kind: string, title = ""): string | null {
  if (mode === "auto") {
    if (SAFETY_KINDS.has(kind) || /secret|keeps failing|still no|read-only/i.test(title)) {
      return "Auto mode still stops here: keel never decides money, new dependencies, secrets or a check that keeps failing by itself.";
    }
    return "Auto mode stops when keel cannot decide: only a send-back fits, a loop is past its rounds, or it already approved this question three times.";
  }
  if (mode === "important") {
    return /^AC gate/.test(title)
      ? "Important only stops at an AC gate when its checks failed or its AC review found something."
      : "Important only approves clean AC gates by itself; every other gate waits for you.";
  }
  if (mode === "readonly") return "Read-only: agents cannot edit or commit; every gate waits for you.";
  return null;
}

/**
 * One short note on a waiting gate (Flow page gate card, Inbox item): the flow's run mode, how many gates keel already
 * approved by itself (and the last one), and why this one waits for you.
 */
export function RunModeNote({ mode, kind, title, auto }: {
  mode?: RunMode | null; kind: string; title?: string; auto?: { count: number; last?: string | null };
}) {
  const why = whyItWaits(mode, kind, title);
  const n = auto?.count ?? 0;
  if (!why && !n) return null;
  return (
    <p className="runmode-note" data-testid="run-mode-note">
      <span className="tag" title="run mode">{runModeLabel(mode)}</span>{" "}
      {n > 0 && <span>keel approved {n} gate{n === 1 ? "" : "s"} by itself in this flow{auto?.last ? <> (last: <span className="mono">{auto.last}</span>)</> : null}. </span>}
      {why && <span className="sub">{why}</span>}
    </p>
  );
}

/** A radio group with one line per mode (Start a flow). */
export function RunModePicker({ value, onChange, legend = "Run mode", hint }: {
  value: RunMode; onChange: (m: RunMode) => void; legend?: string; hint?: string;
}) {
  const name = useId();
  return (
    <fieldset className="field runmode-pick">
      <legend className="lab">{legend}</legend>
      {RUN_MODES.map(([k, label, about]) => (
        <label key={k} className="radio">
          <input type="radio" name={name} value={k} checked={value === k} onChange={() => onChange(k)} />
          <span><b>{label}</b> <span className="sub">{about}</span></span>
        </label>
      ))}
      {hint && <span className="hint">{hint}</span>}
    </fieldset>
  );
}

/**
 * The run mode of one running flow, changeable now. Mount it on the Flow page:
 * `<RunModeSwitch pid={pid} threadId={thread.thread_id} mode={thread.run_mode} onChange={(t) => ...} />`.
 * The change counts from the next gate on; a gate that waits now keeps waiting for you.
 */
export function RunModeSwitch({ pid, threadId, mode, onChange, compact = false }: {
  pid: string; threadId: string; mode?: RunMode | null; onChange?: (thread: ThreadState) => void; compact?: boolean;
}) {
  const { toast, reloadProjects } = useApp();
  const id = useId();
  const [value, setValue] = useState<RunMode>(mode ?? "manual");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setValue(mode ?? "manual"), [mode, threadId]);

  const change = async (next: RunMode) => {
    const before = value;
    setValue(next);
    setBusy(true);
    setErr(null);
    try {
      const t = await inboxApi.setMode(threadId, next);
      onChange?.(t);
      toast(`Run mode: ${runModeLabel(next)}, from the next gate on.`);
      void reloadProjects();
    } catch (e) {
      setValue(before);
      const p = errorParts(e);
      setErr(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`runmode-switch${compact ? " compact" : ""}`} data-testid="run-mode-switch" data-project={pid}>
      <label htmlFor={id} className="lab">Run mode</label>
      <select id={id} value={value} disabled={busy} onChange={(e) => void change(e.target.value as RunMode)}
        aria-describedby={`${id}-about`}>
        {RUN_MODES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
      </select>
      {!compact && <span className="hint" id={`${id}-about`}>{runModeAbout(value)} Changes count from the next gate.</span>}
      {compact && <span className="sr-only" id={`${id}-about`}>{runModeAbout(value)}</span>}
      {err && <span className="hint bad" role="alert">{err}</span>}
    </div>
  );
}
