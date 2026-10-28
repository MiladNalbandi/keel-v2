// v0.9.0 KeelBot's buttons (start a flow it suggests, save a workflow it wrote), inside keel's web as the full image has
// it (web/src/test/setup.ts runs every plugin's setup()). Moved with the plugin; the Workflows page's folders and run
// counts, the Flow page's tabs and history and the pages' names stay keel's (web/src/test/runs.test.tsx).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
// keel's own app and test harness: the panel and its buttons run inside keel's web, as keel loads them
import { App } from "../../../../web/src/App";
import * as fx from "../../../../web/src/test/fixtures";
import { db, server } from "../../../../web/src/test/setup";
import { http, HttpResponse } from "msw";
import { skillHead, splitActions } from "../Actions";
import { answeredBy, ThinkingBlock } from "../HelperPanel";

const panel = () => screen.findByRole("complementary", { name: "KeelBot" });

const START =
  '```keel-start\n{"workflow": "feature", "title": "Weekly report page", "request": "A page that lists last week\'s scores."}\n```';
const WORKFLOW = [
  "```keel-workflow",
  "id: check-before-push",
  "name: check before push",
  "keel_rules: false",
  "version: 1",
  "steps:",
  '  - { id: lint, kind: code, name: run the linters, action: "run: npm run lint", soft: true }',
  '  - { id: tests, kind: code, name: run the tests, action: "run: npm test" }',
  "  - { id: look, kind: gate, name: look at the results }",
  "```",
].join("\n");

/** A KeelBot chat whose last answer is `text` (as the engine stores it), on KeelBot's own page. */
async function chatWith(text: string, after: { role: "user" | "helper"; text: string }[] = [], extra: Record<string, unknown> = {}) {
  db.helper.sessions.push({
    id: "h1",
    project: "ludus-engine",
    root: "/w",
    mode: "ask",
    title: "Report page",
    status: "idle",
    busy: false,
    model: { provider: "claude", mode: "subscription", model: "sonnet" },
    tokens_in: 0,
    tokens_out: 0,
    tokens_cached: 0,
    cost_usd: 0,
    turns: 1,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    messages: [
      {
        n: 1,
        role: "user",
        text: "How do I add a weekly report page?",
        data: {},
        at: new Date().toISOString(),
      },
      {
        n: 2,
        role: "helper",
        text,
        call_id: "c1",
        data: {
          status: "done",
          provider: "claude",
          model: "sonnet",
          tokens_in: 900,
          tokens_out: 200,
          cost_usd: 0,
          ms: 3000,
          ...extra,
        },
        at: new Date().toISOString(),
      },
      // what the chat said after that answer (a reload reads the whole chat again)
      ...after.map((m, k) => ({ n: 3 + k, role: m.role, text: m.text, data: m.role === "helper" ? { status: "done" } : {},
        at: new Date().toISOString() })),
    ],
  } as never);
  localStorage.setItem("keel2.helper.ludus-engine.session", "h1");
  location.hash = "#/keelbot";
  render(<App />);
  return panel();
}

describe("KeelBot's new rule pack and agent (0.18.5)", () => {
  it("an agent: its tools and instructions, saved only when pressed", async () => {
    const user = userEvent.setup();
    const p = await chatWith('A docs agent:\n\n```keel-agent\n{"name": "doc writer", "about": "Writes the docs.", "prompt": "Write docs/x.md.", "tools": ["Read", "Write"]}\n```');
    const card = await within(p).findByRole("region", { name: "New agent: doc writer" });
    expect(card).toHaveTextContent("Read, Write");
    expect(card).toHaveTextContent("Write docs/x.md.");
    expect(db.calls.some((c) => c.method === "POST" && c.path === "/api/projects/ludus-engine/agents")).toBe(false);
    await user.click(within(card).getByRole("button", { name: "Save the agent" }));
    await waitFor(() => expect(db.calls.find((c) => c.method === "POST" && c.path === "/api/projects/ludus-engine/agents")?.body)
      .toMatchObject({ name: "doc writer", prompt: "Write docs/x.md.", tools: ["Read", "Write"], phases: ["any"] }));
  });

  it("a rule pack and an agent split out of the answer as their own cards", () => {
    expect(splitActions("a\n```keel-rule\nname: x\n```\n```keel-agent\n{}\n```").map((s) => s.kind)).toEqual(["text", "rule", "agent"]);
  });
});

