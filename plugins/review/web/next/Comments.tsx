// The new Review view's Comments tab (0.18.57): the review threads, by Waiting for you / Open / Resolved, each one a
// line you open. Open, it shows the lines it is on, its messages, and keel's check: is the reviewer right, point by
// point, why, and the fix. You decide: use keel's answer as your reply, start a flow that addresses it (after you
// confirm), ask KeelBot a follow-up, or let keel think harder. keel never posts by itself.
//
//   (Waiting for you 1) (Open 3) (Resolved 1)                      [Check all open (2)]
//   ┌ refunds.py · line 20  alex  The amount comes from…  ● Reviewer (alex) is right  ▾ ┐
//   │  19 + # never give back more than the customer paid                                 │
//   │  20 + if amount > refundable_amount(order):                                         │
//   │ (A) alex · 1 h ago  The amount comes from a form…                                   │
//   │ ┌ keel: Reviewer (alex) is right ──────────────────────────────────────────┐       │
//   │ │ • Reviewer (alex) is right · A string can reach this check. … Fix: …     │       │
//   │ │ [Use as my reply] [Address it] Ask a follow-up  Think harder             │       │
//   │ └──────────────────────────────────────────────────────────────────────────┘       │
//   │ [Reply] [Resolve] Show in the file                                                  │
//   └─────────────────────────────────────────────────────────────────────────────────────┘
//
// The same store actions as the classic Threads panel (R.judge, R.judgeAll, R.address, R.reply, R.resolve), and the
// same kept reply drafts (a reply you started in one view is in the other). Comments that make the same point are one
// check: each answer links the others, and one reply can go into all of their drafts. When the author says "fixed" and
// pushes, keel checks the new code: "Fixed in abc1234 · Resolve" (only your click resolves it).

import { useEffect, useMemo, useRef, useState } from "react";
import {
  askAssistant,
  ErrorBox,
  kfmt,
  Markdown,
  parseDiff,
  SLOTS,
  useLoad,
  useSlot,
  useStored,
} from "@keel/web-sdk";
import {
  linesLabel,
  prLabel,
  reviewApi,
  type AiState,
  type ReviewThread,
  type ReviewView,
} from "../reviewApi";
import * as R from "../store";
import {
  ago,
  estimate,
  FillDraft,
  firstLine,
  initials,
  quoteThread,
  uncheckedPoints,
} from "../Threads";
import {
  checkLines,
  fixNote,
  samePointLabel,
  sidesOf,
  useOnAllLabel,
  verdictLabel,
} from "../verdict";
import {
  doneCheck,
  needsChange,
  same,
  threadPill,
  threadsOf,
  VERDICT_TONE,
  verdictOf,
  whereOf,
  type ThreadFilter,
} from "./model";
import { Tag } from "./ui";

const isFilter = (v: unknown): v is ThreadFilter =>
  v === "wait" || v === "open" || v === "resolved";

