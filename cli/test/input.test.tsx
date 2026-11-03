// 0.18.18 keel TUI phase 2, input comfort: the person's earlier prompts (↑ / ↓, from keel, so they survive a restart),
// a prompt of several lines (Alt+Enter, /editor), `!command` through keel's guard, and images with a turn.
import React from "react";
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { render } from "ink-testing-library";
import { App, type Probe } from "../src/ui/App";
import { KeelCore } from "../src/core/keel";
import { makeTheme } from "../src/core/theme";
import { stripAnsi } from "../src/script";
import { isNewline, moveRow, promptRows, PROMPT_ROWS, wordLeft, wordRight } from "../src/core/prompt";
import { findEditor } from "../src/core/editor";
import { imagesIn } from "../src/core/mentions";
import { stubKeel, tick, waitFor } from "./stub";

const UP = "\x1b[A";
const DOWN = "\x1b[B";
const ESC = "\x1b";

async function app(setup?: (stub: ReturnType<typeof stubKeel>) => void) {
  const stub = stubKeel();
  setup?.(stub);
  const core = new KeelCore({ url: "http://keel.test", fetch: stub.fetch, noLive: true, cwd: "/work/demo/src" });
  const probe: Probe = { started: false, busy: false, pick: false, ask: false, chatBusy: false, answers: 0 };
  const r = render(<App core={core} probe={probe} width={120} theme={makeTheme({ NO_COLOR: "1" }, 0)} />);
  const frame = () => stripAnsi(r.lastFrame() ?? "");
  /** the prompt's own line(s): between the box's top and bottom border */
  const prompt = () => {
    const ls = frame().split("\n");
    const top = ls.map((l) => l.includes("╭")).lastIndexOf(true);
    const bottom = ls.findIndex((l, i) => i > top && l.includes("╰"));
    return ls.slice(top + 1, bottom).map((l) => l.replace(/^│\s?/, "").replace(/\s*│\s*$/, "")).join("\n");
  };
  const key = async (k: string, ms = 40) => {
    r.stdin.write(k);
    await tick(ms);
  };
  await waitFor(() => probe.started);
  await tick(60);
  return { stub, core, probe, r, frame, prompt, key };
}

describe("prompt history", () => {
  it("↑ walks the project's earlier prompts from keel, newest first; ↓ comes back, down to an empty prompt", async () => {
    const a = await app((s) => (s.state.prompts = ["the newest question", "an older question"]));
    await waitFor(() => a.stub.calls.some((c) => c.path === "/api/projects/demo/helper/prompts"));
    await a.key(UP);
    expect(a.prompt()).toContain("the newest question");
    await a.key(UP);
    expect(a.prompt()).toContain("an older question");
    await a.key(UP);                                       // the oldest stays
    expect(a.prompt()).toContain("an older question");
    await a.key(DOWN);
    expect(a.prompt()).toContain("the newest question");
    await a.key(DOWN);
    expect(a.prompt()).not.toContain("question");
    a.r.unmount();
  });

  it("↑ in a prompt the person typed leaves it alone; Esc or typing leaves the history", async () => {
    const a = await app((s) => (s.state.prompts = ["the newest question"]));
    await waitFor(() => a.stub.calls.some((c) => c.path === "/api/projects/demo/helper/prompts"));
    await a.key("my own");
    await a.key(UP);
    expect(a.prompt()).toContain("my own");
    expect(a.prompt()).not.toContain("newest");
    await a.key("\x15");                                   // Ctrl+U: an empty prompt again
    await a.key(UP);
    expect(a.prompt()).toContain("the newest question");
    await a.key(ESC);
    expect(a.prompt()).not.toContain("newest");
    await a.key(UP);
    await a.key("!");                                      // typing edits the prompt (↑ put the cursor at its start)
    await a.key(UP);
    expect(a.prompt()).toContain("!the newest question");
    a.r.unmount();
  });

  it("in a prompt of several lines ↑ / ↓ move between its rows first, then walk the history (as opencode)", async () => {
    const a = await app((s) => (s.state.prompts = ["newest a\nnewest b", "older"]));
    await waitFor(() => a.stub.calls.some((c) => c.path === "/api/projects/demo/helper/prompts?limit=50" || c.path.endsWith("/helper/prompts")));
    await a.key(UP);
    expect(a.prompt()).toContain("newest b");             // the whole prompt, the cursor at its start
    await a.key(UP);
    expect(a.prompt()).toContain("older");
    await a.key(DOWN);                                     // back: the cursor at its end, on the last row
    expect(a.prompt()).toContain("newest b");
    await a.key(UP);                                       // ↑ moves to the first row; it stays this prompt
    expect(a.prompt()).toContain("newest b");
    await a.key(DOWN);
    await a.key(DOWN);                                     // the last row's end, then the empty prompt
    expect(a.prompt()).not.toContain("newest");
    await a.key("mine");
    await a.key("\x1b\r");
    await a.key("two");
    await a.key(UP);
    await a.key(UP);
    await a.key(UP);                                       // a prompt the person wrote: rows only, never the history
    expect(a.prompt()).toContain("mine");
    expect(a.prompt()).not.toContain("older");
    a.r.unmount();
  });

  it("what the person just sent comes first, before keel's older prompts", async () => {
    const a = await app((s) => (s.state.prompts = ["an older question"]));
    await waitFor(() => a.stub.calls.some((c) => c.path === "/api/projects/demo/helper/prompts"));
    await a.key("/help", 60);
    await a.key("\r");
    await waitFor(() => a.frame().includes("/copy"), 3000);
    await a.key(UP);
    expect(a.prompt()).toContain("/help");
    await a.key(UP);
    expect(a.prompt()).toContain("an older question");
    a.r.unmount();
  });
});

