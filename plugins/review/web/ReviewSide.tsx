// v0.14.0 Code › Review (the Code Review plugin's tool window, ⇧⌘9), like JetBrains' Pull Requests / Merge Requests
// window: first the branch you are on and the list (to review, assigned, mine, all); a pull request then opens here
// with its actions (Approve, Submit, Merge your own, Check out) and its tabs (Changes as a folder tree, Commits,
// keel's Overview and Findings, Threads, the Checklist). Each file opens as an editor tab.

import { useEffect, useState } from "react";
import { askAssistant, ErrorBox, Pill, useApp, useLoad } from "@keel/web-sdk";
import {
  prLabel,
  prWord,
  reviewApi,
  type Finding,
  type PrSummary,
  type ReviewView,
} from "./reviewApi";
import { FileTree, treeOrder } from "./FileTree";
import { keyLabel, readKeymap } from "./keymap";
import {
  ChecklistTab,
  findingComment,
  FindingsTab,
  OverviewTab,
  ThreadsTab,
  type PanelTab,
} from "./ReviewPanel";
import * as R from "./store";

type Filter = "review" | "assigned" | "mine" | "all";
const FILTERS: [Filter, string][] = [
  ["review", "To review"],
  ["assigned", "Assigned"],
  ["mine", "Mine"],
  ["all", "All"],
];
const SEL = (pid: string) => `keel2.review.sel.${pid}`;
const OPEN_EVENT = "keel:open-review";

/** v0.15.0 open one review in the Review tool window (the launcher, a #/repo/@review/pr:7 link). */
export function openReview(pid: string, key: string) {
  try {
    sessionStorage.setItem(SEL(pid), key);
  } catch {
    /* private window */
  }
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: { pid, key } }));
}

export function ReviewSide({
  pid,
  activeFile,
}: {
  pid: string;
  activeFile: { key: string; path: string } | null;
}) {
  const [selected, setSelected] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(SEL(pid));
    } catch {
      return null;
    }
  });
  const pick = (key: string | null) => {
    setSelected(key);
    try {
      if (key) sessionStorage.setItem(SEL(pid), key);
      else sessionStorage.removeItem(SEL(pid));
    } catch {
      /* private window */
    }
  };
  useEffect(() => {
    if (activeFile && activeFile.key !== selected) pick(activeFile.key);
  }, [activeFile?.key]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<{ pid: string; key: string }>).detail;
      if (d?.pid === pid && d.key) setSelected(d.key);
    };
    window.addEventListener(OPEN_EVENT, on);
    return () => window.removeEventListener(OPEN_EVENT, on);
  }, [pid]);
  return (
    <div className="rv-side" role="region" aria-label="Review">
      {selected ? (
        <OneReview
          pid={pid}
          reviewKey={selected}
          activePath={activeFile?.key === selected ? activeFile.path : null}
          onBack={() => pick(null)}
        />
      ) : (
        <ReviewList pid={pid} onPick={pick} />
      )}
    </div>
  );
}