describe("KeelBot's new skill (0.18.6)", () => {
  it("a skill: its text and agents from its front matter, saved and attached only when pressed", async () => {
    const user = userEvent.setup();
    const p = await chatWith("A skill for the explorer:\n\n```keel-skill\n---\nname: changelog-entries\ndescription: How to write a changelog entry.\n"
      + "agents: [explorer]\n---\n# Changelog\nOne line per change.\n```");
    const card = await within(p).findByRole("region", { name: "New skill: changelog-entries" });
    expect(card).toHaveTextContent("How to write a changelog entry.");
    await waitFor(() => expect(within(card).getByRole("checkbox", { name: "explorer" })).toBeChecked());
    expect(within(card).getByRole("checkbox", { name: "implementer" })).not.toBeChecked();
    expect(db.calls.some((c) => c.path === "/api/projects/ludus-engine/skills/import")).toBe(false);
    await user.click(within(card).getByRole("checkbox", { name: "implementer" }));
    await user.click(within(card).getByRole("button", { name: "Save the skill" }));
    await waitFor(() => expect(db.calls.find((c) => c.method === "PUT" && c.path === "/api/projects/ludus-engine/skills/imported-skill")?.body)
      .toEqual({ agents: ["explorer", "implementer"] }));
    expect(db.calls.find((c) => c.path === "/api/projects/ludus-engine/skills/import")?.body).toMatchObject({ body: expect.stringContaining("name: changelog-entries") });
    expect(await within(card).findByRole("status")).toHaveTextContent("Saved imported-skill: loaded by explorer, implementer");
  });

  it("reads the front matter, and splits a skill out of the answer", () => {
    expect(skillHead("---\nname: x\ndescription: d\nagents: reviewer, 'implementer'\n---\nbody")).toEqual(
      { name: "x", description: "d", agents: ["reviewer", "implementer"] });
    expect(skillHead("no front matter").name).toBe("");
    expect(splitActions("a\n```keel-skill\n---\nname: x\n---\n```").map((s) => s.kind)).toEqual(["text", "skill"]);
  });
});

describe("KeelBot's new stack (0.18.9)", () => {
  it("a stack: keel checks it first, then Save writes it", async () => {
    const user = userEvent.setup();
    const p = await chatWith("A stack for this project:\n\n```keel-stack\nname: zig-api\nlane: api\ndetect: {files: [build.zig]}\n"
      + "layers: [{id: unit, what: x}]\n```");
    const card = await within(p).findByRole("region", { name: "New stack: zig-api" });
    await waitFor(() => expect(db.calls.find((c) => c.path === "/api/projects/ludus-engine/stacks/check")?.body)
      .toMatchObject({ yaml: expect.stringContaining("name: zig-api") }));
    expect(await within(card).findByText(/warning: there is no api_test_ac/)).toBeInTheDocument();
    expect(db.calls.some((c) => c.path === "/api/projects/ludus-engine/stacks/save")).toBe(false);
    await user.click(within(card).getByRole("button", { name: "Save the stack" }));
    await waitFor(() => expect(db.calls.find((c) => c.path === "/api/projects/ludus-engine/stacks/save")?.body)
      .toMatchObject({ yaml: expect.stringContaining("lane: api") }));
    expect(await within(card).findByRole("status")).toHaveTextContent("Saved zig-api as .keel/stacks/zig-api.yml");
    expect(splitActions("a\n```keel-stack\nname: x\n```").map((s) => s.kind)).toEqual(["text", "stack"]);
  });
});

