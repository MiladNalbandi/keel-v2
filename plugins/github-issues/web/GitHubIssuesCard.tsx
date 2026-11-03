// Settings › Connections › GitHub Issues (issue #64): one card per project. Which repo (the project's origin by
// default), which open issues become tasks (assigned to you, with a label, or all), how often; what keel does to the
// issue as the task moves (nothing, close, close as not planned, reopen, or a status label); comments; "Closes #N" in
// keel's pull request; the GitHub reviewers. The token is the one of Connections › GitHub: this card never asks for one.

import { useEffect, useState } from "react";
import { agoText, EmptyState, ErrorBox, errorParts, Note, Seg, Tile, TonePill, useApp, useLoad } from "@keel/web-sdk";
import {
  ghIssuesApi, STATUS_LABEL, TASK_STATUSES, type Follow, type GhIssuesSave, type GhIssuesTest, type GhIssuesView, type TaskStatus,
} from "./ghIssuesApi";

const list = (s: string) => s.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);

/** What keel can do to an issue, as the status map's select offers it ("label" asks for the label's name). */
const ACTIONS: [string, string][] = [
  ["-", "Leave the issue"],
  ["closed", "Close"],
  ["closed:not planned", "Close as not planned"],
  ["open", "Reopen"],
  ["label", "Status label…"],
];
const actionOf = (v: string) => (v.toLowerCase().startsWith("label:") ? "label" : v || "-");
/** The label of a "label:<name>" value as typed (spaces kept while typing; save trims it). */
const labelOf = (v: string) => (v.toLowerCase().startsWith("label:") ? v.slice(6) : "");
/** The map's value as saved: "label:<name>" trimmed, an empty label = leave the issue. */
const saved = (v: string | undefined) => {
  if (!v) return "-";
  if (actionOf(v) !== "label") return v;
  const l = labelOf(v).trim();
  return l ? `label:${l}` : "-";
};

