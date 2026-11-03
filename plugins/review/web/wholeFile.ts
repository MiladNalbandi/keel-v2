// 0.21.1 the whole file with its changes marked: which lines of the new file were added or changed, where lines were
// deleted, and the old code of each change. Read from the file's unified diff, so the old file is never fetched
// (a renamed file works too): outside the diff's hunks a line is the same on both sides.
//
//   new file           mark      the peek (a click on the mark)
//    6  …                        ┌ Change 2 of 3: changed · lines 7–8, was 1 line ┐
//    7  …              ┃ blue    │ 10 − the old line                              │
//    8  …              ┃ blue    └────────────────────────────────────────────────┘
//    9  …              ▶ red     lines deleted between 8 and 9
//   12  …              ┃ green   added

import type { DiffRow } from "@keel/web-sdk";

export type ChangeKind = "add" | "mod" | "del";

/** One change: a run of added and removed lines in the diff, with nothing unchanged between them. */
export type Change = {
  /** 1, 2, … in the file's order */
  n: number;
  /** add: only new lines; mod: lines replaced; del: only removed lines */
  kind: ChangeKind;
  /** the new file's lines of this change (none for del) */
  first: number | null;
  last: number | null;
  /** the new file's line just before the change (0: the file's start) */
  after: number;
  /** the old file's line just before the change */
  oldAfter: number;
  /** the old file's lines the change removed or replaced */
  old: { line: number; text: string }[];
  /** how many new lines it added */
  added: number;
};

export type FileChanges = {
  changes: Change[];
  /** a new line → the add or mod change it belongs to */
  marks: Map<number, Change>;
  /** a new line → the del change whose lines were removed just before it (the file's line count + 1: at its end) */
  cuts: Map<number, Change>;
};

const HUNK = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;

/** The changes of one file, from its parsed diff rows (`parseDiff(diff).rows`). */
export function fileChanges(rows: DiffRow[]): FileChanges {
  const changes: Change[] = [];
  let lastNew = 0;
  let lastOld = 0;
  let cur: { adds: number[]; old: Change["old"]; after: number; oldAfter: number } | null = null;
  const close = () => {
    if (!cur) return;
    const kind: ChangeKind = cur.adds.length && cur.old.length ? "mod" : cur.adds.length ? "add" : "del";
    changes.push({
      n: changes.length + 1,
      kind,
      first: cur.adds[0] ?? null,
      last: cur.adds[cur.adds.length - 1] ?? null,
      after: cur.after,
      oldAfter: cur.oldAfter,
      old: cur.old,
      added: cur.adds.length,
    });
    cur = null;
  };
  for (const r of rows) {
    if (r.kind === "meta") continue; // "\ No newline at end of file" sits inside a change, it does not end one
    if (r.kind === "hunk") {
      close();
      const h = r.text.match(HUNK);
      if (h) {
        // a side with no lines ("+4,0") names the line before it; otherwise its first line
        lastOld = h[2] === "0" ? Number(h[1]) : Number(h[1]) - 1;
        lastNew = h[4] === "0" ? Number(h[3]) : Number(h[3]) - 1;
      }
      continue;
    }
    if (r.kind === "ctx") {
      close();
      lastOld = r.old ?? lastOld + 1;
      lastNew = r.new ?? lastNew + 1;
      continue;
    }
    if (!cur) cur = { adds: [], old: [], after: lastNew, oldAfter: lastOld };
    if (r.kind === "add") {
      lastNew = r.new ?? lastNew + 1;
      cur.adds.push(lastNew);
    } else {
      lastOld = r.old ?? lastOld + 1;
      cur.old.push({ line: lastOld, text: r.text });
    }
  }
  close();
  const marks = new Map<number, Change>();
  const cuts = new Map<number, Change>();
  for (const c of changes) {
    if (c.kind === "del") cuts.set(c.after + 1, c);
    else for (let l = c.first!; l <= c.last!; l++) marks.set(l, c);
  }
  return { changes, marks, cuts };
}

/** Where a change's peek goes: under this new line (0: above the first line). */
export const peekAfter = (c: Change) => c.last ?? c.after;

/** One line of the change's own words: "Change 2 of 5: changed", and where. */
export function changeTitle(c: Change, total: number): { title: string; where: string } {
  const word = c.kind === "mod" ? "changed" : c.kind === "add" ? "added" : "deleted";
  const lines = (a: number, b: number) => (a === b ? `line ${a}` : `lines ${a}–${b}`);
  const was = (k: number) => `${k} line${k === 1 ? "" : "s"}`;
  const where =
    c.kind === "del"
      ? `${was(c.old.length)} ${c.after === 0 ? "at the start" : `after line ${c.after}`}`
      : c.kind === "mod"
        ? `${lines(c.first!, c.last!)}, was ${was(c.old.length)}`
        : lines(c.first!, c.last!);
  return { title: `Change ${c.n} of ${total}: ${word}`, where };
}

/** A row of the whole file side by side: the old file's line on the left, the new file's on the right. */
export type SideRow = {
  left: { line: number; text: string; kind: "ctx" | "del" } | null;
  right: { line: number; text: string; kind: "ctx" | "add" | "mod" } | null;
  /** the change the row belongs to (none: unchanged) */
  change: Change | null;
};

/**
 * The whole file in two columns, old | new: the unchanged lines side by side, a change's removed lines next to the
 * lines that replaced them, an empty cell where one side has no line.
 */
export function sideBySide(newLines: string[], fc: FileChanges): SideRow[] {
  const out: SideRow[] = [];
  let o = 1;
  let n = 1;
  const same = (upTo: number) => {
    for (; n <= Math.min(upTo, newLines.length); n++, o++)
      out.push({ left: { line: o, text: newLines[n - 1], kind: "ctx" }, right: { line: n, text: newLines[n - 1], kind: "ctx" }, change: null });
  };
  for (const c of fc.changes) {
    same(c.after);
    // a diff that does not fit the file (a long diff cut short): keep the lines in step with the change
    n = c.after + 1;
    o = c.oldAfter + 1;
    for (let i = 0; i < Math.max(c.old.length, c.added); i++) {
      const was = c.old[i];
      const line = c.first != null && i < c.added ? c.first + i : null;
      out.push({
        left: was ? { line: was.line, text: was.text, kind: "del" } : null,
        right: line != null && line <= newLines.length ? { line, text: newLines[line - 1], kind: c.kind === "mod" ? "mod" : "add" } : null,
        change: c,
      });
    }
    n += c.added;
    o += c.old.length;
  }
  same(newLines.length);
  return out;
}