describe("word keys and fast typing (0.21.2)", () => {
  it("a word left goes to the start of the word before the cursor, a word right to the end of the next one", () => {
    const t = "fix the  late-order bug";
    expect(wordLeft(t, t.length)).toBe(20);              // before "bug"
    expect(wordLeft(t, 20)).toBe(14);                    // before "order", past the space
    expect(wordLeft(t, 14)).toBe(9);                     // before "late", past the "-"
    expect(wordLeft(t, 2)).toBe(0);
    expect(wordRight(t, 0)).toBe(3);
    expect(wordRight(t, 3)).toBe(7);                     // past the space, to the end of "the"
    expect(wordRight(t, 7)).toBe(13);                    // past both spaces, to the end of "late"
    expect(wordRight(t, t.length)).toBe(t.length);
    expect(wordLeft("ändra ön", 8)).toBe(6);             // letters of any language are a word
  });

  it("keys that arrive together build on each other: the second never overwrites the first", async () => {
    const a = await app();
    a.r.stdin.write("a");
    a.r.stdin.write("b");
    a.r.stdin.write("c");
    await tick(60);
    expect(a.prompt()).toContain("abc");
    // two edits before one render: a backspace, then a letter
    a.r.stdin.write("\x7f");
    a.r.stdin.write("d");
    await tick(60);
    expect(a.prompt()).toContain("abd");
    a.r.unmount();
  });

  it("Option / Alt + ← → jump a word, Option+Delete and Ctrl+W delete one, Alt+D the next one", async () => {
    const a = await app();
    await a.key("hello big world");
    await a.key(ESC + "b");                                // Option+← in macOS Terminal
    await a.key("X");
    expect(a.prompt()).toContain("hello big Xworld");
    await a.key("\x1b[1;3D");                             // Alt+← in xterm, iTerm2 and others
    await a.key("\x1b[1;3D");
    await a.key("Y");
    expect(a.prompt()).toContain("hello Ybig Xworld");
    await a.key(ESC + "f");                                // Option+→: to the end of "Ybig"
    await a.key("\x1b[1;5C");                             // Ctrl+→: to the end of "Xworld"
    await a.key("!");
    expect(a.prompt()).toContain("hello Ybig Xworld!");
    await a.key(ESC + "\x7f");                            // Option+Delete: "Xworld!" goes, the space stays
    expect(a.prompt()).toContain("hello Ybig");
    expect(a.prompt()).not.toContain("Xworld");
    await a.key("\x17");                                  // Ctrl+W
    expect(a.prompt()).toMatch(/hello\s*$/m);
    await a.key("\x1b[1;3D");
    await a.key(ESC + "d");                                // Alt+D: the word after the cursor
    expect(a.prompt()).not.toContain("hello");
    a.r.unmount();
  });
});

