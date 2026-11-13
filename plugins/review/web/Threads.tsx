// The review's threads in the Review tool window: filter chips (Waiting for you, Open, Resolved, All), sections
// (Waiting for your answer, Open, Resolved), one card per thread with the code lines it is on, its messages (yours
// marked, "new" since your last visit, long ones folded) and its actions: Reply, ✦ Is the reviewer right? (keel's
// cheap check, point by point), ✦ Address it (a flow on the pull request's branch), Resolve, and the extra buttons
// from Settings › Code Review threads. keel never posts: a suggested reply goes into your reply box. Threads that make
// the same point are one check: each answer links the others, and one reply can go into all of their boxes. When the
// author says "fixed" and pushes, keel checks the new code: "Fixed in abc1234 · Resolve" (your click resolves it).
//
//   ┌ Waiting for you · 2 ┐ ┌ Open · 3 ┐ ┌ Resolved · 1 ┐ ┌ All · 4 ┐
//   ● WAITING FOR YOUR ANSWER
//   ┌ src/Repo/ File.kt  L12–14  open ───────────────┐
//   │ 12 + code …                       (scrolls →)   │
//   │ (DH) bo · reviewer · Oct 21   Why raw SQL?     │
//   │   (MN) You · Oct 21           On purpose: …    │
//   │ ┌ k keel checked the code ── ~4k tokens ─────┐ │
//   │ │ Reviewer (bo) is right  "indexes" — add …  │ │
//   │ └ [Use as my reply] [Address it] [Ask…]     ┘ │
//   └ [Reply] [✦ Is the reviewer right?] [✦ Address it] [Resolve]
//   ● RESOLVED
//   ✓ src/a.kt · L3   "Do we need this?" → You: yes     2 replies ›

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
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
  type AiRun,
  type AiState,
  type JudgeItem,
  type JudgeResult,
  type ReviewComment,
  type ReviewThread,
  type ReviewView,
  type ThreadButtonInfo,
  type Verdict,
} from "./reviewApi";
import * as R from "./store";
import {
  checkLines,
  fixNote,
  samePointLabel,
  sidesOf,
  useOnAllLabel,
  verdictLabel,
  type Sides,
} from "./verdict";

type Open = (
  path: string,
  line?: number | null,
  side?: "RIGHT" | "LEFT",
) => void;
export type ThreadFilter = "waiting" | "open" | "resolved" | "all";
const isThreadFilter = (v: unknown): v is ThreadFilter =>
  v === "waiting" || v === "open" || v === "resolved" || v === "all";
const isIds = (v: unknown): v is string[] =>
  Array.isArray(v) && v.every((x) => typeof x === "string");
/** how many dismissed answers one review remembers (the oldest are forgotten first) */
export const HIDDEN_RUNS_CAP = 200;

/** keel's answers you dismissed in one review (a check: "<thread>|<run>", an extra button's: "<thread>|<button>|<run>"),
 *  kept in this browser. The Threads panel and the threads in the code share it: a dismiss in one hides the answer in
 *  the other at once. The saved answer stays on the server and comes back when you ask again; a new run shows. */
export function useJudgeHidden(pid: string, reviewKey: string) {
  const [hidden, setHidden] = useStored<string[]>(
    `keel2.review.judgeHidden.${pid}.${reviewKey}`,
    [],
    { store: "local", valid: isIds },
  );
  const hide = (id: string) =>
    setHidden((h) => (h.includes(id) ? h : [...h, id].slice(-HIDDEN_RUNS_CAP)));
  const show = (id: string) => setHidden((h) => h.filter((x) => x !== id));
  return { hidden, hide, show };
}

// ---------------------------------------------------------------- small helpers (exported for tests)

/** The thread's comments as a quote ("> bo: ..."), then an empty line for your answer: "What should the fix be?". */
export const quoteThread = (t: ReviewThread) =>
  t.comments
    .map((c) => `${c.author}: ${c.body}`.split("\n").map((l) => (l ? `> ${l}` : "> ")).join("\n"))
    .join("\n> \n") + "\n\n";

const same = (a: string | null | undefined, b: string | null | undefined) =>
  !!a && !!b && a.toLowerCase() === b.toLowerCase();

/** Unresolved and the last word is not yours: it waits for your answer. */
export function waitsForYou(t: ReviewThread, me: string | null): boolean {
  if (t.resolved || t.pending || !t.comments.length) return false;
  return !same(t.comments[t.comments.length - 1].author, me);
}

/** A message since your last visit that is not yours. */
export function hasNew(
  t: ReviewThread,
  me: string | null,
  seen: string | null,
): boolean {
  return !!seen && t.comments.some((c) => !same(c.author, me) && c.at > seen);
}

/** A thread in the code starts open when it waits for you, has something new, or is not resolved. */
export function openByDefault(
  t: ReviewThread,
  me: string | null,
  seen: string | null,
): boolean {
  return waitsForYou(t, me) || hasNew(t, me, seen) || !t.resolved;
}

/** The first line of a message that has text. */
export const firstLine = (s: string) =>
  s
    .split("\n")
    .find((x) => x.trim())
    ?.trim() ?? "";

export function initials(name: string): string {
  const parts = name.split(/[\s._-]+/).filter(Boolean);
  const s = parts.length > 1 ? parts[0][0] + parts[1][0] : name.slice(0, 2);
  return s.toUpperCase();
}

function day(at: string): string {
  const d = new Date(at);
  return Number.isNaN(d.getTime())
    ? at.slice(0, 10)
    : d.toLocaleDateString("en", { month: "short", day: "numeric" });
}

