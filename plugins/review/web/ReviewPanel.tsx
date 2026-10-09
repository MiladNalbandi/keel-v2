// v0.14.0 the keel panel of a review: Overview (what the change does), Findings (two reviewers, every serious claim
// checked again), Threads (reply, resolve) and the Checklist (the reviewer's steps). keel never posts: a finding becomes
// a pending comment only when you press Add as comment.

import { useState } from "react";
import { ErrorBox, Markdown, Pill, type PillTone } from "@keel/web-sdk";
import {
  prLabel,
  type AiState,
  type Finding,
  type ReviewThread,
  type ReviewView,
} from "./reviewApi";

export type PanelTab = "changes" | "commits" | "overview" | "findings" | "threads" | "checklist";

const SEV: Record<string, [string, PillTone]> = {
  blocking: ["blocking", "bad"],
  should_fix: ["should fix", "warn"],
  nit: ["nit", "idle"],
};
const CHECK: Record<string, [string, PillTone]> = {
  confirmed: ["checked", "ok"],
  rejected: ["rejected", "idle"],
  "not checked": ["not checked", "warn"],
};

export function findingComment(f: Finding, gitlab: boolean): string {
  const label =
    f.severity === "blocking"
      ? "issue (blocking)"
      : f.severity === "should_fix"
        ? "suggestion (non-blocking)"
        : "nitpick (non-blocking)";
  const parts = [`**${label}:** ${f.title}`];
  if (f.why) parts.push(f.why);
  if (f.suggestion)
    parts.push(
      "```" +
        (gitlab ? "suggestion:-0+0" : "suggestion") +
        "\n" +
        f.suggestion.replace(/\n$/, "") +
        "\n```",
    );
  else if (f.fix) parts.push(`Fix: ${f.fix}`);
  return parts.join("\n\n");
}

function Running({
  what,
  sessions,
}: {
  what: string;
  sessions: { role: string; status: string }[];
}) {
  return (
    <div className="rv-running" role="status">
      <span className="pg-spin">{what}</span>
      <ul>
        {sessions.map((s) => (
          <li key={s.role}>
            <span className="mono">{s.role}</span>{" "}
            {s.status === "running" ? "…" : s.status === "done" ? "✓" : "✗"}
          </li>
        ))}
      </ul>
      <span className="rv-dim">
        Open Live agents to watch it read the code.
      </span>
    </div>
  );
}

