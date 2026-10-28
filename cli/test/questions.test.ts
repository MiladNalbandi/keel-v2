// keel 0.18.13 one way to ask, in keel TUI: a gate's named choices with the engine's words (choice_info), a reason only
// where the picked choice needs one, `/answer id=a,b` for a question that takes several, and KeelBot's answer button.
import { describe, expect, it } from "vitest";
import { run, type Reply } from "../src/core/commands";
import { KeelCore } from "../src/core/keel";
import * as fmt from "../src/core/format";
import {
  parseQuestions,
  prepare,
  pressButton,
  question,
  toButton,
} from "../src/core/buttons";
import { stubKeel } from "./stub";

const STUCK = {
  step: "verify_green",
  kind: "fix",
  title: "AC-002 is stuck at verify_green",
  detail: "failed 3 times",
  options: ["approve", "reject"],
  id: "q-stuck",
  choices: ["retry", "skip", "stop"],
  choice_info: [
    {
      name: "retry",
      label: "Try again",
      description: "one more round, with your note",
      why: "optional",
      default: true,
    },
    {
      name: "skip",
      label: "Skip it for now",
      description: "move AC-002 to the end of the list",
      why: "required",
      default: false,
    },
    {
      name: "stop",
      label: "Stop the flow",
      description: "the commits so far stay",
      why: "none",
      default: false,
    },
  ],
};

async function waitingOn(waiting: object) {
  const stub = stubKeel();
  stub.state.thread = { ...stub.state.thread, waiting } as never;
  const core = new KeelCore({
    url: "http://keel.test",
    fetch: stub.fetch,
    noLive: true,
  });
  await core.start();
  return {
    stub,
    ctx: { core, width: 100 },
    acts: () =>
      stub.calls.filter(
        (c) => c.method === "POST" && c.path === "/api/inbox/t1/act",
      ),
  };
}