function StatusMap({ pid, map, labels, onChange }: {
  pid: string;
  map: Record<TaskStatus, string>;
  labels: string[] | null;
  onChange: (m: Record<TaskStatus, string>) => void;
}) {
  const set = (s: TaskStatus, v: string) => onChange({ ...map, [s]: v });
  return (
    <div className="ghi-map" role="group" aria-label="What keel does to the issue" style={{ display: "grid", gridTemplateColumns: "minmax(110px, auto) 1fr", gap: "8px 14px", alignItems: "center" }}>
      {labels && <datalist id={`ghi-labels-${pid}`}>{labels.map((l) => <option key={l} value={l} />)}</datalist>}
      {TASK_STATUSES.map((s) => {
        const a = actionOf(map[s] ?? "-");
        return (
          <div key={s} style={{ display: "contents" }}>
            <label className="ss-label" htmlFor={`ghi-${s}-${pid}`} style={{ margin: 0 }}>{STATUS_LABEL[s]}</label>
            <span style={{ display: "flex", gap: 8, flexWrap: "wrap", minWidth: 0 }}>
              <select id={`ghi-${s}-${pid}`} className="ss-input" aria-label={`GitHub issue for ${STATUS_LABEL[s]}`} value={a}
                onChange={(e) => set(s, e.target.value === "label" ? `label:${labelOf(map[s] ?? "").trim() || STATUS_LABEL[s].toLowerCase()}` : e.target.value)}
                style={{ flex: "1 1 160px", minWidth: 0 }}>
                {ACTIONS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
              </select>
              {a === "label" && (
                <input type="text" className="ss-input mono" aria-label={`Label for ${STATUS_LABEL[s]}`} value={labelOf(map[s] ?? "")}
                  list={labels ? `ghi-labels-${pid}` : undefined} onChange={(e) => set(s, `label:${e.target.value}`)} style={{ flex: "1 1 140px", minWidth: 0 }} />
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function GitHubIssuesForm({ pid, view, onSaved, onRemoved }: {
  pid: string;
  view: GhIssuesView;
  onSaved: (v: GhIssuesView) => void;
  onRemoved: () => void;
}) {
  const { toast } = useApp();
  const s = view.settings;
  const [repo, setRepo] = useState(s.repo ?? "");
  const [follow, setFollow] = useState<Follow>(s.follow);
  const [label, setLabel] = useState(s.label ?? "");
  const [poll, setPoll] = useState(String(s.poll_minutes ?? 5));
  const [comment, setComment] = useState(s.comment);
  const [closes, setCloses] = useState(s.closes_on_merge);
  const [reviewers, setReviewers] = useState(s.github_reviewers.join(", "));
  const [map, setMap] = useState<Record<TaskStatus, string>>(view.status_map);
  const [labels, setLabels] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [test, setTest] = useState<GhIssuesTest | null>(null);
  const [removing, setRemoving] = useState(false);
  const id = (x: string) => `ghi-${x}-${pid}`;

  // the repo's labels for the status labels (best effort: without them the label is typed)
  useEffect(() => {
    if (!view.token_set || !view.repo) return;
    let gone = false;
    ghIssuesApi.labels(pid).then((l) => { if (!gone) setLabels(l); }, () => {});
    return () => { gone = true; };
  }, [pid, view.token_set, view.repo]);

  const body = (): GhIssuesSave => ({
    repo: repo.trim(), follow, label: follow === "label" ? label.trim() : "", poll_minutes: Number(poll) || 0, comment, closes_on_merge: closes,
    github_reviewers: list(reviewers),
    status_map: Object.fromEntries(TASK_STATUSES.map((st) => [st, saved(map[st])])) as Record<TaskStatus, string>,
  });
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const v = await ghIssuesApi.save(pid, body());
      onSaved(v);
      toast(view.connected ? "GitHub Issues saved." : `Connected to ${v.repo ?? "GitHub"}. Sync now on the Tasks page brings the issues in.`);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const runTest = async () => {
    setBusy(true);
    setTest(null);
    try {
      setTest(await ghIssuesApi.test(pid, { repo: repo.trim() }));
    } catch (e) {
      const p = errorParts(e);
      setTest({ ok: false, error: p.message, hint: p.hint });
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    try {
      await ghIssuesApi.remove(pid);
      toast("GitHub Issues removed. Tasks stay, with their history.");
      onRemoved();
    } catch (e) {
      setErr(errorParts(e));
    }
  };
  const blocked = !view.token_set || !!view.other_tracker;

  return (
    <div className="ghi-form" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {!view.token_set && (
        <Note tone="warn">keel has no GitHub token yet. Sign in to GitHub in Settings › Connections › GitHub first: GitHub Issues uses that token.</Note>
      )}
      {view.other_tracker && (
        <Note tone="warn">This project follows {view.other_tracker}. A project follows one tracker: remove its {view.other_tracker} connection first.</Note>
      )}
      <div className="ss-fields" style={{ alignItems: "flex-start" }}>
        <div className="ss-label" style={{ flex: "2 1 240px" }}>
          <label htmlFor={id("repo")}>Repository</label>
          <input type="text" className="ss-input mono" id={id("repo")} value={repo} onChange={(e) => setRepo(e.target.value)}
            placeholder={view.origin ?? "owner/repo"} autoComplete="off" />
          <span className="ss-help">{view.origin ? `Empty: this project's origin, ${view.origin}.` : `This project's origin is not on ${view.host}: name the repository.`}</span>
        </div>
        <div className="ss-label" style={{ flex: "1 1 140px" }}>
          <label htmlFor={id("poll")}>Sync every (minutes)</label>
          <input type="text" className="ss-input mono" id={id("poll")} inputMode="numeric" value={poll} onChange={(e) => setPoll(e.target.value)} />
          <span className="ss-help">0 = only when you press Sync now.</span>
        </div>
      </div>
      <Seg label="Which issues" value={follow} options={[["assigned", "Assigned to me"], ["label", "With a label"], ["all", "All open issues"]]} onChange={setFollow} />
      {follow === "label" && (
        <label className="ss-label" htmlFor={id("label")}>Label
          <input type="text" className="ss-input mono" id={id("label")} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="keel"
            list={labels ? `ghi-labels-${pid}` : undefined} />
        </label>
      )}
      <section aria-label="What keel does to the issue" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <h3 className="ss-h3" style={{ margin: 0 }}>When a task moves</h3>
        <p className="ss-meta" style={{ margin: 0 }}>What keel does to the real issue. A status label is added, and keel's other status labels are taken off.</p>
        <StatusMap pid={pid} map={map} labels={labels} onChange={setMap} />
        <label className="ss-check" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={closes} onChange={(e) => setCloses(e.target.checked)} />
          keel's pull request says “Closes #N”, so merging it closes the issue
        </label>
        <label className="ss-check" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={comment} onChange={(e) => setComment(e.target.checked)} />
          Comment on the issue as the task moves (the flow, the pull request, the review)
        </label>
      </section>
      <label className="ss-label" htmlFor={id("reviewers")}>GitHub reviewers (optional)
        <input type="text" className="ss-input mono" id={id("reviewers")} value={reviewers} onChange={(e) => setReviewers(e.target.value)} placeholder="ana, org/team" />
        <span className="ss-help">Asked on keel's pull request when the task names none.</span>
      </label>
      {test && (
        <Note tone={test.ok ? "ok" : "err"} role="status">
          {test.ok ? `Test OK: ${test.repo} as ${test.user ?? "?"}.${test.read_only ? " The token may only read: keel cannot close or label the issues." : ""}`
            : `Test failed: ${test.error ?? "no answer"}`}
          {!test.ok && test.hint && <span> {test.hint}</span>}
        </Note>
      )}
      {err && <ErrorBox error={err} />}
      <div className="ss-actions">
        <button className="ss-btn sm" type="button" onClick={runTest} disabled={busy || !view.token_set}>Test</button>
        <button className="ss-btn sm p" type="button" onClick={save} disabled={busy || blocked}>{busy ? "Working…" : view.connected ? "Save" : "Connect"}</button>
        {view.connected && <button className="ss-btn sm q" type="button" onClick={() => setRemoving(true)}>Remove</button>}
      </div>
      {removing && (
        <div className="ss-confirm" role="group" aria-label="Confirm">
          <span>Remove GitHub Issues from this project? Tasks stay in keel; the issues stay on GitHub.</span>
          <button className="ss-btn sm dp" type="button" onClick={() => void remove()}>Remove</button>
          <button className="ss-btn sm" type="button" onClick={() => setRemoving(false)}>Keep</button>
        </div>
      )}
    </div>
  );
}

function GitHubIssuesCard({ pid, name, current }: { pid: string; name: string; current: boolean }) {
  const r = useLoad(`github-issues:${pid}`, () => ghIssuesApi.view(pid));
  const v = r.data;
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (v && current && !v.connected && !v.other_tracker) setOpen(true);
  }, [v?.connected, current]);
  const head = `ghi-${pid}`;
  return (
    <article className="ss-list ghi-card" aria-labelledby={head} data-testid={`github-issues-${pid}`}>
      <div className="ss-row0">
        <Tile>GH</Tile>
        <span className="ss-grow">
          <b id={head} style={{ fontWeight: 600 }}>{name}</b>
          <span className="ss-meta">
            {v?.connected ? `GitHub Issues · ${v.repo ?? "no repository"}` : v?.other_tracker ? `Follows ${v.other_tracker}.` : "GitHub Issues is not connected: tasks stay local."}
          </span>
          {v?.connected && (
            <span className="ss-meta ss-small">{v.last_sync_at ? `synced ${agoText(v.last_sync_at)}` : "not synced yet"}{v.me ? ` · as ${v.me}` : ""}</span>
          )}
          {v?.last_sync_error && <span className="ss-small" style={{ color: "var(--bad)" }}>{v.last_sync_error}</span>}
        </span>
        {!v ? <span className="ss-meta">Checking…</span> : v.connected ? <TonePill tone="ok">connected</TonePill> : <TonePill tone="off">not connected</TonePill>}
        <button className={`ss-btn sm${v && !v.connected && !open ? " p" : ""}`} type="button" aria-expanded={open} aria-controls={`${head}-body`}
          onClick={() => setOpen((o) => !o)}>
          {open ? "Close" : v?.connected ? "Edit" : "Set up GitHub Issues"}
        </button>
      </div>
      {r.error && <div className="ss-row"><ErrorBox error={r.error} onRetry={() => void r.reload()} /></div>}
      {open && v && (
        <div className="ghi-body" id={`${head}-body`}
          style={{ display: "flex", flexDirection: "column", gap: 14, padding: "16px 20px 20px", borderTop: "1px solid var(--border)", background: "var(--bg)" }}>
          <GitHubIssuesForm key={String(v.connected)} pid={pid} view={v} onSaved={(n) => r.setData(n)} onRemoved={() => void r.reload()} />
        </div>
      )}
    </article>
  );
}

/** The GitHub Issues section of Connections: the current project's card first. */
export function GitHubIssuesSection() {
  const { projects, pid } = useApp();
  const ordered = [...projects].sort((a, b) => (a.id === pid ? -1 : b.id === pid ? 1 : 0));
  return (
    <section className="ss-sec" aria-labelledby="c-github-issues" data-testid="github-issues">
      <div>
        <h2 id="c-github-issues" className="ss-h2 ss-title-row">GitHub Issues <span className="ss-tag run">per project</span></h2>
        <p className="ss-sub">Per project: the repository whose issues become tasks, and what keel does to an issue as its task moves. Uses keel's GitHub sign-in.</p>
      </div>
      {ordered.length ? ordered.map((p) => <GitHubIssuesCard key={p.id} pid={p.id} name={p.name} current={p.id === pid} />)
        : <div className="ss-card"><EmptyState title="No project yet">Add a repo on All projects, then connect its GitHub issues here.</EmptyState></div>}
    </section>
  );
}
