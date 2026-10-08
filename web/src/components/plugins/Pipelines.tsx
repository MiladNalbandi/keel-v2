// v0.11.0 Run › Jobs › Pipelines (the CI/CD plugin): the project's pipeline runs on GitHub Actions, newest first. Open
// a failed run to see its failed jobs and steps and the end of their log; Fix it starts the ci-fix flow (read the
// failure, fix, commit, push, wait for CI), Run again re-runs the failed jobs, Ask KeelBot explains the failure.

import { useState } from "react";
import { api, errorParts, type CiRun } from "../../api";
import { clock } from "../../format";
import { go, useApp, useLoad } from "../../state";
import { ErrorBox, Pill, type PillTone } from "../ui";
import { askAssistant } from "../../sdk/assistant";

export function runTone(r: CiRun): PillTone {
  if (r.status !== "completed") return "run";
  if (r.failed) return "bad";
  return r.conclusion === "success" ? "ok" : "idle";
}

export function PipelinesView({ pid }: { pid: string }) {
  const runs = useLoad(`ci-runs:${pid}`, () => api.ciRuns(pid));
  const [open, setOpen] = useState<number | null>(null);
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const check = async () => {
    setBusy(true);
    try {
      const r = await api.ciCheck(pid);
      toast(
        r.told.length
          ? `${r.told.length} new failed run${r.told.length === 1 ? "" : "s"}.`
          : "No new failed run.",
      );
      void runs.reload();
    } catch (e) {
      toast(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="ci" aria-label="Pipelines">
      <div className="row ci-bar">
        <span className="sub">
          GitHub Actions · keel looks every 2 minutes and tells you when a run
          fails (Settings › When CI fails).
        </span>
        <button
          type="button"
          className="btn sm"
          disabled={busy}
          onClick={() => void check()}
          style={{ marginLeft: "auto" }}
        >
          Check now
        </button>
      </div>
      {runs.error ? (
        <ErrorBox error={runs.error} onRetry={() => void runs.reload()} />
      ) : !runs.data ? (
        <p className="sub">Reading the pipelines…</p>
      ) : !runs.data.length ? (
        <p className="sub">No pipeline ran yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="ci-table" aria-label="Pipeline runs">
            <thead>
              <tr>
                <th>Result</th>
                <th>Workflow</th>
                <th>Branch</th>
                <th>Commit</th>
                <th>When</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {runs.data.map((r) => (
                <RunRow
                  key={r.id}
                  pid={pid}
                  r={r}
                  open={open === r.id}
                  onToggle={() => setOpen(open === r.id ? null : r.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function RunRow({
  pid,
  r,
  open,
  onToggle,
}: {
  pid: string;
  r: CiRun;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr className={open ? "on" : undefined}>
        <td>
          <Pill tone={runTone(r)}>
            {r.status === "completed"
              ? (r.conclusion ?? "done")
              : r.status.replace(/_/g, " ")}
          </Pill>
        </td>
        <td>
          <b>{r.workflow}</b>
          <span className="sub"> · {r.title}</span>
        </td>
        <td className="mono">{r.branch}</td>
        <td className="mono">{r.sha.slice(0, 7)}</td>
        <td>{clock(r.updated_at, false)}</td>
        <td>
          <button
            type="button"
            className="btn sm"
            aria-expanded={open}
            onClick={onToggle}
            aria-label={`Open run #${r.id}`}
          >
            {open ? "Close" : "Open"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="ci-detail">
          <td colSpan={6}>
            <RunDetail pid={pid} id={r.id} />
          </td>
        </tr>
      )}
    </>
  );
}

function RunDetail({ pid, id }: { pid: string; id: number }) {
  const run = useLoad(`ci-run:${pid}:${id}`, () => api.ciRun(pid, id), {
    live: false,
  });
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(
    null,
  );
  const r = run.data;
  const fix = async () => {
    setBusy(true);
    setErr(null);
    try {
      const t = await api.ciFix(pid, id);
      toast("The fix flow started.");
      go("flow", t.thread_id);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const rerun = async () => {
    setBusy(true);
    try {
      await api.ciRerun(pid, id);
      toast(`The failed jobs of run #${id} run again.`);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  if (run.error) return <ErrorBox error={run.error} />;
  if (!r) return <p className="sub">Reading the run…</p>;
  const failedJobs = (r.jobs ?? []).filter(
    (j) =>
      j.conclusion && j.conclusion !== "success" && j.conclusion !== "skipped",
  );
  return (
    <div className="ci-run" role="group" aria-label={`Run #${id}`}>
      <ul className="ci-jobs">
        {(r.jobs ?? []).map((j) => (
          <li key={j.id}>
            <span
              className={`gp-dot ${j.conclusion === "success" ? "ok" : j.conclusion ? "bad" : "run"}`}
              aria-hidden="true"
            />
            <b>{j.name}</b>: {j.conclusion ?? j.status}
            {j.failed_steps.length ? (
              <span className="sub"> at {j.failed_steps.join(", ")}</span>
            ) : null}
          </li>
        ))}
      </ul>
      {r.log && (
        <pre className="ci-log mono" aria-label="The failed steps' log">
          {r.log}
        </pre>
      )}
      {err && <ErrorBox error={err} />}
      <div className="row">
        {r.failed && (
          <button
            type="button"
            className="btn sm primary"
            disabled={busy}
            onClick={() => void fix()}
          >
            Fix it
          </button>
        )}
        {r.failed && (
          <button
            type="button"
            className="btn sm"
            disabled={busy}
            onClick={() => void rerun()}
          >
            Run the failed jobs again
          </button>
        )}
        {r.failed && (
          <button
            type="button"
            className="btn sm ghost"
            onClick={() => {
              askAssistant(
                `/ci run #${id}${failedJobs.length ? ` (${failedJobs.map((j) => j.name).join(", ")})` : ""}`,
              );
              go("helper");
            }}
          >
            Ask KeelBot why
          </button>
        )}
        <a
          className="btn sm ghost"
          href={r.url}
          target="_blank"
          rel="noreferrer"
        >
          Open on GitHub
        </a>
      </div>
    </div>
  );
}