describe("a gate's named choices (choice_info)", () => {
  it("the waiting lines, /status and /next name each choice: its label, what it does, the default, the reason it needs", () => {
    const w = STUCK as never;
    expect(fmt.choiceLines(w)).toEqual([
      "1. Try again (retry) · default · reason optional — one more round, with your note",
      "2. Skip it for now (skip) · needs a reason — move AC-002 to the end of the list",
      "3. Stop the flow (stop) — the commits so far stay",
    ]);
    expect(fmt.waitingLines(w).join("\n")).toContain(
      "2. Skip it for now (skip) · needs a reason",
    );
    const s = stubKeel();
    const view = {
      thread: { ...s.state.thread, waiting: STUCK } as never,
      workflow: s.workflow as never,
    };
    const status = fmt.status({ id: "demo", branch: "x" } as never, view);
    expect(status).toMatch(
      /\/answer skip <why> +Skip it for now — move AC-002/,
    );
    expect(status).toMatch(/\/answer retry \[why\] +Try again \(default\)/);
    expect(status).not.toContain("/approve ");
    // a stuck criterion (kind fix) is answered with its choices, not "approve to try again"
    expect(fmt.next({ id: "demo", branch: "x" } as never, view)).toContain(
      "/answer <retry | skip | stop>",
    );
    // an older engine without choice_info: the raw names, as before
    expect(
      fmt.choiceLines({ ...STUCK, choice_info: undefined } as never),
    ).toEqual([]);
  });

  it("/answer <choice> refuses a choice that needs a reason without one, and sends it with one", async () => {
    const { ctx, acts } = await waitingOn(STUCK);
    expect((await run(ctx, "/answer skip")).blocks[0]).toEqual({
      kind: "error",
      text: "Skip it for now needs a reason: /answer skip <why>.",
    });
    expect(acts()).toHaveLength(0);
    expect((await run(ctx, "/answer nope")).blocks[0].text).toBe(
      "Choose one of: retry, skip, stop",
    );
    expect(
      (await run(ctx, "/answer skip it needs the queue first")).blocks[0].kind,
    ).toBe("ok");
    expect(acts()[0].body).toEqual({
      decision: "approve",
      why: "it needs the queue first",
      payload: { choice: "skip" },
      id: "q-stuck",
    });
  });

  it("/answer alone: a pick with the labels and descriptions, on the default; a reason asked only where it is needed", async () => {
    const { ctx, acts } = await waitingOn(STUCK);
    const r = await run(ctx, "/answer");
    expect(r.pick?.choices).toEqual([
      {
        label: "Try again (default)",
        value: "retry",
        hint: "one more round, with your note",
      },
      {
        label: "Skip it for now · needs a reason",
        value: "skip",
        hint: "move AC-002 to the end of the list",
      },
      {
        label: "Stop the flow",
        value: "stop",
        hint: "the commits so far stay",
      },
    ]);
    expect(r.pick?.index).toBe(0);
    // needs a reason: asked, and asked again while empty
    const ask = (await r.pick!.then("skip")) as Reply;
    expect(ask.ask?.prompt).toBe("Why Skip it for now? (needed)");
    const again = (await ask.ask!.then("  ")) as Reply;
    expect(again.blocks[0]).toEqual({
      kind: "error",
      text: "Skip it for now needs a reason.",
    });
    expect(acts()).toHaveLength(0);
    expect(
      ((await again.ask!.then("blocked by AC-003")) as Reply).blocks[0].kind,
    ).toBe("ok");
    expect(acts()[0].body).toMatchObject({
      decision: "approve",
      why: "blocked by AC-003",
      payload: { choice: "skip" },
    });
  });

  it("a choice that needs no reason is sent at once; an optional reason may be left empty", async () => {
    const a = await waitingOn(STUCK);
    const r = await run(a.ctx, "/answer");
    expect(((await r.pick!.then("stop")) as Reply).blocks[0].kind).toBe("ok");
    expect(a.acts()[0].body).toEqual({
      decision: "approve",
      payload: { choice: "stop" },
      id: "q-stuck",
    });
    const b = await waitingOn(STUCK);
    const r2 = await run(b.ctx, "/answer");
    const ask = (await r2.pick!.then("retry")) as Reply;
    expect(ask.ask?.prompt).toBe(
      "Why Try again? (optional: Enter sends without)",
    );
    expect(((await ask.ask!.then("")) as Reply).blocks[0].kind).toBe("ok");
    expect(b.acts()[0].body).toEqual({
      decision: "approve",
      payload: { choice: "retry" },
      id: "q-stuck",
    });
  });

  it("an older engine (no choice_info): the raw names, and a reject still needs its reason", async () => {
    const { ctx, acts } = await waitingOn({
      ...STUCK,
      choices: ["fix", "reject"],
      choice_info: undefined,
      kind: "gate",
    });
    const r = await run(ctx, "/answer");
    expect(r.pick?.choices.map((c) => c.label)).toEqual([
      "fix (default)",
      "reject · needs a reason",
    ]);
    expect((await run(ctx, "/answer reject")).blocks[0].kind).toBe("error");
    expect((await run(ctx, "/answer fix")).blocks[0].kind).toBe("ok");
    expect(acts()[0].body).toMatchObject({ payload: { choice: "fix" } });
  });
});

describe("/answer for questions", () => {
  it("id=a,b sends a list for a question that takes several; one that takes one keeps its words", async () => {
    const { ctx, acts } = await waitingOn({
      step: "questions",
      kind: "clarify",
      title: "Three questions",
      detail: "",
      options: ["approve"],
      id: "q-init",
      questions: [
        {
          id: "run",
          question: "How does it run?",
          options: [
            { label: "docker compose", recommended: true },
            { label: "locally" },
          ],
        },
        {
          id: "knowledge",
          question: "Which pages?",
          multiple: true,
          options: [
            { label: "architecture", recommended: true },
            { label: "domain" },
            { label: "data" },
          ],
        },
      ],
    });
    expect(
      (
        await run(
          ctx,
          '/answer knowledge=architecture,data run="docker compose, with a seed"',
        )
      ).blocks[0].kind,
    ).toBe("ok");
    expect(acts()[0].body).toEqual({
      decision: "approve",
      payload: {
        answers: {
          knowledge: ["architecture", "data"],
          run: "docker compose, with a seed",
        },
      },
      id: "q-init",
    });
  });
});

