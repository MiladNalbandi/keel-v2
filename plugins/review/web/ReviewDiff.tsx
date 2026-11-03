// v0.14.0 one file of a review: the diff (side by side or inline) with the threads, your pending comments and keel's
// findings on their lines. A line number starts a comment (shift-click another, or drag over the numbers, for a
// comment on several lines); ⌘-click on a name goes to its declaration.

import {
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ErrorBox,
  highlight,
  languageOf,
  parseDiff,
  scrollToCenter,
  useLanguage,
  useSelectAll,
  splitRows,
  useLoad,
  type DiffRow,
} from "@keel/web-sdk";
import { reviewApi } from "./reviewApi";
import * as R from "./store";
import { wordAt } from "./keymap";
import { rowsOf, useLineRange, type Point, type Span } from "./range";
import { fileChanges, peekAfter, sideBySide, type Change } from "./wholeFile";
import { ChangeGutter, ChangeKey, ChangePeek } from "./WholeFileMarks";

export type Side = "RIGHT" | "LEFT";
export type LineRef = { path: string; line: number; side: Side };
/** The lines a new comment is on: one line, or from startLine on startSide to line. */
export type LineSpan = LineRef & { startLine: number | null; startSide: Side | null };
/** What sits under a line; a thread or comment on several lines also marks its rows from startLine. */
export type Anchor = {
  line: number;
  side: Side;
  node: ReactNode;
  startLine?: number | null;
  startSide?: Side | null;
};
/** The lines picked for the comment being written. */
export type Picked = Omit<Span, "line"> & { line: number | null };

/** The rows every range anchor covers, and the picked span's rows. */
function marks(
  anchors: Anchor[],
  picked: Picked | null,
  rowOf: (side: Side, line: number) => number | undefined,
) {
  const ranges = new Set<number>();
  for (const a of anchors) {
    if (a.startLine == null) continue;
    const r = rowsOf(a, rowOf);
    if (r) for (let i = r[0]; i <= r[1]; i++) ranges.add(i);
  }
  return { ranges, picked: picked ? rowsOf(picked, rowOf) : null };
}

const esc = (s: string) =>
  s.replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!,
  );
const cell = (r: DiffRow | undefined, lang: string | null) =>
  r ? (highlight(r.text, lang) ?? esc(r.text)) : "";

/** The word under a click (for ⌘-click), from the browser's caret at that point. */
export function wordFromClick(e: React.MouseEvent): string | null {
  const doc = document as Document & {
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
    caretPositionFromPoint?: (
      x: number,
      y: number,
    ) => { offsetNode: Node; offset: number } | null;
  };
  const pos = doc.caretPositionFromPoint?.(e.clientX, e.clientY);
  if (pos?.offsetNode?.textContent)
    return wordAt(pos.offsetNode.textContent, pos.offset);
  const r = doc.caretRangeFromPoint?.(e.clientX, e.clientY);
  if (r?.startContainer?.textContent)
    return wordAt(r.startContainer.textContent, r.startOffset);
  const sel = window.getSelection()?.toString().trim();
  return sel && /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(sel) ? sel : null;
}