export function OverviewTab({
  view,
  ai,
  busy,
  onStart,
  onOpen,
  blocking,
}: {
  view: ReviewView;
  ai: AiState | null;
  busy: string | null;
  blocking: number;
  onStart: (k: "overview" | "findings") => void;
  onOpen: (path: string) => void;
}) {
  const run = ai?.overview;
  const o = run?.status === "done" ? run.result : null;
  const counts = ai?.findings?.result?.counts;
  return (
    <div className="rv-stack">
      <div className="rv-chips">
        {counts && (
          <Pill tone={blocking ? "bad" : "ok"}>
            {blocking ? `${blocking} blocking` : "No blocking issues"}
          </Pill>
        )}
        {counts?.should_fix ? (
          <Pill tone="warn">{counts.should_fix} should fix</Pill>
        ) : null}
        {o?.effort ? <Pill tone="idle">effort {o.effort}/5</Pill> : null}
        {o?.risk && (
          <Pill
            tone={
              o.risk === "high" ? "bad" : o.risk === "medium" ? "warn" : "ok"
            }
          >
            risk {o.risk}
          </Pill>
        )}
        <Pill tone="idle">
          {view.files.length} files · +{view.added} −{view.removed}
        </Pill>
      </div>
      {!run && (
        <div className="rv-empty">
          <p>
            keel reads the change and tells you what it does, file by file, with
            a diagram and the files to open first.
          </p>
          <button
            type="button"
            className="btn sm primary"
            disabled={!!busy}
            onClick={() => onStart("overview")}
          >
            Explain this change
          </button>
        </div>
      )}
      {run?.status === "running" && (
        <Running what="keel reads the change…" sessions={run.sessions} />
      )}
      {run?.status === "failed" && (
        <div className="rv-empty">
          <ErrorBox error={{ message: run.error ?? "The overview failed" }} />
          <button
            type="button"
            className="btn sm"
            onClick={() => onStart("overview")}
          >
            Try again
          </button>
        </div>
      )}
      {o && (
        <>
          {run?.stale && (
            <p className="rv-warn">
              The code changed since keel read it.{" "}
              <button
                type="button"
                className="btn sm"
                onClick={() => onStart("overview")}
              >
                Read it again
              </button>
            </p>
          )}
          <h4>What it does</h4>
          <p>{o.summary}</p>
          {o.risk_why && <p className="rv-dim">Risk: {o.risk_why}</p>}
          {o.diagram && (
            <>
              <h4>Flow</h4>
              <pre className="rv-diagram">{o.diagram}</pre>
            </>
          )}
          {o.order.length > 0 && (
            <>
              <h4>Open first</h4>
              <ol className="rv-list">
                {o.order.map((p) => (
                  <li key={p}>
                    <button
                      type="button"
                      className="rv-link mono"
                      onClick={() => onOpen(p)}
                    >
                      {p}
                    </button>
                  </li>
                ))}
              </ol>
            </>
          )}
          {o.files.length > 0 && (
            <>
              <h4>Each file</h4>
              <ul className="rv-list">
                {o.files.map((f) => (
                  <li key={f.path}>
                    <button
                      type="button"
                      className="rv-link mono"
                      onClick={() => onOpen(f.path)}
                    >
                      {f.path}
                    </button>{" "}
                    {f.what}
                  </li>
                ))}
              </ul>
            </>
          )}
          {o.split && <p className="rv-warn">Split it? {o.split}</p>}
          {o.questions.length > 0 && (
            <>
              <h4>Ask the author</h4>
              <ul className="rv-list">
                {o.questions.map((q) => (
                  <li key={q}>{q}</li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
      {view.body && (
        <>
          <h4>The author's description</h4>
          <div className="rv-desc">
            <Markdown text={view.body} fold={12} />
          </div>
        </>
      )}
      {view.notes.map((n) => (
        <p key={n} className="rv-dim">
          {n}
        </p>
      ))}
    </div>
  );
}

function FindingCard({
  f,
  view,
  decision,
  onOpen,
  onComment,
  onDismiss,
  onReopen,
  onAsk,
  muted,
}: {
  f: Finding;
  view: ReviewView;
  decision?: { decision: string; why: string | null };
  muted?: boolean;
  onOpen: (path: string, line?: number | null, side?: "RIGHT" | "LEFT") => void;
  onComment?: (f: Finding) => void;
  onDismiss?: (f: Finding, why: string) => void;
  onReopen?: (f: Finding) => void;
  onAsk?: (f: Finding) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [why, setWhy] = useState("");
  const [sevLabel, sevTone] = SEV[f.severity] ?? [f.severity, "idle"];
  const check = f.check ? CHECK[f.check] : null;
  const dismissed = decision?.decision === "dismissed";
  const commented =
    decision?.decision === "commented" ||
    view.drafts.some((d) => d.finding_id === f.id);
  return (
    <div
      className={`rv-find${muted || dismissed ? " muted" : ""}`}
      data-finding={f.id}
    >
      <div className="rv-find-h">
        <Pill tone={sevTone}>{sevLabel}</Pill>
        {check && <Pill tone={check[1]}>{check[0]}</Pill>}
        <b>{f.title}</b>
      </div>
      {f.path && (
        <button
          type="button"
          className="rv-link mono rv-where"
          onClick={() => onOpen(f.path!, f.line, f.side)}
        >
          {f.path}
          {f.line ? `:${f.line}` : ""}
        </button>
      )}
      <span className="rv-dim">
        {f.category}
        {f.reviewer ? ` · reviewer ${f.reviewer}` : ""}
      </span>
      {f.why && <p>{f.why}</p>}
      {f.check_why && (
        <p className="rv-dim">
          {f.check === "rejected" ? "Why rejected: " : "Check: "}
          {f.check_why}
        </p>
      )}
      {f.suggestion && <pre className="rv-sugg">{f.suggestion}</pre>}
      {!f.suggestion && f.fix && <p className="rv-dim">Fix: {f.fix}</p>}
      {dismissed && (
        <p className="rv-dim">
          Dismissed: {decision?.why}{" "}
          <button
            type="button"
            className="btn sm ghost"
            onClick={() => onReopen?.(f)}
          >
            Undo
          </button>
        </p>
      )}
      {commented && !dismissed && (
        <p className="rv-ok">Added as a pending comment.</p>
      )}
      {!dismissed &&
        !commented &&
        (onComment || onDismiss) &&
        (asking ? (
          <form
            className="rv-row"
            onSubmit={(e) => {
              e.preventDefault();
              if (why.trim()) onDismiss?.(f, why.trim());
            }}
          >
            <input
              aria-label={`Why dismiss: ${f.title}`}
              className="rv-in"
              value={why}
              onChange={(e) => setWhy(e.target.value)}
              placeholder="why it is not a problem"
              autoFocus
            />
            <button type="submit" className="btn sm" disabled={!why.trim()}>
              Dismiss
            </button>
            <button
              type="button"
              className="btn sm ghost"
              onClick={() => setAsking(false)}
            >
              Cancel
            </button>
          </form>
        ) : (
          <div className="rv-row">
            {onComment && (
              <button
                type="button"
                className="btn sm primary"
                disabled={!view.can_post && view.kind === "pr"}
                onClick={() => onComment(f)}
              >
                Add as comment
              </button>
            )}
            {onDismiss && (
              <button
                type="button"
                className="btn sm ghost"
                onClick={() => setAsking(true)}
              >
                Dismiss…
              </button>
            )}
            {onAsk && (
              <button
                type="button"
                className="btn sm ghost"
                onClick={() => onAsk(f)}
              >
                Ask KeelBot
              </button>
            )}
          </div>
        ))}
    </div>
  );
}

export function FindingsTab({
  view,
  ai,
  busy,
  onStart,
  onOpen,
  onComment,
  onDismiss,
  onReopen,
  onAsk,
}: {
  view: ReviewView;
  ai: AiState | null;
  busy: string | null;
  onStart: (k: "overview" | "findings") => void;
  onOpen: (path: string, line?: number | null, side?: "RIGHT" | "LEFT") => void;
  onComment: (f: Finding) => void;
  onDismiss: (f: Finding, why: string) => void;
  onReopen: (f: Finding) => void;
  onAsk: (f: Finding) => void;
}) {
  const run = ai?.findings;
  const r = run?.status === "done" ? run.result : null;
  const [show, setShow] = useState<Record<string, boolean>>({});
  const group = (
    key: string,
    title: string,
    list: Finding[] | undefined,
    opts: { muted?: boolean; actions?: boolean } = {},
  ) =>
    !list?.length ? null : (
      <section className="rv-group">
        <button
          type="button"
          className="rv-group-h"
          aria-expanded={opts.actions !== false || !!show[key]}
          onClick={() => setShow((s) => ({ ...s, [key]: !s[key] }))}
        >
          {title} <span className="rv-n">{list.length}</span>
        </button>
        {(opts.actions !== false || show[key]) &&
          list.map((f) => (
            <FindingCard
              key={f.id}
              f={f}
              view={view}
              decision={ai?.decisions[f.id]}
              muted={opts.muted}
              onOpen={onOpen}
              onComment={opts.actions === false ? undefined : onComment}
              onDismiss={opts.actions === false ? undefined : onDismiss}
              onReopen={onReopen}
              onAsk={opts.actions === false ? undefined : onAsk}
            />
          ))}
      </section>
    );
  const found = r?.findings ?? [];
  const live = found.filter((f) => ai?.decisions[f.id]?.decision !== "dismissed");
  return (
    <div className="rv-stack">
      {!run && (
        <div className="rv-empty">
          <p>
            Two keel reviewers read the change (A: correctness and security, B:
            tests, the description and design). Then a fresh run checks every
            serious claim against the code. Only checked findings are shown
            here; nothing is posted.
          </p>
          <button
            type="button"
            className="btn sm primary"
            disabled={!!busy}
            onClick={() => onStart("findings")}
          >
            Find problems
          </button>
        </div>
      )}
      {run?.status === "running" && (
        <Running
          what={
            run.stage === "verify"
              ? "keel checks each claim…"
              : "Two reviewers read the change…"
          }
          sessions={run.sessions}
        />
      )}
      {run?.status === "failed" && (
        <div className="rv-empty">
          <ErrorBox error={{ message: run.error ?? "The review failed" }} />
          <button
            type="button"
            className="btn sm"
            onClick={() => onStart("findings")}
          >
            Try again
          </button>
        </div>
      )}
      {r && (
        <>
          {run?.stale && (
            <p className="rv-warn">
              New commits since keel looked.{" "}
              <button
                type="button"
                className="btn sm"
                onClick={() => onStart("findings")}
              >
                Review again
              </button>
            </p>
          )}
          <p
            className={
              live.some((f) => f.severity === "blocking") ? "rv-bad" : "rv-ok"
            }
          >
            {live.filter((f) => f.severity === "blocking").length
              ? `${live.filter((f) => f.severity === "blocking").length} blocking · ${live.filter((f) => f.severity === "should_fix").length} should fix`
              : live.length
                ? `No blocking issues · ${live.length} to look at`
                : "No problems found."}
          </p>
          {group(
            "blocking",
            "Blocking",
            found.filter((f) => f.severity === "blocking"),
          )}
          {group(
            "should_fix",
            "Should fix",
            found.filter((f) => f.severity !== "blocking"),
          )}
          {group(
            "nits",
            `Nits${r.more_nits ? ` (+${r.more_nits} more not shown)` : ""}`,
            r.nits,
          )}
          <section className="rv-group">
            <h4>Security</h4>
            <p className="rv-dim">
              {r.security || "No security problem reported."}
            </p>
            <h4>Tests</h4>
            <p className="rv-dim">
              {r.tests || "Nothing reported about the tests."}
            </p>
          </section>
          {group(
            "pre",
            "Already there before this change (never blocking)",
            r.pre_existing,
            { muted: true, actions: false },
          )}
          {group("rejected", "Rejected by the check", r.rejected, {
            muted: true,
            actions: false,
          })}
        </>
      )}
      {!view.can_post && view.kind === "branch" && (
        <p className="rv-dim">
          A branch review keeps your comments in keel. Open its{" "}
          {view.host?.kind === "gitlab" ? "merge" : "pull"} request to post
          them.
        </p>
      )}
    </div>
  );
}

export function ThreadsTab({
  view,
  onOpen,
  onReply,
  onResolve,
}: {
  view: ReviewView;
  onOpen: (path: string, line?: number | null, side?: "RIGHT" | "LEFT") => void;
  onReply: (t: ReviewThread, body: string) => Promise<void>;
  onResolve: (t: ReviewThread, resolved: boolean) => Promise<void>;
}) {
  const sorted = [...view.threads].sort(
    (a, b) => Number(a.resolved) - Number(b.resolved),
  );
  if (!view.threads.length && !view.conversation.length) {
    return (
      <p className="rv-dim">
        {view.kind === "pr"
          ? `No comments on ${prLabel(view.host, view.number)} yet.`
          : "A branch has no threads. Its pull request does."}
      </p>
    );
  }
  return (
    <div className="rv-stack">
      {sorted.map((t) => (
        <ThreadCard
          key={t.id}
          t={t}
          canPost={view.can_post}
          onOpen={onOpen}
          onReply={onReply}
          onResolve={onResolve}
        />
      ))}
      {view.conversation.length > 0 && (
        <section className="rv-group">
          <h4>Conversation</h4>
          {view.conversation.map((c) => (
            <div key={c.id} className="rv-comment">
              <b>{c.author}</b>{" "}
              <span className="rv-dim">{c.at.slice(0, 10)}</span>
              <Markdown text={c.body} fold={10} />
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

export function ThreadCard({
  t,
  canPost,
  onOpen,
  onReply,
  onResolve,
  compact,
}: {
  t: ReviewThread;
  canPost: boolean;
  compact?: boolean;
  onOpen?: (
    path: string,
    line?: number | null,
    side?: "RIGHT" | "LEFT",
  ) => void;
  onReply: (t: ReviewThread, body: string) => Promise<void>;
  onResolve: (t: ReviewThread, resolved: boolean) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [replying, setReplying] = useState(false);
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    try {
      await onReply(t, text.trim());
      setText("");
      setReplying(false);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      className={`rv-thread${t.resolved ? " resolved" : ""}`}
      data-thread={t.id}
    >
      {!compact && t.path && onOpen && (
        <button
          type="button"
          className="rv-link mono rv-where"
          onClick={() => onOpen(t.path!, t.line, t.side)}
        >
          {t.path}
          {t.line ? `:${t.line}` : ""}
        </button>
      )}
      {t.comments.map((c, i) => (
        <div key={c.id} className="rv-comment">
          <div className="rv-comment-h">
            <b>{c.author}</b>{" "}
            <span className="rv-dim">{c.at.slice(0, 10)}</span>
            {i === 0 &&
              (t.resolved ? (
                <Pill tone="ok">resolved</Pill>
              ) : (
                <Pill tone="warn">open</Pill>
              ))}
            {i === 0 && t.outdated && <Pill tone="idle">outdated</Pill>}
          </div>
          <Markdown text={c.body} fold={10} />
        </div>
      ))}
      {canPost &&
        (replying ? (
          <form
            className="rv-reply"
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim()) void send();
            }}
          >
            <textarea
              className="rv-ta"
              aria-label="Your reply"
              value={text}
              onChange={(e) => setText(e.target.value)}
              autoFocus
            />
            <div className="rv-row">
              <button
                type="submit"
                className="btn sm primary"
                disabled={busy || !text.trim()}
              >
                Reply
              </button>
              <button
                type="button"
                className="btn sm ghost"
                onClick={() => setReplying(false)}
              >
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <div className="rv-row">
            <button
              type="button"
              className="btn sm"
              onClick={() => setReplying(true)}
            >
              Reply
            </button>
            <button
              type="button"
              className="btn sm ghost"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                void onResolve(t, !t.resolved).finally(() => setBusy(false));
              }}
            >
              {t.resolved ? "Open again" : "Resolve"}
            </button>
          </div>
        ))}
    </div>
  );
}

const STEPS: { id: string; title: string; text: string }[] = [
  {
    id: "broad",
    title: "Broad view (5 min)",
    text: "Read the description, the ticket and keel's overview. Should this change exist at all? Is CI green?",
  },
  {
    id: "ticket",
    title: "Ticket (5–10 min)",
    text: "Check what the ticket asks. Read the test of one thing marked done.",
  },
  {
    id: "tests",
    title: "Tests first, then the files to open first",
    text: "Send design comments early, before you read the rest.",
  },
  {
    id: "findings",
    title: "keel's findings, blocking first",
    text: "Add as comment, or dismiss with a reason. You stay responsible.",
  },
  {
    id: "security",
    title: "The rest, plus one security pass",
    text: "Auth, input, data exposure, secrets. Look outside the diff where needed.",
  },
  {
    id: "comments",
    title: "Write comments",
    text: "Label them (issue, suggestion, nitpick; blocking or not). Say why. Praise good parts.",
  },
  {
    id: "decide",
    title: "Decide",
    text: "Approve when it makes the code better, even if not perfect. Request changes only for blocking items.",
  },
];

export function ChecklistTab({
  view,
  ai,
  checked,
  onCheck,
}: {
  view: ReviewView;
  ai: AiState | null;
  checked: string[];
  onCheck: (id: string, on: boolean) => void;
}) {
  const ci = view.checks.length
    ? `${view.checks.filter((c) => c.state === "success" || c.state === "skipped" || c.state === "neutral").length}/${view.checks.length} green`
    : "no checks";
  const r = ai?.findings?.result;
  const hint: Record<string, string> = {
    broad: `CI: ${ci}.`,
    findings: r?.findings
      ? `${r.findings.length} finding(s), ${(r.findings ?? []).filter((f) => ai?.decisions[f.id]).length} decided.`
      : "Run Find problems first.",
    security: r?.security ? `keel checked: ${r.security}` : "",
    comments: `${view.drafts.length} pending comment(s).`,
  };
  return (
    <ol className="rv-check">
      {STEPS.map((s) => (
        <li key={s.id}>
          <label className="chk">
            <input
              type="checkbox"
              checked={checked.includes(s.id)}
              onChange={(e) => onCheck(s.id, e.target.checked)}
            />
            <span>
              <b>{s.title}</b>
              <br />
              <span className="rv-dim">
                {s.text}
                {hint[s.id] ? ` ${hint[s.id]}` : ""}
              </span>
            </span>
          </label>
        </li>
      ))}
      <li className="rv-dim">
        About 60 minutes and 200–400 lines per sitting find the most. Ask for a
        split when it is bigger.
      </li>
    </ol>
  );
}