export function CommentsTab({
  pid,
  reviewKey,
  view,
  ai,
  running,
  focus,
  onShowInFile,
}: {
  pid: string;
  reviewKey: string;
  view: ReviewView;
  ai: AiState | null;
  running: string[];
  /** a thread to open and show (a marker in the Files tab), n changes every time */
  focus: { id: string; n: number } | null;
  onShowInFile: (path: string, line: number | null) => void;
}) {
  const [filter, setFilter] = useStored<ThreadFilter>(
    `keel2.review.next.tfilter.${pid}.${reviewKey}`,
    "open",
    {
      valid: isFilter,
    },
  );
  const [openId, setOpenId] = useState<string | null>(null);
  // opens one comment and shows it (a marker in Files, or the same point's link)
  const go = (id: string) => {
    const t = view.threads.find((x) => x.id === id);
    if (!t) return;
    setOpenId(id);
    if (t.resolved && filter !== "resolved") setFilter("resolved");
    if (!t.resolved && filter === "resolved") setFilter("open");
    window.setTimeout(
      () =>
        document
          .querySelector(`[data-rvn-thread="${CSS.escape(id)}"]`)
          ?.scrollIntoView?.({ block: "nearest" }),
      0,
    );
  };
  useEffect(() => {
    if (focus) go(focus.id);
  }, [focus?.n]); // eslint-disable-line react-hooks/exhaustive-deps
  const cfg = ai?.config;
  const quick = (cfg?.judge?.mode ?? "quick") === "quick";
  // the same point in two comments is one check
  const unchecked = uncheckedPoints(view, ai);
  const checkAllBusy = running.includes("judge-all");
  const list = threadsOf(view, filter);
  const chips: [ThreadFilter, string][] = [
    ["wait", "Waiting for you"],
    ["open", "Open"],
    ["resolved", "Resolved"],
  ];

  if (!view.threads.length && !view.conversation.length)
    return (
      <p className="rvn-empty">
        {view.kind === "pr"
          ? `No comments on ${prLabel(view.host, view.number)} yet. Comments you add in Files wait for your review.`
          : "A branch has no comments. Its pull request does."}
      </p>
    );

  return (
    <div className="rvn-stack">
      <div className="rvn-row rvn-tbar">
        <div className="rvn-row" role="group" aria-label="Show comments">
          {chips.map(([f, label]) => (
            <button
              key={f}
              type="button"
              className="rvn-chip"
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
            >
              {label}
              <span className="rvn-n">{threadsOf(view, f).length}</span>
            </button>
          ))}
        </div>
        <span className="rvn-grow" />
        {quick && view.can_post && view.kind === "pr" && unchecked > 0 && (
          <button
            type="button"
            className="rvn-btn ghost accent"
            disabled={checkAllBusy}
            title="One cheap call for every open point keel has not checked yet (the same point in two comments is one)"
            onClick={() => void R.judgeAll(pid, reviewKey)}
          >
            {checkAllBusy ? "Checking…" : `Check all open (${unchecked})`}
          </button>
        )}
      </div>
      <p className="rvn-dim rvn-lead">
        For each comment keel reads the code and says if the reviewer is right,
        and why. You decide.
      </p>
      {list.length === 0 && (
        <p className="rvn-empty">
          {filter === "resolved"
            ? "No resolved comment yet."
            : filter === "wait"
              ? "Nothing waits for your answer."
              : "No open comment."}
        </p>
      )}
      {list.map((t) => (
        <ThreadCard
          key={t.id}
          pid={pid}
          reviewKey={reviewKey}
          view={view}
          ai={ai}
          running={running}
          t={t}
          open={openId === t.id}
          onToggle={() => setOpenId((o) => (o === t.id ? null : t.id))}
          onGo={go}
          onShowInFile={onShowInFile}
        />
      ))}
      {view.conversation.length > 0 && (
        <details className="rvn-conv">
          <summary>Conversation ({view.conversation.length})</summary>
          {view.conversation.map((c) => (
            <Msg
              key={c.id}
              who={same(c.author, view.me) ? "You" : c.author}
              at={c.at}
              body={c.body}
            />
          ))}
        </details>
      )}
    </div>
  );
}

function Msg({ who, at, body }: { who: string; at: string; body: string }) {
  return (
    <div className="rvn-msg">
      <span className="rvn-av" aria-hidden="true">
        {initials(who)}
      </span>
      <div className="rvn-msg-b">
        <div>
          <b>{who}</b> <span className="rvn-dim">{ago(at)}</span>
        </div>
        <div className="rvn-msg-t">
          <Markdown text={body} fold={12} />
        </div>
      </div>
    </div>
  );
}

