// keel TUI's look, opencode's way: the person's message with a coloured bar down its left side (KeelBot's answer
// without), one quiet line per KeelBot tool call with its result folded (Ctrl+O or a click opens it), the time a turn
// took under its answer, and a sidebar on a wide terminal (context, flow, MCP, files changed; Ctrl+B, /sidebar).
import React from "react";
import { describe, expect, it } from "vitest";
import { render } from "ink-testing-library";
import { App, type Probe } from "../src/ui/App";
import { KeelCore } from "../src/core/keel";
import { blockLines } from "../src/core/lines";
import { makeTheme, PLAIN } from "../src/core/theme";
import { memorySettings } from "../src/core/settings";
import { answeredBy, took } from "../src/core/thinking";
import {
  argsText,
  lineCount,
  toolFromEvent,
  toolFromStep,
  toolLines,
  upsertTool,
} from "../src/core/tools";
import { stripAnsi } from "../src/script";
import { stubKeel, tick, waitFor } from "./stub";
import { renderAt } from "./wide";

const none = makeTheme({ NO_COLOR: "1" }, 0);
const dark = makeTheme({ KEEL_TUI_THEME: "dark" }, 3);

type Opts = { width?: number; rows?: number; sidebar?: boolean; mcp?: boolean };

function setup({ width = 100, rows = 40, sidebar, mcp }: Opts = {}) {
  const stub = stubKeel();
  // keel's MCP servers and the project's settings (the stub has neither: an older keel)
  const f: typeof fetch = async (input, init) => {
    const url = String(input);
    if (mcp && url.endsWith("/api/mcp-servers"))
      return Response.json([
        {
          name: "keel",
          label: "keel",
          command: "x",
          args: [],
          enabled: true,
          builtin: true,
          status: "ok",
          tools: ["keel_status"],
        },
        {
          name: "jira",
          label: "Jira",
          command: "x",
          args: [],
          enabled: true,
          builtin: false,
          status: "error",
          tools: [],
        },
        {
          name: "slack",
          label: "Slack",
          command: "x",
          args: [],
          enabled: true,
          builtin: false,
          status: "ok",
          tools: [],
        },
      ]);
    if (mcp && url.endsWith("/api/projects/demo/settings"))
      return Response.json({
        general: {},
        overrides: {},
        effective: { mcp: ["keel", "jira"] },
      });
    return stub.fetch(input, init);
  };
  const core = new KeelCore({
    url: "http://keel.test",
    fetch: f,
    noLive: true,
  });
  const probe: Probe = {
    started: false,
    busy: false,
    pick: false,
    ask: false,
    chatBusy: false,
    answers: 0,
  };
  const settings = memorySettings(sidebar === undefined ? {} : { sidebar });
  const r = renderAt(
    <App
      core={core}
      probe={probe}
      width={width}
      rows={rows}
      mouse
      theme={none}
      settings={settings}
    />,
    width,
  );
  const lines = () => stripAnsi(r.lastFrame() ?? "").split("\n");
  const frame = () => lines().join("\n");
  const key = async (s: string) => {
    r.stdin.write(s);
    await tick(30);
  };
  const ask = async (text: string) => {
    await key(text);
    await key("\r");
    await waitFor(() => probe.chatBusy);
  };
  const ev = (type: string, data: Record<string, unknown>) =>
    core.onMessage({
      event: type,
      data: JSON.stringify({
        type,
        thread_id: "s1",
        project_id: "demo",
        at: "x",
        call_id: "call-9",
        step: "helper",
        data,
      }),
    });
  const finish = async (text: string, data: Record<string, unknown> = {}) => {
    stub.answer(text, data);
    ev("helper.finished", { status: "done" });
    await waitFor(() => probe.answers > 0);
    await tick(40);
  };
  /** a left click on the first line that has `text` (SGR: 1-based column and row) */
  const click = async (text: string) => {
    const row = lines().findIndex((l) => l.includes(text));
    if (row < 0) throw new Error(`no line with ${text}:\n${frame()}`);
    const col = lines()[row].indexOf(text);
    r.stdin.write(`\x1b[<0;${col + 2};${row + 1}M`);
    await tick(20);
    r.stdin.write(`\x1b[<0;${col + 2};${row + 1}m`);
    await tick(60);
  };
  return {
    stub,
    core,
    probe,
    r,
    lines,
    frame,
    key,
    ask,
    ev,
    finish,
    click,
    settings,
  };
}

