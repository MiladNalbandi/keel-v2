// The new Review view's list (0.18.57, the redesign's Work › Review), in the Code page's Review side bar: what needs you,
// the pull requests by All / To review / Mine / Assigned with a search, and "Review #N" for the next one that asks for
// you. A pull request opens in an editor tab (NextReview), with the whole width for its comments and files.
//
//   Review                          [ Classic | New view ]
//   2 reviews are asked of you · 1 of yours is open
//   [Refresh] [Review #45]
//   (All 5) (To review 2) (Mine 2) (Assigned 1)   [search…]
//   ┃ #42 Add refunds to orders
//   ┃ ● 2 comments wait for you · ana · 20 min ago · feature/refunds
//   GitHub · acme/shop · read just now
//
// 0.28 (the redesign's board PgReview) on the Review page it draws the page: the head (Review, that line, Refresh and
// "Review #N" with its key ⇧N) over two columns, this branch as a card at the top of the list, the pull request (the
// page's `main`) on the right.
//
// It reads the same api as the classic view (reviewApi.prs, reviewApi.branch) and the same store (a review the classic
// view or a tab loaded already tells how many comments wait for you).

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ErrorBox,
  useApp,
  useLoad,
  useStored,
  type CodeOpen,
} from "@keel/web-sdk";
import {
  decisionText,
  prLabel,
  prWord,
  reviewApi,
  type PrSummary,
} from "../reviewApi";
import * as R from "../store";
import { ago, waitsForYou } from "../Threads";
import {
  isListFilter,
  LIST_FILTERS,
  matchesSearch,
  nextUp,
  prPill,
  type ListFilter,
  type ReviewMode,
} from "./model";
import { ModeSwitch } from "./Switch";
import { Tag } from "./ui";

/** A pull request of the new view opens as an editor tab of the review kind with no file: "pr:42|" (a review's file
 *  tab is "pr:42|src/a.kt"), so the Code page needs no new tab kind. */
export const nextTab = (key: string) => ({ kind: "review", path: `${key}|` });
/** What you sent per review in this browser tab ("you approved"): the list's pill and "Review #N" skip it. */
export const SENT = (pid: string) => `keel2.review.next.sent.${pid}`;
const isSent = (v: unknown): v is Record<string, string> =>
  !!v &&
  typeof v === "object" &&
  Object.values(v as object).every((x) => typeof x === "string");

/** A review's link (#/repo/@review/pr:7) opened while the new view is not there yet: it opens once the view is. */
const waiting = new Map<string, string>();
export function linkToNext(pid: string, key: string) {
  waiting.set(pid, key);
  window.dispatchEvent(
    new CustomEvent("keel:open-review-next", { detail: { pid, key } }),
  );
}

