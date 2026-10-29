// The new Review view's Files tab (0.18.57): the changed files with a Viewed tick, and the chosen file's changed lines.
// A line number starts a comment on that line (it waits in your review until you submit); a comment that is there
// already shows under its line and opens in the Comments tab.
//
//   ┌ refunds.py  +24 −3 · 2 comments [✓] ┐ ┌ orders/refunds.py        Click a line number to comment ┐
//   │ views.py    +3 −1               [ ] │ │  13 − return order.total - already                        │
//   └─────────────────────────────────────┘ │  13 + return order.paid_total - already                   │
//                                           │      [bo  Should this also take off the coupon? ● Not right]│
//                                           │  20 + if amount > refundable_amount(order):               │
//                                           │      [Your comment on this line…] [Add to my review]      │
//
// The same api and store as the classic view: reviewApi.diff, R.toggleViewed, R.addDraft, R.deleteDraft.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  askAssistant,
  ErrorBox,
  go,
  parseDiff,
  searchTextOf,
  SelectionBar,
  selectionReviewRequest,
  SLOTS,
  useLoad,
  useSlot,
  useStored,
  type AssistantItem,
  type DiffRow,
  type SelectionAction,
} from "@keel/web-sdk";
import { prLabel, prWord, reviewApi, type AiState, type ReviewView } from "../reviewApi";
import * as R from "../store";
import { firstLine } from "../Threads";
import { same, threadPill } from "./model";
import { Tag } from "./ui";

type Side = "RIGHT" | "LEFT";
const isText = (v: unknown): v is string => typeof v === "string";

export function FilesTab({
  pid,
  reviewKey,
  view,
  ai,
  running,
  focus,
  onOpenThread,
  onOpenEditor,
}: {
  pid: string;
  reviewKey: string;
  view: ReviewView;
  ai: AiState | null;
  running: string[];
  /** a file and line to show (a comment's "Show in the file"), n changes every time */
  focus: { path: string; line: number | null; n: number } | null;
  onOpenThread: (id: string) => void;
  /** the classic view's editor tab of this file, with all its tools */
  onOpenEditor: (path: string, line: number | null) => void;
}) {
  const [stored, setPath] = useStored<string>(
    `keel2.review.next.file.${pid}.${reviewKey}`,
    "",
    { valid: isText },
  );
  const path = view.files.some((f) => f.path === stored)
    ? stored
    : (view.files[0]?.path ?? "");
  useEffect(() => {
    if (!focus) return;
    setPath(focus.path);
    if (focus.line == null) return;
    // the line shows once its file's diff is here
    let tries = 0;
    const t = window.setInterval(() => {
      const el = document.querySelector<HTMLElement>(
        `[data-rvn-line="${CSS.escape(`${focus.path}:${focus.line}`)}"]`,
      );
      if (el || ++tries > 20) {
        window.clearInterval(t);
        el?.scrollIntoView?.({ block: "center" });
        el?.querySelector<HTMLElement>("button")?.focus();
      }
    }, 100);
    return () => window.clearInterval(t);
  }, [focus?.n]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!view.files.length)
    return <p className="rvn-empty">This review changes no file.</p>;
  const count = (p: string) =>
    view.threads.filter((t) => t.path === p).length +
    view.drafts.filter((d) => d.path === p).length;

  return (
    <div className="rvn-files">
      <div className="rvn-flist-w">
        <p className="rvn-dim rvn-flist-h">
          {view.viewed.length} of {view.files.length} viewed
        </p>
        <ul className="rvn-flist" aria-label="Changed files">
          {view.files.map((f) => {
            const name = f.path.slice(f.path.lastIndexOf("/") + 1);
            // 0.18.68 the folder on its own dim line (it was only in the tooltip); a long one keeps its nearest folders
            const dir = f.path.slice(0, f.path.lastIndexOf("/") + 1);
            const n = count(f.path);
            const viewed = view.viewed.includes(f.path);
            return (
              <li key={f.path} className={f.path === path ? "on" : undefined}>
                <button
                  type="button"
                  className="rvn-fpick"
                  title={f.path}
                  aria-current={f.path === path || undefined}
                  aria-label={`Show ${f.path}`}
                  onClick={() => setPath(f.path)}
                >
                  {dir && (
                    <span className="rvn-fdir" data-testid="rvn-fdir">
                      {dir}
                      {"\u200e"}
                    </span>
                  )}
                  <span className="rvn-fname">{name}</span>
                  <span className="rvn-fmeta">
                    <span className="rvn-add">+{f.added}</span>{" "}
                    <span className="rvn-del">−{f.removed}</span>
                    {n > 0 && ` · ${n} comment${n === 1 ? "" : "s"}`}
                  </span>
                </button>
                <input
                  type="checkbox"
                  className="rvn-viewed"
                  checked={viewed}
                  aria-label={`Viewed: ${f.path}`}
                  title="Viewed"
                  disabled={running.includes("viewed")}
                  onChange={() => void R.toggleViewed(pid, reviewKey, f.path)}
                />
              </li>
            );
          })}
        </ul>
      </div>
      {path && (
        <FileDiff
          key={path}
          pid={pid}
          reviewKey={reviewKey}
          view={view}
          ai={ai}
          running={running}
          path={path}
          onOpenThread={onOpenThread}
          onOpenEditor={onOpenEditor}
        />
      )}
    </div>
  );
}