const STATUS_OUT = "project demo-project\nflow feature waiting\nstep spec_gate";

describe("the person's message has a bar, KeelBot's answer none", () => {
  it("draws the bar on each of the person's lines (and its air) and on no other block", () => {
    const u = blockLines(
      {
        kind: "user",
        text: "a question long enough to fold onto a second line here",
      },
      30,
      PLAIN,
    );
    expect(u[0]).toBe("");
    expect(u.slice(1).every((l) => l.startsWith("│"))).toBe(true);
    expect(u.length).toBeGreaterThan(4); // folded: two rows of words, a line of air above and below
    for (const b of [
      { kind: "markdown", text: "An answer\n\n- a point" },
      { kind: "dim", text: "■ Ask · fake" },
      { kind: "ok", text: "done" },
    ] as const)
      expect(blockLines(b, 40, PLAIN).some((l) => l.startsWith("│"))).toBe(
        false,
      );
    // the bar in the person's colour, the words bold
    const c = blockLines({ kind: "user", text: "hi" }, 40, dark.paint);
    expect(c[1]).toBe("\x1b[94m│\x1b[39m");
    expect(c[2]).toBe("\x1b[94m│\x1b[39m  \x1b[1mhi\x1b[22m");
  });

  it("in the app: the question has the bar, the answer below it has none", async () => {
    const t = setup();
    await waitFor(() => t.probe.started);
    await t.ask("where does the app start?");
    await t.finish("It starts in **src/main.ts:3**.");
    const ls = t.lines();
    const q = ls.findIndex((l) => l.includes("where does the app start?"));
    expect(ls[q]).toMatch(/^│  where does the app start\?/);
    expect(ls[q - 1]).toMatch(/^│\s*$/);
    expect(ls[q + 1]).toMatch(/^│\s*$/);
    const a = ls.findIndex((l) => l.includes("It starts in src/main.ts:3."));
    expect(a).toBeGreaterThan(q);
    expect(ls[a]).toMatch(/^ {2}It starts/);
    expect(ls.slice(q + 2, a + 1).some((l) => l.startsWith("│"))).toBe(false);
    t.r.unmount();
  });
});