describe("KeelBot asks the person to choose (0.18.9)", () => {
  it("its questions are keel's form: tick several, send, and the answers go back to it", async () => {
    const user = userEvent.setup();
    const p = await chatWith('Two things first:\n\n```keel-questions\n[{"id": "where", "question": "Where should it show?", "multiple": true,'
      + ' "options": [{"label": "Web", "recommended": true}, {"label": "iOS"}, {"label": "Android"}]},'
      + ' {"id": "size", "question": "How many players?", "options": ["Top 10", "Top 100"]}]\n```');
    const card = await within(p).findByRole("region", { name: "KeelBot's questions" });
    // 0.18.13 the recommended option of a question that takes several starts ticked
    expect(within(card).getByRole("checkbox", { name: /Web/ })).toHaveAttribute("aria-checked", "true");
    await user.click(within(card).getByRole("checkbox", { name: /iOS/ }));
    await user.click(within(card).getByRole("radio", { name: /Top 100/ }));
    expect(within(card).getByText(/2 of 2 answered/)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Send my answers" }));
    await waitFor(() => expect(db.calls.find((c) => c.method === "POST" && c.path.endsWith("/helper/sessions/h1/turn"))?.body).toMatchObject({
      text: "Answers to your questions:\n1. Where should it show?\n   → Web; iOS\n2. How many players?\n   → Top 100" }));
    expect(within(card).getByRole("status")).toHaveTextContent("Your answers went to KeelBot:");
    expect(within(card).getByRole("list", { name: "Your answers" })).toHaveTextContent("Where should it show? → Web; iOS");
  });

  it("0.18.63 an option with \"mode\": \"edit\" switches the chat to Edit first, so its answer already runs in Edit", async () => {
    const user = userEvent.setup();
    const p = await chatWith('Ask mode is read only.\n\n```keel-questions\n[{"id": "next", "question": "How do we go on?", "options": ['
      + '{"label": "Switch to Edit and fix it", "mode": "edit", "recommended": true}, {"label": "Leave it"}]}]\n```');
    const next = await within(p).findByRole("region", { name: "Next steps" });
    await user.click(within(next).getByRole("button", { name: "Switch to Edit and fix it" }));
    await waitFor(() => expect(db.calls.some((c) => c.method === "POST" && c.path.endsWith("/helper/sessions/h1/turn"))).toBe(true));
    const patch = db.calls.findIndex((c) => c.method === "PATCH" && c.path.endsWith("/helper/sessions/h1"));
    const turn = db.calls.findIndex((c) => c.method === "POST" && c.path.endsWith("/helper/sessions/h1/turn"));
    expect(db.calls[patch]?.body).toEqual({ mode: "edit" });
    expect(patch).toBeLessThan(turn);
    expect(db.calls[turn].body).toMatchObject({ text: "Answers to your questions:\n1. How do we go on?\n   → Switch to Edit and fix it" });
  });

  it("0.18.63 an option without a mode changes no mode", async () => {
    const user = userEvent.setup();
    const p = await chatWith('```keel-questions\n[{"id": "next", "question": "How do we go on?", "options": ["Switch to Edit and fix it", "Leave it"]}]\n```');
    await user.click(within(await within(p).findByRole("region", { name: "Next steps" })).getByRole("button", { name: "Leave it" }));
    await waitFor(() => expect(db.calls.some((c) => c.method === "POST" && c.path.endsWith("/helper/sessions/h1/turn"))).toBe(true));
    expect(db.calls.some((c) => c.method === "PATCH" && c.path.endsWith("/helper/sessions/h1"))).toBe(false);
  });

  it("0.18.64 the picked options and own words are kept after another page, and go once they are sent", async () => {
    const user = userEvent.setup();
    const asked = 'Two things first:\n\n```keel-questions\n[{"id": "where", "question": "Where should it show?", "multiple": true,'
      + ' "options": [{"label": "Web", "recommended": true}, {"label": "Mobile"}]},'
      + ' {"id": "size", "question": "How many rows?", "options": ["Ten", "A hundred"]}]\n```';
    let p = await chatWith(asked);
    let card = await within(p).findByRole("region", { name: "KeelBot's questions" });
    await user.click(within(card).getByRole("checkbox", { name: /Mobile/ }));
    await user.type(within(card).getByLabelText("Your own answer to: How many rows?"), "as many as fit");
    expect(within(card).queryByTestId("draft-kept")).toBeNull();
    // another page and back
    location.hash = "#/flow";
    await screen.findByRole("region", { name: "Gate waits for you" });
    location.hash = "#/keelbot";
    p = await panel();
    card = await within(p).findByRole("region", { name: "KeelBot's questions" });
    expect(within(card).getByRole("checkbox", { name: /Mobile/ })).toHaveAttribute("aria-checked", "true");
    expect(within(card).getByLabelText("Your own answer to: How many rows?")).toHaveValue("as many as fit");
    expect(within(card).getByTestId("draft-kept")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Send my answers" }));
    await waitFor(() => expect(db.calls.find((c) => c.method === "POST" && c.path.endsWith("/helper/sessions/h1/turn"))?.body).toMatchObject({
      text: "Answers to your questions:\n1. Where should it show?\n   → Web; Mobile\n2. How many rows?\n   → as many as fit" }));
    expect(Object.keys(localStorage).filter((k) => k.startsWith("keel2.answer."))).toEqual([]);
  });

  it("0.18.13 after a reload the card stays answered: it reads the answers from the chat's later message", async () => {
    const asked = '```keel-questions\n[{"id": "size", "question": "How many players?", "options": ["Top 10", "Top 100"]}]\n```';
    const p = await chatWith(`One thing first:\n\n${asked}`, [
      { role: "user", text: "Answers to your questions:\n1. How many players?\n   → Top 100" },
      { role: "helper", text: "Top 100 it is." },
    ]);
    const card = await within(p).findByRole("region", { name: "KeelBot's questions" });
    expect(within(card).getByRole("status")).toHaveTextContent("Your answers went to KeelBot:");
    expect(within(card).getByRole("list", { name: "Your answers" })).toHaveTextContent("How many players? → Top 100");
    expect(within(card).queryByRole("radio")).not.toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Send my answers" })).not.toBeInTheDocument();
  });

  it("0.18.13 a later chat message that is not the answers leaves the questions open; answers to a newer question are its own", () => {
    const q = '```keel-questions\n[{"question": "Q?", "options": ["a", "b"]}]\n```';
    const msgs = [
      { n: 1, role: "helper", text: q },
      { n: 2, role: "user", text: "what do you mean?" },
      { n: 3, role: "helper", text: `I mean:\n${q}` },
      { n: 4, role: "user", text: "Answers to your questions:\n1. Q?\n   → a" },
    ];
    expect(answeredBy(msgs, 1)).toBeNull();
    expect(answeredBy(msgs, 3)).toBe(msgs[3].text);
  });
});

describe("0.18.13 KeelBot proposes the answer to a flow's question (keel-flow answer)", () => {
  const STUCK = { step: "verify_green", kind: "fix" as const, title: "AC-002 is stuck at verify_green", detail: "failed 3 times",
    options: ["approve" as const, "reject" as const], choices: ["retry", "skip"], choice_info: [
      { name: "retry", label: "Try again", description: "one more round", why: "optional" as const, default: true },
      { name: "skip", label: "Skip it for now", description: "AC-002 goes to the end of the list", why: "required" as const, default: false }] };

  it("a choice: its label, what it does and the reason on a card; Send is the Flow page's resume, only when pressed", async () => {
    const user = userEvent.setup();
    const t = db.flows["ludus-engine"].thread!;
    t.waiting = STUCK;
    const p = await chatWith(`Skip it, it waits for AC-003:\n\n\`\`\`keel-flow\n{"do": "answer", "flow": "${t.thread_id}", "choice": "skip", "why": "blocked by AC-003"}\n\`\`\``);
    const card = await within(p).findByRole("region", { name: "Flow: Choose “Skip it for now”" });
    expect(card).toHaveTextContent("AC-002 goes to the end of the list");
    expect(card).toHaveTextContent("The reason: blocked by AC-003");
    expect(card).toHaveTextContent('"AC-002 is stuck at verify_green"');          // 0.18.23 the gate's name, like the gate
    expect(db.calls.some((c) => c.path.endsWith("/resume"))).toBe(false);
    await user.click(within(card).getByRole("button", { name: "Choose “Skip it for now”" }));
    await waitFor(() => expect(db.calls.find((c) => c.path === `/api/threads/${t.thread_id}/resume`)?.body).toEqual(
      { decision: "approve", why: "blocked by AC-003", payload: { choice: "skip" } }));
    expect(await within(card).findByRole("status")).toHaveTextContent("Sent. The flow is running");
  });

  it("answers to questions: each with the question's words; a choice that needs a reason and has none cannot be sent", async () => {
    const user = userEvent.setup();
    const t = db.flows["ludus-engine"].thread!;
    t.waiting = { step: "questions", kind: "clarify", title: "Three questions", detail: "", options: ["approve"], questions: [
      { id: "run", question: "How does the project run?", options: [{ label: "docker compose", recommended: true }, { label: "locally" }] },
      { id: "knowledge", question: "Which knowledge pages?", multiple: true, options: [{ label: "architecture" }, { label: "data" }] }] };
    const p = await chatWith(`\`\`\`keel-flow\n{"do": "answer", "flow": "${t.thread_id}", "answers": {"run": "locally", "knowledge": ["architecture", "data"]}}\n\`\`\``
      + `\n\n\`\`\`keel-flow\n{"do": "answer", "flow": "${t.thread_id}", "choice": "reject"}\n\`\`\``);
    const card = await within(p).findByRole("region", { name: "Flow: Send these answers" });
    await waitFor(() => expect(within(card).getByRole("list", { name: "KeelBot's answers" })).toHaveTextContent(
      "How does the project run? → locallyWhich knowledge pages? → architecture; data"));
    const bad = within(p).getByRole("region", { name: "Flow: Choose “reject”" });
    expect(within(bad).getByRole("alert")).toHaveTextContent("needs a reason");
    expect(within(bad).getByRole("button", { name: "Choose “reject”" })).toBeDisabled();
    await user.click(within(card).getByRole("button", { name: "Send these answers" }));
    await waitFor(() => expect(db.calls.find((c) => c.path === `/api/threads/${t.thread_id}/resume`)?.body).toEqual(
      { decision: "approve", payload: { answers: { run: "locally", knowledge: ["architecture", "data"] } } }));
  });
});

describe("0.18.23 KeelBot's suggested gate answer reads like the gate, and a new message drops it", () => {
  const FRAME = { step: "frame_questions", kind: "clarify" as const, title: "frame the question has 2 questions", detail: "",
    options: ["approve" as const], questions: [
      { id: "eta_semantics", question: "What should the ETA mean?", options: [{ label: "A · Overwrite one column" }, { label: "B · Keep history" }] },
      { id: "eta_consumers", question: "Who reads the ETA?", options: [{ label: "A · Database only" }, { label: "B · Outbound too" }] }] };
  const block = (tid: string) => `\`\`\`keel-flow\n{"do": "answer", "flow": "${tid}", "answers": {"eta_semantics": "A · Overwrite one column", "eta_consumers": "A · Database only"}}\n\`\`\``;

  it("the step's short name, one line per question with its words, never the ids or the request", async () => {
    const t = db.flows["ludus-engine"].thread!;
    t.waiting = FRAME;
    t.title = "I want to extend the pricingengine webhook to support the estmiated delivery date";
    const p = await chatWith(`I read src/webhook/EtaHandler.kt:249.\n\n${block(t.thread_id)}`);
    const card = await within(p).findByRole("region", { name: "Flow: Send these answers" });
    await waitFor(() => expect(card).toHaveTextContent('"frame the question"'));
    const list = within(card).getByRole("list", { name: "KeelBot's answers" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "What should the ETA mean? → A · Overwrite one column", "Who reads the ETA? → A · Database only"]);
    expect(card.textContent).not.toMatch(/eta_semantics|eta_consumers|pricingengine/);
    expect(within(card).getByRole("button", { name: "Send these answers" })).toBeEnabled();
  });

  it("a message after the suggestion: not sent, says why, no Send button", async () => {
    const t = db.flows["ludus-engine"].thread!;
    t.waiting = FRAME;
    const p = await chatWith(block(t.thread_id), [{ role: "user", text: "did you read the code?" }]);
    const card = await within(p).findByRole("region", { name: "Flow: Send these answers" });
    await waitFor(() => expect(within(card).getByRole("status")).toHaveTextContent(
      "Suggestion not sent: you wrote a new message. KeelBot will update it."));
    expect(within(card).queryByRole("button", { name: "Send these answers" })).not.toBeInTheDocument();
    expect(db.calls.some((c) => c.path.endsWith("/resume"))).toBe(false);
  });
});

describe("0.18.28 a gate decision names the step and says what reject really does", () => {
  it("the step's name, not the flow's title; a reject that only stops the flow warns that no agent gets the reason", async () => {
    const t = db.flows["ludus-engine"].thread!;
    t.title = "Extend the PricingEngine (Pricing.AI) webhook to support the estimated delivery date";
    t.waiting = { step: "integration_commit", name: "verify the suite + commit", kind: "fix" as const,
      title: "verify the suite + commit keeps failing", detail: "2 tests fail", options: ["approve" as const, "reject" as const],
      plain: { approve: { does: "runs the check again", reason: "used" },
        reject: { does: "stops the flow (the work so far stays)", reason: "not used: it is only kept as the stop reason" } } };
    const p = await chatWith(`\`\`\`keel-flow\n{"do": "reject", "flow": "${t.thread_id}", "why": "the AC-008 test leaks a row"}\n\`\`\``);
    const card = await within(p).findByRole("region", { name: "Flow: Send back" });
    await waitFor(() => expect(card).toHaveTextContent('"verify the suite + commit"'));
    expect(card.textContent).not.toMatch(/PricingEngine/);
    expect(within(card).getByTestId("kb-plain-effect")).toHaveTextContent("Send back here: stops the flow (the work so far stays)");
    expect(card).toHaveTextContent("The reason (no agent gets it here): the AC-008 test leaks a row");
  });
});

describe("KeelBot's flow buttons (0.18.4)", () => {
  it("stop: names the flow, stops it only when pressed, then offers to open it", async () => {
    const user = userEvent.setup();
    const tid = db.flows["ludus-engine"].thread!.thread_id;
    const p = await chatWith(`It waits at a gate. I can stop it:\n\n\`\`\`keel-flow\n{"do": "stop", "flows": ["${tid}"]}\n\`\`\``);
    const card = await within(p).findByRole("region", { name: "Flow: Stop the flow" });
    await waitFor(() => expect(card).toHaveTextContent(db.flows["ludus-engine"].thread!.title));
    expect(db.calls.some((c) => c.path === `/api/threads/${tid}/stop`)).toBe(false);
    await user.click(within(card).getByRole("button", { name: "Stop the flow" }));
    await waitFor(() => expect(db.calls.some((c) => c.method === "POST" && c.path === `/api/threads/${tid}/stop`)).toBe(true));
    expect(await within(card).findByRole("status")).toHaveTextContent("Stopped. Every finished step stays saved.");
  });

  it("a flow button it cannot read says so; a send-back without a reason is not a button", async () => {
    const p = await chatWith('```keel-flow\n{"do": "reject", "flow": "t1"}\n```');
    expect(await within(p).findByRole("note")).toHaveTextContent("KeelBot's flow button could not be read.");
    expect(splitActions('```keel-flow\n{"do": "mode", "flow": "t1", "mode": "auto"}\n```').map((s) => s.kind)).toEqual(["flow"]);
  });
});

describe("KeelBot's answers: text and buttons", () => {
  it("splits an answer into text and action blocks; a keel block inside another code block stays text", () => {
    const segs = splitActions(
      `Use **feature**: it has a spec.\n\n${START}\n\nOr write your own:\n\n${WORKFLOW}`,
    );
    expect(segs.map((s) => s.kind)).toEqual([
      "text",
      "start",
      "text",
      "workflow",
    ]);
    expect(segs[1]).toMatchObject({
      body: expect.stringContaining('"workflow": "feature"'),
    });
    const quoted = splitActions(["````markdown", START, "````"].join("\n"));
    expect(quoted.map((s) => s.kind)).toEqual(["text"]);
  });

  it("the start button starts the flow it suggests, with the title the person may change, and opens it", async () => {
    const user = userEvent.setup();
    const p = await chatWith(
      `**feature** fits: a new page needs a spec and tests.\n\n${START}`,
    );
    const card = await within(p).findByRole("region", {
      name: "Start a flow: Weekly report page",
    });
    expect(card).toHaveTextContent("A page that lists last week's scores.");
    const title = within(card).getByRole("textbox", {
      name: "Title of the flow",
    });
    await user.clear(title);
    await user.type(title, "Weekly scores page");
    await user.click(
      within(card).getByRole("button", { name: "Start the flow" }),
    );
    await waitFor(() =>
      expect(
        db.calls.find(
          (c) =>
            c.method === "POST" &&
            c.path === "/api/projects/ludus-engine/flows",
        )?.body,
      ).toEqual({
        workflow_id: "feature",
        title: "Weekly scores page",
        request: "A page that lists last week's scores.",
      }),
    );
    await user.click(
      await within(card).findByRole("button", { name: "Open the flow ▸" }),
    );
    await waitFor(() => expect(location.hash).toMatch(/^#\/flow\//));
  });

  it("0.18.28 a start button with a run mode starts the flow in that mode and says so", async () => {
    const user = userEvent.setup();
    const p = await chatWith('```keel-start\n{"workflow": "feature", "title": "ETA", "request": "add it", "mode": "important"}\n```');
    const card = await within(p).findByRole("region", { name: "Start a flow: ETA" });
    expect(within(card).getByTestId("kb-start-mode")).toHaveTextContent("Run mode: important");
    await user.click(within(card).getByRole("button", { name: "Start the flow" }));
    await waitFor(() => expect(db.calls.find((c) => c.method === "POST" && c.path === "/api/projects/ludus-engine/flows")?.body)
      .toEqual({ workflow_id: "feature", title: "ETA", request: "add it", run_mode: "important" }));
  });

  it("0.18.62 a start refused because main went its own way offers to start on the current branch", async () => {
    const user = userEvent.setup();
    server.use(http.post("/api/projects/:pid/flows", async ({ request }) => {
      const b = (await request.clone().json()) as { branch?: string };
      if (b.branch !== "continue") return HttpResponse.json({ error: "The local main and origin/main went their own ways (4 and 31 commits)",
        hint: "Pull (or rebase) main first, so the flow's new branch starts from the latest code; or continue on the branch the folder is on." }, { status: 409 });
      return HttpResponse.json({ ...fx.thread, thread_id: "t-new", status: "running", waiting: undefined, title: "ETA" });
    }));
    const p = await chatWith('```keel-start\n{"workflow": "feature", "title": "ETA", "request": "add it"}\n```');
    const card = await within(p).findByRole("region", { name: "Start a flow: ETA" });
    await user.click(within(card).getByRole("button", { name: "Start the flow" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent("went their own ways");
    await user.click(within(card).getByRole("button", { name: "Start on the current branch" }));
    expect(await within(card).findByText(/Started/)).toBeInTheDocument();
  });

  it("a workflow it wrote is checked, shows what it runs, and saves into a folder", async () => {
    const user = userEvent.setup();
    db.importAs = {
      ...fx.fixWorkflow,
      id: "ludus-engine-check-before-push",
      name: "check before push",
    };
    const p = await chatWith(`A small one, code steps only:\n\n${WORKFLOW}`);
    const card = await within(p).findByRole("region", {
      name: "New workflow: check before push",
    });
    expect(
      await within(card).findByText(
        /3 steps · 1 gate · no agents \(code steps only, no model runs\)/,
      ),
    ).toBeInTheDocument();
    expect(within(card).getByText("npm test")).toBeInTheDocument();
    await user.type(
      within(card).getByRole("textbox", { name: "Folder for the workflow" }),
      "Checks",
    );
    await user.click(
      within(card).getByRole("button", { name: "Save the workflow" }),
    );
    await waitFor(() =>
      expect(
        db.calls.find(
          (c) => c.path === "/api/projects/ludus-engine/workflows/import",
        )?.body,
      ).toEqual({
        yaml: expect.stringContaining("id: check-before-push"),
        folder: "Checks",
      }),
    );
    expect(await within(card).findByText(/in the folder/)).toHaveTextContent(
      "Saved as check before push in the folder Checks.",
    );
  });

  it("when keel's check finds problems, Save waits and KeelBot can be asked to fix them", async () => {
    const user = userEvent.setup();
    const p = await chatWith(
      WORKFLOW.replace("look at the results", "INVALID"),
    );
    const card = await within(p).findByRole("region", {
      name: "New workflow: check before push",
    });
    expect(await within(card).findByRole("alert")).toHaveTextContent(
      "INVALID is not a step kind",
    );
    expect(
      within(card).getByRole("button", { name: "Save the workflow" }),
    ).toBeDisabled();
    await user.click(
      within(card).getByRole("button", { name: "Ask KeelBot to fix it" }),
    );
    await waitFor(() =>
      expect(
        db.calls.find(
          (c) =>
            c.method === "POST" && c.path.endsWith("/helper/sessions/h1/turn"),
        )?.body,
      ).toMatchObject({
        text: expect.stringContaining(
          "Fix them and give the whole workflow again.",
        ),
      }),
    );
  });
});

describe("0.18.23 KeelBot's thinking in the panel", () => {
  it("a stored answer's thinking is folded above it, apart from the answer; a click opens it", async () => {
    const user = userEvent.setup();
    const p = await chatWith("The report page goes in web/src/pages/Report.tsx:1.", [],
      { thinking: "The person wants a weekly page. I look at the pages first.", thinking_ms: 12000 });
    const box = await within(p).findByText("⋮ Thought for 12s");
    const details = box.closest("details")!;
    expect(details).not.toHaveAttribute("open");
    const answer = within(p).getByText(/The report page goes in/).closest(".hp-answer")!;
    expect(answer.textContent).not.toContain("I look at the pages first.");
    await user.click(box);
    expect(details).toHaveAttribute("open");
    expect(within(details).getByLabelText("KeelBot's thinking")).toHaveTextContent("I look at the pages first.");
  });

  it("live: open while KeelBot thinks, folded once the answer starts", () => {
    const r = render(<ThinkingBlock text="Thinking about it" answering={false} />);
    const details = r.container.querySelector("details")!;
    expect(details).toHaveAttribute("open");
    expect(r.getByText("⋮ Thinking")).toBeInTheDocument();
    r.rerender(<ThinkingBlock text="Thinking about it, done" answering />);
    expect(details).not.toHaveAttribute("open");
    expect(r.getByText("⋮ Thought")).toBeInTheDocument();
    r.unmount();
  });
});