export function NextSide({
  pid,
  open,
  activeKey,
  mode,
  onMode,
  page,
}: {
  pid: string;
  open: CodeOpen;
  /** the review whose tab the editor shows now */
  activeKey: string | null;
  /** the Classic | New view switch; the Review page (0.18.62) has none */
  mode?: ReviewMode;
  onMode?: (m: ReviewMode) => void;
  /** 0.28 the Review page: NextSide draws the page head and both columns; `main` is the pull request's column */
  page?: { main: ReactNode; showList: boolean; phone: boolean };
}) {
  R.useReviewStore();
  const { toast } = useApp();
  const [filter, setFilter] = useStored<ListFilter>(
    `keel2.review.next.filter.${pid}`,
    "all",
    { valid: isListFilter },
  );
  const [q, setQ] = useState("");
  const [sent] = useStored<Record<string, string>>(
    SENT(pid),
    {},
    { valid: isSent },
  );
  const list = useLoad(`review-prs:${pid}:${filter}`, () =>
    reviewApi.prs(pid, filter),
  );
  // "Review #N" needs the pull requests that ask for you, whatever the filter shows
  const asked = useLoad(
    filter === "review" ? null : `review-prs:${pid}:review`,
    () => reviewApi.prs(pid, "review"),
  );
  const branch = useLoad(`review-branch:${pid}`, () => reviewApi.branch(pid));
  const [readAt, setReadAt] = useState<string | null>(null);
  useEffect(() => {
    if (list.data) setReadAt(new Date().toISOString());
  }, [list.data]);

  const pick = (key: string) =>
    open(nextTab(key), { pin: true });
  // a review's link (the launcher, #/repo/@review/pr:7) opens its tab; after the Code page showed this side bar
  useEffect(() => {
    const go = (key: string) => {
      waiting.delete(pid);
      window.setTimeout(
        () => open(nextTab(key), { pin: true }),
        0,
      );
    };
    const early = waiting.get(pid);
    if (early) go(early);
    const on = (e: Event) => {
      const d = (e as CustomEvent<{ pid: string; key: string }>).detail;
      if (d?.pid === pid && d.key) go(d.key);
    };
    window.addEventListener("keel:open-review-next", on);
    return () => window.removeEventListener("keel:open-review-next", on);
  }, [pid, open]);

  const host = list.data?.host ?? null;
  const word = prWord(host);
  const counts = list.data?.counts ?? {};
  const askedList = filter === "review" ? list.data?.prs : asked.data?.prs;
  const next = nextUp(askedList ?? [], Object.keys(sent));
  const shown = useMemo(
    () => (list.data?.prs ?? []).filter((p) => matchesSearch(p, q)),
    [list.data, q],
  );
  const waitingOn = (p: PrSummary): number | null => {
    const v = R.review(pid, `pr:${p.number}`).view;
    return v ? v.threads.filter((t) => waitsForYou(t, v.me)).length : null;
  };
  const head =
    list.data && !list.data.note
      ? `${n(counts.review ?? 0, "review is", "reviews are")} asked of you · ${n(counts.mine ?? 0, `${word} of yours is`, `${word}s of yours are`)} open`
      : "Pull requests that need you, then the rest.";
  const b = branch.data;
  const refresh = () => {
    void list.reload();
    void asked.reload();
    void branch.reload();
  };

  const reviewNext = () =>
    next ? pick(`pr:${next.number}`) : toast("No review is waiting for you.");
  const nextRef = useRef(reviewNext);
  nextRef.current = reviewNext;
  // 0.28 ⇧N on the Review page: the next pull request that asks for you (the button says so); not while typing or in a
  // dialog. keel's own N (New flow) has no Shift, so the two never meet.
  useEffect(() => {
    if (!page) return;
    const on = (e: KeyboardEvent) => {
      if (e.key !== "N" || !e.shiftKey || e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if (document.querySelector('[aria-modal="true"]')) return;
      e.preventDefault();
      nextRef.current();
    };
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, [page]);

  const headActions = (
    <>
      {mode && onMode && <ModeSwitch mode={mode} onPick={onMode} />}
      <button
        type="button"
        className={`rvn-btn${page ? " ghost" : ""}`}
        onClick={refresh}
        disabled={list.loading}
      >
        {list.loading && list.data ? "Reading…" : "Refresh"}
      </button>
      <button
        type="button"
        className="rvn-btn primary"
        onClick={reviewNext}
        aria-keyshortcuts={page ? "Shift+N" : undefined}
        title={
          next
            ? `${next.title} · ${next.author}`
            : "Nothing asks for your review now"
        }
      >
        {next ? `Review ${prLabel(host, next.number)}` : "All reviewed"}
        {page && next && <kbd aria-hidden="true">⇧N</kbd>}
      </button>
    </>
  );

  const branchCard = b?.branch && (b.ahead > 0 || b.files > 0) && (
    <div className="rvn-branch">
      <div className="rvn-row rvn-branch-1">
        <span className="rvn-eyebrow">This branch</span>
        <span className="rvn-grow" />
        <span className="rvn-mono rvn-btag" title={b.branch}>{b.branch}</span>
      </div>
      <span className="rvn-dim">
        {n(b.ahead, "commit", "commits")} · {n(b.files, "file", "files")}
        {b.pr && <> · {prWord(host)} {prLabel(host, b.pr.number)}</>}
        {b.pr && <BranchDecisions p={b.pr} />}
      </span>
      {b.base_warning && <span className="rv-base rvn-base" data-testid="branch-base-warning">{b.base_warning}</span>}
      <div className="rvn-row">
        <button
          type="button"
          className="rvn-btn rvn-btn-sm"
          onClick={() =>
            pick(b.pr ? `pr:${b.pr.number}` : `branch:${b.branch}`)
          }
        >
          Review this branch
        </button>
      </div>
    </div>
  );

  const side = (
    <div className="rvn-side" role="region" aria-label="Review">
      <div className="rvn-side-in">
        {!page && (
          <header className="rvn-shead">
            <div className="rvn-shead-1">
              <h2>Review</h2>
            </div>
            <p className="rvn-dim">{head}</p>
            <div className="rvn-row">{headActions}</div>
          </header>
        )}
        {page && branchCard}
        <div className="rvn-chips" role="group" aria-label="Show">
          {LIST_FILTERS.map(([f, label]) => (
            <button
              key={f}
              type="button"
              className="rvn-chip"
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
            >
              {label}
              {counts[f] != null && <span className="rvn-n">{counts[f]}</span>}
            </button>
          ))}
        </div>
        <label className="rvn-search">
          <span className="rvn-sr">Search {word}s</span>
          <input
            type="search"
            value={q}
            placeholder={`Search by title, number, author or branch`}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        {list.error && (
          <ErrorBox error={list.error} onRetry={() => void list.reload()} />
        )}
        {list.data?.note && <p className="rvn-dim rvn-pad">{list.data.note}</p>}
        {!list.data && !list.error && (
          <span className="pg-spin rvn-pad" role="status">
            Reading the {word}s…
          </span>
        )}
        {list.data && !list.data.note && shown.length === 0 && (
          <p className="rvn-empty">
            {q.trim()
              ? `No ${word} matches "${q.trim()}".`
              : filter === "review"
                ? `No ${word} waits for your review.`
                : filter === "assigned"
                  ? `No open ${word} is assigned to you.`
                  : filter === "mine"
                    ? `You have no open ${word}.`
                    : `No open ${word}.`}
          </p>
        )}
        {shown.length > 0 && (
          <ul
            className="rvn-prs"
            aria-label={
              host?.kind === "gitlab" ? "Merge requests" : "Pull requests"
            }
          >
            {shown.map((p) => {
              const key = `pr:${p.number}`;
              const [pill, tone] = prPill(
                p,
                p.mine ? waitingOn(p) : null,
                sent[key],
              );
              const on = activeKey === key;
              return (
                <li key={p.number}>
                  <button
                    type="button"
                    className={`rvn-pr${on ? " on" : ""}`}
                    aria-current={on || undefined}
                    aria-label={`${prLabel(host, p.number)} ${p.title}`}
                    onClick={() => pick(key)}
                  >
                    <span className="rvn-pr-1">
                      <span className="rvn-mono rvn-dim">
                        {prLabel(host, p.number)}
                      </span>
                      <b>{p.title}</b>
                    </span>
                    <span className="rvn-pr-2">
                      <Tag tone={tone}>{pill}</Tag>
                      <span className="rvn-pr-3">
                        {p.mine ? "you" : p.author}
                        {p.updated_at ? ` · ${ago(p.updated_at)}` : ""}
                        {" · "}
                        <span className="rvn-mono">{p.branch}</span>
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        {host && (
          <p className="rvn-dim rvn-pad rvn-foot">
            {host.kind === "gitlab" ? "GitLab" : "GitHub"} · {host.path}
            {readAt ? ` · read ${ago(readAt)}` : ""}
          </p>
        )}
        {!page && branchCard}
      </div>
    </div>
  );

  if (!page) return side;
  // 0.28 the Review page (the redesign's board): the page head over both columns, the list on the left, the pull
  // request on the right; a phone shows one of them (the head stays)
  return (
    <div className="rdw rdw-review rvn-pagewrap">
      <div className="top rvn-phead">
        <div>
          <h1>Review</h1>
          <p>{head}</p>
        </div>
        <div className="actions">{headActions}</div>
      </div>
      <div className={`rvn-page${page.phone ? ` phone ${activeKey ? "s-pr" : "s-list"}` : ""}`}>
        {page.showList && <aside className="rvn-page-side rise r1">{side}</aside>}
        {page.main}
      </div>
    </div>
  );
}

/** 0.18.60 who approved the branch's pull request and who asked for changes. */
function BranchDecisions({ p }: { p: PrSummary }) {
  const d = decisionText(p);
  return (
    <>
      {d.approved && (
        <>
          {" · "}
          <Tag tone="ok">{d.approved}</Tag>
        </>
      )}
      {d.changes && (
        <>
          {" · "}
          <Tag tone="warn">{d.changes}</Tag>
        </>
      )}
    </>
  );
}

const n = (k: number, one: string, many: string) =>
  `${k} ${k === 1 ? one : many}`;