/** The lines a thread is on, from the review's diff (the classic view reads the same diff). */
function Snippet({
  pid,
  reviewKey,
  t,
}: {
  pid: string;
  reviewKey: string;
  t: ReviewThread;
}) {
  const d = useLoad(
    `review-diff:${pid}:${reviewKey}:${t.path}`,
    () => reviewApi.diff(pid, reviewKey, t.path!),
    {
      live: false,
    },
  );
  const rows = useMemo(() => {
    if (!d.data || t.line == null) return [];
    const left = t.side === "LEFT";
    const start = t.start_line != null ? t.start_line : Math.max(1, t.line - 1);
    const end = t.start_line != null ? t.line : t.line + 1;
    return parseDiff(d.data.diff)
      .rows.filter(
        (r) => r.kind === "add" || r.kind === "del" || r.kind === "ctx",
      )
      .filter((r) => (left ? r.kind !== "add" : r.kind !== "del"))
      .filter((r) => {
        const n = left ? r.old : r.new;
        return n != null && n >= start && n <= end;
      })
      .slice(0, 12);
  }, [d.data, t.line, t.start_line, t.side]);
  if (!rows.length) return null;
  return (
    <pre
      className="rvn-snip"
      tabIndex={0}
      aria-label={`The code of ${t.path} at line ${linesLabel(t)}`}
    >
      {rows.map((r, i) => {
        const n = t.side === "LEFT" ? r.old : r.new;
        const on =
          n === t.line ||
          (t.start_line != null &&
            n != null &&
            n >= t.start_line &&
            n <= t.line!);
        return (
          <span key={i} className={`rvn-snip-l${on ? " on" : ""}`}>
            <span className="rvn-snip-n">{n}</span>
            {r.text || " "}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}

function ThreadCard({
  pid,
  reviewKey,
  view,
  ai,
  running,
  t,
  open,
  onToggle,
  onGo,
  onShowInFile,
}: {
  pid: string;
  reviewKey: string;
  view: ReviewView;
  ai: AiState | null;
  running: string[];
  t: ReviewThread;
  open: boolean;
  onToggle: () => void;
  /** opens another comment (the same point) */
  onGo: (threadId: string) => void;
  onShowInFile: (path: string, line: number | null) => void;
}) {
  const assistant = useSlot(SLOTS.assistant).length > 0;
  // the reply you are writing: the classic view keeps the same draft (localStorage) until it is sent or cancelled
  const [text, setText, dropText] = R.useReviewDraft(
    pid,
    reviewKey,
    `reply.${t.id}`,
  );
  const [replying, setReplying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<null | {
    note?: string;
    fix?: string;
  }>(null);
  const [err, setErr] = useState<R.Err | null>(null);
  const ta = useRef<HTMLTextAreaElement>(null);
  const cfg = ai?.config;
  const quick = (cfg?.judge?.mode ?? "quick") === "quick";
  const flowName = cfg?.address_workflow ?? "address";
  const flows = cfg?.address_flows?.length
    ? cfg.address_flows
    : [{ id: flowName, name: flowName }];
  // the flow you picked in the confirmation; none: the configured one (the config comes with keel's state, maybe later)
  const [picked, setFlow] = useState<string | null>(null);
  const flow = picked ?? flowName;
  const run = ai?.judges?.[t.id] ?? null;
  const check = doneCheck(run);
  const checking =
    running.includes(`judge:${t.id}`) || run?.status === "running";
  const addressing = running.includes(`address:${t.id}`);
  const addressed = ai?.addressed?.[t.id];
  // the author said fixed and pushed: keel's check of the new code (Resolve is your click)
  const fix = fixNote(ai?.fixed?.[t.id], t);
  const canWork = view.kind === "pr" && !t.pending;
  const [pill, tone] = threadPill(t, view, ai, checking);
  const sum = verdictOf(check?.items ?? []);
  const sides = sidesOf(view, t);
  const first = t.comments[0];
  const [lo, hi] = estimate(t);
  const failed = (fallback: string) =>
    setErr(R.review(pid, reviewKey).err ?? { message: fallback });

  const ask = async (opts: { again?: boolean; harder?: boolean } = {}) => {
    setErr(null);
    if (!(await R.judge(pid, reviewKey, t.id, opts)))
      failed("keel could not check this comment");
  };
  const useReply = (s: string) => {
    setText(s);
    setReplying(true);
    window.setTimeout(() => ta.current?.focus(), 0);
  };
  // the same point in other comments: keel checked them as one; its reply can go into each one's draft
  const sameAs = check?.same_as ?? [];
  const [fillOthers, setFillOthers] = useState<{ text: string; n: number } | null>(null);
  const useOnAll = (s: string) => {
    useReply(s);
    setFillOthers((f) => ({ text: s, n: (f?.n ?? 0) + 1 }));
  };
  const send = async () => {
    setBusy(true);
    try {
      await R.reply(pid, reviewKey, t.id, text.trim());
      if (!R.review(pid, reviewKey).err) {
        dropText();
        setReplying(false);
      } else failed("Your reply was not sent");
    } finally {
      setBusy(false);
    }
  };
  // 0.18.57 no reply of yours in the thread: keel asks what the fix should be (the reviewer's words quoted)
  const replied = t.comments.some((c) => same(c.author, view.me));
  const note = check?.items
    ?.filter((i) => i.verdict !== "author_right")
    .map((i) => `${i.claim}: ${i.why}${i.fix ? ` Fix: ${i.fix}` : ""}`)
    .join("\n");
  const startAddress = async () => {
    setErr(null);
    const r = await R.address(
      pid,
      reviewKey,
      t.id,
      confirm?.note,
      flow !== flowName ? flow : undefined,
      confirm?.fix?.trim() ? confirm.fix.trim() : undefined,
    );
    setConfirm(null);
    if (!r) failed("keel could not start the flow");
  };
  const followUp = () =>
    askAssistant(
      `About the review comment on ${t.path ?? "the pull request"}${t.line ? `:${linesLabel(t)}` : ""} of ${prLabel(view.host, view.number)} ` +
        `(commit ${view.head_sha.slice(0, 12)}):\n` +
        t.comments.map((c) => `${c.author}: ${c.body}`).join("\n\n") +
        (check?.items?.length
          ? `\n\nkeel's check:\n${checkLines(check.items, sides)}`
          : "") +
        "\n\nMy question: ",
    );
  const bodyId = `rvn-th-${t.id}`;

  return (
    <article
      className={`rvn-thread${open ? " open" : ""}`}
      data-rvn-thread={t.id}
      aria-label={`Comment on ${whereOf(t.path, t.line)}`}
    >
      <button
        type="button"
        className="rvn-thread-h"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={onToggle}
      >
        <span className="rvn-mono rvn-dim">{whereOf(t.path, t.line)}</span>
        <b>
          {first
            ? same(first.author, view.me)
              ? "You"
              : first.author
            : "Comment"}
        </b>
        <span className="rvn-thread-t">{firstLine(first?.body ?? "")}</span>
        <Tag tone={tone}>{pill}</Tag>
        <span className="rvn-dim" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
      </button>
      {open && (
        <div className="rvn-thread-b" id={bodyId}>
          {t.path &&
            t.line != null &&
            !t.outdated &&
            view.files.some((f) => f.path === t.path) && (
              <Snippet pid={pid} reviewKey={reviewKey} t={t} />
            )}
          {t.outdated && (
            <p className="rvn-dim">
              This comment is on code that changed since.
            </p>
          )}
          {t.comments.map((c) => (
            <Msg
              key={c.id}
              who={same(c.author, view.me) ? "You" : c.author}
              at={c.at}
              body={c.body}
            />
          ))}

          {fix && (
            <p
              className={`rvn-fixed t-${fix.fixed ? "ok" : "warn"}`}
              role="group"
              aria-label="keel checked the fix"
            >
              <span>{fix.text}</span>
              {fix.fixed && view.can_post && !t.pending && (
                <>
                  {" · "}
                  <button
                    type="button"
                    className="rvn-link"
                    disabled={busy}
                    onClick={() => {
                      setBusy(true);
                      void R.resolve(pid, reviewKey, t.id, true).finally(() =>
                        setBusy(false),
                      );
                    }}
                  >
                    Resolve
                  </button>
                </>
              )}
            </p>
          )}
          {canWork && !t.resolved && !run && !checking && (
            <button
              type="button"
              className="rvn-btn soft"
              title={
                quick
                  ? `One quick check: about ${kfmt(lo)}–${kfmt(hi)} tokens`
                  : `Runs the ${cfg?.judge?.workflow ?? "review"} workflow`
              }
              onClick={() => void ask()}
            >
              Is the reviewer right? Ask keel
            </button>
          )}
          {checking && (
            <p className="rvn-checking" role="status">
              <span className="pg-spin">
                keel is reading the comment and its code…
              </span>
            </p>
          )}
          {!checking && run?.status === "failed" && (
            <div className="rvn-stack">
              <ErrorBox error={{ message: run.error ?? "The check failed" }} />
              <button
                type="button"
                className="rvn-btn"
                onClick={() => void ask({ again: true })}
              >
                Try again
              </button>
            </div>
          )}
          {!checking &&
            run?.status === "done" &&
            run.result?.stage === "flow" && (
              <p className="rvn-flow" role="status">
                The {run.result.workflow} flow has this comment as its request.{" "}
                {run.result.flow_id && (
                  <a href={`#/flow/${encodeURIComponent(run.result.flow_id)}`}>
                    Open flow ▸
                  </a>
                )}
              </p>
            )}
          {!checking && check && sum && (
            <section
              className={`rvn-verdict t-${VERDICT_TONE[sum]}`}
              aria-label="keel's check"
            >
              <b className="rvn-verdict-h">keel: {verdictLabel(sum, sides)}</b>
              {sameAs.map((o) => (
                <button
                  key={o.thread_id}
                  type="button"
                  className="rvn-link rvn-same"
                  onClick={() => onGo(o.thread_id)}
                >
                  {samePointLabel(o, view)}
                </button>
              ))}
              {run?.stale && (
                <p className="rvn-dim">
                  The code changed since keel checked it.
                </p>
              )}
              <ul>
                {(check.items ?? []).map((it, i) => (
                  <li key={i}>
                    <span className={`rvn-pt t-${VERDICT_TONE[it.verdict]}`}>
                      {verdictLabel(it.verdict, sides)}
                    </span>
                    {" · "}
                    <b>{it.claim.replace(/\.$/, "")}.</b> {it.why}
                    {it.fix && <span className="rvn-dim"> Fix: {it.fix}</span>}
                  </li>
                ))}
              </ul>
              <div className="rvn-row">
                {view.can_post && !t.pending && check.reply && (
                  <button
                    type="button"
                    className="rvn-btn"
                    onClick={() => useReply(check.reply!)}
                  >
                    Use as my reply
                  </button>
                )}
                {view.can_post && !t.pending && check.reply && sameAs.length > 0 && (
                  <button
                    type="button"
                    className="rvn-btn"
                    title="keel's reply into the draft of each comment that makes this point (nothing is posted)"
                    onClick={() => useOnAll(check.reply!)}
                  >
                    {useOnAllLabel(sameAs.length)}
                  </button>
                )}
                {canWork &&
                  view.state === "open" &&
                  needsChange(check) &&
                  !addressed && (
                    <button
                      type="button"
                      className="rvn-btn primary"
                      onClick={() =>
                        setConfirm({
                          note,
                          fix: replied ? undefined : quoteThread(t),
                        })
                      }
                    >
                      Address it
                    </button>
                  )}
                {assistant && (
                  <button type="button" className="rvn-link" onClick={followUp}>
                    Ask a follow-up
                  </button>
                )}
                {quick && (cfg?.judge?.allow_harder ?? true) && (
                  <button
                    type="button"
                    className="rvn-link quiet"
                    title="A stronger model or more effort: more tokens"
                    onClick={() => void ask({ again: true, harder: true })}
                  >
                    Think harder
                  </button>
                )}
              </div>
            </section>
          )}

          {confirm && (
            <div
              className="rvn-confirm"
              role="group"
              aria-label="Confirm Address it"
            >
              <span>
                Start the {flow} flow on{" "}
                <b className="rvn-mono">{view.branch}</b>? keel checks out the
                branch in the project folder and works on this comment there. It
                does not push or post without you.
              </span>
              {confirm.fix !== undefined && (
                <label className="rvn-field">
                  <span>
                    You have not replied here. What should the fix be? Write it
                    under the reviewer's words.
                  </span>
                  <textarea
                    aria-label="What should the fix be?"
                    rows={4}
                    value={confirm.fix}
                    onChange={(e) =>
                      setConfirm({ ...confirm, fix: e.target.value })
                    }
                  />
                </label>
              )}
              {flows.length > 1 && (
                <label className="rvn-row">
                  <span>Flow</span>
                  <select
                    aria-label="Flow for Address it"
                    value={flow}
                    onChange={(e) => setFlow(e.target.value)}
                  >
                    {flows.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <div className="rvn-row">
                <button
                  type="button"
                  className="rvn-btn primary"
                  disabled={addressing}
                  onClick={() => void startAddress()}
                >
                  {addressing ? "Starting…" : "Start the flow"}
                </button>
                <button
                  type="button"
                  className="rvn-btn"
                  onClick={() => setConfirm(null)}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
          {sameAs.map((o) => (
            <FillDraft
              key={o.thread_id}
              pid={pid}
              reviewKey={reviewKey}
              threadId={o.thread_id}
              fill={fillOthers}
            />
          ))}
          {addressed && (
            <a
              className="rvn-flow"
              href={`#/flow/${encodeURIComponent(addressed.flow_id)}`}
            >
              <span className="rvn-tag-dot" aria-hidden="true" />A{" "}
              {addressed.workflow ?? flowName} flow works on this comment
              {addressed.branch ? ` on ${addressed.branch}` : ""} · Open flow ▸
            </a>
          )}

          {replying && (
            <form
              className="rvn-stack"
              onSubmit={(e) => {
                e.preventDefault();
                if (text.trim()) void send();
              }}
            >
              <textarea
                ref={ta}
                className="rvn-ta"
                aria-label="Your reply"
                rows={3}
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
              <div className="rvn-row">
                <button
                  type="submit"
                  className="rvn-btn primary"
                  disabled={busy || !text.trim()}
                >
                  {busy ? "Sending…" : "Send reply"}
                </button>
                <button
                  type="button"
                  className="rvn-btn"
                  onClick={() => {
                    dropText();
                    setReplying(false);
                  }}
                >
                  Cancel
                </button>
                <span className="rvn-dim">
                  It is posted on{" "}
                  {view.host?.kind === "gitlab" ? "GitLab" : "GitHub"} at once.
                </span>
              </div>
            </form>
          )}
          {err && <ErrorBox error={err} />}
          <div className="rvn-row rvn-thread-f">
            {view.can_post && !t.pending && !replying && (
              <button
                type="button"
                className="rvn-btn"
                onClick={() => {
                  setReplying(true);
                  window.setTimeout(() => ta.current?.focus(), 0);
                }}
              >
                {text ? "Reply (draft kept)" : "Reply"}
              </button>
            )}
            {view.can_post && !t.pending && (
              <button
                type="button"
                className="rvn-btn"
                disabled={busy}
                onClick={() => {
                  setBusy(true);
                  void R.resolve(pid, reviewKey, t.id, !t.resolved).finally(
                    () => setBusy(false),
                  );
                }}
              >
                {t.resolved ? "Open again" : "Resolve"}
              </button>
            )}
            {t.path && view.files.some((f) => f.path === t.path) && (
              <button
                type="button"
                className="rvn-link"
                onClick={() => onShowInFile(t.path!, t.line)}
              >
                Show in the file
              </button>
            )}
          </div>
        </div>
      )}
    </article>
  );
}
