// v0.15.4 the Flow page's Runs: every flow of the project, newest first, when no flow runs or waits there. A run opens
// read only (Load), the project's newest flow can be resumed when it was stopped, and a run can be deleted from the
// history (DELETE /api/projects/{pid}/flows/{tid}: only a mark, its checkpoints, branch and calls stay).

import { useMemo, useState } from "react";
import { api, errorParts, type RunRow } from "../api";
import { clock, kfmt, usd } from "../format";
import { go, useApp, useLoad } from "../state";
import { Confirm, StatusPill } from "./ui";

const PAGE = 12;
const live = (r: RunRow) => r.status === "running" || r.status === "waiting";
const canStop = (r: RunRow) => r.status === "stopped" || r.status === "failed";

/** "1h 12m", "12m", "40s": from its start to its last change. */
function took(r: RunRow): string {
  const ms = Date.parse(r.updated_at) - Date.parse(r.created_at);
  if (Number.isNaN(ms)) return "—";
  const s = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(s / 3600),
    m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : m ? `${m}m` : `${s}s`;
}

/** The runs, newest start first; the newest one is `latest` (the server says which, else the first one). */
export function sortRuns(rows: RunRow[]): RunRow[] {
  return [...rows].sort(
    (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
  );
}

export function latestOf(rows: RunRow[]): string | null {
  return (
    rows.find((r) => r.latest)?.thread_id ??
    sortRuns(rows)[0]?.thread_id ??
    null
  );
}

/** Why a run cannot be deleted now, or null when it can. */
export function whyKeep(
  r: RunRow,
  current: string | null,
  latest: string | null,
): string | null {
  if (r.status === "running") return "It still runs. Stop it first.";
  if (r.status === "waiting")
    return "It waits for you. Answer it or stop it first.";
  if (canStop(r) && (r.thread_id === current || r.thread_id === latest))
    return "It can still be resumed, so it stays.";
  return null;
}

/** What came out of it: the error of a failed run, else its acceptance criteria. */
function result(r: RunRow): string {
  if (r.status === "failed" && r.error) return r.error;
  if (r.acs_total) return `ACs done ${r.acs_done} of ${r.acs_total}`;
  return r.status === "done"
    ? "Done"
    : r.status === "stopped"
      ? "Stopped before the end"
      : "—";
}

export function RunHistory({
  pid,
  current,
  viewing,
  onLoad,
  onChanged,
  resume,
}: {
  pid: string;
  /** the flow the page shows as the project's current one */
  current: string | null;
  /** the earlier run that is open read only, if any */
  viewing: string | null;
  onLoad: (r: RunRow | null) => void;
  /** after a delete: the page reloads what it shows */
  onChanged: () => Promise<void> | void;
  /** the Resume card for the newest stopped run (Flow.tsx's ResumeRow) */
  resume: (r: RunRow, close: () => void) => React.ReactNode;
}) {
  const { toast } = useApp();
  const runs = useLoad(`runhist:${pid}`, () => api.runs(pid, undefined, 100));
  const [q, setQ] = useState("");
  const [st, setSt] = useState("");
  const [wf, setWf] = useState("");
  const [more, setMore] = useState(false);
  const [asking, setAsking] = useState<string | null>(null);
  const [resuming, setResuming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const all = useMemo(() => sortRuns(runs.data ?? []), [runs.data]);
  const latest = latestOf(all);
  if (!all.length) return null;
  const workflows = [
    ...new Set(all.map((r) => r.workflow_id).filter((w): w is string => !!w)),
  ].sort();
  const statuses = [...new Set(all.map((r) => r.status))].sort();
  const words = q.trim().toLowerCase();
  const found = all.filter(
    (r) =>
      (!st || r.status === st) &&
      (!wf || r.workflow_id === wf) &&
      (!words ||
        [r.title, r.workflow_id, r.branch, r.thread_id].some((x) =>
          (x ?? "").toLowerCase().includes(words),
        )),
  );
  const shown = more ? found : found.slice(0, PAGE);

  const remove = async (r: RunRow) => {
    setBusy(true);
    try {
      await api.deleteRun(pid, r.thread_id);
      toast(
        `“${r.title}” is gone from the history. Its branch and commits stay.`,
      );
      setAsking(null);
      if (viewing === r.thread_id) onLoad(null);
      await Promise.all([runs.reload(), onChanged()]);
    } catch (e) {
      const p = errorParts(e);
      toast(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel rh" aria-label="Runs">
      <div className="panel-head">
        <h2>Runs</h2>
        <span className="sub">
          {all.length} flow{all.length === 1 ? "" : "s"} of this project, newest
          first
        </span>
      </div>
      <div className="panel-body rh-body">
        <div className="rh-filters" role="search">
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search title, workflow, branch"
            aria-label="Search the runs"
          />
          <select
            value={st}
            onChange={(e) => setSt(e.target.value)}
            aria-label="Show the runs with one status"
          >
            <option value="">every status</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          {workflows.length > 1 && (
            <select
              value={wf}
              onChange={(e) => setWf(e.target.value)}
              aria-label="Show the runs of one workflow"
            >
              <option value="">every workflow</option>
              {workflows.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
          )}
        </div>
        {!found.length ? (
          <span className="sub">No run matches.</span>
        ) : (
          <ul className="rh-list">
            {shown.map((r) => {
              const keep = whyKeep(r, current, latest);
              const resumable =
                r.thread_id === latest && r.status === "stopped";
              const isCurrent = r.thread_id === current;
              const open = r.thread_id === viewing;
              const why = `rh-why-${r.thread_id}`;
              return (
                <li
                  key={r.thread_id}
                  className={`rh-row${open ? " on" : ""}${isCurrent ? " cur" : ""}`}
                  aria-label={`Run ${r.title}`}
                >
                  <div className="rh-main">
                    <div className="rh-title">
                      <b>{r.title || r.thread_id}</b>
                      <StatusPill status={r.status} />
                      {isCurrent && <span className="tag">current</span>}
                      {r.thread_id === latest && !isCurrent && (
                        <span className="tag">newest</span>
                      )}
                    </div>
                    <div className="rh-facts sub">
                      <span className="mono">{r.workflow_id ?? "—"}</span>
                      <span title={r.created_at}>
                        started {clock(r.created_at, false)}
                      </span>
                      <span>took {took(r)}</span>
                      <span>
                        {r.tokens ? `${kfmt(r.tokens)} tokens` : "no tokens"}
                      </span>
                      <span>{usd(r.cost_usd)}</span>
                      {r.branch && (
                        <span>
                          branch <span className="mono">{r.branch}</span>
                        </span>
                      )}
                    </div>
                    <div
                      className={`rh-result${r.status === "failed" ? " bad" : ""}`}
                    >
                      {result(r)}
                    </div>
                    {!resumable && canStop(r) && !isCurrent && (
                      <span className="hint">
                        Only the last stopped flow can be resumed.
                      </span>
                    )}
                    {keep && (
                      <span className="hint" id={why}>
                        {keep}
                      </span>
                    )}
                  </div>
                  <div className="rh-actions">
                    {live(r) ? (
                      <button
                        type="button"
                        className="btn sm"
                        onClick={() => go("flow", r.thread_id)}
                        aria-label={`Open the flow ${r.title}`}
                      >
                        Open
                      </button>
                    ) : isCurrent ? (
                      <button
                        type="button"
                        className="btn sm"
                        disabled={!viewing}
                        onClick={() => onLoad(null)}
                        aria-label={`Show the current flow ${r.title}`}
                      >
                        {viewing ? "Show" : "Shown"}
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn sm"
                        disabled={open}
                        onClick={() => onLoad({ ...r, latest: r.thread_id === latest })}
                        aria-label={`Load the run ${r.title}`}
                      >
                        {open ? "Loaded" : "Load"}
                      </button>
                    )}
                    {resumable && (
                      <button
                        type="button"
                        className="btn sm primary"
                        onClick={() =>
                          setResuming(
                            resuming === r.thread_id ? null : r.thread_id,
                          )
                        }
                        aria-expanded={resuming === r.thread_id}
                        aria-label={`Resume the run ${r.title}`}
                      >
                        Resume…
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn sm ghost"
                      disabled={!!keep || busy}
                      aria-describedby={keep ? why : undefined}
                      onClick={() => setAsking(r.thread_id)}
                      aria-label={`Delete the run ${r.title}`}
                    >
                      Delete
                    </button>
                  </div>
                  {resuming === r.thread_id && resumable && (
                    <div className="rh-more">
                      {resume(r, () => setResuming(null))}
                    </div>
                  )}
                  {asking === r.thread_id && (
                    <div className="rh-more">
                      <Confirm
                        text={
                          <>
                            Delete “{r.title}” from the history? Only the record
                            goes: its branch, commits and checkpoints stay.
                          </>
                        }
                        yes="Yes, delete it"
                        busy={busy}
                        onYes={() => void remove(r)}
                        onNo={() => setAsking(null)}
                      />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {found.length > PAGE && (
          <button
            type="button"
            className="btn sm ghost rh-page"
            onClick={() => setMore((m) => !m)}
          >
            {more ? "Show fewer" : `Show all ${found.length}`}
          </button>
        )}
      </div>
    </section>
  );
}