describe("a prompt of several lines", () => {
  it("knows the keys that add a line: Alt+Enter, Ctrl+J and Shift+Enter as kitty or xterm report it", () => {
    const none = { return: false, meta: false, shift: false };
    expect(isNewline("\r", none)).toBe(true);              // Alt / Option+Enter: Ink gets ESC CR, strips the ESC
    expect(isNewline("\n", none)).toBe(true);
    expect(isNewline("[13;2u", none)).toBe(true);
    expect(isNewline("[27;2;13~", none)).toBe(true);
    expect(isNewline("", { ...none, return: true })).toBe(false);   // a plain Enter sends
    expect(isNewline("a", none)).toBe(false);
  });

  it("shows at most PROMPT_ROWS rows around the cursor and says how many are hidden", () => {
    const text = Array.from({ length: 12 }, (_, i) => `line ${i + 1}`).join("\n");
    const v = promptRows(text, text.length);
    expect(v.rows.length).toBe(PROMPT_ROWS);
    expect(v.rows[v.rows.length - 1]).toEqual({ text: "line 12", cursor: 7 });
    expect(v.above).toBe(12 - PROMPT_ROWS);
    expect(v.below).toBe(0);
    const top = promptRows(text, 0);
    expect(top.rows[0]).toEqual({ text: "line 1", cursor: 0 });
    expect(top.below).toBe(12 - PROMPT_ROWS);
    expect(promptRows("one", 1).rows).toEqual([{ text: "one", cursor: 1 }]);
  });

  it("↑ / ↓ move between the rows, at the same column where it can", () => {
    const t = "first line\nab\nthird";
    expect(moveRow(t, t.length, -1)).toBe("first line\nab".length);           // col 5 → the end of "ab"
    expect(moveRow(t, 3, 1)).toBe("first line\nab".length);
    expect(moveRow(t, 2, -1)).toBeNull();
    expect(moveRow(t, t.length, 1)).toBeNull();
  });

  it("Alt+Enter and Shift+Enter add a line, the box grows, and Enter sends it all to KeelBot", async () => {
    const a = await app();
    await a.key("first line");
    await a.key("\x1b\r");
    await a.key("second line");
    await a.key("\x1b[13;2u");
    await a.key("third");
    expect(a.prompt().split("\n").length).toBe(3);
    expect(a.prompt()).toContain("second line");
    a.stub.answer("seen");
    await a.key("\r", 200);
    await waitFor(() => a.stub.calls.some((c) => c.path === "/api/projects/demo/helper/sessions/s1/turn"));
    const turn = a.stub.calls.find((c) => c.path === "/api/projects/demo/helper/sessions/s1/turn")!;
    expect((turn.body as { text: string }).text).toBe("first line\nsecond line\nthird");
    a.r.unmount();
  });

  it("the box stops growing at PROMPT_ROWS rows and says how many lines are above", async () => {
    const a = await app();
    for (let i = 1; i <= 12; i++) {
      await a.key(`row ${i}`, 10);
      if (i < 12) await a.key("\x1b\r", 10);
    }
    await tick(60);
    const shown = a.prompt().split("\n");
    expect(shown.length).toBe(PROMPT_ROWS + 1);
    expect(shown[0]).toContain(`${12 - PROMPT_ROWS} more lines above`);
    expect(a.prompt()).toContain("row 12");
    expect(a.prompt()).not.toContain("row 1\n");
    a.r.unmount();
  });
});

