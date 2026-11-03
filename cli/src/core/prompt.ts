// 0.18.18 keel TUI's prompt of several lines: the keys that add a line instead of sending, the rows the prompt box
// shows (it grows up to PROMPT_ROWS, then scrolls with the cursor) and ↑ / ↓ between its rows. No UI here.

/** The prompt box grows up to this many rows of text; past it, it shows the rows around the cursor. */
export const PROMPT_ROWS = 8;

/** A key that adds a line to the prompt instead of sending it. Alt / Option+Enter arrives as ESC CR (Ink strips the
 *  ESC and reports no Enter); Ctrl+J, and Shift+Enter in terminals that map it so, as LF; Shift / Alt / Ctrl+Enter in
 *  kitty's keyboard protocol (CSI 13;<m>u) and xterm's modifyOtherKeys (CSI 27;<m>;13~), ESC stripped as well. A plain
 *  Enter is never one (most terminals send Shift+Enter as a plain CR: there it sends). */
export function isNewline(input: string, key: { return?: boolean; meta?: boolean; shift?: boolean }): boolean {
  if (key.return) return !!(key.meta || key.shift);
  return input === "\r" || input === "\n" || /^\[(13;[2-8]u|27;[2-8];13~)$/.test(input);
}

type Place = { row: number; col: number };

function placeOf(line: string, cursor: number): Place {
  const before = line.slice(0, cursor).split("\n");
  return { row: before.length - 1, col: before[before.length - 1].length };
}

/** The rows the prompt box shows: at most `max`, the cursor's among them (its column on its row, else null), and how
 *  many rows are hidden above and below. */
export function promptRows(line: string, cursor: number, max = PROMPT_ROWS): {
  rows: { text: string; cursor: number | null }[]; above: number; below: number } {
  const all = line.split("\n");
  const at = placeOf(line, cursor);
  const start = Math.max(0, Math.min(at.row - max + 1, all.length - max));
  const shown = all.slice(start, start + max);
  return {
    rows: shown.map((text, i) => ({ text, cursor: start + i === at.row ? at.col : null })),
    above: start,
    below: Math.max(0, all.length - start - shown.length),
  };
}

/** The cursor one row up (-1) or down (1), at the same column where the row is long enough; null on the first or
 *  last row (there ↑ / ↓ do nothing). */
export function moveRow(line: string, cursor: number, dir: -1 | 1): number | null {
  const all = line.split("\n");
  const at = placeOf(line, cursor);
  const row = at.row + dir;
  if (row < 0 || row >= all.length) return null;
  const startOf = all.slice(0, row).reduce((n, r) => n + r.length + 1, 0);
  return startOf + Math.min(at.col, all[row].length);
}

// 0.21.2 a word: letters, digits and _. Going a word left or right first passes the spaces and signs next to the
// cursor, then the word, as a shell's Alt+B / Alt+F do.
const isWord = (ch: string | undefined) => ch !== undefined && /[\p{L}\p{N}_]/u.test(ch);

/** Where the cursor goes one word to the left: the start of the word before it. */
export function wordLeft(line: string, cursor: number): number {
  let i = Math.max(0, Math.min(cursor, line.length));
  while (i > 0 && !isWord(line[i - 1])) i--;
  while (i > 0 && isWord(line[i - 1])) i--;
  return i;
}

/** Where the cursor goes one word to the right: the end of the word after it. */
export function wordRight(line: string, cursor: number): number {
  let i = Math.max(0, Math.min(cursor, line.length));
  while (i < line.length && !isWord(line[i])) i++;
  while (i < line.length && isWord(line[i])) i++;
  return i;
}
