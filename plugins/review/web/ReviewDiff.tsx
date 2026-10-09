// v0.14.0 one file of a review: the diff (side by side or inline) with the threads, your pending comments and keel's
// findings on their lines. A line number starts a comment; ⌘-click on a name goes to its declaration.

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
  splitRows,
  useLoad,
  type DiffRow,
} from "@keel/web-sdk";
import { reviewApi } from "./reviewApi";
import { wordAt } from "./keymap";

export type Side = "RIGHT" | "LEFT";
export type LineRef = { path: string; line: number; side: Side };
export type Anchor = { line: number; side: Side; node: ReactNode };

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
  onLine: (l: LineRef) => void;
  onWord: (word: string, l: LineRef | null) => void;
  onChanges: (count: number) => void;
}) {
  const d = useLoad(
    `review-diff:${pid}:${reviewKey}:${path}`,
    () => reviewApi.diff(pid, reviewKey, path),
    { live: false },
  );
  const parsed = useMemo(
    () => (d.data ? parseDiff(d.data.diff) : null),
    [d.data],
  );
  const lang = languageOf(path);
  const body = useRef<HTMLDivElement>(null);
  const [current, setCurrent] = useState<LineRef | null>(null);

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
    body.current
      ?.querySelector<HTMLElement>(`[data-row="${i}"]`)
      ?.scrollIntoView({ block: "center" });
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
    el?.scrollIntoView({ block: "center" });
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
  const pick = (line: number | undefined, side: Side) => {
    if (line == null) return;
    const l = { path, line, side };
    setCurrent(l);
    onLine(l);
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
  const gutter = (line: number | undefined, side: Side, cls = "") => (
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
      onClick={() => pick(line, side)}
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
                    className={`dv-row ${r.kind}${isCur(line, side) ? " cur" : ""}`}
                    data-row={i}
                    data-old={r.old}
                    data-new={r.new}
                  >
                    {gutter(r.old, "LEFT", "old")}
                    {gutter(r.new, "RIGHT", "new")}
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
          : splitRows(parsed.rows).map((s, i) => {
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
                    className={`dv-srow${isCur(s.right?.new, "RIGHT") || isCur(s.left?.old, "LEFT") ? " cur" : ""}`}
                    data-row={first}
                    data-old={s.left?.old}
                    data-new={s.right?.new}
                  >
                    {gutter(s.left?.old, "LEFT", s.left?.kind ?? "none")}
                    <code
                      className={`dv-code ${s.left?.kind ?? "none"}`}
                      data-side="old"
                      onClick={(e) => click(e, s.left?.old, "LEFT")}
                      dangerouslySetInnerHTML={{ __html: cell(s.left, lang) }}
                    />
                    {gutter(s.right?.new, "RIGHT", s.right?.kind ?? "none")}
                    <code
                      className={`dv-code ${s.right?.kind ?? "none"}`}
                      data-side="new"
                      onClick={(e) => click(e, s.right?.new, "RIGHT")}
                      dangerouslySetInnerHTML={{ __html: cell(s.right, lang) }}
                    />
                  </div>
                  {under(s.right?.new, "RIGHT")}
                  {s.left?.kind === "del" && under(s.left.old, "LEFT")}
                </Fragment>
              );
            })}
      </div>
    </div>
  );
}

/** A whole file of the reviewed commit (Jump to source, Go to declaration), with ⌘-click to keep jumping. */
export function FileAtView({
  pid,
  reviewKey,
  path,
  line,
  onWord,
  onBackToDiff,
  changed,
}: {
  pid: string;
  reviewKey: string;
  path: string;
  line: number | null;
  changed: boolean;
  onWord: (word: string, l: LineRef | null) => void;
  onBackToDiff: () => void;
}) {
  const f = useLoad(
    `review-file:${pid}:${reviewKey}:${path}`,
    () => reviewApi.file(pid, reviewKey, path),
    { live: false },
  );
  const box = useRef<HTMLDivElement>(null);
  const lang = languageOf(path);
  useEffect(() => {
    if (!f.data || !line) return;
    box.current
      ?.querySelector<HTMLElement>(`[data-line="${line}"]`)
      ?.scrollIntoView({ block: "center" });
  }, [f.data, line]);
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
  const lines = f.data.text.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  return (
    <div className="dv rv-file">
      <div className="dv-head">
        <span>
          <b className="mono">{path}</b> as it is in {f.data.ref}{" "}
          <span className="mono rv-dim">{f.data.sha.slice(0, 7)}</span>
        </span>
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
        {lines.map((t, i) => (
          <div
            key={i}
            className={`rv-fl${line === i + 1 ? " cur" : ""}`}
            data-line={i + 1}
          >
            <span className="dv-ln">{i + 1}</span>
            <code
              className="dv-code"
              onClick={(e) => {
                if (e.metaKey || e.ctrlKey) {
                  const w = wordFromClick(e);
                  if (w) onWord(w, { path, line: i + 1, side: "RIGHT" });
                }
              }}
              dangerouslySetInnerHTML={{ __html: highlight(t, lang) ?? esc(t) }}
            />
          </div>
        ))}
        {f.data.truncated && (
          <div className="ed-note">
            The file is longer: keel shows its first 1 MB.
          </div>
        )}
      </div>
    </div>
  );
}