export function ReviewDiff({
  pid,
  reviewKey,
  path,
  mode,
  anchors,
  focus,
  change,
  picked = null,
  onLine,
  onWord,
  onChanges,
}: {
  pid: string;
  reviewKey: string;
  path: string;
  mode: "split" | "inline";
  /** what sits under a line: threads, pending comments, findings, the comment box */
  anchors: Anchor[];
  /** the line to scroll to and mark */
  focus: { line: number; side: Side; n: number } | null;
  /** F7 / ⇧F7: the index of the change block to show (n changes on every press) */
  change: { index: number; n: number } | null;
  /** the lines of the comment being written: marked */
  picked?: Picked | null;
  onLine: (l: LineSpan) => void;
  onWord: (word: string, l: LineRef | null) => void;
  onChanges: (count: number) => void;
}) {
  // 0.18.65 another base (the panel's pick) is another diff
  const baseSha = R.review(pid, reviewKey).view?.base_sha ?? "";
  const d = useLoad(
    `review-diff:${pid}:${reviewKey}:${path}:${baseSha}`,
    () => reviewApi.diff(pid, reviewKey, path),
    { live: false },
  );
  const parsed = useMemo(
    () => (d.data ? parseDiff(d.data.diff) : null),
    [d.data],
  );
  const lang = languageOf(path);
  useLanguage(lang);
  const body = useRef<HTMLDivElement>(null);
  // ⌘/Ctrl+A selects this diff, not the whole window
  useSelectAll(body, ".dv-code, .dv-hunk");
  const [current, setCurrent] = useState<LineRef | null>(null);
  const split = useMemo(
    () => (parsed && mode === "split" ? splitRows(parsed.rows) : null),
    [parsed, mode],
  );
  // where a side's line is drawn: the row of the inline list or of the side-by-side one
  const rowIndex = useMemo(() => {
    const m = new Map<string, number>();
    if (split)
      split.forEach((s, i) => {
        if (s.kind === "hunk") return;
        if (s.left?.old != null) m.set(`L${s.left.old}`, i);
        if (s.right?.new != null) m.set(`R${s.right.new}`, i);
      });
    else
      parsed?.rows.forEach((r, i) => {
        if (r.kind === "hunk" || r.kind === "meta") return;
        if (r.old != null && r.kind !== "add") m.set(`L${r.old}`, i);
        if (r.new != null && r.kind !== "del") m.set(`R${r.new}`, i);
      });
    return m;
  }, [split, parsed]);
  const rowOf = (side: Side, line: number) =>
    rowIndex.get(`${side === "LEFT" ? "L" : "R"}${line}`);
  const range = useLineRange((sp: Span) => {
    const l = { path, line: sp.line, side: sp.side };
    setCurrent(l);
    onLine({ ...l, startLine: sp.startLine, startSide: sp.startSide });
  });

  // the first row of every change block (F7 jumps between them)
  const blocks = useMemo(() => {
    const out: number[] = [];
    parsed?.rows.forEach((r, i) => {
      const changed = r.kind === "add" || r.kind === "del";
      const prev = parsed.rows[i - 1];
      if (changed && !(prev && (prev.kind === "add" || prev.kind === "del")))
        out.push(i);
    });
    return out;
  }, [parsed]);
  useEffect(() => onChanges(blocks.length), [blocks.length, onChanges]);

  useEffect(() => {
    if (!change || !blocks.length) return;
    const i = blocks[Math.max(0, Math.min(blocks.length - 1, change.index))];
    scrollToCenter(body.current, body.current?.querySelector<HTMLElement>(`[data-row="${i}"]`));
    const r = parsed?.rows[i];
    if (r)
      setCurrent({
        path,
        line: (r.kind === "del" ? r.old : r.new) ?? 1,
        side: r.kind === "del" ? "LEFT" : "RIGHT",
      });
  }, [change?.n, blocks, parsed, path]);

  useEffect(() => {
    if (!focus || !parsed) return;
    const el = body.current?.querySelector<HTMLElement>(
      `[data-${focus.side === "LEFT" ? "old" : "new"}="${focus.line}"]`,
    );
    scrollToCenter(body.current, el);
    setCurrent({ path, line: focus.line, side: focus.side });
  }, [focus?.n, focus?.line, focus?.side, parsed, path]);

  if (d.error)
    return (
      <div className="ed-note">
        <ErrorBox error={d.error} onRetry={() => void d.reload()} />
      </div>
    );
  if (!d.data || !parsed)
    return (
      <div className="ed-note">
        <span className="pg-spin" role="status">
          Reading the changes…
        </span>
      </div>
    );
  if (d.data.binary)
    return (
      <div className="ed-note">
        A binary file changed. keel does not show binary diffs.
      </div>
    );

  const under = (line: number | undefined, side: Side) =>
    line == null
      ? null
      : anchors
          .filter((a) => a.line === line && a.side === side)
          .map((a, i) => <Fragment key={i}>{a.node}</Fragment>);
  const marked = marks(anchors, picked, rowOf);
  const rowCls = (i: number) => {
    const sel = range.rows ?? marked.picked;
    return (
      (sel && i >= sel[0] && i <= sel[1] ? " sel" : "") +
      (marked.ranges.has(i) ? " rng" : "")
    );
  };
  const click = (e: React.MouseEvent, line: number | undefined, side: Side) => {
    if (line != null) setCurrent({ path, line, side });
    if (e.metaKey || e.ctrlKey) {
      const w = wordFromClick(e);
      if (w) {
        e.preventDefault();
        onWord(w, line != null ? { path, line, side } : null);
      }
    }
  };
  const isCur = (line: number | undefined, side: Side) =>
    current != null && line === current.line && side === current.side;
  const gutter = (
    row: number,
    line: number | undefined,
    side: Side,
    cls = "",
    alt?: number,
  ) => (
    <button
      type="button"
      className={`rv-ln ${cls}`}
      disabled={line == null}
      tabIndex={-1}
      aria-label={
        line == null
          ? undefined
          : `Comment on line ${line}${side === "LEFT" ? " (old)" : ""}`
      }
      title={line == null ? undefined : "Shift-click or drag for several lines"}
      {...range.on(line == null ? null : ({ row, line, side, alt } satisfies Point))}
    >
      {line ?? ""}
    </button>
  );

  return (
    <div
      className={`dv rv-dv ${mode}`}
      data-current={current ? `${current.side}:${current.line}` : undefined}
    >
      <div className="dv-head">
        <span>
          {d.data.ref} · <b className="mono">{path}</b>
        </span>
        <span className="dv-stat">
          <span className="add">+{parsed.added}</span>{" "}
          <span className="del">−{parsed.removed}</span> · {blocks.length}{" "}
          change{blocks.length === 1 ? "" : "s"}
        </span>
      </div>
      <div
        className="dv-body"
        ref={body}
        tabIndex={0}
        role="region"
        aria-label={`Changes in ${path}`}
      >
        {mode === "inline"
          ? parsed.rows.map((r, i) => {
              if (r.kind === "hunk" || r.kind === "meta")
                return (
                  <div key={i} className={`dv-row ${r.kind}`}>
                    <span className="dv-hunk">{r.text}</span>
                  </div>
                );
              const side: Side = r.kind === "del" ? "LEFT" : "RIGHT";
              const line = side === "LEFT" ? r.old : r.new;
              return (
                <Fragment key={i}>
                  <div
                    className={`dv-row ${r.kind}${isCur(line, side) ? " cur" : ""}${rowCls(i)}`}
                    data-row={i}
                    data-old={r.old}
                    data-new={r.new}
                  >
                    {gutter(i, r.kind === "add" ? undefined : r.old, "LEFT", "old", r.kind === "ctx" ? r.new : undefined)}
                    {gutter(i, r.kind === "del" ? undefined : r.new, "RIGHT", "new", r.kind === "ctx" ? r.old : undefined)}
                    <span className="dv-sg" aria-hidden="true">
                      {r.kind === "add" ? "+" : r.kind === "del" ? "−" : ""}
                    </span>
                    <code
                      className="dv-code"
                      onClick={(e) => click(e, line, side)}
                      dangerouslySetInnerHTML={{ __html: cell(r, lang) }}
                    />
                  </div>
                  {under(line, side)}
                  {r.kind === "ctx" && under(r.old, "LEFT")}
                </Fragment>
              );
            })
          : (split ?? []).map((s, i) => {
              if (s.kind === "hunk")
                return (
                  <div key={i} className="dv-srow hunk">
                    <span className="dv-hunk">{s.text}</span>
                  </div>
                );
              const first = parsed.rows.indexOf((s.left ?? s.right)!);
              return (
                <Fragment key={i}>
                  <div
                    className={`dv-srow${isCur(s.right?.new, "RIGHT") || isCur(s.left?.old, "LEFT") ? " cur" : ""}${rowCls(i)}`}
                    data-row={first}
                    data-old={s.left?.old}
                    data-new={s.right?.new}
                  >
                    {gutter(i, s.left?.old, "LEFT", s.left?.kind ?? "none", s.left?.kind === "ctx" ? s.right?.new : undefined)}
                    <code
                      className={`dv-code ${s.left?.kind ?? "none"}`}
                      data-side="old"
                      onClick={(e) => click(e, s.left?.old, "LEFT")}
                      dangerouslySetInnerHTML={{ __html: cell(s.left, lang) }}
                    />
                    {gutter(i, s.right?.new, "RIGHT", s.right?.kind ?? "none", s.right?.kind === "ctx" ? s.left?.old : undefined)}
                    <code
                      className={`dv-code ${s.right?.kind ?? "none"}`}
                      data-side="new"
                      onClick={(e) => click(e, s.right?.new, "RIGHT")}
                      dangerouslySetInnerHTML={{ __html: cell(s.right, lang) }}
                    />
                  </div>
                  {under(s.right?.new, "RIGHT")}
                  {(s.left?.kind === "del" || s.left?.kind === "ctx") && under(s.left.old, "LEFT")}
                </Fragment>
              );
            })}
      </div>
    </div>
  );
}