function ReviewList({
  pid,
  onPick,
}: {
  pid: string;
  onPick: (key: string) => void;
}) {
  const [filter, setFilter] = useState<Filter>("review");
  const branch = useLoad(`review-branch:${pid}`, () => reviewApi.branch(pid));
  const list = useLoad(`review-prs:${pid}:${filter}`, () =>
    reviewApi.prs(pid, filter),
  );
  const host = list.data?.host;
  const word = prWord(host);
  const b = branch.data;
  return (
    <>
      <div className="sv-head">
        <span>Review</span>
        <span className="sub">
          {readKeymap() === "intellij" ? keyLabel("shift+meta+9") : ""}
        </span>
      </div>
      <section className="rv-card" aria-label="This branch">
        <div className="rv-card-h">
          <b>This branch</b>
          {b?.branch && <span className="mono rv-dim">{b.branch}</span>}
        </div>
        {branch.error && <ErrorBox error={branch.error} />}
        {b && !b.branch && <span className="rv-dim">{b.note}</span>}
        {b?.branch &&
          (b.ahead > 0 || b.files > 0 ? (
            <>
              <span className="rv-dim">
                {b.ahead} commit{b.ahead === 1 ? "" : "s"} · {b.files} file
                {b.files === 1 ? "" : "s"} ·{" "}
                <span className="add">+{b.added}</span>{" "}
                <span className="del">−{b.removed}</span>
                {b.pr && (
                  <>
                    {" "}
                    · {word} {prLabel(host, b.pr.number)}
                  </>
                )}
              </span>
              <div className="rv-row">
                <button
                  type="button"
                  className="btn sm primary"
                  onClick={() =>
                    onPick(b.pr ? `pr:${b.pr.number}` : `branch:${b.branch}`)
                  }
                >
                  Review this branch
                </button>
                {b.pr && (
                  <button
                    type="button"
                    className="btn sm ghost"
                    onClick={() => onPick(`branch:${b.branch}`)}
                    title="Only the commits, without the threads"
                  >
                    Commits only
                  </button>
                )}
              </div>
            </>
          ) : (
            <span className="rv-dim">
              {b.note ??
                `Nothing to review: ${b.branch} has no commits that ${b.base ?? "the base"} does not have.`}
            </span>
          ))}
        {b?.note && b.branch && (b.ahead > 0 || b.files > 0) && (
          <span className="rv-dim">{b.note}</span>
        )}
      </section>
      <section
        className="rv-prs"
        aria-label={
          host?.kind === "gitlab" ? "Merge requests" : "Pull requests"
        }
      >
        <h3 className="br-h">
          {host?.kind === "gitlab" ? "Merge requests" : "Pull requests"}
          <button
            type="button"
            className="ib rv-refresh"
            aria-label="Refresh the list"
            title="Refresh"
            onClick={() => void list.reload()}
          >
            ↻
          </button>
        </h3>
        <div className="rv-chips" role="tablist" aria-label="Show">
          {FILTERS.map(([f, label]) => (
            <button
              key={f}
              type="button"
              role="tab"
              aria-selected={filter === f}
              className={`rv-chip${filter === f ? " on" : ""}`}
              onClick={() => setFilter(f)}
            >
              {label}
              {list.data?.counts?.[f] != null && (
                <span className="rv-n">{list.data.counts[f]}</span>
              )}
            </button>
          ))}
        </div>
        {list.error && (
          <ErrorBox error={list.error} onRetry={() => void list.reload()} />
        )}
        {list.data?.note && <p className="rv-dim rv-note">{list.data.note}</p>}
        {list.data && !list.data.note && list.data.prs.length === 0 && (
          <p className="rv-dim rv-note">
            {filter === "review"
              ? `No ${word} waits for your review.`
              : filter === "assigned"
                ? `No open ${word} is assigned to you.`
                : filter === "mine"
                  ? `You have no open ${word}.`
                  : `No open ${word}.`}
          </p>
        )}
        {list.data?.prs.map((p) => (
          <PrRow
            key={p.number}
            p={p}
            host={list.data!.host}
            onOpen={() => onPick(`pr:${p.number}`)}
          />
        ))}
        {!list.data && !list.error && (
          <span className="pg-spin rv-note" role="status">
            Reading the {word}s…
          </span>
        )}
      </section>
    </>
  );
}

function PrRow({
  p,
  host,
  onOpen,
}: {
  p: PrSummary;
  host: Parameters<typeof prLabel>[0];
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      className="rv-pr"
      onClick={onOpen}
      aria-label={`${prLabel(host, p.number)} ${p.title}`}
      title={`${p.branch} → ${p.base}`}
    >
      <span className="rv-pr-1">
        <span className="mono rv-dim">{prLabel(host, p.number)}</span>
        <b>{p.title}</b>
      </span>
      <span className="rv-dim rv-pr-2">
        {p.author}
        {p.draft && (
          <>
            {" "}
            · <Pill tone="idle">draft</Pill>
          </>
        )}
        {p.review_requested && (
          <>
            {" "}
            · <Pill tone="warn">review requested</Pill>
          </>
        )}
        {p.assigned && (
          <>
            {" "}
            · <Pill tone="run">assigned</Pill>
          </>
        )}
        <span className="mono"> · {p.branch}</span>
      </span>
    </button>
  );
}

