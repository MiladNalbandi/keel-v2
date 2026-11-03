// 0.21.1 the whole file's changes from its diff: added, changed and deleted lines, where a deletion sits in the new
// file, the old code of each change, and the two columns of Old | New.

import { describe, expect, it } from "vitest";
import { parseDiff } from "@keel/web-sdk";
import { changeTitle, fileChanges, peekAfter, sideBySide } from "../wholeFile";

const rowsOf = (...lines: string[]) =>
  parseDiff(["diff --git a/f.js b/f.js", "--- a/f.js", "+++ b/f.js", ...lines].join("\n")).rows;

describe("fileChanges", () => {
  it("finds added, changed and deleted lines, with the old code of each", () => {
    const fc = fileChanges(
      rowsOf(
        "@@ -1,8 +1,9 @@",
        " a",
        "+b",
        " c",
        "-d",
        "+D",
        "+D2",
        " e",
        "-f",
        " g",
        " h",
        " i",
      ),
    );
    expect(fc.changes.map((c) => [c.n, c.kind, c.first, c.last, c.after])).toEqual([
      [1, "add", 2, 2, 1],
      [2, "mod", 4, 5, 3],
      [3, "del", null, null, 6],
    ]);
    expect(fc.changes[1].old).toEqual([{ line: 3, text: "d" }]);
    expect(fc.changes[2].old).toEqual([{ line: 5, text: "f" }]);
    expect([...fc.marks.keys()]).toEqual([2, 4, 5]);
    // the deleted line sat between new lines 6 and 7: its mark is on line 7
    expect([...fc.cuts.keys()]).toEqual([7]);
    expect(peekAfter(fc.changes[1])).toBe(5);
    expect(peekAfter(fc.changes[2])).toBe(6);
  });

  it("reads the line numbers of several hunks, a deletion at the start and one at the end", () => {
    const fc = fileChanges(
      rowsOf(
        "@@ -1,2 +0,0 @@",
        "-old one",
        "-old two",
        "@@ -40,2 +38,3 @@",
        " x",
        "+y",
        " z",
        "@@ -60,1 +59,0 @@",
        "-last",
      ),
    );
    expect(fc.changes.map((c) => [c.kind, c.after, c.oldAfter])).toEqual([
      ["del", 0, 0],
      ["add", 38, 40],
      ["del", 59, 59],
    ]);
    expect(fc.changes[0].old.map((o) => o.line)).toEqual([1, 2]);
    expect(fc.changes[1].first).toBe(39);
    expect(fc.changes[2].old).toEqual([{ line: 60, text: "last" }]);
    expect([...fc.cuts.keys()]).toEqual([1, 60]);
  });

  it("keeps one change across a 'No newline at end of file' line", () => {
    const fc = fileChanges(rowsOf("@@ -1,1 +1,1 @@", "-a", "\\ No newline at end of file", "+a"));
    expect(fc.changes).toHaveLength(1);
    expect(fc.changes[0].kind).toBe("mod");
  });

  it("says what a change is, in a few words", () => {
    const fc = fileChanges(rowsOf("@@ -1,3 +1,4 @@", " a", "-b", "+B", "+B2", " c", "+d"));
    expect(changeTitle(fc.changes[0], 2)).toEqual({ title: "Change 1 of 2: changed", where: "lines 2–3, was 1 line" });
    expect(changeTitle(fc.changes[1], 2)).toEqual({ title: "Change 2 of 2: added", where: "line 5" });
    const del = fileChanges(rowsOf("@@ -1,3 +1,1 @@", "-a", "-b", " c"));
    expect(changeTitle(del.changes[0], 1)).toEqual({ title: "Change 1 of 1: deleted", where: "2 lines at the start" });
  });
});

describe("sideBySide", () => {
  it("puts the old file next to the new one: removed lines next to their new lines, an empty cell where a side has none", () => {
    const newLines = ["a", "b", "c", "D", "D2", "e", "g"];
    const fc = fileChanges(rowsOf("@@ -1,6 +1,7 @@", " a", "+b", " c", "-d", "+D", "+D2", " e", "-f", " g"));
    const rows = sideBySide(newLines, fc).map((r) => [
      r.left ? `${r.left.line}${r.left.kind === "del" ? "-" : " "}${r.left.text}` : "",
      r.right ? `${r.right.line}${r.right.kind === "ctx" ? " " : r.right.kind === "add" ? "+" : "~"}${r.right.text}` : "",
    ]);
    expect(rows).toEqual([
      ["1 a", "1 a"],
      ["", "2+b"],
      ["2 c", "3 c"],
      ["3-d", "4~D"],
      ["", "5~D2"],
      ["4 e", "6 e"],
      ["5-f", ""],
      ["6 g", "7 g"],
    ]);
  });

  it("numbers the unchanged lines outside the hunks on both sides", () => {
    const newLines = Array.from({ length: 12 }, (_, i) => `n${i + 1}`);
    // line 5 was removed; old lines after it are one higher than the new ones
    const fc = fileChanges(rowsOf("@@ -4,3 +4,2 @@", " n4", "-gone", " n5"));
    const rows = sideBySide(newLines, fc);
    expect(rows).toHaveLength(13);
    expect(rows[12].left?.line).toBe(13);
    expect(rows[12].right?.line).toBe(12);
    expect(rows[4].left).toEqual({ line: 5, text: "gone", kind: "del" });
    expect(rows[4].right).toBeNull();
  });
});
