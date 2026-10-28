// The new Review view's pull request (0.18.57, the redesign's Work › Review), in an editor tab: its head (state, who
// wants to merge what, the size, the checks, keel's overview folded in), and its tabs Comments · Files · Problems ·
// Commits · Checklist (and the tabs another part registers: ./tabs). Submit review opens a drawer.
//
//   ┌ #42 Add refunds to orders                       Open on GitHub ↗  [Submit review (2)] ┐
//   │ ● open  ana wants to merge feature/refunds into main  +184 −22 · 6 files  checks pass │
//   │ ▸ What this change does (keel's overview)                                              │
//   └────────────────────────────────────────────────────────────────────────────────────────┘
//   Comments 3 │ Files 6 │ Problems 1 │ Commits 4 │ Checklist
//
// It reads the same store as the classic view (R.load, R.loadAi, polling while keel works), so a comment or a
// pending comment made in one view shows in the other.

import { useEffect, useId, useState } from "react";
import { ErrorBox, useApp, useStored } from "@keel/web-sdk";
import { BaseLine } from "../BaseLine";
import { ChecksList } from "../Checks";
import { prLabel } from "../reviewApi";
import * as R from "../store";
import { ago, waitsForYou } from "../Threads";
import { CommentsTab } from "./Comments";
import { FilesTab } from "./Files";
import {
  CHECKLIST,
  checksOf,
  openProblems,
  same,
  stateOf,
  threadsOf,
} from "./model";
import { SENT } from "./NextSide";
import { ProblemsTab } from "./Problems";
import { SubmitDrawer } from "./Submit";
import { useReviewTabs, type ReviewTabProps } from "./tabs";
import { Tag } from "./ui";

type Tab = {
  id: string;
  label: string;
  order: number;
  n: number | null;
  warn?: boolean;
};
const isText = (v: unknown): v is string => typeof v === "string";
const isIds = (v: unknown): v is number[] =>
  Array.isArray(v) && v.every((x) => typeof x === "number");
const isSent = (v: unknown): v is Record<string, string> =>
  !!v &&
  typeof v === "object" &&
  Object.values(v as object).every((x) => typeof x === "string");