function OneReview({
  pid,
  reviewKey,
  activePath,
  onBack,
}: {
  pid: string;
  reviewKey: string;
  activePath: string | null;
  onBack: () => void;
}) {
  R.useReviewStore();
  const { tick, toast } = useApp();
  const r = R.review(pid, reviewKey);
  const [tab, setTab] = useState<PanelTab>("changes");
  const [approving, setApproving] = useState(false);
  const [checked, setChecked] = useState<string[]>(() => {
    try {
      return JSON.parse(
        localStorage.getItem(`keel2.review.check.${reviewKey}`) ?? "[]",
      );
    } catch {
      return [];
    }
  });

  useEffect(() => {
    if (!r.view && !r.loading && !r.error) void R.load(pid, reviewKey);
  }, [pid, reviewKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    void R.loadAi(pid, reviewKey);
  }, [pid, reviewKey, tick]);
  const running =
    r.ai?.overview?.status === "running" ||
    r.ai?.findings?.status === "running";
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => void R.loadAi(pid, reviewKey), 3000);
    return () => window.clearInterval(t);
  }, [running, pid, reviewKey]);

  const v = r.view;
  const head = (
    <div className="sv-head rv-back">
      <button
        type="button"
        className="btn sm ghost"
        onClick={onBack}
        aria-label="Back to the list"
      >
        ← {v?.host?.kind === "gitlab" ? "Merge requests" : "Pull requests"}
      </button>
    </div>
  );
  if (r.error && !v)
    return (
      <>
        {head}
        <div className="rv-note">
          <ErrorBox
            error={r.error}
            onRetry={() => void R.load(pid, reviewKey)}
          />
        </div>
      </>
    );
  if (!v)
    return (
      <>
        {head}
        <span className="pg-spin rv-note" role="status">
          Opening the review…
        </span>
      </>
    );

  const open = (
    path: string,
    line?: number | null,
    side: "RIGHT" | "LEFT" = "RIGHT",
    pin = false,
  ) =>
    R.openPlace(
      {
        key: reviewKey,
        path,
        view: v.files.some((f) => f.path === path) ? "diff" : "code",
        line: line ?? null,
        side,
      },
      { pin },
    );
  const comments = (path: string) =>
    v.threads.filter((t) => t.path === path).length +
    v.drafts.filter((d) => d.path === path).length;
  const greens = v.checks.filter((c) =>
    ["success", "skipped", "neutral"].includes(c.state),
  ).length;
  const failed = v.checks.some((c) =>
    ["failure", "failed", "error", "cancelled", "timed_out"].includes(c.state),
  );
  const label = v.kind === "pr" ? prLabel(v.host, v.number) : v.branch;
  const findings = r.ai?.findings?.result?.findings ?? [];
  const comment = async (f: Finding) => {
    const at =
      f.path && f.line ? { path: f.path, line: f.line, side: f.side } : null;
    if (
      await R.addDraft(
        pid,
        reviewKey,
        findingComment(f, v.host?.kind === "gitlab"),
        at,
        f.id,
      )
    )
      await R.decide(pid, reviewKey, f.id, "commented");
  };
  const tabs: [PanelTab, string, number | null][] = [
    ["changes", "Changes", v.files.length],
    ["commits", "Commits", v.commits.length],
    ["overview", "Overview", null],
    [
      "findings",
      "Findings",
      r.ai?.findings?.status === "done" ? findings.length : null,
    ],
    ["threads", "Threads", v.threads.filter((t) => !t.resolved).length || null],
    ["checklist", "Checklist", null],
  ];
  const approve = async () => {
    const res = await R.submit(pid, reviewKey, "APPROVE", "");
    if (res) {
      setApproving(false);
      toast(`Approved ${label}.`);
    }
  };

  return (
    <div className="rv-one">
      {head}
      <div className="rv-pr-head">
        <b className="rv-title" title={v.title}>
          {v.title}
        </b>
        <span className="mono rv-dim">
          {v.kind === "pr" ? `${label} · ${v.author} · ` : ""}
          {v.base} ← {v.branch}
        </span>
        <div className="rv-row">
          {v.state && v.state !== "open" && <Pill tone="idle">{v.state}</Pill>}
          {v.draft && <Pill tone="idle">draft</Pill>}
          {v.checks.length > 0 && (
            <Pill
              tone={failed ? "bad" : greens === v.checks.length ? "ok" : "run"}
            >
              CI {greens}/{v.checks.length}
            </Pill>
          )}
          {v.approved.length > 0 && (
            <Pill tone="ok">approved by {v.approved.join(", ")}</Pill>
          )}
          {v.changes_requested.length > 0 && (
            <Pill tone="warn">
              changes requested by {v.changes_requested.join(", ")}
            </Pill>
          )}
          {v.mine && v.state === "open" && v.mergeable === false && (
            <Pill tone="warn">
              not mergeable yet{v.merge_state ? ` (${v.merge_state})` : ""}
            </Pill>
          )}
        </div>
        <div className="rv-row rv-acts">
          {v.can_post &&
            !v.mine &&
            (approving ? (
              <span
                className="rv-confirm"
                role="group"
                aria-label="Confirm approve"
              >
                <span>Approve {label}?</span>
                <button
                  type="button"
                  className="btn sm primary"
                  disabled={!!r.busy}
                  onClick={() => void approve()}
                >
                  Approve
                </button>
                <button
                  type="button"
                  className="btn sm ghost"
                  onClick={() => setApproving(false)}
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                className="btn sm"
                onClick={() =>
                  v.drafts.length
                    ? R.setUi({
                        popup: {
                          kind: "submit",
                          key: reviewKey,
                          event: "APPROVE",
                        },
                      })
                    : setApproving(true)
                }
              >
                Approve
              </button>
            ))}
          {v.can_post && (
            <button
              type="button"
              className="btn sm primary"
              onClick={() =>
                R.setUi({ popup: { kind: "submit", key: reviewKey } })
              }
            >
              Submit review{v.drafts.length ? ` (${v.drafts.length})` : ""}
            </button>
          )}
          {v.mine && v.state === "open" && (
            <button
              type="button"
              className="btn sm"
              disabled={v.draft}
              title={v.draft ? "A draft cannot be merged" : undefined}
              onClick={() =>
                R.setUi({ popup: { kind: "merge", key: reviewKey } })
              }
            >
              Merge…
            </button>
          )}
          {v.kind === "pr" && (
            <button
              type="button"
              className="btn sm ghost"
              disabled={!!r.busy}
              onClick={() =>
                void R.checkout(pid, reviewKey).then((x) => x && toast(x.note))
              }
            >
              Check out
            </button>
          )}
          {v.kind === "pr" && (
            <button
              type="button"
              className="btn sm ghost"
              disabled={r.loading}
              onClick={() => void R.load(pid, reviewKey, true)}
            >
              Refresh
            </button>
          )}
          {v.url && (
            <a
              className="btn sm ghost"
              href={v.url}
              target="_blank"
              rel="noreferrer"
            >
              Open on {v.host?.kind === "gitlab" ? "GitLab" : "GitHub"}
            </a>
          )}
        </div>
        {r.err && <ErrorBox error={r.err} />}
      </div>
      <div className="rv-tabs" role="tablist" aria-label="Review">
        {tabs.map(([k, l, n]) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={tab === k}
            className={tab === k ? "on" : ""}
            onClick={() => setTab(k)}
          >
            {l}
            {n != null && n > 0 && <span className="rv-n">{n}</span>}
          </button>
        ))}
      </div>
      <div
        className="rv-pbody"
        role="tabpanel"
        aria-label={tabs.find((t) => t[0] === tab)?.[1]}
      >
        {tab === "changes" &&
          (v.files.length === 0 ? (
            <p className="rv-dim">This review changes no file.</p>
          ) : (
            <>
              <FileTree
                files={v.files}
                viewed={v.viewed}
                comments={comments}
                active={activePath}
                onOpen={(path, pin) => open(path, null, "RIGHT", pin)}
                onViewed={(path) => void R.toggleViewed(pid, reviewKey, path)}
              />
              <p className="rv-dim rv-foot">
                {v.viewed.length}/{v.files.length} viewed · +{v.added} −
                {v.removed}
                {v.drafts.length ? ` · ${v.drafts.length} pending` : ""}
              </p>
              {v.notes.map((n) => (
                <p key={n} className="rv-dim">
                  {n}
                </p>
              ))}
            </>
          ))}
        {tab === "commits" && (
          <ul className="rv-commits">
            {v.commits.map((c) => (
              <li key={c.sha}>
                <span className="mono rv-dim">{c.sha.slice(0, 7)}</span>{" "}
                {c.message} <span className="rv-dim">· {c.author}</span>
              </li>
            ))}
          </ul>
        )}
        {tab === "overview" && (
          <OverviewTab
            view={v}
            ai={r.ai}
            busy={r.busy}
            onStart={(k) => void R.startAi(pid, reviewKey, k)}
            onOpen={(p) => open(p)}
            blocking={
              findings.filter(
                (f) =>
                  f.severity === "blocking" &&
                  r.ai?.decisions[f.id]?.decision !== "dismissed",
              ).length
            }
          />
        )}
        {tab === "findings" && (
          <FindingsTab
            view={v}
            ai={r.ai}
            busy={r.busy}
            onStart={(k) => void R.startAi(pid, reviewKey, k)}
            onOpen={open}
            onComment={(f) => void comment(f)}
            onDismiss={(f, why) =>
              void R.decide(pid, reviewKey, f.id, "dismissed", why)
            }
            onReopen={(f) => void R.decide(pid, reviewKey, f.id, "open")}
            onAsk={(f) => askAboutFinding(v, f)}
          />
        )}
        {tab === "threads" && (
          <ThreadsTab
            view={v}
            onOpen={open}
            onReply={(t, b) => R.reply(pid, reviewKey, t.id, b)}
            onResolve={(t, x) => R.resolve(pid, reviewKey, t.id, x)}
          />
        )}
        {tab === "checklist" && (
          <ChecklistTab
            view={v}
            ai={r.ai}
            checked={checked}
            onCheck={(id, on) =>
              setChecked((c) => {
                const n = on ? [...c, id] : c.filter((x) => x !== id);
                try {
                  localStorage.setItem(
                    `keel2.review.check.${reviewKey}`,
                    JSON.stringify(n),
                  );
                } catch {
                  /* private window */
                }
                return n;
              })
            }
          />
        )}
      </div>
    </div>
  );
}

export function askAboutFinding(v: ReviewView, f: Finding) {
  askAssistant(
    `In ${v.kind === "pr" ? `${prWord(v.host)} ${prLabel(v.host, v.number)}` : `branch ${v.branch}`} (commit ${v.head_sha.slice(0, 12)}), ` +
      `keel found: "${f.title}" at ${f.path}${f.line ? `:${f.line}` : ""}. ${f.why} Is it a real problem? Read it with git show ${v.head_sha.slice(0, 12)}:${f.path}.`,
  );
}

/** Next / previous file of a review in the tree's order. */
export function neighbour(
  v: ReviewView,
  path: string | null,
  dir: 1 | -1,
): string | null {
  const order = treeOrder(v.files).map((f) => f.path);
  if (!order.length) return null;
  const i = path ? order.indexOf(path) : -1;
  return order[(i + dir + order.length) % order.length];
}