describe("keel 0.18.64 /answer keeps the answers picked so far", () => {
  const ROUND = {
    step: "clarify_gate",
    kind: "clarify",
    title: "Questions before the plan",
    detail: "",
    options: ["approve"],
    id: "q-1",
    questions: [
      {
        id: "store",
        question: "Where should the cache live?",
        options: [{ label: "in memory", recommended: true }, { label: "on disk" }],
      },
      {
        id: "ttl",
        question: "How long should entries live?",
        options: [{ label: "one hour", recommended: true }, { label: "one day" }],
      },
    ],
  };
  const then = async (r: Reply | Promise<Reply>, v: string) => (await r).pick!.then(v);

  it("Esc and /answer again goes on at the next question; sent, nothing is kept", async () => {
    const { ctx, acts, stub } = await waitingOn({ ...ROUND, id: "q-keep" });
    await then(run(ctx, "/answer"), "on disk");
    // the person pressed Esc here (to switch project, to read the gate first): /answer again
    const again = await run(ctx, "/answer");
    expect(again.blocks[0]).toEqual({ kind: "dim", text: expect.stringContaining("draft kept: your answers to 1") });
    expect(again.pick!.title).toBe("How long should entries live?");
    const check = await then(again, "one day");
    expect(check.pick!.title).toBe("Send these answers?");
    expect((await then(check, "send")).blocks[0].kind).toBe("ok");
    expect(acts().at(-1)!.body).toMatchObject({ payload: { answers: { store: "on disk", ttl: "one day" } } });
    // the same question again (the stub moved on): nothing was kept
    stub.state.thread = { ...stub.state.thread, status: "waiting", waiting: { ...ROUND, id: "q-keep" } } as never;
    const fresh = await run(ctx, "/answer");
    expect(fresh.blocks.some((b) => b.text.includes("draft kept"))).toBe(false);
    expect(fresh.pick!.title).toBe("Where should the cache live?");
  });

  it("a new question of the flow does not show the old one's answers", async () => {
    const { ctx, stub } = await waitingOn({ ...ROUND, id: "q-old" });
    await then(run(ctx, "/answer"), "on disk");
    stub.state.thread = { ...stub.state.thread, waiting: { ...ROUND, id: "q-next" } } as never;
    const next = await run(ctx, "/answer");
    expect(next.blocks.some((b) => b.text.includes("draft kept"))).toBe(false);
    expect(next.pick!.title).toBe("Where should the cache live?");
  });
});