describe("one tidy line per tool call", () => {
  it("reads a call from the engine's helper.tool, else from its step, and shows it in one line", () => {
    const fromStep = toolFromStep({
      n: 2,
      at: "x",
      kind: "tool",
      tool: "keel_status",
      server: "keel",
      text: '{"project":"demo-project"}',
      output: STATUS_OUT,
      ok: true,
    });
    expect(fromStep).toMatchObject({
      name: "keel_status",
      args: "project=demo-project",
      status: "ok",
      lines: 3,
      summary: "3 lines",
      shell: false,
    });
    expect(toolLines(fromStep!, 80)).toEqual([
      "  ⚙ keel_status [project=demo-project]",
      "    └ 3 lines",
    ]);
    const sh = toolFromEvent({
      n: 3,
      name: "Bash",
      args: "git status --short",
      shell: true,
      status: "ok",
      summary: "M src/a.ts",
      lines: 1,
      output: "M src/a.ts",
    })!;
    expect(toolLines(sh, 80)).toEqual([
      "  $ git status --short",
      "    └ M src/a.ts",
    ]);
    // long arguments: one line, cut with "…"
    const long = toolFromEvent({
      n: 4,
      name: "keel_search",
      args: { query: "x".repeat(200), limit: 5 },
      status: "ok",
      output: "",
    })!;
    const head = toolLines(long, 60)[0];
    expect(head.length).toBeLessThanOrEqual(60);
    expect(head).toMatch(/^ {2}⚙ keel_search \[query=x+…\]$/);
    // an error: the fold says what failed, in the error colour
    const bad = toolFromEvent({
      n: 5,
      name: "keel_status",
      args: {},
      status: "error",
      summary: "no project app",
      output: "no project app\nat x",
    })!;
    const red = toolLines(bad, 80, dark.paint);
    expect(red[1]).toContain("\x1b[31m    └ no project app");
    expect(red[0]).toContain("\x1b[31m⚙ ");
    // the runner's cut output still says how many lines it had
    expect(lineCount("a\nb\n… (98 more lines)")).toBe(100);
    expect(argsText('{"a":1,"b":"two words"}')).toBe("a=1 b=two words");
    // the richer record of the same call takes the step's place
    expect(
      upsertTool([fromStep!], { ...fromStep!, summary: "better" }),
    ).toEqual([{ ...fromStep!, summary: "better" }]);
  });

  it("while KeelBot works: each call one line, folded; Ctrl+O opens the last one's output and folds it again", async () => {
    const t = setup();
    await waitFor(() => t.probe.started);
    await t.ask("what is the status?");
    t.ev("helper.step", {
      n: 1,
      kind: "tool",
      tool: "keel_status",
      server: "keel",
      text: '{"project":"demo-project"}',
      output: STATUS_OUT,
      ok: true,
    });
    t.ev("helper.tool", {
      n: 1,
      name: "keel_status",
      server: "keel",
      args: { project: "demo-project" },
      shell: false,
      status: "ok",
      summary: "3 lines",
      lines: 3,
      output: STATUS_OUT,
    });
    t.ev("helper.step", {
      n: 2,
      kind: "tool",
      tool: "Bash",
      text: "git status --short",
      output: "M src/a.ts",
      ok: true,
    });
    await waitFor(() => t.frame().includes("$ git status --short"));
    expect(t.frame()).toContain(
      "⚙ keel_status [project=demo-project]\n    └ 3 lines (Ctrl+O to show)",
    );
    expect(t.frame()).toContain("$ git status --short\n    └ M src/a.ts");
    expect(t.frame()).not.toContain("flow feature waiting");
    await t.key("\x0f"); // Ctrl+O
    await waitFor(() => t.frame().includes("flow feature waiting"));
    expect(t.frame()).toContain(
      "    └ 3 lines (open)\n      project demo-project\n      flow feature waiting",
    );
    await t.key("\x0f");
    await waitFor(() => !t.frame().includes("flow feature waiting"));
    t.r.unmount();
  });

  it("after the answer: the calls stay above it, folded; Ctrl+O or a click opens one in the transcript", async () => {
    const t = setup();
    await waitFor(() => t.probe.started);
    await t.ask("what is the status?");
    t.ev("helper.tool", {
      n: 1,
      name: "keel_status",
      server: "keel",
      args: { project: "demo-project" },
      status: "ok",
      summary: "3 lines",
      lines: 3,
      output: STATUS_OUT,
    });
    t.ev("helper.tool", {
      n: 2,
      name: "keel_next",
      server: "keel",
      args: {},
      status: "error",
      summary: "keel is not reachable",
      lines: 2,
      output: "keel is not reachable\nretry later",
    });
    await tick(30);
    await t.finish("The flow waits at the spec gate.");
    let f = t.frame();
    expect(f).toMatch(
      /⚙ keel_status \[project=demo-project\]\n {4}└ 3 lines\n/,
    );
    expect(f).toContain(
      "⚙ keel_next\n    └ keel is not reachable (Ctrl+O to show)",
    );
    expect(f.indexOf("⚙ keel_status")).toBeLessThan(
      f.indexOf("The flow waits at the spec gate."),
    );
    expect(f).not.toContain("retry later");
    // Ctrl+O: the last call that has more to show opens in its place, and folds again
    await t.key("\x0f");
    await waitFor(() => t.frame().includes("retry later"));
    expect(t.frame()).toContain(
      "    └ keel is not reachable (open)\n      keel is not reachable\n      retry later",
    );
    await t.key("\x0f");
    await waitFor(() => !t.frame().includes("retry later"));
    // a click on a call's line opens that one
    await t.click("⚙ keel_status");
    await waitFor(() => t.frame().includes("flow feature waiting"));
    await t.click("⚙ keel_status");
    await waitFor(() => !t.frame().includes("flow feature waiting"));
    f = t.frame();
    expect(f).not.toContain("[<0;");
    t.r.unmount();
  });

  it("the inline screen prints the output once below, as it cannot change what it printed", async () => {
    const stub = stubKeel();
    const core = new KeelCore({
      url: "http://keel.test",
      fetch: stub.fetch,
      noLive: true,
    });
    const probe: Probe = {
      started: false,
      busy: false,
      pick: false,
      ask: false,
      chatBusy: false,
      answers: 0,
    };
    const r = render(
      <App core={core} probe={probe} width={100} theme={none} />,
    );
    const frame = () => stripAnsi(r.lastFrame() ?? "");
    await waitFor(() => probe.started);
    r.stdin.write("what is the status?");
    await tick(30);
    r.stdin.write("\r");
    await waitFor(() => probe.chatBusy);
    core.onMessage({
      event: "helper.tool",
      data: JSON.stringify({
        type: "helper.tool",
        thread_id: "s1",
        project_id: "demo",
        at: "x",
        call_id: "call-9",
        data: {
          n: 1,
          name: "keel_status",
          args: { project: "demo-project" },
          status: "ok",
          summary: "3 lines",
          lines: 3,
          output: STATUS_OUT,
        },
      }),
    });
    await tick(30);
    stub.answer("It waits.");
    core.onMessage({
      event: "helper.finished",
      data: JSON.stringify({
        type: "helper.finished",
        thread_id: "s1",
        project_id: "demo",
        at: "x",
        data: {},
      }),
    });
    await waitFor(() => probe.answers === 1);
    await tick(30);
    expect(frame()).toContain(
      "  ⚙ keel_status [project=demo-project]\n    └ 3 lines (Ctrl+O to show)",
    );
    r.stdin.write("\x0f");
    await waitFor(() => frame().includes("flow feature waiting"));
    expect(frame()).toContain(
      "    └ 3 lines (open)\n      project demo-project",
    );
    r.unmount();
  });
});

