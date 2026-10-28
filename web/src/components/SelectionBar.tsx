// keel 0.18.63 a small toolbar over the text you select in code (the Code page's file view, the Review page's diff):
// Search for it, ask KeelBot about it, or have KeelBot review it. The page that shows the code says which actions there
// are and what they do; this part only finds the selection, places the bar above it and keeps the selection while you
// press a button.
//
//   select lines in the code ─▶ mouseup / shift+arrows ─▶ [ Search  Ask KeelBot  Review ] above the selection
//                                                          a press runs the action; Esc, a click elsewhere or an
//                                                          empty selection hides it
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";

export type SelectionAction = {
  id: string;
  label: string;
  title?: string;
  /** runs while the selection is still there: read it with window.getSelection() (or the page's own reader) */
  run: () => void;
};

/** What Search looks for: the selection's first line with text, trimmed (the search is line by line). */
export function searchTextOf(text: string): string {
  return (text.split("\n").find((l) => l.trim()) ?? "").trim().slice(0, 200);
}

/** What KeelBot gets for Review: the code is the selection the page attaches, or `code` when it has to be in the text. */
export function selectionReviewRequest(where: string, code?: string): string {
  return `Review ${where}${code ? `:\n\n\`\`\`\n${code}\n\`\`\`` : ""}\n\n`
    + "Look for bugs, cases it does not handle, security and performance problems, and anything unclear. For each finding "
    + "give the file:line, how sure you are, and the fix. Change nothing.";
}

const BAR_H = 34;
const GAP = 6;

/** The selection's text when it lies inside `root` (both ends), else null. */
export function selectionIn(root: HTMLElement | null): { text: string; range: Range } | null {
  const sel = typeof window !== "undefined" ? window.getSelection() : null;
  if (!root || !sel || sel.isCollapsed || sel.rangeCount === 0) return null;
  if (!sel.anchorNode || !sel.focusNode || !root.contains(sel.anchorNode) || !root.contains(sel.focusNode)) return null;
  const text = sel.toString().trim();
  return text ? { text, range: sel.getRangeAt(0) } : null;
}

/** Where the bar goes: above the selection, below it when there is no room above, always inside the window. */
function placeOf(range: Range, width: number): { top: number; left: number } {
  const r = range.getBoundingClientRect?.() ?? null;
  const box = r && (r.width || r.height) ? r
    : (range.startContainer instanceof Element ? range.startContainer : range.startContainer.parentElement)?.getBoundingClientRect();
  const top0 = box?.top ?? 0;
  const bottom = box?.bottom ?? 0;
  const left0 = box?.left ?? 0;
  const top = top0 - BAR_H - GAP < 4 ? bottom + GAP : top0 - BAR_H - GAP;
  const vw = typeof window !== "undefined" ? window.innerWidth : 1024;
  return { top: Math.max(4, top), left: Math.max(8, Math.min(left0, vw - width - 8)) };
}

export function SelectionBar({ within, actions }: { within: RefObject<HTMLElement | null>; actions: SelectionAction[] }) {
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);
  const bar = useRef<HTMLDivElement | null>(null);
  const shown = at != null;

  const update = useCallback(() => {
    const s = selectionIn(within.current);
    if (!s || !actions.length) return setAt(null);
    setAt(placeOf(s.range, bar.current?.offsetWidth || 260));
  }, [within, actions.length]);

  useEffect(() => {
    // shown when the person finished selecting (mouse up, or shift+arrows / ⌘A), not while dragging
    const done = (e: Event) => {
      if (bar.current && e.target instanceof Node && bar.current.contains(e.target)) return;
      window.setTimeout(update, 0);
    };
    const keyUp = (e: KeyboardEvent) => {
      if (e.key === "Escape") return setAt(null);
      if (e.shiftKey || e.key === "Shift" || e.metaKey || e.ctrlKey) done(e);
    };
    // an empty selection, or one that left the code, hides it
    const changed = () => {
      if (!selectionIn(within.current)) setAt(null);
    };
    document.addEventListener("mouseup", done);
    document.addEventListener("keyup", keyUp);
    document.addEventListener("selectionchange", changed);
    return () => {
      document.removeEventListener("mouseup", done);
      document.removeEventListener("keyup", keyUp);
      document.removeEventListener("selectionchange", changed);
    };
  }, [update, within]);

  useEffect(() => {
    if (!shown) return;
    // the code scrolls under it: the bar follows the selection
    const move = () => update();
    window.addEventListener("scroll", move, true);
    window.addEventListener("resize", move);
    return () => {
      window.removeEventListener("scroll", move, true);
      window.removeEventListener("resize", move);
    };
  }, [shown, update]);

  if (!at || typeof document === "undefined") return null;
  return createPortal(
    <div ref={bar} className="selbar" role="toolbar" aria-label="Selected code" style={{ top: at.top, left: at.left }}
      // a press must not take the selection away before the action reads it
      onMouseDown={(e) => e.preventDefault()}>
      {actions.map((a) => (
        <button key={a.id} type="button" className="selbar-b" title={a.title}
          onClick={() => {
            a.run();
            setAt(null);
          }}>
          {a.label}
        </button>
      ))}
    </div>,
    document.body,
  );
}
