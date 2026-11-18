// 0.40 (#201) keel's rules for a flow's agents: Normal (every rule) or Safety only (keel-gate's safety rules only).
// The picker for "Start a flow", the Flow page's chip and switch, and the words Settings uses.

import { useEffect, useId, useState } from "react";
import { errorParts, type RulesMode, type ThreadState } from "../api";
import { inboxApi } from "../inboxApi";
import { useApp } from "../state";
import "./inbox.css";

/** [mode, label, what it does] — the same words as the engine's mcp_tui.RULES_MODES. */
export const RULES_MODES: [RulesMode, string, string][] = [
  ["normal", "Normal", "Every keel rule: phase rules (tests first, then code), rule packs, read limits, and the safety rules."],
  ["safety-only", "Safety only", "Only the safety rules: anything that cannot be undone is refused (dropping a database, rm -rf of your home, deleting a remote branch, terraform destroy, a disk format), secrets and keel's keys stay out. Phase rules and rule packs are off, so agents stop getting stuck on them. Commits, tests and your gates stay."],
];

export const rulesLabel = (m?: string | null) => RULES_MODES.find(([k]) => k === m)?.[1] ?? "Normal";
export const rulesAbout = (m?: string | null) => RULES_MODES.find(([k]) => k === m)?.[2] ?? RULES_MODES[0][2];

/** A radio group with one line per mode (Start a flow). */
export function RulesModePicker({ value, onChange, hint }: { value: RulesMode; onChange: (m: RulesMode) => void; hint?: string }) {
  const name = useId();
  return (
    <fieldset className="field runmode-pick" data-testid="rules-mode-pick">
      <legend className="lab">Rules</legend>
      {RULES_MODES.map(([k, label, about]) => (
        <label key={k} className="radio">
          <input type="radio" name={name} value={k} checked={value === k} onChange={() => onChange(k)} />
          <span><b>{label}</b> <span className="sub">{about}</span></span>
        </label>
      ))}
      {hint && <span className="hint">{hint}</span>}
    </fieldset>
  );
}

/** The Flow page's "Safety only" chip (nothing in Normal). */
export function RulesChip({ mode }: { mode?: RulesMode | null }) {
  if (mode !== "safety-only") return null;
  return <span className="tag warn" data-testid="rules-chip" title={rulesAbout(mode)}>Safety only</span>;
}

/** The rules of one running flow, changeable now; they count from its next agent call. */
export function RulesSwitch({ threadId, mode, onChange }: {
  threadId: string; mode?: RulesMode | null; onChange?: (thread: ThreadState) => void;
}) {
  const { toast, reloadProjects } = useApp();
  const id = useId();
  const [value, setValue] = useState<RulesMode>(mode ?? "normal");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setValue(mode ?? "normal"), [mode, threadId]);

  const change = async (next: RulesMode) => {
    const before = value;
    setValue(next);
    setBusy(true);
    setErr(null);
    try {
      const t = await inboxApi.setRules(threadId, next);
      onChange?.(t);
      toast(`Rules: ${rulesLabel(next)}, from the next agent call.`);
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
    <div className="runmode-switch compact" data-testid="rules-switch">
      <label htmlFor={id} className="lab">Rules</label>
      <select id={id} value={value} disabled={busy} onChange={(e) => void change(e.target.value as RulesMode)}>
        {RULES_MODES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
      </select>
      <span className="sr-only">{rulesAbout(value)}</span>
      {err && <span className="hint bad" role="alert">{err}</span>}
    </div>
  );
}