export function NextReview({ pid, reviewKey }: { pid: string; reviewKey: string }) {
  R.useReviewStore();
  const { tick, toast } = useApp();
  const r = R.review(pid, reviewKey);
  const [tab, setTab] = useStored<string>(
    `keel2.review.next.tab.${pid}.${reviewKey}`,
    "comments",
    { valid: isText },
  );
  const [submitting, setSubmitting] = useState(false);
  const [threadFocus, setThreadFocus] = useState<{
    id: string;
    n: number;
  } | null>(null);
  const [fileFocus, setFileFocus] = useState<{
    path: string;
    line: number | null;
    n: number;
  } | null>(null);
  const [, setSent] = useStored<Record<string, string>>(
    SENT(pid),
    {},
    { valid: isSent },
  );
  const extra = useReviewTabs();
  const uid = useId();

  useEffect(() => {
    if (!r.view && !r.loading && !r.error) void R.load(pid, reviewKey);
  }, [pid, reviewKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    void R.loadAi(pid, reviewKey);
  }, [pid, reviewKey, tick]);
  const ai = r.ai;
  const working =
    ai?.overview?.status === "running" ||
    ai?.findings?.status === "running" ||
    Object.values(ai?.judges ?? {}).some((j) => j.status === "running") ||
    Object.values(ai?.buttons ?? {}).some((j) => j.status === "running");
  useEffect(() => {
    if (!working) return;
    const t = window.setInterval(() => void R.loadAi(pid, reviewKey), 3000);
    return () => window.clearInterval(t);
  }, [working, pid, reviewKey]);

  const v = r.view;
  if (r.error && !v)
    return (
      <div className="rvn-review rvn-pad">
        <ErrorBox error={r.error} onRetry={() => void R.load(pid, reviewKey)} />
      </div>
    );
  if (!v)
    return (
      <div className="rvn-review rvn-pad">
        <span className="pg-spin" role="status">
          Opening the review…
        </span>
      </div>
    );

  const label = v.kind === "pr" ? prLabel(v.host, v.number) : v.branch;
  const you = !!v.mine || same(v.author, v.me);
  const where = v.host?.kind === "gitlab" ? "GitLab" : "GitHub";
  const [st, stTone] = stateOf(v);
  const checks = checksOf(v);
  const pendingN = v.drafts.length + v.threads.filter((t) => t.pending).length;
  const waiting = v.threads.filter((t) => waitsForYou(t, v.me)).length;
  const problems = openProblems(ai);
  const ctx: ReviewTabProps = {
    pid,
    reviewKey,
    view: v,
    ai,
    onOpenFile: (path, line) => showInFile(path, line ?? null),
  };
  const tabs: Tab[] = [
    {
      id: "comments",
      label: "Comments",
      order: 10,
      n: threadsOf(v, "open").length || null,
      warn: waiting > 0,
    },
    { id: "files", label: "Files", order: 20, n: v.files.length },
    {
      id: "problems",
      label: "Problems",
      order: 30,
      n: ai?.findings?.status === "done" ? problems.open || null : null,
      warn: problems.blocking > 0,
    },
    { id: "commits", label: "Commits", order: 40, n: v.commits.length || null },
    { id: "checklist", label: "Checklist", order: 50, n: null },
    ...extra
      .filter((t) => !t.show || t.show(ctx))
      .map((t) => ({
        id: `x:${t.id}`,
        label: t.label,
        order: t.order ?? 60,
        n: t.count?.(ctx) ?? null,
      })),
  ].sort((a, b) => a.order - b.order);
  const current = tabs.some((t) => t.id === tab) ? tab : "comments";
  const ext = current.startsWith("x:")
    ? extra.find((t) => `x:${t.id}` === current)
    : null;

  function showInFile(path: string, line: number | null) {
    setFileFocus({ path, line, n: Date.now() });
    setTab("files");
  }
  const showThread = (id: string) => {
    setThreadFocus({ id, n: Date.now() });
    setTab("comments");
  };
  const o = ai?.overview;
  const ov = o?.status === "done" ? o.result : null;

  return (
    <div className="rvn-review" role="region" aria-label={`Review of ${label}`}>
      <div className="rvn-review-in">
        <header className="rvn-prhead">
          <div className="rvn-prhead-1">
            <h2>
              {v.kind === "pr" && <span className="rvn-dim">{label}</span>}{" "}
              {v.title}
            </h2>
            <span className="rvn-grow" />
            {v.url && (
              <a
                className="rvn-link"
                href={v.url}
                target="_blank"
                rel="noreferrer"
              >
                Open on {where} ↗
              </a>
            )}
            {v.can_post && (
              <button
                type="button"
                className="rvn-btn primary"
                onClick={() => setSubmitting(true)}
              >
                Submit review{pendingN ? ` (${pendingN})` : ""}
              </button>
            )}
          </div>
          <div className="rvn-prmeta">
            <Tag tone={stTone}>{st}</Tag>
            <span>
              {v.kind === "pr"
                ? `${you ? "You want" : `${v.author} wants`} to merge`
                : "Branch"}
            </span>
            <span className="rvn-mono rvn-fg">{v.branch}</span>
            <span>into</span>
            <span className="rvn-mono rvn-fg">{v.base}</span>
            <span>
              <span className="rvn-add">+{v.added}</span>{" "}
              <span className="rvn-del">−{v.removed}</span> · {v.files.length}{" "}
              file{v.files.length === 1 ? "" : "s"}
            </span>
            {checks && (
              <ChecksList
                checks={v.checks}
                where={where}
                summary={
                  <span className={`rvn-tone t-${checks[1]}`}>{checks[0]}</span>
                }
              />
            )}
            {v.approved.length > 0 && (
              <span>approved by {v.approved.join(", ")}</span>
            )}
            {v.changes_requested.length > 0 && (
              <span>changes asked by {v.changes_requested.join(", ")}</span>
            )}
          </div>
          <BaseLine pid={pid} view={v} className="rvn-base" />
          <details className="rvn-overview">
            <summary>What this change does (keel's overview)</summary>
            <div className="rvn-stack">
              {!o && (
                <>
                  <span className="rvn-dim">
                    keel reads the change and says what it does, file by file,
                    and which files to open first.
                  </span>
                  <button
                    type="button"
                    className="rvn-btn"
                    disabled={r.running.includes("overview")}
                    onClick={() => void R.startAi(pid, reviewKey, "overview")}
                  >
                    Explain this change
                  </button>
                </>
              )}
              {o?.status === "running" && (
                <span className="pg-spin">keel reads the change…</span>
              )}
              {o?.status === "failed" && (
                <ErrorBox
                  error={{ message: o.error ?? "The overview failed" }}
                />
              )}
              {ov && (
                <>
                  <p>{ov.summary}</p>
                  {ov.order.length > 0 && (
                    <p className="rvn-dim">
                      Open first:{" "}
                      {ov.order.slice(0, 3).map((p, i) => (
                        <span key={p}>
                          {i > 0 && ", then "}
                          <button
                            type="button"
                            className="rvn-link rvn-mono"
                            onClick={() => showInFile(p, null)}
                          >
                            {p}
                          </button>
                        </span>
                      ))}
                      {ov.risk &&
                        ` · risk ${ov.risk}${ov.risk_why ? `: ${ov.risk_why}` : ""}`}
                    </p>
                  )}
                  {o?.stale && (
                    <p className="rvn-dim">
                      The code changed since keel read it.
                    </p>
                  )}
                </>
              )}
            </div>
          </details>
          {r.err && <ErrorBox error={r.err} />}
        </header>

        <div className="rvn-tabs" role="tablist" aria-label={`${label} tabs`}>
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`${uid}-tab-${t.id}`}
              aria-selected={current === t.id}
              aria-controls={`${uid}-panel`}
              tabIndex={current === t.id ? 0 : -1}
              onClick={() => setTab(t.id)}
              onKeyDown={(e) => {
                const i = tabs.findIndex((x) => x.id === t.id);
                const to =
                  e.key === "ArrowRight"
                    ? i + 1
                    : e.key === "ArrowLeft"
                      ? i - 1
                      : null;
                if (to === null) return;
                e.preventDefault();
                const n = tabs[(to + tabs.length) % tabs.length];
                setTab(n.id);
                window.setTimeout(
                  () => document.getElementById(`${uid}-tab-${n.id}`)?.focus(),
                  0,
                );
              }}
            >
              {t.label}
              {t.n != null && t.n > 0 && (
                <span className={`rvn-n${t.warn ? " warn" : ""}`}>{t.n}</span>
              )}
            </button>
          ))}
        </div>

        <div
          id={`${uid}-panel`}
          className="rvn-panel"
          role="tabpanel"
          aria-labelledby={`${uid}-tab-${current}`}
        >
          {current === "comments" && (
            <CommentsTab
              pid={pid}
              reviewKey={reviewKey}
              view={v}
              ai={ai}
              running={r.running}
              focus={threadFocus}
              onShowInFile={showInFile}
            />
          )}
          {current === "files" && (
            <FilesTab
              pid={pid}
              reviewKey={reviewKey}
              view={v}
              ai={ai}
              running={r.running}
              focus={fileFocus}
              onOpenThread={showThread}
              onOpenEditor={(path, line) =>
                R.openPlace(
                  { key: reviewKey, path, view: "diff", line, side: "RIGHT" },
                  { pin: true },
                )
              }
            />
          )}
          {current === "problems" && (
            <ProblemsTab
              pid={pid}
              reviewKey={reviewKey}
              view={v}
              ai={ai}
              running={r.running}
              onShowInFile={showInFile}
            />
          )}
          {current === "commits" &&
            (v.commits.length ? (
              <ul className="rvn-commits" aria-label="Commits">
                {v.commits.map((c) => (
                  <li key={c.sha}>
                    <span className="rvn-mono rvn-dim">{c.sha.slice(0, 7)}</span>
                    <span className="rvn-grow">{c.message.split("\n")[0]}</span>
                    <span className="rvn-dim">
                      {c.author}
                      {c.at ? ` · ${ago(c.at) || c.at}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="rvn-empty">No commits.</p>
            ))}
          {current === "checklist" && (
            <Checklist pid={pid} reviewKey={reviewKey} />
          )}
          {ext && <ext.component {...ctx} />}
        </div>
      </div>
      {submitting && (
        <SubmitDrawer
          pid={pid}
          reviewKey={reviewKey}
          onClose={() => setSubmitting(false)}
          onDone={(res) => {
            setSubmitting(false);
            const word =
              res.event === "APPROVE"
                ? "you approved"
                : res.event === "REQUEST_CHANGES"
                  ? "you asked for changes"
                  : "you commented";
            setSent((s) => ({ ...s, [reviewKey]: word }));
            toast(
              `Review sent to ${where}: ${word} on ${label}` +
                (res.posted
                  ? ` with ${res.posted} line comment${res.posted === 1 ? "" : "s"}.`
                  : "."),
            );
          }}
        />
      )}
    </div>
  );
}

/** A short path through a good review; the ticks are only for you (this browser). */
function Checklist({ pid, reviewKey }: { pid: string; reviewKey: string }) {
  const [done, setDone] = useStored<number[]>(
    `keel2.review.next.check.${pid}.${reviewKey}`,
    [],
    {
      store: "local",
      valid: isIds,
    },
  );
  return (
    <div className="rvn-stack">
      <p className="rvn-dim rvn-lead">
        A short path through a good review. Tick what you did; it is only for
        you.
      </p>
      <ol className="rvn-check">
        {CHECKLIST.map(([title, text], i) => {
          const on = done.includes(i);
          return (
            <li key={title}>
              <label className={on ? "on" : undefined}>
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() =>
                    setDone((d) =>
                      d.includes(i) ? d.filter((x) => x !== i) : [...d, i],
                    )
                  }
                />
                <span>
                  <b>{title}</b>
                  <br />
                  <span className="rvn-dim">{text}</span>
                </span>
              </label>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