function FileDiff({
  pid,
  reviewKey,
  view,
  ai,
  running,
  path,
  onOpenThread,
  onOpenEditor,
}: {
  pid: string;
  reviewKey: string;
  view: ReviewView;
  ai: AiState | null;
  running: string[];
  path: string;
  onOpenThread: (id: string) => void;
  onOpenEditor: (path: string, line: number | null) => void;
}) {
  const d = useLoad(
    `review-diff:${pid}:${reviewKey}:${path}:${view.base_sha}`,
    () => reviewApi.diff(pid, reviewKey, path),
    {
      live: false,
    },
  );
  const rows = useMemo(
    () => (d.data ? parseDiff(d.data.diff).rows : []),
    [d.data],
  );
  const [at, setAt] = useState<{ line: number; side: Side } | null>(null);
  const [text, setText] = useState("");
  const viewed = view.viewed.includes(path);
  const add = async () => {
    if (!at || !text.trim()) return;
    const ok = await R.addDraft(pid, reviewKey, text.trim(), {
      path,
      line: at.line,
      side: at.side,
    });
    if (ok) {
      setAt(null);
      setText("");
    }
  };
  const lineOf = (r: DiffRow): [number, Side] | null =>
    r.kind === "del"
      ? r.old != null
        ? [r.old, "LEFT"]
        : null
      : r.new != null
        ? [r.new, "RIGHT"]
        : null;
  const err = R.review(pid, reviewKey).err;

  // 0.18.63 the bar over selected lines: Search the project for them, ask KeelBot about them, or have it review them
  const code = useRef<HTMLDivElement>(null);
  const assistant = useSlot<AssistantItem>(SLOTS.assistant)[0] ?? null;
  const actions = useMemo<SelectionAction[]>(() => {
    const out: SelectionAction[] = [{
      id: "search", label: "Search", title: "Search the project for the selected text (Code › Search)",
      run: () => {
        const t = searchTextOf(pickedLines(code.current, path)?.first ?? "");
        if (t) location.hash = `#/repo?search=${encodeURIComponent(t)}`;
      },
    }];
    const helper = assistant?.title;
    if (!helper) return out;
    const where = view.kind === "pr" ? `${prWord(view.host)} ${prLabel(view.host, view.number)}` : `branch ${view.branch}`;
    const sha = view.head_sha.slice(0, 12);
    const hand = (text: string) => {
      askAssistant(text);
      go("helper");
    };
    return [
      ...out,
      {
        id: "ask", label: `Ask ${helper}`, title: `Ask ${helper} about the selected lines`,
        run: () => {
          const s = pickedLines(code.current, path);
          if (s) hand(`About ${s.where} in ${where} (commit ${sha}; read it with git show ${sha}:${path}):\n\n\`\`\`\n${s.text}\n\`\`\`\n\nMy question: `);
        },
      },
      {
        id: "review", label: "Review", title: `${helper} reviews the selected lines: bugs, missed cases, risks`,
        run: () => {
          const s = pickedLines(code.current, path);
          if (s) hand(selectionReviewRequest(`${s.where} in ${where} (commit ${sha}; the file: git show ${sha}:${path})`, s.text));
        },
      },
    ];
  }, [view, path, assistant]);

  return (
    <section className="rvn-diff" aria-label={`Changes of ${path}`}>
      <div className="rvn-diff-h">
        <span className="rvn-mono rvn-diff-p" title={path}>
          {path}
        </span>
        <span className="rvn-dim">Click a line number to comment</span>
        <button
          type="button"
          className="rvn-link"
          onClick={() => onOpenEditor(path, null)}
        >
          Open in the editor
        </button>
        <label className="rvn-row rvn-dim">
          <input
            type="checkbox"
            checked={viewed}
            disabled={running.includes("viewed")}
            onChange={() => void R.toggleViewed(pid, reviewKey, path)}
          />
          Viewed
        </label>
      </div>
      {d.error && <ErrorBox error={d.error} onRetry={() => void d.reload()} />}
      {!d.data && !d.error && (
        <span className="pg-spin rvn-pad" role="status">
          Reading the changes…
        </span>
      )}
      {d.data?.binary && (
        <p className="rvn-empty">A binary file: no lines to show.</p>
      )}
      {d.data && !d.data.binary && <SelectionBar within={code} actions={actions} />}
      {d.data && !d.data.binary && (
        <div className="rvn-code" ref={code}>
          {rows.map((r, i) => {
            if (r.kind === "hunk" || r.kind === "meta")
              return (
                <div key={i} className="rvn-dl hunk">
                  <span>{r.text}</span>
                </div>
              );
            const ln = lineOf(r);
            const marks = ln
              ? view.threads.filter(
                  (t) =>
                    t.path === path &&
                    t.line === ln[0] &&
                    (t.side ?? "RIGHT") === ln[1],
                )
              : [];
            const drafts = ln
              ? view.drafts.filter(
                  (x) =>
                    x.path === path &&
                    x.line === ln[0] &&
                    (x.side ?? "RIGHT") === ln[1],
                )
              : [];
            const composing = !!ln && at?.line === ln[0] && at.side === ln[1];
            return (
              <div
                key={i}
                className="rvn-dl-w"
                data-rvn-line={
                  ln && ln[1] === "RIGHT" ? `${path}:${ln[0]}` : undefined
                }
                data-rvn-n={ln ? `${ln[1] === "LEFT" ? "-" : ""}${ln[0]}` : undefined}
              >
                <div className={`rvn-dl ${r.kind}`}>
                  <span className="rvn-dl-n">
                    {ln && (
                      <button
                        type="button"
                        aria-label={`Comment on line ${ln[0]}${ln[1] === "LEFT" ? " (old)" : ""}`}
                        onClick={() => {
                          setAt({ line: ln[0], side: ln[1] });
                          setText("");
                        }}
                      >
                        {ln[0]}
                      </button>
                    )}
                  </span>
                  <span className="rvn-dl-m" aria-hidden="true">
                    {r.kind === "add" ? "+" : r.kind === "del" ? "−" : ""}
                  </span>
                  <span className="rvn-dl-t">{r.text || " "}</span>
                </div>
                {marks.map((t) => {
                  const [pill, tone] = threadPill(
                    t,
                    view,
                    ai,
                    running.includes(`judge:${t.id}`),
                  );
                  const first = t.comments[0];
                  return (
                    <button
                      key={t.id}
                      type="button"
                      className="rvn-mark"
                      aria-label={`Open the comment of ${first?.author ?? "someone"} on line ${t.line}`}
                      onClick={() => onOpenThread(t.id)}
                    >
                      <b>
                        {first
                          ? same(first.author, view.me)
                            ? "You"
                            : first.author
                          : "Comment"}
                      </b>
                      <span className="rvn-mark-t">
                        {firstLine(first?.body ?? "")}
                      </span>
                      <Tag tone={tone}>{pill}</Tag>
                    </button>
                  );
                })}
                {drafts.map((x) => (
                  <div key={x.id} className="rvn-mark pending">
                    <Tag tone="run">pending</Tag>
                    <span className="rvn-mark-t">{firstLine(x.body)}</span>
                    <button
                      type="button"
                      className="rvn-x"
                      aria-label={`Remove the pending comment on line ${x.line}`}
                      onClick={() => void R.deleteDraft(pid, reviewKey, x)}
                    >
                      ×
                    </button>
                  </div>
                ))}
                {composing && (
                  <form
                    className="rvn-compose"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void add();
                    }}
                  >
                    <textarea
                      className="rvn-ta"
                      rows={2}
                      autoFocus
                      aria-label={`Your comment on line ${at!.line}`}
                      placeholder="Your comment on this line (pending until you submit)"
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") setAt(null);
                        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                          e.preventDefault();
                          void add();
                        }
                      }}
                    />
                    <div className="rvn-row">
                      <button
                        type="submit"
                        className="rvn-btn primary"
                        disabled={!text.trim() || running.includes("draft")}
                      >
                        Add to my review
                      </button>
                      <button
                        type="button"
                        className="rvn-btn"
                        onClick={() => setAt(null)}
                      >
                        Cancel
                      </button>
                    </div>
                    {err && <ErrorBox error={err} />}
                  </form>
                )}
              </div>
            );
          })}
          {d.data.truncated && (
            <p className="rvn-dim rvn-pad">
              The diff is long: keel shows its start.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

/** The diff lines the selection covers, as KeelBot gets them: "+"/"−" marks kept, and where they are ("path:12-18"). */
export function pickedLines(root: HTMLElement | null, path: string): { where: string; text: string; first: string } | null {
  const sel = window.getSelection();
  if (!root || !sel || sel.isCollapsed || !sel.anchorNode || !sel.focusNode) return null;
  const rowOf = (n: Node) => (n instanceof Element ? n : n.parentElement)?.closest<HTMLElement>(".rvn-dl-w") ?? null;
  const a = rowOf(sel.anchorNode);
  const b = rowOf(sel.focusNode);
  if (!a || !b || !root.contains(a) || !root.contains(b)) return null;
  const all = [...root.querySelectorAll<HTMLElement>(".rvn-dl-w")];
  const [i, j] = [all.indexOf(a), all.indexOf(b)].sort((x, y) => x - y);
  const rows = all.slice(i, j + 1);
  const codeOf = (r: HTMLElement) => r.querySelector(".rvn-dl-t");
  // one row: the part selected (all of its code when the whole row is), never the line number's text
  const one = () => {
    const c = codeOf(rows[0]);
    return c && sel.containsNode(c, false) ? c.textContent ?? "" : sel.toString();
  };
  const text = rows.length === 1
    ? one().trim()
    : rows.map((r) => {
        const dl = r.querySelector(".rvn-dl");
        const mark = dl?.classList.contains("add") ? "+" : dl?.classList.contains("del") ? "-" : " ";
        return `${mark} ${codeOf(r)?.textContent ?? ""}`;
      }).join("\n");
  if (!text.trim()) return null;
  const ns = rows.map((r) => r.dataset.rvnN).filter((n): n is string => !!n);
  const news = ns.filter((n) => !n.startsWith("-")).map(Number);
  const olds = ns.filter((n) => n.startsWith("-")).map((n) => Number(n.slice(1)));
  const span = (xs: number[]) => (xs.length ? (xs[0] === xs[xs.length - 1] ? `${xs[0]}` : `${xs[0]}-${xs[xs.length - 1]}`) : "");
  const where = news.length ? `${path}:${span(news)}` : olds.length ? `${path} (old file) :${span(olds)}` : path;
  const first = rows.length === 1 ? text : codeOf(rows[0])?.textContent ?? "";
  return { where: rows.length > 1 ? `the lines ${where}` : `line ${where}`, text: text.slice(0, 8000), first };
}
