// v0.14.0 one file of a review in an editor tab: its diff (side by side or inline) with the threads, your pending
// comments and keel's findings on their lines, or the whole file as the reviewed commit has it (Jump to source, Go to
// declaration). A small bar: viewed, diff or whole file, previous / next file.

import { useEffect, useState } from "react";
import { Pill } from "@keel/web-sdk";
import { prLabel } from "./reviewApi";
import { neighbour } from "./ReviewSide";
import { FileAtView, ReviewDiff, type Anchor } from "./ReviewDiff";
import { findingComment, ThreadCard } from "./ReviewPanel";
import { askAboutFinding } from "./ReviewSide";
import { keysFor } from "./keymap";
import * as R from "./store";
import type { Draft, Finding } from "./reviewApi";

export function ReviewFileTab({
  pid,
  reviewKey,
  path,
  view,
  mode,
}: {
  pid: string;
  reviewKey: string;
  path: string;
  view: R.FileView;
  mode: "split" | "inline";
}) {
  R.useReviewStore();
  const r = R.review(pid, reviewKey);
  const ui = R.uiState();
  useEffect(() => {
    if (!r.view && !r.loading && !r.error) void R.load(pid, reviewKey);
  }, [pid, reviewKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!r.ai) void R.loadAi(pid, reviewKey);
  }, [pid, reviewKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const v = r.view;
  const focus = ui.focus && ui.focus.key === reviewKey && ui.focus.path === path && ui.focus.line ? { line: ui.focus.line, side: ui.focus.side, n: ui.focus.n } : null;
  const change =
    ui.change && ui.change.key === reviewKey && ui.change.path === path
      ? { index: ui.change.index, n: ui.change.n }
      : null;
  const [, setChanges] = useState(0);

  if (!v)
    return (
      <div className="ed-note">
        {r.error ? (
          r.error.message
        ) : (
          <span className="pg-spin" role="status">
            Opening the review…
          </span>
        )}
      </div>
    );
  const isChanged = v.files.some((f) => f.path === path);
  const viewed = v.viewed.includes(path);
  const findings = r.ai?.findings?.result?.findings ?? [];
  const canComment = v.can_post || v.kind === "branch";

  const anchors: Anchor[] = [];
  for (const t of v.threads.filter((x) => x.path === path && x.line)) {
    anchors.push({
      line: t.line!,
      side: t.side,
      node: (
        <div className="rv-under">
          <ThreadCard
            t={t}
            canPost={v.can_post}
            compact
            onReply={(th, b) => R.reply(pid, reviewKey, th.id, b)}
            onResolve={(th, x) => R.resolve(pid, reviewKey, th.id, x)}
          />
        </div>
      ),
    });
  }
  for (const d of v.drafts.filter((x) => x.path === path && x.line)) {
    anchors.push({
      line: d.line!,
      side: d.side,
      node: (
        <DraftCard
          d={d}
          onSave={(b) => void R.editDraft(pid, reviewKey, d, b)}
          onDelete={() => void R.deleteDraft(pid, reviewKey, d)}
        />
      ),
    });
  }
  for (const f of findings.filter((x) => x.path === path && x.line)) {
    const dec = r.ai?.decisions[f.id];
    if (
      dec?.decision === "dismissed" ||
      dec?.decision === "commented" ||
      v.drafts.some((d) => d.finding_id === f.id)
    )
      continue;
    anchors.push({
      line: f.line!,
      side: f.side,
      node: (
        <InlineFinding
          f={f}
          canComment={canComment}
          onComment={() =>
            void R.addDraft(
              pid,
              reviewKey,
              findingComment(f, v.host?.kind === "gitlab"),
              { path, line: f.line!, side: f.side },
              f.id,
            ).then((d) => d && R.decide(pid, reviewKey, f.id, "commented"))
          }
          onDismiss={(why) =>
            void R.decide(pid, reviewKey, f.id, "dismissed", why)
          }
          onAsk={() => askAboutFinding(v, f)}
        />
      ),
    });
  }
  const composing =
    ui.composing && ui.composing.key === reviewKey && ui.composing.path === path
      ? ui.composing
      : null;
  if (composing?.line) {
    anchors.push({
      line: composing.line,
      side: composing.side,
      node: (
        <CommentBox
          where={`${path}:${composing.line}${composing.side === "LEFT" ? " (old side)" : ""}`}
          busy={r.busy === "draft"}
          onCancel={() => R.setUi({ composing: null })}
          onSave={(b) =>
            void R.addDraft(pid, reviewKey, b, {
              path,
              line: composing.line!,
              side: composing.side,
            })
          }
        />
      ),
    });
  }
  const go = (dir: 1 | -1) => {
    const next = neighbour(v, path, dir);
    if (next)
      R.openPlace({
        key: reviewKey,
        path: next,
        view: "diff",
        line: null,
        side: "RIGHT",
      });
  };

  return (
    <div
      className="rv-ftab"
      role="region"
      aria-label={`${path} in review ${v.kind === "pr" ? prLabel(v.host, v.number) : v.branch}`}
    >
      <div className="rv-fbar">
        <Pill tone="run">
          {v.kind === "pr" ? prLabel(v.host, v.number) : v.branch}
        </Pill>
        {isChanged && (
          <label className="chk rv-viewed">
            <input
              type="checkbox"
              checked={viewed}
              onChange={() => void R.toggleViewed(pid, reviewKey, path)}
            />{" "}
            Viewed
          </label>
        )}
        {isChanged && (
          <span className="rv-seg" role="group" aria-label="Show">
            <button
              type="button"
              aria-pressed={view === "diff"}
              onClick={() =>
                R.openPlace({
                  key: reviewKey,
                  path,
                  view: "diff",
                  line: ui.current?.line ?? null,
                  side: "RIGHT",
                })
              }
            >
              Changes
            </button>
            <button
              type="button"
              aria-pressed={view === "code"}
              onClick={() =>
                R.openPlace({
                  key: reviewKey,
                  path,
                  view: "code",
                  line: ui.current?.line ?? null,
                  side: "RIGHT",
                })
              }
              title={`Jump to source (${keysFor("jumpToSource")})`}
            >
              Whole file
            </button>
          </span>
        )}
        <span className="rv-fbar-r">
          {v.drafts.length > 0 && (
            <span className="rv-dim">{v.drafts.length} pending</span>
          )}
          <button
            type="button"
            className="ib"
            aria-label="Previous file"
            title={`Previous file (${keysFor("prevFile")})`}
            onClick={() => go(-1)}
          >
            ‹
          </button>
          <button
            type="button"
            className="ib"
            aria-label="Next file"
            title={`Next file (${keysFor("nextFile")})`}
            onClick={() => go(1)}
          >
            ›
          </button>
        </span>
      </div>
      {view === "code" || !isChanged ? (
        <FileAtView
          pid={pid}
          reviewKey={reviewKey}
          path={path}
          line={focus?.line ?? null}
          changed={isChanged}
          onWord={(w) => void R.lookUp(pid, reviewKey, w, "declaration")}
          onBackToDiff={() =>
            R.openPlace({
              key: reviewKey,
              path,
              view: "diff",
              line: null,
              side: "RIGHT",
            })
          }
        />
      ) : (
        <ReviewDiff
          pid={pid}
          reviewKey={reviewKey}
          path={path}
          mode={mode}
          anchors={anchors}
          focus={focus}
          change={change}
          onChanges={(n) => {
            setChanges(n);
            R.uiState().changes = n;
          }}
          onLine={(l) =>
            R.setUi({
              current: {
                key: reviewKey,
                path,
                view: "diff",
                line: l.line,
                side: l.side,
              },
              composing: canComment
                ? {
                    key: reviewKey,
                    path,
                    view: "diff",
                    line: l.line,
                    side: l.side,
                  }
                : null,
            })
          }
          onWord={(w, l) => {
            if (l)
              R.setUi({
                current: {
                  key: reviewKey,
                  path,
                  view: "diff",
                  line: l.line,
                  side: l.side,
                },
              });
            void R.lookUp(pid, reviewKey, w, "declaration");
          }}
        />
      )}
      <div className="rv-hint" aria-hidden="true">
        {keysFor("nextChange")} next change · {keysFor("declaration")}{" "}
        declaration · {keysFor("usages")} usages · {keysFor("back")} back ·{" "}
        {keysFor("findAction")} all keys
      </div>
    </div>
  );
}

function CommentBox({
  where,
  busy,
  onSave,
  onCancel,
}: {
  where: string;
  busy: boolean;
  onSave: (body: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState("");
  return (
    <form
      className="rv-under rv-compose"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) onSave(text.trim());
      }}
    >
      <span className="rv-dim">Comment on {where}</span>
      <textarea
        className="rv-ta"
        aria-label={`Comment on ${where}`}
        value={text}
        onChange={(e) => setText(e.target.value)}
        autoFocus
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && text.trim()) {
            e.preventDefault();
            e.stopPropagation();
            onSave(text.trim());
          }
        }}
        placeholder="issue (blocking): … · suggestion: … · question: …"
      />
      <div className="rv-row">
        <button
          type="submit"
          className="btn sm primary"
          disabled={busy || !text.trim()}
        >
          Add comment
        </button>
        <button type="button" className="btn sm ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export function DraftCard({
  d,
  onSave,
  onDelete,
}: {
  d: Draft;
  onSave: (b: string) => void;
  onDelete: () => void;
}) {
  const [edit, setEdit] = useState(false);
  const [text, setText] = useState(d.body);
  return (
    <div className="rv-under rv-draft" data-draft={d.id}>
      <div className="rv-comment-h">
        <Pill tone="run">pending</Pill>
        {d.finding_id && <span className="rv-dim">from keel</span>}
      </div>
      {edit ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSave(text);
            setEdit(false);
          }}
        >
          <textarea
            className="rv-ta"
            aria-label="Edit the pending comment"
            value={text}
            onChange={(e) => setText(e.target.value)}
            autoFocus
          />
          <div className="rv-row">
            <button type="submit" className="btn sm primary">
              Save
            </button>
            <button
              type="button"
              className="btn sm ghost"
              onClick={() => setEdit(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          <pre className="rv-body-text">{d.body}</pre>
          <div className="rv-row">
            <button
              type="button"
              className="btn sm ghost"
              onClick={() => setEdit(true)}
            >
              Edit
            </button>
            <button type="button" className="btn sm ghost" onClick={onDelete}>
              Delete
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function InlineFinding({
  f,
  canComment,
  onComment,
  onDismiss,
  onAsk,
}: {
  f: Finding;
  canComment: boolean;
  onComment: () => void;
  onDismiss: (why: string) => void;
  onAsk: () => void;
}) {
  const [asking, setAsking] = useState(false);
  const [why, setWhy] = useState("");
  return (
    <div className="rv-under rv-ai" data-finding={f.id}>
      <div className="rv-comment-h">
        <Pill
          tone={
            f.severity === "blocking"
              ? "bad"
              : f.severity === "should_fix"
                ? "warn"
                : "idle"
          }
        >
          {f.severity.replace("_", " ")}
        </Pill>
        {f.check === "confirmed" && <Pill tone="ok">checked</Pill>}
        {f.check === "not checked" && <Pill tone="warn">not checked</Pill>}
        <b>keel</b>
        <span className="rv-dim">{f.category}</span>
      </div>
      <p>
        <b>{f.title}</b>
        {f.why ? ` ${f.why}` : ""}
      </p>
      {f.suggestion && <pre className="rv-sugg">{f.suggestion}</pre>}
      {asking ? (
        <form
          className="rv-row"
          onSubmit={(e) => {
            e.preventDefault();
            if (why.trim()) onDismiss(why.trim());
          }}
        >
          <input
            className="rv-in"
            aria-label={`Why dismiss: ${f.title}`}
            value={why}
            onChange={(e) => setWhy(e.target.value)}
            placeholder="why it is not a problem"
            autoFocus
          />
          <button type="submit" className="btn sm" disabled={!why.trim()}>
            Dismiss
          </button>
        </form>
      ) : (
        <div className="rv-row">
          {canComment && (
            <button
              type="button"
              className="btn sm primary"
              onClick={onComment}
            >
              Add as comment
            </button>
          )}
          <button
            type="button"
            className="btn sm ghost"
            onClick={() => setAsking(true)}
          >
            Dismiss…
          </button>
          <button type="button" className="btn sm ghost" onClick={onAsk}>
            Ask KeelBot
          </button>
        </div>
      )}
    </div>
  );
}
