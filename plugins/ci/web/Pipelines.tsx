// v0.11.0 Run › Jobs › Pipelines (the CI/CD plugin): the project's pipeline runs on GitHub Actions, newest first. Open
// a failed run to see its failed jobs and steps and the end of their log; Fix it starts the ci-fix flow (read the
// failure, fix, commit, push, wait for CI), Run again re-runs the failed jobs, Ask KeelBot explains the failure.
// The CI/CD plugin's page part (plugins/ci): it imports only react and @keel/web-sdk.

import { useEffect, useRef, useState } from "react";
import {
  askAssistant,
  clock,
  ErrorBox,
  errorParts,
  get,
  hashForScreen,
  Pill,
  post,
  settingsHash,
  useApp,
  useLoad,
  usePages,
  type PillTone,
} from "@keel/web-sdk";

/** One pipeline run, as the api's /projects/{pid}/ci/runs answers (keel.api.plugins.CiController). */
export type CiRun = {
  id: number;
  workflow: string;
  title: string;
  branch: string;
  sha: string;
  event: string;
  status: string;
  conclusion: string | null;
  url: string;
  attempt?: number;
  created_at: string;
  updated_at: string;
  failed: boolean;
  jobs?: {
    id: number;
    name: string;
    status: string;
    conclusion: string | null;
    url: string;
    failed_steps: string[];
  }[];
  log?: string;
};