export function ago(at: string | undefined, now = Date.now()): string {
  const t = at ? Date.parse(at) : NaN;
  if (Number.isNaN(t)) return "";
  const m = Math.max(0, Math.round((now - t) / 60000));
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h} h ago` : `${Math.round(h / 24)} d ago`;
}

/** The open threads keel has not checked, counted by point: threads that make the same point (the view's
 *  thread_groups) are one check. */
export function uncheckedPoints(view: ReviewView, ai: AiState | null): number {
  const group = new Map<string, number>();
  (view.thread_groups ?? []).forEach((g, i) => g.forEach((id) => group.set(id, i)));
  const points = new Set<string>();
  view.threads
    .filter(
      (t) =>
        !t.resolved && !t.pending && ai?.judges?.[t.id]?.status !== "done",
    )
    .forEach((t) =>
      points.add(group.has(t.id) ? `group ${group.get(t.id)}` : t.id),
    );
  return points.size;
}

/** Puts [fill]'s text into another thread's reply draft each time [fill] changes (one reply for the same point in two
 *  threads): a box of that thread shows it at once, one opened later finds it. */
export function FillDraft({
  pid,
  reviewKey,
  threadId,
  fill,
}: {
  pid: string;
  reviewKey: string;
  threadId: string;
  fill: { text: string; n: number } | null;
}) {
  const [, setText] = R.useReviewDraft(pid, reviewKey, `reply.${threadId}`);
  useEffect(() => {
    if (fill) setText(fill.text);
  }, [fill?.n]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

const SEEN = (pid: string, key: string) => `keel2.review.seen.${pid}.${key}`;

/** When you last looked at this review's threads (null: never), from this browser. */
export function lastSeen(pid: string, key: string): string | null {
  try {
    return localStorage.getItem(SEEN(pid, key));
  } catch {
    return null;
  }
}

function markSeen(pid: string, key: string, at: string) {
  try {
    localStorage.setItem(SEEN(pid, key), at);
  } catch {
    /* private window */
  }
}

/** A rough guess of a quick check's tokens before it runs: the thread, about 60 lines of code, the prompt, the answer. */
export function estimate(t: ReviewThread): [number, number] {
  const chars = t.comments.reduce((n, c) => n + c.body.length + 40, 0);
  const low = Math.round(chars / 4 + 2600);
  return [low, Math.round(low * 1.7)];
}

/** The colour of a verdict, from where you stand (you wrote the change, you are the reviewer, or neither): "you" when
 *  your side holds. Its words come from ./verdict, the same in both views. */
export function verdictTone(
  v: Verdict,
  who: "author" | "reviewer" | "other",
): string {
  if (v === "partly" || v === "unclear") return v;
  const reviewer = v === "reviewer_right";
  if (who === "other") return reviewer ? "them" : "you";
  return (who === "reviewer") === reviewer ? "you" : "them";
}

/** The parts of a path: the folder (ellipsized first) and the file name (bold). */
function pathParts(p: string): [string, string] {
  const i = p.lastIndexOf("/");
  return i < 0 ? ["", p] : [p.slice(0, i + 1), p.slice(i + 1)];
}

/** Narrow when the box is under [px] wide (ResizeObserver; never narrow without one). */
function useNarrowBox<T extends HTMLElement>(px: number) {
  const ref = useRef<T>(null);
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((es) => {
      const w = es[0]?.contentRect.width ?? 0;
      if (w > 0) setNarrow(w < px);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [px]);
  return [ref, narrow] as const;
}

// ---------------------------------------------------------------- the view

export function ThreadsView({
  pid,
  reviewKey,
  view,
  ai,
  running,
  onOpen,
}: {
  pid: string;
  reviewKey: string;
  view: ReviewView;
  ai: AiState | null;
  running: string[];
  onOpen: Open;
}) {
  const me = view.me;
  // the "new" tags compare with the visit before this one; this visit counts from now
  const [seen] = useState(() => lastSeen(pid, reviewKey));
  useEffect(() => {
    const newest = view.threads
      .flatMap((t) => t.comments.map((c) => c.at))
      .concat(new Date().toISOString())
      .sort()
      .at(-1)!;
    markSeen(pid, reviewKey, newest);
  }, [pid, reviewKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const waiting = view.threads.filter((t) => waitsForYou(t, me));
  const open = view.threads.filter((t) => !t.resolved);
  const resolved = view.threads.filter((t) => t.resolved);
  // the filter and the resolved threads you unfolded, kept per review for this browser tab
  const [filter, setFilter] = useStored<ThreadFilter>(
    `keel2.review.tfilter.${pid}.${reviewKey}`,
    "all",
    { valid: isThreadFilter },
  );
  const [unfolded, setUnfolded] = useStored<string[]>(
    `keel2.review.texp.${pid}.${reviewKey}`,
    [],
    { valid: isIds },
  );
  const toggleUnfolded = (id: string) =>
    setUnfolded((u) =>
      // ids of threads that are not in the review any more fall away
      u.includes(id)
        ? u.filter((x) => x !== id)
        : [...u.filter((x) => view.threads.some((t) => t.id === x)), id],
    );
  const [box, narrow] = useNarrowBox<HTMLDivElement>(420);
  const cfg = ai?.config;
  const quickOn = (cfg?.judge?.mode ?? "quick") === "quick";
  const checkAllBusy = running.includes("judge-all");
  const unchecked = uncheckedPoints(view, ai);

  if (!view.threads.length && !view.conversation.length) {
    return (
      <p className="rv-dim">
        {view.kind === "pr"
          ? `No comments on ${prLabel(view.host, view.number)} yet.`
          : "A branch has no threads. Its pull request does."}
      </p>
    );
  }
  const chips: [ThreadFilter, string, number, string][] = [
    [
      "waiting",
      "Waiting for you",
      waiting.length,
      waiting.length ? " warn" : "",
    ],
    ["open", "Open", open.length, ""],
    ["resolved", "Resolved", resolved.length, ""],
    ["all", "All", view.threads.length, ""],
  ];
  const show = (s: "waiting" | "open" | "resolved") =>
    filter === "all" || filter === s || (filter === "open" && s === "waiting");
  const openOnly = open.filter((t) => !waitsForYou(t, me));
  const card = (t: ReviewThread) => (
    <ThreadBox
      key={t.id}
      pid={pid}
      reviewKey={reviewKey}
      view={view}
      t={t}
      ai={ai}
      running={running}
      seen={seen}
      onOpen={onOpen}
    />
  );
  const section = (
    id: "waiting" | "open" | "resolved",
    title: string,
    list: ReviewThread[],
    body: ReactNode,
  ) =>
    show(id) && list.length > 0 ? (
      <section className="rv-tsec" aria-labelledby={`rv-tsec-${id}`}>
        <h4 className={`rv-tsec-h ${id}`} id={`rv-tsec-${id}`}>
          <span className="rv-dot" aria-hidden="true" />
          {title} · {list.length}
        </h4>
        {body}
      </section>
    ) : null;
  const empty =
    (filter === "waiting" &&
      !waiting.length &&
      "Nothing waits for your answer.") ||
    (filter === "open" && !open.length && "No open thread.") ||
    (filter === "resolved" && !resolved.length && "No resolved thread yet.") ||
    null;

  return (
    <div
      ref={box}
      className={`rv-threads${narrow ? " narrow" : ""}`}
      data-testid="rv-threads"
    >
      <div className="rv-th-head">
        <b>Threads</b>
        {view.number != null && (
          <span className="mono rv-dim">{prLabel(view.host, view.number)}</span>
        )}
        <span className="rv-dim rv-th-count">
          {view.threads.length} thread{view.threads.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="rv-tchips" role="group" aria-label="Show threads">
        {chips.map(([f, label, n, tone]) => (
          <button
            key={f}
            type="button"
            className={`rv-tchip${tone}${filter === f ? " on" : ""}`}
            aria-pressed={filter === f}
            onClick={() => setFilter(f)}
          >
            {label} · {n}
          </button>
        ))}
        {quickOn && view.can_post && unchecked > 1 && (
          <button
            type="button"
            className="btn sm ghost rv-checkall"
            disabled={checkAllBusy}
            title="One cheap call for every open point keel has not checked yet (the same point in two threads is one)"
            onClick={() => void R.judgeAll(pid, reviewKey)}
          >
            {checkAllBusy ? "Checking…" : `✦ Check all open (${unchecked})`}
          </button>
        )}
      </div>
      {empty && <p className="rv-dim">{empty}</p>}
      {section(
        "waiting",
        "Waiting for your answer",
        waiting,
        waiting.map(card),
      )}
      {section("open", "Open", openOnly, openOnly.map(card))}
      {section(
        "resolved",
        "Resolved",
        resolved,
        resolved.map((t) => (
          <ResolvedRow
            key={t.id}
            t={t}
            me={me}
            open={unfolded.includes(t.id)}
            onToggle={() => toggleUnfolded(t.id)}
            expanded={card(t)}
          />
        )),
      )}
      {view.conversation.length > 0 && filter === "all" && (
        <section className="rv-tsec" aria-labelledby="rv-tsec-conv">
          <h4 className="rv-tsec-h" id="rv-tsec-conv">
            Conversation
          </h4>
          {view.conversation.map((c) => (
            <Message key={c.id} c={c} view={view} seen={seen} />
          ))}
        </section>
      )}
      <ul className="rv-legend" aria-label="Legend">
        <li>
          <span className="rv-dot you" aria-hidden="true" />
          you
        </li>
        <li>
          <span className="rv-dot reviewer" aria-hidden="true" />
          reviewer
        </li>
        <li>
          <span className="rv-dot waiting" aria-hidden="true" />
          needs you
        </li>
        <li>
          <span className="rv-dot resolved" aria-hidden="true" />
          resolved
        </li>
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------- one resolved thread, folded to a line

function ResolvedRow({
  t,
  me,
  open,
  onToggle,
  expanded,
}: {
  t: ReviewThread;
  me: string | null;
  open: boolean;
  onToggle: () => void;
  expanded: ReactNode;
}) {
  const first = t.comments[0];
  const last = t.comments[t.comments.length - 1];
  const replies = t.comments.length - 1;
  return (
    <div className="rv-resolved">
      <button
        type="button"
        className="rv-rrow"
        aria-expanded={open}
        onClick={onToggle}
      >
        <span className="rv-rcheck" aria-hidden="true">
          ✓
        </span>
        <span className="rv-rtext">
          <span className="mono rv-rpath">
            {t.path ?? "conversation"}
            {t.line ? ` · L${linesLabel(t)}` : ""}
          </span>
          <span className="rv-rsum">
            "{firstLine(first?.body ?? "")}"
            {replies > 0 && last && (
              <>
                {" "}
                → {same(last.author, me) ? "You" : last.author}:{" "}
                {firstLine(last.body)}
              </>
            )}
          </span>
        </span>
        <span className="rv-rn">
          {replies === 0
            ? "no replies"
            : `${replies} repl${replies === 1 ? "y" : "ies"}`}{" "}
          {open ? "⌄" : "›"}
        </span>
      </button>
      {open && expanded}
    </div>
  );
}

// ---------------------------------------------------------------- one thread in the code, folded to a line

/** A thread drawn under its lines in the code: a head you can fold it to (who, the first line, the replies, whether it
 *  waits for you) and, open, the same box as in the Threads panel without the code excerpt. */
export function InlineThread({
  open,
  onToggle,
  ...box
}: Omit<Parameters<typeof ThreadBox>[0], "inline"> & {
  open: boolean;
  onToggle: () => void;
}) {
  const { t, view, seen } = box;
  const me = view.me;
  const waiting = waitsForYou(t, me);
  const fresh = hasNew(t, me, seen);
  const first = t.comments[0];
  const replies = Math.max(0, t.comments.length - 1);
  const tone = t.resolved ? "resolved" : waiting ? "waiting" : "open";
  const body = useId();
  return (
    <div
      className={`rv-ithread${open ? " open" : ""}${t.resolved ? " resolved" : ""}`}
      data-thread={t.id}
    >
      <button
        type="button"
        className="rv-ihead"
        aria-expanded={open}
        aria-controls={open ? body : undefined}
        title={open ? "Fold this thread" : "Open this thread"}
        onClick={onToggle}
      >
        <span className="rv-icaret" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        <span className={`rv-dot ${tone}`} aria-hidden="true" />
        <b className="rv-iwho">
          {first ? (same(first.author, me) ? "You" : first.author) : "Thread"}
        </b>
        <span className="rv-isum">"{firstLine(first?.body ?? "")}"</span>
        <span className="rv-dim rv-in">
          {replies} repl{replies === 1 ? "y" : "ies"}
        </span>
        {waiting && <span className="rv-pill warn">Waiting for you</span>}
        {fresh && <span className="rv-new">new</span>}
        {t.resolved && <span className="rv-pill ok">Resolved</span>}
      </button>
      {open && (
        <div id={body}>
          <ThreadBox {...box} inline />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- one message

function Message({
  c,
  view,
  seen,
}: {
  c: ReviewComment;
  view: ReviewView;
  seen: string | null;
}) {
  const mine = same(c.author, view.me);
  const isNew = !mine && !!seen && c.at > seen;
  const role = mine
    ? null
    : same(c.author, view.author)
      ? "author"
      : "reviewer";
  const long =
    c.body.split("\n").filter((l) => l.trim()).length > 4 ||
    c.body.length > 320;
  const [more, setMore] = useState(false);
  return (
    <div
      className={`rv-msg${mine ? " mine" : ""}`}
      data-mine={mine || undefined}
    >
      <span className={`rv-av${mine ? " mine" : ""}`} aria-hidden="true">
        {initials(c.author)}
      </span>
      <div className="rv-msg-b">
        <div className="rv-msg-h">
          <b>{mine ? "You" : c.author}</b>
          <span className="rv-dim">
            {role ? `${role} · ` : ""}
            {day(c.at)}
          </span>
          {isNew && <span className="rv-new">new</span>}
        </div>
        <div className={`rv-msg-t${long && !more ? " folded" : ""}`}>
          <Markdown text={c.body} fold={12} />
        </div>
        {long && (
          <button
            type="button"
            className="rv-link rv-more"
            aria-expanded={more}
            onClick={() => setMore((m) => !m)}
          >
            {more ? "Show less" : "Show more"}
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- the code a thread is on

function CodeLines({
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
    { live: false },
  );
  const rows = useMemo(() => {
    if (!d.data || t.line == null) return [];
    const left = t.side === "LEFT";
    const end = t.line;
    const start =
      t.start_line != null && (t.start_side ?? t.side) === t.side
        ? t.start_line
        : Math.max(1, end - 3);
    return parseDiff(d.data.diff)
      .rows.filter(
        (r) => r.kind === "add" || r.kind === "del" || r.kind === "ctx",
      )
      .filter((r) => {
        const n = left ? r.old : r.new;
        return n != null && n >= start && n <= end;
      })
      .slice(0, 14);
  }, [d.data, t.line, t.start_line, t.side, t.start_side]);
  if (!rows.length) return null;
  return (
    <pre
      className="rv-tcode"
      tabIndex={0}
      aria-label={`The code of ${t.path} at line ${linesLabel(t)}`}
    >
      {rows.map((r, i) => {
        const n = t.side === "LEFT" ? r.old : r.new;
        return (
          <span key={i} className={`rv-tline ${r.kind}`}>
            <span className="rv-tln">{n}</span>
            <span className="rv-tmk">
              {r.kind === "add" ? "+" : r.kind === "del" ? "−" : " "}
            </span>
            {r.text}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}

// ---------------------------------------------------------------- one thread

export function ThreadBox({
  pid,
  reviewKey,
  view,
  t,
  ai,
  running,
  seen,
  inline = false,
  onOpen,
}: {
  pid: string;
  reviewKey: string;
  view: ReviewView;
  t: ReviewThread;
  ai: AiState | null;
  running: string[];
  seen: string | null;
  /** drawn in the code under its lines: no code excerpt and no file header (the code is right there) */
  inline?: boolean;
  onOpen: Open;
}) {
  const { hidden, hide: onHide, show: onShow } = useJudgeHidden(pid, reviewKey);
  const assistant = useSlot(SLOTS.assistant).length > 0;
  // the reply you are writing, kept (localStorage) until it is sent or cancelled: the box is open again when you come back
  const [text, setText, dropText] = R.useReviewDraft(
    pid,
    reviewKey,
    `reply.${t.id}`,
  );
  const [replying, setReplying] = useState(() => !!text);
  // a reply box that is open again from a kept draft does not take the focus; one you open does
  const [focusReply, setFocusReply] = useState(false);
  // a draft started in the other place (the panel or the code) opens this box too
  useEffect(() => {
    if (text) setReplying(true);
  }, [text]);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<null | { note?: string; fix?: string }>(null);
  const [err, setErr] = useState<R.Err | null>(null);
  const [started, setStarted] = useState<string | null>(null);
  // keel's reply put into the other threads of the same point (FillDraft)
  const [fillOthers, setFillOthers] = useState<{ text: string; n: number } | null>(null);
  const ta = useRef<HTMLTextAreaElement>(null);
  const self = useRef<HTMLElement>(null);
  const cfg = ai?.config;
  const quick = (cfg?.judge?.mode ?? "quick") === "quick";
  const flowName = cfg?.address_workflow ?? "address";
  // 0.18.56 the confirmation may pick another flow (a feature flow for a big comment); the configured one first
  const flowChoices = cfg?.address_flows?.length
    ? cfg.address_flows
    : [{ id: flowName, name: flowName }];
  const [pickedFlow, setPickedFlow] = useState<string>(flowName);
  const judge = ai?.judges?.[t.id] ?? null;
  // the author said fixed and pushed: keel's check of the new code (it never resolves; Resolve is your click)
  const fix = fixNote(ai?.fixed?.[t.id], t);
  const addressed = ai?.addressed?.[t.id];
  const judging =
    running.includes(`judge:${t.id}`) || judge?.status === "running";
  const addressing = running.includes(`address:${t.id}`);
  // keel's check you dismissed: hidden until you ask again (the saved answer, no new call) or a new check runs
  const judgeId = judge ? `${t.id}|${judge.id}` : null;
  const judgeHidden = !!judgeId && !judging && hidden.includes(judgeId);
  const buttonRunId = (b: ThreadButtonInfo) => {
    const run = ai?.buttons?.[`${b.id}|${t.id}`];
    return run ? `${t.id}|${b.id}|${run.id}` : null;
  };
  const waiting = waitsForYou(t, view.me);
  const canWork = view.kind === "pr" && !t.pending;
  const [dir, name] = pathParts(t.path ?? "");
  const who: "author" | "reviewer" | "other" = same(view.me, view.author)
    ? "author"
    : same(view.me, t.comments[0]?.author)
      ? "reviewer"
      : "other";
  const [lo, hi] = estimate(t);
  const failed = (fallback: string) =>
    setErr(R.review(pid, reviewKey).err ?? { message: fallback });

  const send = async () => {
    setBusy(true);
    try {
      await R.reply(pid, reviewKey, t.id, text.trim());
      if (!R.review(pid, reviewKey).err) {
        dropText();
        setReplying(false);
      }
    } finally {
      setBusy(false);
    }
  };
  const useReply = (s: string) => {
    setText(s);
    setReplying(true);
    window.setTimeout(() => ta.current?.focus(), 0);
  };
  // the same point in other threads: keel checked them as one
  const sameAs = (judge?.status === "done" && judge.result?.same_as) || [];
  const useOnAll = (s: string) => {
    useReply(s);
    setFillOthers((f) => ({ text: s, n: (f?.n ?? 0) + 1 }));
  };
  // shows another thread of the same point (in this panel, or in the code)
  const goTo = (id: string) =>
    (self.current?.closest(".rv-threads") ?? document)
      .querySelector<HTMLElement>(`article[data-thread="${CSS.escape(id)}"]`)
      ?.scrollIntoView?.({ block: "nearest" });
  const check = async (opts: { again?: boolean; harder?: boolean } = {}) => {
    setErr(null);
    if (!(await R.judge(pid, reviewKey, t.id, opts)))
      failed("keel could not check this thread");
  };
  // 0.18.57 no reply of yours in the thread: Address it asks what the fix should be, the others' words quoted above
  const replied = t.comments.some((c) => same(c.author, view.me));
  const askFix = (note?: string) =>
    setConfirm({ note, fix: replied ? undefined : quoteThread(t) });
  const startAddress = async (note?: string, fix?: string) => {
    setErr(null);
    const r = await R.address(
      pid,
      reviewKey,
      t.id,
      note,
      pickedFlow !== flowName ? pickedFlow : undefined,
      fix?.trim() ? fix.trim() : undefined,
    );
    setConfirm(null);
    if (r) setStarted(r.note);
    else failed("keel could not start the flow");
  };
  const followUp = (r: JudgeResult | null) =>
    askAssistant(
      `About the review thread on ${t.path ?? "the pull request"}${t.line ? `:${linesLabel(t)}` : ""} of ${prLabel(view.host, view.number)} ` +
        `(commit ${view.head_sha.slice(0, 12)}):\n` +
        t.comments.map((c) => `${c.author}: ${c.body}`).join("\n\n") +
        (r?.items?.length
          ? `\n\nkeel's check:\n${checkLines(r.items, sidesOf(view, t))}`
          : "") +
        "\n\nMy question: ",
    );
  const dbCheck = () =>
    askAssistant(
      `For the review thread on ${t.path}${t.line ? `:${linesLabel(t)}` : ""} of ${prLabel(view.host, view.number)}: ` +
        `run read-only checks against the configured database (EXPLAIN, never a statement that writes, never EXPLAIN ANALYZE ` +
        `of a write) to see whether the reviewer's point holds:\n` +
        t.comments.map((c) => `${c.author}: ${c.body}`).join("\n\n"),
    );
  const sqlish =
    /\b(select|update|insert|delete|join|index|explain|query|sql)\b/i.test(
      t.comments.map((c) => c.body).join(" "),
    );
  const judgeNote = (r: JudgeResult | null) =>
    r?.items
      ?.filter((i) => i.verdict !== "author_right")
      .map((i) => `${i.claim}: ${i.why}${i.fix ? ` Fix: ${i.fix}` : ""}`)
      .join("\n");

  return (
    <article
      ref={self}
      className={`rv-tcard${waiting ? " waiting" : ""}${t.resolved ? " resolved" : ""}${inline ? " inline" : ""}`}
      data-thread={t.id}
      aria-label={`Thread on ${t.path ?? "the pull request"}${t.line ? ` line ${linesLabel(t)}` : ""}`}
    >
      {inline ? (
        (t.start_line != null || t.outdated || t.pending) && (
          <header className="rv-tcard-h rv-tcard-hi">
            {t.start_line != null && (
              <span className="rv-dim">lines {linesLabel(t)}</span>
            )}
            {t.outdated && <span className="rv-pill idle">outdated</span>}
            {t.pending && <span className="rv-pill run">pending</span>}
          </header>
        )
      ) : (
        <>
          <header className="rv-tcard-h">
            <svg
              className="rv-ficon"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
              <path d="M14 3v6h6" />
            </svg>
            {t.path ? (
              <button
                type="button"
                className="rv-tpath mono"
                title={t.path}
                aria-label={`Open ${t.path}${t.line ? ` at line ${t.line}` : ""}`}
                onClick={() => onOpen(t.path!, t.line, t.side)}
              >
                {dir && <span className="rv-tdir">{dir}</span>}
                <b className="rv-tname">{name}</b>
              </button>
            ) : (
              <span className="rv-tpath">The pull request</span>
            )}
            {t.line != null && (
              <span className="mono rv-tlines">L{linesLabel(t)}</span>
            )}
            {t.outdated && <span className="rv-pill idle">outdated</span>}
            {t.pending && <span className="rv-pill run">pending</span>}
            <span className={`rv-pill ${t.resolved ? "ok" : "warn"}`}>
              {t.resolved ? "resolved" : "open"}
            </span>
          </header>
          {t.path &&
            t.line != null &&
            !t.outdated &&
            view.files.some((f) => f.path === t.path) && (
              <CodeLines pid={pid} reviewKey={reviewKey} t={t} />
            )}
        </>
      )}
      <div className="rv-tbody">
        {t.comments.map((c) => (
          <Message key={c.id} c={c} view={view} seen={seen} />
        ))}
        {(judge || judging) && !judgeHidden && (
          <CheckBox
            run={judge}
            busy={judging}
            who={who}
            sides={sidesOf(view, t)}
            quick={quick}
            canPost={view.can_post && !t.pending}
            assistant={assistant}
            flowName={flowName}
            allowHarder={cfg?.judge?.allow_harder ?? true}
            onUse={useReply}
            same={sameAs.map((o) => ({
              id: o.thread_id,
              label: samePointLabel(o, view),
            }))}
            onGo={goTo}
            onUseAll={useOnAll}
            onAddress={(r) => askFix(judgeNote(r))}
            onAsk={followUp}
            onAgain={(harder) => void check({ again: true, harder })}
            onDismiss={judgeId && onHide ? () => onHide(judgeId) : undefined}
          />
        )}
        {(cfg?.buttons ?? []).map((b) => {
          const runId = buttonRunId(b);
          const busyB = running.includes(`button:${b.id}|${t.id}`);
          if (runId && !busyB && hidden.includes(runId)) return null;
          return (
            <ButtonAnswer
              key={b.id}
              b={b}
              run={ai?.buttons?.[`${b.id}|${t.id}`] ?? null}
              busy={busyB}
              canPost={view.can_post && !t.pending}
              onUse={useReply}
              onAgain={() =>
                void R.runButton(pid, reviewKey, t.id, b.id, { again: true })
              }
              onDismiss={runId && onHide ? () => onHide(runId) : undefined}
            />
          );
        })}
        {fix && (
          <p
            className={`rv-tnote rv-fixed ${fix.fixed ? "ok" : "open"}`}
            role="group"
            aria-label="keel checked the fix"
          >
            <span>{fix.text}</span>
            {fix.fixed && view.can_post && !t.pending && (
              <>
                {" · "}
                <button
                  type="button"
                  className="rv-link"
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
        {sameAs.map((o) => (
          <FillDraft
            key={o.thread_id}
            pid={pid}
            reviewKey={reviewKey}
            threadId={o.thread_id}
            fill={fillOthers}
          />
        ))}
        {(addressed || started) && (
          <p className="rv-tnote" role="status">
            {started ??
              `A ${addressed?.workflow ?? flowName} flow addresses this thread${addressed?.branch ? ` on ${addressed.branch}` : ""}.`}{" "}
            {addressed?.flow_id && (
              <a
                className="rv-link"
                href={`#/flow/${encodeURIComponent(addressed.flow_id)}`}
              >
                Open the flow ›
              </a>
            )}
          </p>
        )}
        {err && <ErrorBox error={err} />}
      </div>
      {replying && (
        <form
          className="rv-reply rv-treply"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) void send();
          }}
        >
          <textarea
            ref={ta}
            className="rv-ta"
            aria-label="Your reply"
            value={text}
            onChange={(e) => setText(e.target.value)}
            autoFocus={focusReply}
          />
          <div className="rv-row">
            <button
              type="submit"
              className="btn sm primary"
              disabled={busy || !text.trim()}
            >
              Send reply
            </button>
            <button
              type="button"
              className="btn sm ghost"
              onClick={() => {
                dropText();
                setReplying(false);
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
      {confirm && (
        <div
          className="rv-confirm rv-tconfirm"
          role="group"
          aria-label="Confirm Address it"
        >
          <span>
            keel checks out {view.branch} in the project folder and starts the{" "}
            {pickedFlow} flow there with this thread as its request. It pushes
            and posts nothing.
          </span>
          {confirm.fix !== undefined && (
            <label className="rv-fix">
              <span>
                You have not replied in this thread. What should the fix be?
                The reviewer's words are quoted; write your answer under them
                (it goes to the flow with the thread).
              </span>
              <textarea
                aria-label="What should the fix be?"
                rows={5}
                value={confirm.fix}
                onChange={(x) => setConfirm({ ...confirm, fix: x.target.value })}
              />
            </label>
          )}
          {flowChoices.length > 1 && (
            <label className="rv-row">
              <span>Flow</span>
              <select
                aria-label="Flow for Address it"
                value={pickedFlow}
                onChange={(x) => setPickedFlow(x.target.value)}
              >
                {flowChoices.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="rv-row">
            <button
              type="button"
              className="btn sm primary"
              disabled={addressing}
              onClick={() => void startAddress(confirm.note, confirm.fix)}
            >
              {addressing ? "Starting…" : "Start the flow"}
            </button>
            <button
              type="button"
              className="btn sm ghost"
              onClick={() => setConfirm(null)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      <footer className="rv-tacts">
        {view.can_post && !t.pending && !replying && (
          <button
            type="button"
            className="btn sm primary"
            onClick={() => {
              setFocusReply(true);
              setReplying(true);
            }}
          >
            Reply
          </button>
        )}
        {canWork && (
          <button
            type="button"
            className="btn sm rv-ai-btn"
            disabled={judging}
            title={
              quick
                ? `One quick check of this thread's lines: about ${kfmt(lo)}–${kfmt(hi)} tokens`
                : `Runs the ${cfg?.judge?.workflow ?? "review"} workflow`
            }
            onClick={() =>
              // a dismissed answer comes back as it was saved: no new check
              judgeHidden && judgeId && onShow ? onShow(judgeId) : void check()
            }
          >
            {judging ? "keel is checking…" : "✦ Is the reviewer right?"}
            {quick && !judge && !judging && (
              <span className="rv-cost">
                {" "}
                ~{kfmt(lo)}–{kfmt(hi)}
              </span>
            )}
          </button>
        )}
        {canWork && view.state === "open" && (
          <button
            type="button"
            className="btn sm rv-ai-btn"
            disabled={addressing}
            onClick={() =>
              askFix(judgeNote(judge?.status === "done" ? judge.result : null))
            }
          >
            ✦ Address it
          </button>
        )}
        {canWork &&
          (cfg?.buttons ?? []).map((b) => (
            <button
              key={b.id}
              type="button"
              className="btn sm ghost"
              disabled={running.includes(`button:${b.id}|${t.id}`)}
              title={
                b.mode === "ask"
                  ? `Asks ${b.agent} (one quick call)`
                  : `Runs the ${b.workflow} workflow`
              }
              onClick={() => {
                // a dismissed answer comes back as it was saved: no new run
                const runId = buttonRunId(b);
                if (runId && hidden.includes(runId) && onShow) {
                  onShow(runId);
                  return;
                }
                void R.runButton(pid, reviewKey, t.id, b.id).then((s) => {
                  if (!s) failed(`keel could not run ${b.label}`);
                });
              }}
            >
              {b.label}
            </button>
          ))}
        {sqlish && assistant && canWork && (
          <button
            type="button"
            className="rv-link rv-dbcheck"
            onClick={dbCheck}
          >
            Check against the database
          </button>
        )}
        {view.can_post && !t.pending && (
          <button
            type="button"
            className="btn sm ghost"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void R.resolve(pid, reviewKey, t.id, !t.resolved).finally(() =>
                setBusy(false),
              );
            }}
          >
            {t.resolved ? "Open again" : "Resolve"}
          </button>
        )}
      </footer>
    </article>
  );
}

// ---------------------------------------------------------------- keel's check

function costLine(r: JudgeResult | null | undefined, at?: string): string {
  if (!r) return "";
  const parts: string[] = [];
  const when = ago(r.checked_at ?? at);
  if (when) parts.push(`checked ${when}`);
  if (r.tokens != null)
    parts.push(
      `~${kfmt(r.tokens)} tokens${r.batch_size && r.batch_size > 1 ? ` for ${r.batch_size} threads` : ""}`,
    );
  if (r.context?.files)
    parts.push(
      `read ${r.context.files} file${r.context.files === 1 ? "" : "s"}`,
    );
  if (r.model) parts.push(`${r.model}${r.effort ? ` · ${r.effort}` : ""}`);
  return parts.join(" · ");
}

export function CheckBox({
  run,
  busy,
  who,
  sides,
  quick,
  canPost,
  assistant,
  flowName,
  allowHarder,
  onUse,
  same = [],
  onGo,
  onUseAll,
  onAddress,
  onAsk,
  onAgain,
  onDismiss,
}: {
  run: AiRun<JudgeResult> | null;
  busy: boolean;
  who: "author" | "reviewer" | "other";
  /** the thread's reviewer and author by name: the verdicts' words */
  sides: Sides;
  quick: boolean;
  canPost: boolean;
  assistant: boolean;
  flowName: string;
  allowHarder: boolean;
  onUse: (text: string) => void;
  /** the other threads of the same point: their ids and links' words */
  same?: { id: string; label: string }[];
  onGo?: (threadId: string) => void;
  /** the reply into this thread's box and the other threads' of the point */
  onUseAll?: (text: string) => void;
  onAddress: (r: JudgeResult | null) => void;
  onAsk: (r: JudgeResult | null) => void;
  onAgain: (harder: boolean) => void;
  /** hide this answer (it stays saved) */
  onDismiss?: () => void;
}) {
  const r = run?.result ?? null;
  const done = run?.status === "done" && r?.stage === "done";
  const flow = run?.status === "done" && r?.stage === "flow";
  const items: JudgeItem[] = (done && r?.items) || [];
  const needsChange = items.some(
    (i) => i.verdict === "reviewer_right" || i.verdict === "partly",
  );
  return (
    <section
      className="rv-check-box"
      aria-label="keel checked this thread"
      aria-live="polite"
      aria-busy={busy || undefined}
    >
      <div className="rv-check-h">
        <span className="rv-k" aria-hidden="true">
          k
        </span>
        <b>
          {busy
            ? "keel is checking the code…"
            : flow
              ? "keel started a flow for this thread"
              : "keel checked the code"}
        </b>
        {done && (
          <span
            className="rv-dim rv-check-cost"
            title={r?.context?.paths?.join("\n") || undefined}
          >
            {costLine(r, run?.updated_at)}
          </span>
        )}
        {!busy && onDismiss && (
          <DismissX label="Dismiss keel's check" onClick={onDismiss} />
        )}
      </div>
      {busy && (
        <span className="pg-spin">Reading the thread and its lines…</span>
      )}
      {!busy && run?.status === "failed" && (
        <>
          <ErrorBox error={{ message: run.error ?? "The check failed" }} />
          <div className="rv-row">
            <button
              type="button"
              className="btn sm"
              onClick={() => onAgain(false)}
            >
              Try again
            </button>
          </div>
        </>
      )}
      {!busy && flow && (
        <p className="rv-tnote">
          The {r?.workflow} flow has the thread as its request.{" "}
          {r?.flow_id && (
            <a
              className="rv-link"
              href={`#/flow/${encodeURIComponent(r.flow_id)}`}
            >
              Open the flow ›
            </a>
          )}
        </p>
      )}
      {!busy && done && (
        <>
          {run?.stale && (
            <p className="rv-warn">The code changed since keel checked it.</p>
          )}
          {r?.over_cap && (
            <p className="rv-warn">
              This check used more than its cap of {kfmt(r.cap ?? 0)} tokens.
            </p>
          )}
          {same.map((o) => (
            <button
              key={o.id}
              type="button"
              className="rv-link rv-same"
              onClick={() => onGo?.(o.id)}
            >
              {o.label}
            </button>
          ))}
          <ul className="rv-verdicts">
            {items.map((it, i) => {
              return (
                <li key={i}>
                  <span className={`rv-verdict ${verdictTone(it.verdict, who)}`}>
                    {verdictLabel(it.verdict, sides)}
                  </span>
                  <span className="rv-vtext">
                    <b>{it.claim}</b> {it.why}
                    {it.fix && <span className="rv-dim"> Fix: {it.fix}</span>}
                  </span>
                </li>
              );
            })}
          </ul>
          <div className="rv-row rv-check-acts">
            {canPost && r?.reply && (
              <button
                type="button"
                className="btn sm primary"
                onClick={() => onUse(r.reply!)}
              >
                Use as my reply
              </button>
            )}
            {canPost && r?.reply && same.length > 0 && onUseAll && (
              <button
                type="button"
                className="btn sm"
                title="keel's reply into the reply box of each thread that makes this point (nothing is posted)"
                onClick={() => onUseAll(r.reply!)}
              >
                {useOnAllLabel(same.length)}
              </button>
            )}
            {needsChange && (
              <button
                type="button"
                className="btn sm"
                onClick={() => onAddress(r)}
              >
                Address it (a {flowName} flow)
              </button>
            )}
            {assistant && (
              <button
                type="button"
                className="btn sm ghost"
                onClick={() => onAsk(r)}
              >
                Ask a follow-up
              </button>
            )}
            {quick && (
              <button
                type="button"
                className="btn sm ghost"
                onClick={() => onAgain(false)}
              >
                Check again
              </button>
            )}
            {quick && allowHarder && (
              <button
                type="button"
                className="btn sm ghost"
                title="A stronger model or more effort: more tokens"
                onClick={() => onAgain(true)}
              >
                Think harder
              </button>
            )}
            {onDismiss && (
              <button
                type="button"
                className="btn sm ghost rv-dismiss"
                title="Hide keel's answer (asking again shows it, without a new check)"
                onClick={onDismiss}
              >
                Dismiss
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}

/** The small ✕ in the top-right corner of keel's answer: hides it (it stays saved). */
function DismissX({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      className="rv-check-x"
      aria-label={label}
      title="Dismiss"
      onClick={onClick}
    >
      ✕
    </button>
  );
}

// ---------------------------------------------------------------- an extra button's answer

function ButtonAnswer({
  b,
  run,
  busy,
  canPost,
  onUse,
  onAgain,
  onDismiss,
}: {
  b: ThreadButtonInfo;
  run: AiRun<JudgeResult> | null;
  busy: boolean;
  canPost: boolean;
  onUse: (text: string) => void;
  onAgain: () => void;
  /** hide this answer (it stays saved) */
  onDismiss?: () => void;
}) {
  if (!run && !busy) return null;
  const r = run?.result ?? null;
  const working = busy || run?.status === "running";
  return (
    <section
      className="rv-check-box"
      aria-label={b.label}
      aria-live="polite"
      aria-busy={working || undefined}
    >
      <div className="rv-check-h">
        <span className="rv-k" aria-hidden="true">
          k
        </span>
        <b>{b.label}</b>
        {run?.status === "done" && r?.stage === "done" && (
          <span className="rv-dim rv-check-cost">
            {costLine(r, run.updated_at)}
          </span>
        )}
        {!working && onDismiss && (
          <DismissX label={`Dismiss ${b.label}`} onClick={onDismiss} />
        )}
      </div>
      {working && <span className="pg-spin">keel is on it…</span>}
      {!working && run?.status === "failed" && (
        <ErrorBox error={{ message: run.error ?? "It failed" }} />
      )}
      {!working && r?.stage === "flow" && (
        <p className="rv-tnote">
          The {r.workflow} flow runs{r.branch ? ` on ${r.branch}` : ""}.{" "}
          {r.flow_id && (
            <a
              className="rv-link"
              href={`#/flow/${encodeURIComponent(r.flow_id)}`}
            >
              Open the flow ›
            </a>
          )}
        </p>
      )}
      {!working && r?.stage === "done" && r.text && (
        <>
          <div className="rv-msg-t">
            <Markdown text={r.text} fold={12} />
          </div>
          <div className="rv-row">
            {canPost && (
              <button
                type="button"
                className="btn sm"
                onClick={() => onUse(r.text!)}
              >
                Use as my reply
              </button>
            )}
            <button type="button" className="btn sm ghost" onClick={onAgain}>
              Ask again
            </button>
            {onDismiss && (
              <button
                type="button"
                className="btn sm ghost rv-dismiss"
                title="Hide this answer (asking again shows it, without a new run)"
                onClick={onDismiss}
              >
                Dismiss
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