describe("/editor", () => {
  it("finds $VISUAL, else $EDITOR, on PATH; with neither it says what to set", () => {
    const on = (c: string) => c === "nvim" || c === "/opt/bin/code";
    expect(findEditor({ VISUAL: "nvim", EDITOR: "nano" }, on)).toEqual({ editor: { cmd: "nvim", args: [], name: "nvim" } });
    expect(findEditor({ EDITOR: "/opt/bin/code -w" }, on)).toEqual({ editor: { cmd: "/opt/bin/code", args: ["-w"], name: "code" } });
    expect("why" in findEditor({}, on) && findEditor({}, on)).toMatchObject({ why: expect.stringContaining("$VISUAL or $EDITOR") });
    expect(findEditor({ EDITOR: "nano" }, on)).toMatchObject({ why: expect.stringContaining("nano is not on PATH") });
  });

  it("opens the text in the editor and puts what it saved back in the prompt, not sent", async () => {
    const dir = mkdtempSync(join(tmpdir(), "keel-editor-"));
    const ed = join(dir, "fake-editor");
    writeFileSync(ed, `#!/bin/sh\ncp "$1" "${dir}/seen"\nprintf 'edited first\\nedited second\\n' > "$1"\n`);
    chmodSync(ed, 0o755);
    vi.stubEnv("VISUAL", ed);
    try {
      const a = await app();
      await a.key("/editor draft text", 60);
      await a.key("\r", 300);
      await waitFor(() => a.prompt().includes("edited second"), 3000);
      expect(readFileSync(join(dir, "seen"), "utf8")).toBe("draft text");
      expect(a.prompt()).toContain("edited first");
      expect(a.stub.calls.some((c) => c.path.endsWith("/turn"))).toBe(false);
      a.r.unmount();
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("says so when no editor is set", async () => {
    vi.stubEnv("VISUAL", "");
    vi.stubEnv("EDITOR", "");
    try {
      const a = await app();
      await a.key("/editor", 60);
      await a.key("\r", 200);
      await waitFor(() => a.frame().includes("$VISUAL or $EDITOR"), 3000);
      a.r.unmount();
    } finally {
      vi.unstubAllEnvs();
    }
  });
});

describe("!command", () => {
  it("runs through keel's guarded run, shows its output as a block, and sends it with the next question once", async () => {
    const a = await app();
    await a.key("!ls");
    expect(a.prompt()).toContain("shell");                 // the prompt says it is a shell command
    await a.key("\r", 200);
    await waitFor(() => a.stub.calls.some((c) => c.path === "/api/projects/demo/helper/sessions/s1/run"));
    const run = a.stub.calls.find((c) => c.path === "/api/projects/demo/helper/sessions/s1/run")!;
    expect(run.body).toEqual({ command: "ls" });
    await waitFor(() => a.frame().includes("README.md"), 3000);
    expect(a.frame()).toContain("$ ls");
    expect(a.frame()).toContain("your next question to KeelBot gets this output");
    expect(a.stub.calls.some((c) => c.path.endsWith("/turn"))).toBe(false);   // KeelBot was not asked
    a.stub.answer("I see two entries.");
    await a.key("what is here?", 60);
    await a.key("\r", 300);
    await waitFor(() => a.stub.calls.some((c) => c.path.endsWith("/s1/turn")));
    const turn = a.stub.calls.find((c) => c.path.endsWith("/s1/turn"))!;
    expect((turn.body as { shell?: unknown }).shell).toEqual({ command: "ls", exit: 0, output: "README.md\nsrc\n" });
    await waitFor(() => a.frame().includes("with the output of !ls"), 3000);
    expect(a.core.shell).toBeNull();                       // once: the next question goes without it
    a.r.unmount();
  });

  it("a refused command says why and keeps no output; ! alone drops a kept one", async () => {
    const a = await app((s) => (s.state.run = { exit: null, output: "", refused: "KeelBot's Ask mode is read only: /mode edit runs it" }));
    await a.key("!touch x", 60);
    await a.key("\r", 200);
    await waitFor(() => a.frame().includes("Ask mode is read only"), 3000);
    expect(a.core.shell).toBeNull();
    a.stub.state.run = { exit: 0, output: "ok\n", refused: null };
    await a.key("!true", 60);
    await a.key("\r", 200);
    await waitFor(() => a.core.shell !== null, 3000);
    await a.key("!", 60);
    await a.key("\r", 200);
    await waitFor(() => a.frame().includes("dropped"), 3000);
    expect(a.core.shell).toBeNull();
    a.r.unmount();
  });
});

describe("images with a question", () => {
  it("finds the image files a prompt names: a path ending .png .jpg .jpeg .gif .webp, or an @mention of one", () => {
    expect(imagesIn("what is wrong in docs/shot.png and @ui/B.JPG? not notes.txt or x.pngs")).toEqual(["docs/shot.png", "ui/B.JPG"]);
    expect(imagesIn("see '/tmp/a b/c.webp' and (an image at ./d.gif)")).toEqual(["/tmp/a b/c.webp", "./d.gif"]);
    expect(imagesIn("no picture here")).toEqual([]);
  });

  it("sends them with the turn and says which went and which did not, and why", async () => {
    const a = await app((s) => (s.state.turnImages = { images: ["/work/demo/docs/shot.png"],
      images_skipped: [{ path: "far.png", why: "it is outside the project folder" }] }));
    a.stub.answer("I see a red button.");
    await a.key("what is wrong in docs/shot.png and far.png?", 60);
    await a.key("\r", 300);
    await waitFor(() => a.stub.calls.some((c) => c.path.endsWith("/s1/turn")));
    const turn = a.stub.calls.find((c) => c.path.endsWith("/s1/turn"))!;
    expect((turn.body as { images?: string[] }).images).toEqual(["docs/shot.png", "far.png"]);
    await waitFor(() => a.frame().includes("with the image shot.png"), 3000);
    expect(a.frame()).toContain("not sent: far.png: it is outside the project folder");
    a.r.unmount();
  });
});
