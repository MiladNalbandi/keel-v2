// v0.9.0 the Flow page's flows (Run section): a tab for each flow that runs or waits, so you can switch between flows
// that run at the same time, and the project's history (newest first, one workflow's or all), where any old flow opens
// with all its details (GET /api/projects/{pid}/runs).

import { useState } from "react";
import { api, type RunRow } from "../api";
import { clock, kfmt } from "../format";
import { go, useLoad } from "../state";
import { StatusPill } from "./ui";

const OPEN_KEY = "keel2.flow.history";
const active = (r: RunRow) => r.status === "running" || r.status === "waiting";

function readOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

export function FlowRuns({
  pid,
  selected,
  loading = false,
  history = true,
}: {
  pid: string;
  selected: string | null;
  /** the open flow is still loading: no tab back to "the" running flow yet */
  loading?: boolean;
  /** v0.15.4 false: no History fold-out (the Runs panel shows the history when no flow runs) */
  history?: boolean;
}) {
  const runs = useLoad(`runs:${pid}`, () => api.runs(pid, undefined, 50));
  const [open, setOpen] = useState(readOpen);
  const [wf, setWf] = useState("");
  const all = runs.data ?? [];
  if (!all.length) return null;
  const now = all.filter(active);
  const tabs = now.length > 1 || (!loading && now.length === 1 && now[0].thread_id !== selected);
  if (!history && !tabs) return null;
  const workflows = [
    ...new Set(all.map((r) => r.workflow_id).filter((w): w is string => !!w)),
  ].sort();
  const shown = all.filter((r) => !wf || r.workflow_id === wf).slice(0, 20);
  const toggle = () => {
    setOpen((o) => {
      try {
        localStorage.setItem(OPEN_KEY, o ? "0" : "1");
      } catch {
        /* the choice is only a convenience */
      }
      return !o;
    });
  };
  return (
    <section className="fr" aria-label="This project's flows">
      {tabs && (
        <div className="fr-tabs" role="tablist" aria-label="Flows that run now">
          {now.map((r) => (
            <button
              key={r.thread_id}
              type="button"
              role="tab"
              aria-selected={r.thread_id === selected}
              className={`fr-tab${r.thread_id === selected ? " on" : ""}`}
              onClick={() => go("flow", r.thread_id)}
              title={`${r.title} · ${r.workflow_id ?? ""} · ${r.status}${r.waiting ? `: ${r.waiting}` : ""}`}
            >
              <span className={`fr-dot ${r.status}`} aria-hidden="true" />
              <span className="fr-tab-t">{r.title}</span>
              <span className="sub">{r.workflow_id}</span>
            </button>
          ))}
        </div>
      )}
      {history && (
      <button
        type="button"
        className="fr-more"
        aria-expanded={open}
        onClick={toggle}
      >
        {open ? "▾" : "▸"} History{" "}
        <span className="sub">
          {all.length} flow{all.length === 1 ? "" : "s"}
        </span>
      </button>
      )}
      {history && open && (
        <div className="fr-hist">
          {workflows.length > 1 && (
            <label className="fr-filter">
              <span className="sub">Workflow</span>
              <select
                value={wf}
                onChange={(e) => setWf(e.target.value)}
                aria-label="Show the flows of one workflow"
              >
                <option value="">all</option>
                {workflows.map((w) => (
                  <option key={w} value={w}>
                    {w}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="table-wrap">
            <table className="fr-table">
              <thead>
                <tr>
                  <th>Flow</th>
                  <th>Workflow</th>
                  <th>Status</th>
                  <th>Criteria</th>
                  <th>Tokens</th>
                  <th>Last change</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr
                    key={r.thread_id}
                    className={r.thread_id === selected ? "on" : undefined}
                  >
                    <td>
                      <b>{r.title}</b>
                      {r.waiting && r.status === "waiting" && (
                        <span className="sub"> · waits: {r.waiting}</span>
                      )}
                    </td>
                    <td className="mono">{r.workflow_id ?? "—"}</td>
                    <td>
                      <StatusPill status={r.status} />
                    </td>
                    <td>
                      {r.acs_total ? `${r.acs_done}/${r.acs_total}` : "—"}
                    </td>
                    <td>{r.tokens ? kfmt(r.tokens) : "—"}</td>
                    <td>{clock(r.updated_at, false)}</td>
                    <td>
                      {r.thread_id === selected ? (
                        <span className="sub">open</span>
                      ) : (
                        <button
                          type="button"
                          className="btn sm"
                          onClick={() => go("flow", r.thread_id)}
                          aria-label={`Open the flow ${r.title}`}
                        >
                          Open
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