/**
 * A whole file of the reviewed commit (Jump to source, Go to declaration), with ⌘-click to keep jumping. Its line
 * numbers start a comment on the new side, one line or several, like the diff's (a line outside the diff goes into
 * the review's text when it is sent). 0.21.1 a changed file shows its changes: a bar next to a changed line, a ▶
 * where lines were deleted, a click shows the old code; side by side, the old file is on the left.
 */
export function FileAtView({
  pid,
  reviewKey,
  path,
  line,
  onWord,
  onBackToDiff,
  changed,
  mode = "inline",
  anchors = [],
  picked = null,
  onLine,
}: {
  pid: string;
  reviewKey: string;
  path: string;
  line: number | null;
  changed: boolean;
  onWord: (word: string, l: LineRef | null) => void;
  onBackToDiff: () => void;
  /** a changed file: its new lines with the changes marked, or the old file next to the new one */
  mode?: "split" | "inline";
  /** what sits under a line (new side only: the whole file is the head commit's) */
  anchors?: Anchor[];
  picked?: Picked | null;
  /** a line number starts a comment; without it the numbers are plain */
  onLine?: (l: LineSpan) => void;
}) {
  const f = useLoad(
    `review-file:${pid}:${reviewKey}:${path}`,
    () => reviewApi.file(pid, reviewKey, path),
    { live: false },
  );
  // the same diff (and cache key) as the Changes view
  const baseSha = R.review(pid, reviewKey).view?.base_sha ?? "";
  const d = useLoad(
    changed ? `review-diff:${pid}:${reviewKey}:${path}:${baseSha}` : null,
    () => reviewApi.diff(pid, reviewKey, path),
    { live: false },
  );
  const fc = useMemo(
    () => (changed && d.data && !d.data.binary ? fileChanges(parseDiff(d.data.diff).rows) : null),
    [changed, d.data],
  );
  const [peek, setPeek] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const lang = languageOf(path);
  useLanguage(lang);
  useSelectAll(box, ".dv-code");
  const range = useLineRange((sp: Span) =>
    onLine?.({ path, line: sp.line, side: sp.side, startLine: sp.startLine, startSide: sp.startSide }),
  );
  useEffect(() => {
    if (!f.data || !line) return;
    scrollToCenter(box.current, box.current?.querySelector<HTMLElement>(`[data-line="${line}"]`));
  }, [f.data, line]);
  const lines = useMemo(() => {
    const all = f.data ? f.data.text.split("\n") : [];
    if (all.length && all[all.length - 1] === "") all.pop();
    return all;
  }, [f.data]);
  const side = mode === "split" && fc ? sideBySide(lines, fc) : null;
  if (f.error)
    return (
      <div className="ed-note">
        <ErrorBox error={f.error} />
      </div>
    );
  if (!f.data)
    return (
      <div className="ed-note">
        <span className="pg-spin" role="status">
          Opening {path}…
        </span>
      </div>
    );
  const right = anchors.filter((a) => a.side === "RIGHT");
  // the row a new line is drawn in: its index, or its row of the two columns
  const newRow = new Map<number, number>();
  side?.forEach((r, i) => r.right && newRow.set(r.right.line, i));
  const rowOf = (s: Side, n: number) =>
    s !== "RIGHT" || n < 1 || n > lines.length ? undefined : side ? newRow.get(n) : n - 1;
  const marked = marks(right, picked, rowOf);
  const sel = range.rows ?? marked.picked;
  const rowCls = (i: number, n: number | null) =>
    `${n != null && line === n ? " cur" : ""}${sel && i >= sel[0] && i <= sel[1] ? " sel" : ""}${marked.ranges.has(i) ? " rng" : ""}`;
  const total = fc?.changes.length ?? 0;
  const open = peek != null ? fc?.changes[peek - 1] ?? null : null;
  const go = (n: number) => {
    setPeek(n);
    const c = fc?.changes[n - 1];
    if (!c) return;
    const at = c.first ?? Math.max(1, c.after);
    scrollToCenter(box.current, box.current?.querySelector<HTMLElement>(`[data-line="${at}"]`));
  };
  const pick = (c: Change) => (peek === c.n ? setPeek(null) : setPeek(c.n));
  const peekBox = (n: number) =>
    open && peekAfter(open) === n ? (
      <ChangePeek change={open} total={total} lang={lang} onGo={go} onClose={() => setPeek(null)} />
    ) : null;
  const number = (i: number, n: number) =>
    onLine ? (
      <button
        type="button"
        className="dv-ln rv-ln"
        tabIndex={-1}
        aria-label={`Comment on line ${n}`}
        title="Shift-click or drag for several lines"
        {...range.on({ row: i, line: n, side: "RIGHT" })}
      >
        {n}
      </button>
    ) : (
      <span className="dv-ln">{n}</span>
    );
  const code = (t: string, n: number | null, s: "old" | "new", cls = "") => (
    <code
      className={`dv-code${cls}`}
      data-side={side ? s : undefined}
      onClick={(e) => {
        if (n != null && (e.metaKey || e.ctrlKey)) {
          const w = wordFromClick(e);
          if (w) onWord(w, { path, line: n, side: s === "old" ? "LEFT" : "RIGHT" });
        }
      }}
      dangerouslySetInnerHTML={{ __html: highlight(t, lang) ?? esc(t) }}
    />
  );
  const under = (n: number) =>
    right
      .filter((a) => a.line === n)
      .map((a, k) => (
        <Fragment key={k}>{a.node}</Fragment>
      ));
  return (
    <div className={`dv rv-file${fc ? " wf-on" : ""}${side ? " split" : ""}`}>
      <div className="dv-head">
        <span>
          <b className="mono">{path}</b> as it is in {f.data.ref}{" "}
          <span className="mono rv-dim">{f.data.sha.slice(0, 7)}</span>
        </span>
        {fc && <ChangeKey peek={!side} />}
        {changed && (
          <button type="button" className="btn sm ghost" onClick={onBackToDiff}>
            Show its changes
          </button>
        )}
      </div>
      <div
        className="dv-body"
        ref={box}
        tabIndex={0}
        role="region"
        aria-label={`${path} in the reviewed commit`}
      >
        {side
          ? side.map((r, i) => (
              <Fragment key={i}>
                <div className={`dv-srow wf-srow${rowCls(i, r.right?.line ?? null)}`} data-line={r.right?.line}>
                  <span className="dv-ln">{r.left?.line ?? ""}</span>
                  <span className="wf-g">{r.left?.kind === "del" && <i className="wf-bar del" aria-hidden="true" />}</span>
                  {r.left ? code(r.left.text, r.left.line, "old") : <code className="dv-code none" data-side="old" />}
                  {r.right ? number(i, r.right.line) : <span className="dv-ln" />}
                  <span className="wf-g">
                    {r.right && r.right.kind !== "ctx" && <i className={`wf-bar ${r.right.kind}`} aria-hidden="true" />}
                  </span>
                  {r.right ? code(r.right.text, r.right.line, "new") : <code className="dv-code none" data-side="new" />}
                </div>
                {r.right && under(r.right.line)}
              </Fragment>
            ))
          : (
            <>
              {peekBox(0)}
              {lines.map((t, i) => (
                <Fragment key={i}>
                  <div className={`rv-fl${rowCls(i, i + 1)}`} data-line={i + 1}>
                    {number(i, i + 1)}
                    {fc && (
                      <ChangeGutter
                        line={i + 1}
                        mark={fc.marks.get(i + 1)}
                        cut={fc.cuts.get(i + 1)}
                        cutBelow={i === lines.length - 1 ? fc.cuts.get(i + 2) : undefined}
                        open={peek}
                        onPick={pick}
                      />
                    )}
                    {code(t, i + 1, "new")}
                  </div>
                  {peekBox(i + 1)}
                  {under(i + 1)}
                </Fragment>
              ))}
            </>
          )}
        {f.data.truncated && (
          <div className="ed-note">
            The file is longer: keel shows its first 1 MB.
          </div>
        )}
        {d.data?.truncated && (
          <div className="ed-note">
            The diff is long: the marks stop where keel cut it.
          </div>
        )}
      </div>
    </div>
  );
}