describe("KeelBot's answer button (keel-flow answer)", () => {
  it("reads answers or a choice, asks yes / no in words, and sends the answer like the Flow page", async () => {
    const answers = toButton(
      "flow",
      '{"do": "answer", "flow": "t1", "answers": {"knowledge": ["architecture", "data"], "run": "docker compose"}}',
    );
    expect(answers).toEqual({
      kind: "answer",
      flow: "t1",
      answers: { knowledge: ["architecture", "data"], run: "docker compose" },
      why: undefined,
    });
    expect(question(answers!, () => '"Set up"')).toBe(
      'KeelBot suggests: answer "Set up" with architecture, data; docker compose?',     // 0.18.23 no question ids
    );
    expect(toButton("flow", '{"do": "answer", "flow": "t1"}')).toBeNull(); // nothing to answer: stays text
    const choice = toButton(
      "flow",
      '{"do": "answer", "flow": "t1", "choice": "skip", "why": "blocked by AC-003"}',
    )!;
    expect(question(choice, () => "it")).toBe(
      "KeelBot suggests: answer it with the choice skip (blocked by AC-003)?",
    );
    const { stub, acts } = await waitingOn(STUCK);
    expect(await pressButton(choice, "demo", () => "it")).toContain(
      "answered it (the choice skip)",
    );
    expect(acts()[0].body).toEqual({
      decision: "approve",
      why: "blocked by AC-003",
      payload: { choice: "skip" },
    });
    expect(stub.calls.some((c) => c.path === "/api/inbox/t1/act")).toBe(true);
  });

  it("a choice that needs a reason, given without one, is not offered; the questions' own words show", async () => {
    await waitingOn(STUCK);                                   // prepare reads the flow's question as it waits now
    const prep = await prepare(toButton("flow", '{"do": "answer", "flow": "t1", "choice": "skip"}')!, "demo", () => "it");
    expect(prep).toMatchObject({ error: expect.stringContaining("Skip it for now, which needs a reason") });
    const ok = await prepare(toButton("flow", '{"do": "answer", "flow": "t1", "choice": "stop"}')!, "demo", () => "it");
    expect(ok).toEqual({ title: 'KeelBot suggests "Stop the flow" for "AC-002 is stuck at verify_green". Send it?',
      detail: "Stop the flow: the commits so far stay" });
  });

  it("parses KeelBot's questions with the dashboard's own parser", () => {
    expect(
      parseQuestions('[{"question": "Where?", "options": ["Web", "iOS"]}]'),
    ).toEqual([
      {
        id: "q1",
        question: "Where?",
        why: undefined,
        multiple: false,
        options: [
          { label: "Web", description: undefined, recommended: true },
          { label: "iOS", description: undefined, recommended: false },
        ],
      },
    ]);
  });
});

const FINDINGS = {
  step: "security__fix",
  kind: "gate",
  title: "security: 2 blocking finding(s)",
  detail: "- [security] a.py:1 no auth\n- [security] b.py:2 weak hash",
  options: ["approve", "reject"],
  labels: { approve: "Fix them", reject: "Go on anyway" },
  id: "q-find",
  questions: ["a.py:1 no auth", "b.py:2 weak hash"].map((q, n) => ({
    id: `f${n + 1}`,
    question: q,
    why: "from security",
    options: [
      { label: "fix", recommended: true },
      { label: "accept", why: "required" },
      { label: "not a finding", why: "required" },
    ],
  })),
};

describe("keel 0.18.22 a findings gate: one question per finding", () => {
  it("the waiting lines show the questions beside approve / reject", () => {
    const lines = fmt.waitingLines(FINDINGS as never).join("\n");
    expect(lines).toContain("/approve");
    expect(lines).toContain("or decide each: /answer");
    expect(lines).toContain("f2: b.py:2 weak hash  [fix (recommended) / accept +why / not a finding +why]");
  });

  it("/answer id=value sends the answers with an approve", async () => {
    const { ctx, acts } = await waitingOn(FINDINGS);
    expect((await run(ctx, '/answer f1=fix f2="not a finding: cache key only"')).blocks[0].kind).toBe("ok");
    expect(acts()[0].body).toEqual({
      decision: "approve",
      payload: { answers: { f1: "fix", f2: "not a finding: cache key only" } },
      id: "q-find",
    });
  });

  it("/answer alone goes through the findings; an option that needs a reason asks for it", async () => {
    const { ctx, acts } = await waitingOn(FINDINGS);
    let r: Reply = await run(ctx, "/answer");
    expect(r.pick?.choices.map((c) => c.label)).toEqual([
      "fix (recommended)",
      "accept · needs a reason",
      "not a finding · needs a reason",
      "Other… (my own words)",
    ]);
    r = await r.pick!.then("fix");
    r = await r.pick!.then("accept");
    expect(r.ask?.prompt).toBe("Why accept? (needed)");
    r = await r.ask!.then("  ");
    expect(r.blocks[0]).toEqual({ kind: "error", text: "accept needs a reason." });
    r = await r.ask!.then("tracked in ticket 7");
    expect(r.pick?.title).toBe("Send these answers?");
    r = await r.pick!.then("send");
    expect(acts()[0].body).toEqual({
      decision: "approve",
      payload: { answers: { f1: "fix", f2: "accept: tracked in ticket 7" } },
      id: "q-find",
    });
  });
});