// the CI/CD plugin's api calls
const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/ci`;
const ci = {
  runs: (pid: string) => get<CiRun[]>(`${base(pid)}/runs`),
  run: (pid: string, id: number) => get<CiRun>(`${base(pid)}/runs/${id}`),
  rerun: (pid: string, id: number) =>
    post<{ id: number; rerun: boolean }>(`${base(pid)}/runs/${id}/rerun`),
  fix: (pid: string, run?: number) =>
    post<{ thread_id: string }>(`${base(pid)}/fix`, run ? { run } : {}),
  check: (pid: string) => post<{ told: number[] }>(`${base(pid)}/check`),
};

/** Open one of keel's screens (#/flow/<id>, #/helper), as keel's own pages do. */
function go(screen: string, arg?: string) {
  const h = hashForScreen(screen, arg);
  if (location.hash !== h) location.hash = h;
  try {
    window.scrollTo(0, 0);
  } catch {
    /* jsdom */
  }
}

export function runTone(r: CiRun): PillTone {
  if (r.status !== "completed") return "run";
  if (r.failed) return "bad";
  return r.conclusion === "success" ? "ok" : "idle";
}

/** The one-letter keys the buttons show (C check now; F fix it, R run again on an open failed run): not while you
 *  type, with a modifier, or while a dialog is open. On the document, so it runs before keel's own keys. */
function usePageKeys(keys: Record<string, (() => void) | null | undefined>) {
  const ref = useRef(keys);
  ref.current = keys;
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.key.length !== 1) return;
      const t = e.target as HTMLElement | null;
      if (t?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t?.tagName ?? "")) return;
      if (document.querySelector('[aria-modal="true"]')) return;
      const fn = ref.current[e.key.toLowerCase()];
      if (!fn) return;
      e.preventDefault();
      fn();
    };
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, []);
}

const Key = ({ k }: { k: string }) => <kbd aria-hidden="true">{k}</kbd>;

type Show = "all" | "failed" | "running";
const SHOW: [Show, string][] = [
  ["all", "All"],
  ["failed", "Failed"],
  ["running", "Running"],
];

function resultText(r: CiRun) {
  return r.status === "completed"
    ? (r.conclusion ?? "done")
    : r.status.replace(/_/g, " ");
}

/** "3m 40s": a run's time from its start to its last change. */
function span(ms: number) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** The three numbers over the list: main's last result, the failures of the last 7 days, the usual run time. */
export function ciStats(runs: CiRun[], now = Date.now()) {
  const main = runs.find((r) => r.branch === "main" || r.branch === "master");
  const week = runs.filter(
    (r) => r.failed && now - Date.parse(r.created_at) < 7 * 86_400_000,
  );
  const times = runs
    .filter((r) => r.status === "completed")
    .slice(0, 20)
    .map((r) => Date.parse(r.updated_at) - Date.parse(r.created_at))
    .filter((t) => Number.isFinite(t) && t > 0)
    .sort((a, b) => a - b);
  return {
    main,
    failed: week.length,
    failedOn: [...new Set(week.map((r) => r.branch))],
    usual: times.length ? span(times[Math.floor(times.length / 2)]) : null,
    timed: times.length,
  };
}

export function PipelinesView({ pid }: { pid: string }) {
  const runs = useLoad(`ci-runs:${pid}`, () => ci.runs(pid));
  const [open, setOpen] = useState<number | null>(null);
  const [show, setShow] = useState<Show>("all");
  const [q, setQ] = useState("");
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const check = async () => {
    setBusy(true);
    try {
      const r = await ci.check(pid);
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
  usePageKeys({ c: busy ? null : () => void check() });
  const all = runs.data ?? [];
  const words = q.trim().toLowerCase();
  const list = all.filter(
    (r) =>
      (show === "all" ||
        (show === "failed" ? r.failed : r.status !== "completed")) &&
      (!words ||
        `${r.workflow} ${r.title} ${r.branch} ${r.sha}`
          .toLowerCase()
          .includes(words)),
  );
  const st = ciStats(all);
  return (
    <section className="rdp ci" aria-label="Pipelines">
      <div className="rdp-head">
        <p className="sub">
          The project's CI runs on GitHub Actions. keel looks every 2 minutes
          and tells you when one fails.
        </p>
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={() => void check()}
        >
          {busy && <span className="rdp-dot run" aria-hidden="true" />}
          Check now <Key k="C" />
        </button>
      </div>
      {runs.error ? (
        <ErrorBox error={runs.error} onRetry={() => void runs.reload()} />
      ) : !runs.data ? (
        <div className="rdp-card rdp-loading" role="status">
          <span className="sub">Reading the pipelines…</span>
          <span className="rdp-shim" style={{ width: "92%" }} />
          <span className="rdp-shim" style={{ width: "76%" }} />
          <span className="rdp-shim" style={{ width: "84%" }} />
        </div>
      ) : !runs.data.length ? (
        <div className="rdp-card rdp-empty">
          <b>No pipeline ran yet.</b>
          <span>
            Add a workflow in .github/workflows and push: keel shows its runs
            here.
          </span>
        </div>
      ) : (
        <>
          <div className="rdp-stats rdp-rise">
            <div className="rdp-card rdp-stat">
              <span className="sub">main</span>
              <b className={st.main ? `t-${runTone(st.main)}` : undefined}>
                {st.main ? resultText(st.main) : "—"}
              </b>
              <span className="sub">
                {st.main
                  ? `${st.main.workflow} · ${clock(st.main.updated_at, false)}`
                  : "no run on main in this list"}
              </span>
            </div>
            <div className="rdp-card rdp-stat">
              <span className="sub">Failed in the last 7 days</span>
              <b className={st.failed ? "t-bad" : undefined}>{st.failed}</b>
              <span className="sub">
                {st.failedOn.length
                  ? `on ${st.failedOn.join(", ")}`
                  : "nothing failed"}
              </span>
            </div>
            <div className="rdp-card rdp-stat">
              <span className="sub">Usual time</span>
              <b>{st.usual ?? "—"}</b>
              <span className="sub">
                {st.usual
                  ? `the middle of the last ${st.timed} run${st.timed === 1 ? "" : "s"}`
                  : "no finished run yet"}
              </span>
            </div>
          </div>
          <div className="rdp-bar">
            <div className="rdp-seg" role="group" aria-label="Show">
              {SHOW.map(([k, l]) => (
                <button
                  key={k}
                  type="button"
                  aria-pressed={show === k}
                  onClick={() => setShow(k)}
                >
                  {l}
                </button>
              ))}
            </div>
            <span className="grow" />
            <input
              type="search"
              className="rdp-inp"
              aria-label="Search runs"
              placeholder="Branch, workflow or commit"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          <div className="rdp-card rdp-table-card table-wrap">
            <table className="ci-table rdp-table" aria-label="Pipeline runs">
              <thead>
                <tr>
                  <th>Result</th>
                  <th>Workflow</th>
                  <th>Branch</th>
                  <th>Commit</th>
                  <th>When</th>
                  <th>
                    <span className="sr-only">Open</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.map((r) => (
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
            {list.length === 0 && (
              <div className="rdp-empty">
                <b>No run matches.</b>
                <span>Clear the search, or pick All.</span>
              </div>
            )}
          </div>
          <p className="sub rdp-foot">
            When a run fails keel tells you in the Inbox. Change it in{" "}
            <a href={settingsHash("general")}>Settings › When CI fails ›</a>
          </p>
        </>
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
      <tr className={`rdp-row${open ? " on" : ""}`} onClick={onToggle}>
        <td>
          <Pill tone={runTone(r)}>{resultText(r)}</Pill>
        </td>
        <td className="rdp-ell">
          <b>{r.workflow}</b>
          <span className="sub"> · {r.title}</span>
        </td>
        <td className="mono rdp-ell">{r.branch}</td>
        <td className="mono sub">{r.sha.slice(0, 7)}</td>
        <td className="sub">{clock(r.updated_at, false)}</td>
        <td className="rdp-end">
          <button
            type="button"
            className="rdp-chev"
            aria-expanded={open}
            onClick={(e) => {
              e.stopPropagation();
              onToggle();
            }}
            aria-label={`Open run #${r.id}`}
          >
            <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
              <path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
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
  const run = useLoad(`ci-run:${pid}:${id}`, () => ci.run(pid, id), {
    live: false,
  });
  const { toast } = useApp();
  // KeelBot is a plugin CI/CD does not need: "Ask KeelBot why" only when its page is there
  const keelbot = usePages().some((p) => p.id === "helper");
  const [busy, setBusy] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(
    null,
  );
  const r = run.data;
  const fix = async () => {
    setBusy(true);
    setErr(null);
    try {
      const t = await ci.fix(pid, id);
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
    setRerunning(true);
    try {
      await ci.rerun(pid, id);
      toast(`The failed jobs of run #${id} run again.`);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
      setRerunning(false);
    }
  };
  usePageKeys({
    f: r?.failed && !busy ? () => void fix() : null,
    r: r?.failed && !busy ? () => void rerun() : null,
  });
  if (run.error) return <ErrorBox error={run.error} />;
  if (!r)
    return (
      <div className="rdp-loading" role="status">
        <span className="sub">Reading the run…</span>
        <span className="rdp-shim" style={{ width: "70%" }} />
      </div>
    );
  const failedJobs = (r.jobs ?? []).filter(
    (j) =>
      j.conclusion && j.conclusion !== "success" && j.conclusion !== "skipped",
  );
  return (
    <div className="ci-run rdp-detail" role="group" aria-label={`Run #${id}`}>
      {r.failed && failedJobs.length > 0 && (
        <div className="rdp-note bad">
          <span className="rdp-lbl">Why it failed</span>
          <span>
            {failedJobs
              .map(
                (j) =>
                  `${j.name}${j.failed_steps.length ? ` at ${j.failed_steps.join(", ")}` : ""}`,
              )
              .join("; ")}
          </span>
        </div>
      )}
      {!r.failed && r.status === "completed" && r.conclusion === "success" && (
        <div className="rdp-note ok">
          <span>Every job passed.</span>
        </div>
      )}
      <ul className="ci-jobs" aria-label="Jobs">
        {(r.jobs ?? []).map((j) => (
          <li key={j.id}>
            <span
              className={`rdp-dot ${j.conclusion === "success" ? "ok" : j.conclusion ? "bad" : "run"}`}
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
        <pre className="ci-log rdp-term" aria-label="The failed steps' log">
          {r.log}
        </pre>
      )}
      {rerunning && (
        <p className="sub" role="status">
          <span className="rdp-dot run" aria-hidden="true" /> Working… the
          failed jobs of run #{id} run again
        </p>
      )}
      {err && <ErrorBox error={err} />}
      <div className="rdp-acts">
        {r.failed && (
          <button
            type="button"
            className="btn sm primary"
            disabled={busy}
            onClick={() => void fix()}
          >
            Fix it <Key k="F" />
          </button>
        )}
        {r.failed && (
          <button
            type="button"
            className="btn sm"
            disabled={busy}
            onClick={() => void rerun()}
          >
            Run the failed jobs again <Key k="R" />
          </button>
        )}
        {r.failed && keelbot && (
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
          Open on GitHub <span aria-hidden="true">↗</span>
        </a>
      </div>
      {r.failed && (
        <p className="sub">
          Fix it starts the ci-fix flow: it reads the failure, fixes, commits,
          pushes and waits for CI. It stops at a gate for you.
        </p>
      )}
    </div>
  );
}
