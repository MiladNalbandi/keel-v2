// The Supervisor's pieces outside its Jobs tab (plugins/supervisor): the chip in a flow's header, the note on a flow's
// Inbox item, and the row on Settings › General. It imports only react and @keel/web-sdk.

import { useEffect, useState } from "react";
import {
  Pill,
  useLoad,
  type FlowChipProps,
  type InboxNoteProps,
  type SettingsSectionProps,
} from "@keel/web-sdk";
import { supervisor, type ChipState, type Defaults, type Note } from "./Supervisor";

const CHIP: Record<ChipState, { text: string; tone: "ok" | "run" | "warn" | "bad" | "idle" }> = {
  off: { text: "off", tone: "idle" },
  watching: { text: "watching", tone: "idle" },
  looking: { text: "looking…", tone: "run" },
  acted: { text: "acted", tone: "ok" },
  escalated: { text: "escalated", tone: "warn" },
  "paused-budget": { text: "paused (budget)", tone: "bad" },
  "paused-repeats": { text: "paused (repeats)", tone: "bad" },
};
const POLL_MS = 15_000;

/** The flow's chip. It is not there at all while the Supervisor is off keel-wide or for the plugin: a keel that never turned
 *  it on looks as it did before. Off for this project or flow only, it says so. */
export function SupervisorChip({ pid, thread, readOnly }: FlowChipProps) {
  const live = !readOnly && (thread.status === "running" || thread.status === "waiting");
  const tid = thread.thread_id;
  const s = useLoad(live ? `supervisor-chip:${pid}:${tid}:${thread.status}:${thread.waiting?.id ?? ""}` : null, () => supervisor.status(pid, tid));
  const { reload } = s;
  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(() => void reload(), POLL_MS);
    return () => window.clearInterval(t);
  }, [live, reload]);
  const v = s.data;
  if (!live || !v || (v.state === "off" && (v.off_by === "keel" || v.off_by === "plugin"))) return null;
  const c = CHIP[v.state] ?? CHIP.watching;
  return (
    <a href="#/jobs/supervisor" title={`Mode: ${v.mode}. Open what the Supervisor did.`} data-testid="supervisor-chip">
      <Pill tone={c.tone}>Supervisor: {c.text}</Pill>
    </a>
  );
}

const ANSWER: Record<string, string> = { approve: "approve", reject: "send it back" };

function noteLine(n: Note): string {
  const s = n.suggestion;
  if (!s) return "";
  return s.choice ? `It suggests: ${s.choice}.` : `It suggests: ${ANSWER[s.decision] ?? s.decision}.`;
}

/** On a flow's own Inbox item: what the Supervisor saw, and "Use suggestion", which fills in the form. You still send it. */
export function SupervisorNote({ item, fill }: InboxNoteProps) {
  const pid = item.project_id;
  const tid = item.thread_id;
  const s = useLoad(`supervisor-note:${pid}:${tid}:${item.id ?? ""}`, () => supervisor.status(pid, tid));
  const [used, setUsed] = useState(false);
  const n = s.data?.note;
  if (!n) return null;
  const a = n.suggestion;
  return (
    <aside className="panel sup-note" aria-label="The Supervisor's note" data-testid="supervisor-note">
      <p>
        <b>Supervisor:</b> {n.summary || n.reason}
        {a ? ` ${noteLine(n)}` : ""}
      </p>
      {n.reason && <p className="hint">Why it did not answer: {n.reason}.</p>}
      {n.patch && (
        <details>
          <summary>The fix it put back</summary>
          <pre className="mono">{n.patch}</pre>
        </details>
      )}
      {a && (
        <p>
          <button type="button" className="btn sm" onClick={() => {
            fill({ why: a.why || undefined, choice: a.choice ?? undefined, answers: Object.keys(a.answers ?? {}).length ? a.answers : undefined });
            setUsed(true);
          }}>
            Use suggestion
          </button>{" "}
          {used && <span className="hint" role="status">Filled in below. Nothing is sent until you send it.</span>}
        </p>
      )}
    </aside>
  );
}

const MODE_TEXT: Record<Defaults["mode"], string> = {
  shadow: "shadow: only suggests",
  retry: "retry: only tries a stuck step again, with its note",
  act: "act: answers when sure",
  fix: "fix: also makes small fixes on the flow's own branch",
};

/** Settings › General › Supervisor: on for the whole keel, the mode, the stall time. A flow's own choice in Jobs › Supervisor wins. */
export function SupervisorSettingsRow({ scope }: SettingsSectionProps) {
  const d = useLoad("supervisor-defaults", () => supervisor.defaults());
  const [stall, setStall] = useState<string | null>(null);
  const [problem, setProblem] = useState("");
  if (scope !== "general") return <p className="hint">The Supervisor's keel-wide choices are under General. A project switches it in Jobs › Supervisor.</p>;
  const v = d.data;
  const save = async (body: Parameters<typeof supervisor.setDefaults>[0]) => {
    setProblem("");
    try {
      d.setData(await supervisor.setDefaults(body));
      if ("stall_minutes" in body) setStall(null);
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    }
  };
  if (!v) return d.error ? <p role="alert">{d.error.message}</p> : null;
  return (
    <div data-testid="supervisor-settings">
      <p>
        <label>
          <input type="checkbox" checked={v.on} onChange={(e) => void save({ on: e.target.checked })} /> On for the whole keel
        </label>
      </p>
      <div className="field">
        <label htmlFor="sup-mode">Mode</label>
        <select id="sup-mode" value={v.mode} onChange={(e) => void save({ mode: e.target.value as Defaults["mode"] })}>
          {v.modes.map((m) => <option key={m} value={m}>{MODE_TEXT[m]}</option>)}
        </select>
      </div>
      {v.mode === "fix" && (
        <p className="hint" role="note">
          In fix mode it may change code by itself: at most 3 files and 40 lines, on the flow's own branch. It never commits, pushes or merges.
        </p>
      )}
      <div className="field">
        <label htmlFor="sup-stall">Ask me when a flow is quiet for (minutes)</label>
        <input id="sup-stall" type="number" min={v.stall_min} max={v.stall_max} value={stall ?? v.stall_minutes}
          onChange={(e) => setStall(e.target.value)}
          onBlur={() => { if (stall != null && Number(stall) !== v.stall_minutes) void save({ stall_minutes: Number(stall) }); }} />
      </div>
      {problem && <p role="alert">{problem}</p>}
    </div>
  );
}