// 0.18.23 a real suggestion read: KeelBot suggests: answer "I want to extend the pricingengine webhook to support the
// estmiated del…" with eta_semantics → A · …; eta_consumers → A · Database only?
const FRAME = {
  step: "frame_questions",
  kind: "clarify",
  title: "frame the question has 2 questions",
  detail: "",
  options: ["approve"],
  id: "q-frame",
  questions: [
    { id: "eta_semantics", question: "What should the ETA mean?", options: [{ label: "A · Overwrite one column" }, { label: "B · Keep history" }] },
    { id: "eta_consumers", question: "Who reads the ETA?", options: [{ label: "A · Database only" }, { label: "B · Outbound too" }] },
  ],
};

describe("KeelBot's suggested answer reads like the gate (0.18.23)", () => {
  it("the gate's short name, then one line per question: its words → the answer; never the ids or the request", async () => {
    const { stub } = await waitingOn(FRAME);
    stub.state.thread = { ...stub.state.thread, title: "I want to extend the pricingengine webhook to support the estmiated delivery date" } as never;
    const b = toButton("flow", '{"do": "answer", "flow": "t1", "answers": {"eta_semantics": "A · Overwrite one column", "eta_consumers": "A · Database only"}}')!;
    const prep = (await prepare(b, "demo", () => '"I want to extend the pricingengine webhook to support the estmiated del…"')) as { title: string; detail: string };
    expect(prep.title).toBe('KeelBot suggests these answers for "frame the question". Send them?');
    expect(prep.detail.split("\n")).toEqual([
      "What should the ETA mean? → A · Overwrite one column",
      "Who reads the ETA? → A · Database only",
    ]);
    const all = prep.title + "\n" + prep.detail;
    expect(all).not.toContain("eta_semantics");
    expect(all).not.toContain("eta_consumers");
    expect(all).not.toContain("pricingengine");
    // without the gate at hand: still no ids
    expect(question(b, () => '"ETA"')).toBe('KeelBot suggests: answer "ETA" with A · Overwrite one column; A · Database only?');
  });
});

describe("a gate's fold-outs in keel TUI (0.18.23)", () => {
  it("show their title line only: a spike's angles with their verdicts", () => {
    const detail = "## Summary\n- **DECISION:** build it\n\n<details><summary>reuse the column · VERDICT promising</summary>\n\nAnswer: it fits.\n- a: b\n\n</details>\n\n<details><summary>a new column · VERDICT possible</summary>\n\nAnswer: no.\n\n</details>";
    expect(fmt.folded(detail)).toBe("## Summary\n- **DECISION:** build it\n\n▸ reuse the column · VERDICT promising\n\n▸ a new column · VERDICT possible");
  });
});

// 0.18.28 a choice gate's suggestion named the flow's title cut short ('the gate of "Extend the PricingEngine …"'):
// it names the step now, and says what a plain reject really does there
const SUITE = {
  step: "integration_commit", name: "verify the suite + commit", kind: "fix", options: ["approve", "reject"], id: "q-suite",
  title: "verify the suite + commit: the suite fails outside the criteria",
  detail: "2 tests fail.",
  choices: ["send_back_ac", "retry", "stop"],
  choice_info: [
    { name: "send_back_ac", label: "Send back to AC-008", description: "its test is the likely cause", why: "optional", default: true },
    { name: "retry", label: "Check again", description: "after you fixed it yourself", why: "none", default: false },
    { name: "stop", label: "Stop the flow", description: "the commits so far stay", why: "optional", default: false },
  ],
  plain: { approve: { does: 'the choice "send_back_ac" (Send back to AC-008): its test is the likely cause', reason: "used" },
    reject: { does: "stops the flow (the work so far stays)", reason: "not used: it is only kept as the stop reason" } },
};

