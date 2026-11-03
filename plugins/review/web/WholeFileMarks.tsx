// 0.21.1 the marks of the whole file with its changes (wholeFile.ts), shared by the classic view's Whole file and the
// Review page's: a thin bar next to a line number (green added, blue changed), a red ▶ where lines were deleted, and
// the peek a click on either opens: the change's old code, previous / next change, close.

import { highlight } from "@keel/web-sdk";
import { changeTitle, type Change } from "./wholeFile";

const WORD: Record<Change["kind"], string> = { add: "added", mod: "changed", del: "deleted" };

const esc = (s: string) =>
  s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

/** The cell next to a line number: the bar of its change, and a ▶ on its top edge when lines were deleted above it. */
export function ChangeGutter({
  line,
  mark,
  cut,
  cutBelow,
  open,
  onPick,
}: {
  /** this row's line in the new file (a change's first line is its one tab stop) */
  line: number;
  mark: Change | undefined;
  cut: Change | undefined;
  /** the last line: lines deleted after it (the ▶ on its bottom edge) */
  cutBelow?: Change;
  /** the change whose peek is open */
  open: number | null;
  onPick: (c: Change) => void;
}) {
  const label = (c: Change) =>
    c.kind === "del"
      ? `Change ${c.n}: ${c.old.length} line${c.old.length === 1 ? "" : "s"} deleted here. Show ${c.old.length === 1 ? "it" : "them"}`
      : `Change ${c.n}: ${WORD[c.kind]}. Show the old code`;
  return (
    <span className="wf-g">
      {mark && (
        <button
          type="button"
          className={`wf-bar ${mark.kind}${open === mark.n ? " open" : ""}`}
          aria-label={label(mark)}
          aria-expanded={open === mark.n}
          title={label(mark)}
          tabIndex={line === mark.first ? 0 : -1}
          onClick={() => onPick(mark)}
        />
      )}
      {cut && (
        <button
          type="button"
          className={`wf-cut${open === cut.n ? " open" : ""}`}
          aria-label={label(cut)}
          aria-expanded={open === cut.n}
          title={label(cut)}
          onClick={() => onPick(cut)}
        />
      )}
      {cutBelow && (
        <button
          type="button"
          className={`wf-cut below${open === cutBelow.n ? " open" : ""}`}
          aria-label={label(cutBelow)}
          aria-expanded={open === cutBelow.n}
          title={label(cutBelow)}
          onClick={() => onPick(cutBelow)}
        />
      )}
    </span>
  );
}

/** The open change: its old code (the lines it removed or replaced), previous / next change, close. */
export function ChangePeek({
  change,
  total,
  lang,
  onGo,
  onClose,
  onSideBySide,
}: {
  change: Change;
  total: number;
  lang: string | null;
  /** another change, by its number */
  onGo: (n: number) => void;
  onClose: () => void;
  /** the whole file side by side (left out where there is none) */
  onSideBySide?: () => void;
}) {
  const { title, where } = changeTitle(change, total);
  const prev = change.n > 1 ? change.n - 1 : total;
  const next = change.n < total ? change.n + 1 : 1;
  return (
    <div
      className="wf-peek"
      role="group"
      aria-label={title}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="wf-peek-h">
        <span className={`wf-peek-k ${change.kind}`} aria-hidden="true" />
        <b>{title}</b>
        <span className="wf-dim">{where}</span>
        <span className="wf-peek-a">
          {total > 1 && (
            <>
              <button type="button" className="wf-btn" aria-label="Previous change" title="Previous change" onClick={() => onGo(prev)}>
                ‹
              </button>
              <button type="button" className="wf-btn" aria-label="Next change" title="Next change" onClick={() => onGo(next)}>
                ›
              </button>
            </>
          )}
          {onSideBySide && (
            <button type="button" className="wf-btn" onClick={onSideBySide}>
              Old | New
            </button>
          )}
          <button type="button" className="wf-btn wf-x" aria-label="Close" title="Close (Esc)" autoFocus onClick={onClose}>
            ×
          </button>
        </span>
      </div>
      {change.old.length ? (
        <div className="wf-old" role="region" aria-label="The old code">
          <div className="wf-dim wf-old-h">Before:</div>
          {change.old.map((o) => (
            <div key={o.line} className="wf-old-l">
              <span className="wf-old-n">{o.line}</span>
              <span className="wf-old-m" aria-hidden="true">
                −
              </span>
              <code dangerouslySetInnerHTML={{ __html: highlight(o.text, lang) ?? esc(o.text) }} />
            </div>
          ))}
        </div>
      ) : (
        <p className="wf-none">
          Nothing was here before. {change.added === 1 ? "This line is new." : `These ${change.added} lines are new.`}
        </p>
      )}
    </div>
  );
}

/** The key under the file bar: what the marks mean. */
export function ChangeKey({ peek = true }: { peek?: boolean }) {
  return (
    <span className="wf-key">
      <span>
        <i className="wf-bar add" aria-hidden="true" /> added
      </span>
      <span>
        <i className="wf-bar mod" aria-hidden="true" /> changed
      </span>
      <span>
        <i className="wf-cut" aria-hidden="true" /> deleted here
      </span>
      {peek && <span className="wf-dim">Click a mark to see the old code.</span>}
    </span>
  );
}