describe("the footer says how long the turn took", () => {
  it("as seconds, or minutes and seconds", () => {
    expect(took(7600)).toBe("7.6s");
    expect(took(125_000)).toBe("2m 05s");
    expect(answeredBy("ask", "Claude Sonnet 5.5", 7600)).toBe(
      "■ Ask · Claude Sonnet 5.5 · 7.6s",
    );
    expect(answeredBy("ask", "Claude Sonnet 5.5")).toBe(
      "■ Ask · Claude Sonnet 5.5",
    );
  });

  it("under the answer, quiet in the meta role", async () => {
    const t = setup();
    await waitFor(() => t.probe.started);
    await t.ask("hi");
    await t.finish("Hello.", { ms: 7600 });
    expect(t.frame()).toMatch(/■ Ask · \S.* · 7\.6s/);
    t.r.unmount();
    expect(
      blockLines(
        { kind: "dim", text: answeredBy("ask", "fake", 7600) },
        60,
        dark.paint,
      )[0],
    ).toContain(`\x1b[38;2;168;168;168m■ Ask · fake · 7.6s`);
  });
});

describe("the sidebar", () => {
  it("shows on a wide terminal (140 columns): context, flow, MCP and files changed; the status line leaves the meter to it", async () => {
    const t = setup({ width: 140, mcp: true });
    await waitFor(() => t.probe.started);
    await waitFor(() => t.frame().includes("● keel"));
    let f = t.frame();
    for (const h of ["Context", "Flow", "MCP", "Files changed"])
      expect(f).toContain(h);
    expect(f).toContain("no chat yet");
    expect(f).toContain('feature "Add a health endpoint"');
    expect(f).toContain("◆ waits: spec approval");
    expect(f).toMatch(/● keel +connected/);
    expect(f).toMatch(/✗ Jira +failed/);
    expect(f).not.toContain("Slack"); // not one of this project's servers
    // a turn: the chat's context and cost, and the files the answer changed
    await t.ask("make the limit 100");
    t.ev("helper.step", {
      n: 1,
      kind: "edit",
      path: "src/app.ts",
      text: "Edit src/app.ts",
      diff: "--- a/src/app.ts\n+++ b/src/app.ts\n@@ -3 +3 @@\n-const limit = 10;\n+const limit = 100;\n",
      ok: true,
    });
    await waitFor(() => /M src\/app\.ts +\+1 -1/.test(t.frame()));
    t.stub.state.session = {
      ...t.stub.state.session,
      ctx_tokens: 82_000,
      ctx_window: 200_000,
      cost_usd: 0.42,
    };
    await t.finish("Done.");
    f = t.frame();
    expect(f).toContain("82.0k / 200.0k tokens");
    expect(f).toContain("41% used");
    expect(f).toContain("$0.42 spent");
    expect(f).not.toContain("ctx 41%"); // the status line leaves the meter to the sidebar
    // its lines stay beside the transcript, its border one column down the screen: none is wider than the screen
    expect(t.lines().every((l) => l.length <= 140)).toBe(true);
    const at = 140 - 36;
    expect(t.lines().slice(2, -1).every((l) => l[at] === "│")).toBe(true);
    expect(t.lines()[2].slice(at)).toBe("│ Context");
    expect(t.r.lastFrame()).not.toMatch(/\x1b\[/);              // NO_COLOR: plain text
    t.r.unmount();
  });

  it("is hidden at 90 columns, where the status line keeps the meter", async () => {
    const t = setup({ width: 90 });
    await waitFor(() => t.probe.started);
    await tick(60);
    expect(t.frame()).not.toContain("Files changed");
    await t.ask("hi");
    t.stub.state.session = {
      ...t.stub.state.session,
      ctx_tokens: 82_000,
      ctx_window: 200_000,
    };
    await t.finish("Hello.");
    expect(t.frame()).not.toContain("Files changed");
    // the status line keeps the meter (its end: the line is cut at 90 columns)
    expect(t.lines().at(-1)).toMatch(/^ \[\?\] ask · demo/);
    const narrow = setup({ width: 118 });
    await waitFor(() => narrow.probe.started);
    await narrow.ask("hi");
    narrow.stub.state.session = { ...narrow.stub.state.session, ctx_tokens: 82_000, ctx_window: 200_000 };
    await narrow.finish("Hello.");
    expect(narrow.lines().at(-1)).toContain("ctx 41%");
    expect(narrow.frame()).not.toContain("Files changed");
    narrow.r.unmount();
    // Ctrl+B says why it does not show here
    await t.key("\x02");
    await waitFor(() => t.frame().includes("sidebar off"));
    await t.key("\x02");
    await waitFor(() =>
      t
        .frame()
        .includes(
          "sidebar on (it shows on a terminal 120 columns wide or more; this one has 90)",
        ),
    );
    t.r.unmount();
  });

  it("Ctrl+B and /sidebar hide and show it, and keel TUI's settings keep the choice", async () => {
    const t = setup({ width: 140 });
    await waitFor(() => t.probe.started);
    await waitFor(() => t.frame().includes("Files changed"));
    // an older keel says nothing of its MCP servers: keel's own tools for KeelBot
    expect(t.frame()).toContain("keel's tools for KeelBot:");
    await t.key("\x02"); // Ctrl+B
    await waitFor(() => !t.frame().includes("Files changed"));
    expect(t.settings.get().sidebar).toBe(false);
    await t.key("/sidebar on");
    await t.key("\r");
    await waitFor(() => t.frame().includes("Files changed"));
    expect(t.settings.get().sidebar).toBe(true);
    expect(t.frame()).toContain("✓ sidebar on");
    t.r.unmount();
    // the next start reads the choice
    const off = setup({ width: 140, sidebar: false });
    await waitFor(() => off.probe.started);
    await tick(60);
    expect(off.frame()).not.toContain("Files changed");
    off.r.unmount();
  });

  it("a click on its flow opens the flow's status", async () => {
    const t = setup({ width: 140 });
    await waitFor(() => t.probe.started);
    await waitFor(() => t.frame().includes('feature "Add a health endpoint"'));
    await t.click("· step spec_gate");                          // the sidebar's flow (the start box has the title too)
    await waitFor(() => t.frame().includes("Criteria: 0/2 done"));
    t.r.unmount();
  });
});