describe("a choice gate's suggestion names the step (0.18.28)", () => {
  it("decide and answer buttons show the step's name, never the flow's title; a reject that only stops says so", async () => {
    const { stub } = await waitingOn(SUITE);
    stub.state.thread = { ...stub.state.thread, title: "Extend the PricingEngine (Pricing.AI) webhook to support the estimated delivery date" } as never;
    const long = () => '"Extend the PricingEngine (Pricing.AI) webhook to support the estimated…"';
    const rej = (await prepare(toButton("flow", '{"do": "reject", "flow": "t1", "why": "the AC-008 test leaks a row"}')!, "demo", long)) as { title: string; detail: string };
    expect(rej.title).toBe('KeelBot suggests: send back "verify the suite + commit" (the AC-008 test leaks a row)?');
    expect(rej.detail).toBe("reject here stops the flow (the work so far stays); the reason goes to no agent.");
    const ans = (await prepare(toButton("flow", '{"do": "answer", "flow": "t1", "choice": "send_back_ac"}')!, "demo", long)) as { title: string };
    expect(ans.title).toBe('KeelBot suggests "Send back to AC-008" for "verify the suite + commit". Send it?');
    expect(rej.title + ans.title).not.toContain("PricingEngine");
  });
});

describe("a flow starts in the run mode the person names (0.18.28)", () => {
  it("/flow --mode important sends it; KeelBot's start button carries one too", async () => {
    const { stub, ctx } = await waitingOn(STUCK);
    const posts = () => stub.calls.filter((c) => c.method === "POST" && c.path === "/api/projects/demo/flows");
    await run(ctx as never, "/flow feature --mode important add the ETA field");
    expect(posts().at(-1)?.body).toMatchObject({ workflow_id: "feature", run_mode: "important", request: "add the ETA field" });
    const bad = (await run(ctx as never, "/flow feature --mode fast x")) as Reply;
    expect(JSON.stringify(bad)).toContain("--mode is one of manual, important, auto, readonly");
    const b = toButton("start", '{"workflow": "feature", "title": "ETA", "request": "add it", "mode": "important"}')!;
    expect(question(b, () => "")).toBe('KeelBot suggests: start feature "ETA" in important mode?');
    await pressButton(b, "demo", () => "");
    expect(posts().at(-1)?.body).toMatchObject({ run_mode: "important" });
  });
});

describe("typing while KeelBot answers (0.18.28)", () => {
  it("waits instead of a \"still answering\" refusal, and goes once the answer ended", async () => {
    const { stub, ctx } = await waitingOn(STUCK);
    const core = ctx.core as KeelCore;
    const turns = () => stub.calls.filter((c) => c.method === "POST" && c.path === "/api/projects/demo/helper/sessions/s1/turn");
    const seen: { text: string; position: number; sent: boolean }[] = [];
    core.on("queued", (m) => seen.push(m));
    await core.send("first");
    expect(core.snapshot.chat.busy).toBe(true);
    expect(await core.send("second")).toBeNull();
    expect(seen).toEqual([{ text: "second", position: 1, sent: false }]);
    expect(turns()).toHaveLength(1);
    stub.state.messages.push({ n: 9, role: "helper", text: "the first answer", data: {}, at: "2026-10-16T10:00:00Z" });
    await (core as unknown as { finishTurn: () => Promise<void> }).finishTurn();
    await new Promise((r) => setTimeout(r, 30));
    expect(turns().map((c) => (c.body as { text: string }).text)).toEqual(["first", "second"]);
    expect(seen[1]).toEqual({ text: "second", position: 0, sent: true });
    expect(core.queue).toEqual([]);
  });
});
