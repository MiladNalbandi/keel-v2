// v0.3: agent output (StepView), the keel mascot, and model / effort pickers that follow the catalog.

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { JobStep, Notification } from "../api";
import { normalizeCatalog } from "../components/ModelPicker";
import { FilesTouched, StepView } from "../components/StepView";
import { db, FakeEventSource } from "./setup";

const at = new Date().toISOString();
const step = (over: Partial<JobStep>): JobStep => ({ n: 1, at, kind: "text", text: "", ...over });
const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);

describe("StepView", () => {
  it("shows a unified diff with green/red lines, line numbers and a file header", () => {
    const diff = [
      "--- a/api/src/main/kotlin/scores/ScoreController.kt",
      "+++ b/api/src/main/kotlin/scores/ScoreController.kt",
      "@@ -10,3 +10,4 @@ class ScoreController",
      " @PostMapping(\"/scores\")",
      "-    fun save(s: ScoreDto)",
      "+    fun save(@Valid s: ScoreDto)",
      "+    // validated",
      " }",
    ].join("\n");
    const { container } = render(<StepView s={step({ kind: "edit", path: "api/src/main/kotlin/scores/ScoreController.kt", diff, text: "validate the body" })} />);
    const added = container.querySelectorAll('.dl[data-kind="add"]');
    const removed = container.querySelectorAll('.dl[data-kind="del"]');
    expect(added).toHaveLength(2);
    expect(removed).toHaveLength(1);
    expect(removed[0]).toHaveTextContent("fun save(s: ScoreDto)");
    // gutters: the removed line is old line 11, the first added line is new line 11
    expect([...removed[0].querySelectorAll(".ln")].map((e) => e.textContent)).toEqual(["11", ""]);
    expect([...added[0].querySelectorAll(".ln")].map((e) => e.textContent)).toEqual(["", "11"]);
    expect(container.querySelector(".dl.hunk")).toHaveTextContent("@@ -10,3 +10,4 @@");
    expect(screen.getByText("kotlin/scores/ScoreController.kt")).toHaveAttribute("title", "api/src/main/kotlin/scores/ScoreController.kt");
    expect(screen.getByText("+2")).toBeInTheDocument();
    expect(screen.getByText("−1")).toBeInTheDocument();
    expect(screen.getByText("validate the body")).toBeInTheDocument();
    // Kotlin is highlighted
    expect(container.querySelector(".dl .hljs-keyword")).toHaveTextContent("fun");
  });

  it("folds long tool output after 14 lines with 'Show all', and shows the command, exit badge and time", async () => {
    const user = userEvent.setup();
    const output = Array.from({ length: 40 }, (_, i) => `line ${i + 1}`).join("\n");
    const { container } = render(<StepView s={step({ kind: "tool", tool: "Bash", text: "./gradlew test --tests ScoreTest", output, ok: true, ms: 1234 })} />);
    expect(screen.getByTestId("cmd")).toHaveTextContent("$./gradlew test --tests ScoreTest");
    expect(screen.getByTestId("exit")).toHaveTextContent("exit 0");
    expect(screen.getByText("1.2 s")).toBeInTheDocument();
    const out = () => container.querySelector(".tool-out pre")!.textContent!;
    expect(out().split("\n")).toHaveLength(14);
    expect(out()).not.toContain("line 15");
    await user.click(screen.getByRole("button", { name: "Show all (40 lines)" }));
    expect(out().split("\n")).toHaveLength(40);
    expect(out()).toContain("line 40");
    await user.click(screen.getByRole("button", { name: "Show less" }));
    expect(out().split("\n")).toHaveLength(14);
  });

  it("a failed command gets a red exit badge; an MCP call shows server and tool", () => {
    render(<>
      <StepView s={step({ n: 1, kind: "tool", tool: "Bash", text: "npm test", output: "1 failed", ok: false, ms: 80 })} />
      <StepView s={step({ n: 2, kind: "tool", tool: "keel_timeline", server: "keel", text: "{\"filter\":\"gates\"}", output: "[]", ok: true, ms: 38 })} />
    </>);
    expect(screen.getAllByTestId("exit")[0]).toHaveTextContent("exit ≠ 0");
    expect(screen.getAllByTestId("exit")[0]).toHaveClass("bad");
    const badge = document.querySelector(".tool-badge.mcp")!;
    expect(badge).toHaveTextContent("keel · keel_timeline");
    expect(document.querySelector(".tool-args")).toHaveTextContent('"filter": "gates"');
  });

  it("renders the answer as Markdown: headings, lists, tables, highlighted code", () => {
    const text = "## Done\n\nAll **green**:\n- `ScoreTest` passes\n- coverage 84%\n\n| AC | status |\n|---|---|\n| AC-002 | done |\n\n```ts\nconst ok: boolean = true;\n```";
    const { container } = render(<StepView s={step({ kind: "answer", text })} />);
    expect(screen.getByRole("heading", { name: "Done" })).toBeInTheDocument();
    expect(screen.getByText("green").tagName).toBe("B");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByRole("cell", { name: "AC-002" })).toBeInTheDocument();
    expect(container.querySelector(".answer-card .hljs-keyword")).toHaveTextContent("const");
  });

  it("keeps newlines: file content, agent text and tool output are never squashed into one line", () => {
    const file = "package scores\n\nclass ScoreController {\n  fun ok() = true\n}";
    const { container } = render(<>
      <StepView s={step({ n: 1, kind: "read", path: "api/ScoreController.kt", text: file })} />
      <StepView s={step({ n: 2, kind: "text", text: "First I read the controller.\nThen I run the tests." })} />
      <StepView s={step({ n: 3, kind: "tool", tool: "Bash", text: "ls", output: "a.kt\nb.kt\nc.kt" })} />
    </>);
    const read = container.querySelector('[data-kind="read"]')!;
    expect(read.querySelector("pre")!.textContent).toBe(file);
    expect(read.querySelectorAll(".gutter span")).toHaveLength(5);
    expect(read).toHaveTextContent("5 lines");
    expect(read.querySelector(".hljs-keyword")).not.toBeNull(); // highlighted as Kotlin from the extension
    const p = container.querySelector('[data-kind="text"] p')!;
    expect(p.querySelector("br")).not.toBeNull();
    expect(container.querySelector('[data-kind="tool"] .tool-out pre')!.textContent).toBe("a.kt\nb.kt\nc.kt");
  });

  it("Files touched links to the last diff of each file", async () => {
    const user = userEvent.setup();
    const onJump = vi.fn();
    const steps = [
      step({ n: 3, kind: "edit", path: "a/One.kt", diff: "-x\n+y" }),
      step({ n: 5, kind: "write", path: "b/Two.kt", diff: "+new" }),
      step({ n: 8, kind: "edit", path: "a/One.kt", diff: "-y\n+z" }),
    ];
    render(<FilesTouched steps={steps} onJump={onJump} />);
    await user.click(screen.getByRole("button", { name: "Show the last change to a/One.kt" }));
    expect(onJump).toHaveBeenCalledWith(8);
    await user.click(screen.getByRole("button", { name: "Show the last change to b/Two.kt" }));
    expect(onJump).toHaveBeenCalledWith(5);
  });

  it("Live agents shows the Outcome card with the final answer as Markdown, and tool output inside the tool step", async () => {
    location.hash = "#/live/j-480";
    const { container } = render(<App />);
    const outcome = await screen.findByRole("region", { name: "Outcome" });
    expect(within(outcome).getByText("done")).toBeInTheDocument();
    expect(within(outcome).getByText("PASS").tagName).toBe("B");
    expect(within(outcome).getAllByRole("listitem")).toHaveLength(2);
    expect(container.querySelector('[data-kind="tool"] .tool-out pre')!.textContent).toBe("RED 4be12d9\nGREEN a81c3f0");
    expect(within(outcome).getByRole("button", { name: /Show the last change to api\/src\/main\/kotlin\/scores\/ScoreController.kt/ })).toBeInTheDocument();
    expect(container.querySelector('[data-kind="edit"] .dl[data-kind="add"]')).toHaveTextContent("fun save(@Valid s: ScoreDto)");
  });
});

// ---------- mascot ----------

const note = (over: Partial<Notification> = {}): Notification => ({
  id: `m${Math.random()}`, type: "review", project_id: "ludus-engine", title: "AC gate waits for you",
  body: "AC-002 — ac-reviewer says PASS", link: "#/jobs", at: new Date().toISOString(), read: false, ...over,
});
async function ready() {
  render(<App />);
  await screen.findByText(/AC gate — AC-002/);
  await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
}

describe("keel mascot", () => {
  it("jumps happily and says the title when 'needs you' arrives; the bubble opens it", async () => {
    const user = userEvent.setup();
    await ready();
    expect(screen.getByTestId("mascot")).not.toHaveAttribute("data-anim");
    act(() => FakeEventSource.emit("notification", note()));
    await waitFor(() => expect(screen.getByTestId("mascot")).toHaveAttribute("data-anim", "jump"));
    expect(screen.getByTestId("mascot")).toHaveAttribute("data-mood", "happy");
    expect(screen.getByTestId("mascot-bubble")).toHaveTextContent("AC gate waits for you");
    await waitFor(() => expect(screen.getByTestId("mascot")).not.toHaveAttribute("data-anim"), { timeout: 2000 });
    await user.click(screen.getByRole("button", { name: "Open notification: AC gate waits for you" }));
    await waitFor(() => expect(location.hash).toBe("#/jobs"));
    expect(screen.queryByTestId("mascot-bubble")).not.toBeInTheDocument();
  });

  it("a failure makes it shake, worried", async () => {
    await ready();
    act(() => FakeEventSource.emit("notification", note({ type: "failed", title: "Guard reverted an edit" })));
    await waitFor(() => expect(screen.getByTestId("mascot")).toHaveAttribute("data-anim", "shake"));
    expect(screen.getByTestId("mascot")).toHaveAttribute("data-mood", "worried");
  });

  it("with reduced motion it does not move, it only shows the bubble", async () => {
    const mm = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {}, onchange: null })) as unknown as typeof window.matchMedia;
    try {
      await ready();
      act(() => FakeEventSource.emit("notification", note()));
      expect(await screen.findByTestId("mascot-bubble")).toHaveTextContent("AC gate waits for you");
      expect(screen.getByTestId("mascot")).not.toHaveAttribute("data-anim");
    } finally {
      window.matchMedia = mm;
    }
  });

  it("'Show keel' in the notification settings hides it, kept in this browser", async () => {
    const user = userEvent.setup();
    await ready();
    expect(screen.getByTestId("mascot")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Notifications/ }));
    await user.click(screen.getByRole("tab", { name: "Settings" }));
    await user.click(screen.getByRole("switch", { name: "Show keel" }));
    expect(screen.queryByTestId("mascot")).not.toBeInTheDocument();
    expect(localStorage.getItem("keel2.mascot")).toBe("0");
    expect(calls("PUT", "/api/notification-settings")).toHaveLength(0);
  });
});

// ---------- model + effort pickers ----------

describe("model pickers follow the catalog", () => {
  it("Agents drawer: provider → mode → model → effort from the catalog, with the source hint", async () => {
    const user = userEvent.setup();
    location.hash = "#/agents";
    render(<App />);
    await user.click(await screen.findByText("Writes the minimum code to make the failing test pass."));
    const dlg = await screen.findByRole("dialog", { name: "implementer" });
    const d = within(dlg);
    // copilot has no effort setting
    await waitFor(() => expect(d.getByTestId("am-source")).toHaveTextContent("built-in list"));
    expect(d.queryByLabelText("Effort")).not.toBeInTheDocument();
    expect([...d.getByLabelText("Runs on").querySelectorAll("option")].map((o) => o.textContent)).toEqual(["Copilot CLI", "OpenCode", "API key (GitHub Models)"]);

    await user.selectOptions(d.getByLabelText("Provider"), "codex");
    expect(d.getByLabelText("Runs on")).toHaveValue("subscription");
    expect(d.getByLabelText("Model")).toHaveValue("gpt-5.6-sol");
    expect(d.getByLabelText("Effort")).toHaveValue("medium");
    expect([...d.getByLabelText("Effort").querySelectorAll("option")].map((o) => o.getAttribute("value"))).toEqual(["", "low", "medium", "high", "ultra"]);
    expect(d.getByTestId("am-source")).toHaveTextContent("from the CLI (cached)");

    // a model with its own effort list; an effort it does not have is dropped
    await user.selectOptions(d.getByLabelText("Effort"), "ultra");
    fireEvent.change(d.getByLabelText("Model"), { target: { value: "gpt-5-mini" } });
    expect([...d.getByLabelText("Effort").querySelectorAll("option")].map((o) => o.getAttribute("value"))).toEqual(["", "low", "medium"]);
    expect(d.getByLabelText("Effort")).toHaveValue("");

    await user.selectOptions(d.getByLabelText("Runs on"), "api");
    expect(d.getByLabelText("Model")).toHaveValue("gpt-5");
    expect(d.getByTestId("am-models").querySelector('option[value="gpt-5"]')).toHaveAttribute("label", "GPT-5 (API)");
    await user.selectOptions(d.getByLabelText("Effort"), "high");
    await user.click(d.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT", "/api/projects/ludus-engine/agents/implementer")[0]?.body).toEqual({
      model: { provider: "codex", mode: "api", model: "gpt-5", effort: "high" },
    }));
  });

  it("the effort select hides for a model with no efforts; free text keeps the provider's efforts", async () => {
    const user = userEvent.setup();
    location.hash = "#/agents";
    render(<App />);
    await user.click(await screen.findByText("Writes the minimum code to make the failing test pass."));
    const d = within(await screen.findByRole("dialog", { name: "implementer" }));
    await user.selectOptions(d.getByLabelText("Provider"), "claude");
    expect(d.getByLabelText("Model")).toHaveValue("sonnet");
    expect(d.getByTestId("am-source")).toHaveTextContent("from the CLI");
    expect(d.getByLabelText("Effort")).toBeInTheDocument();
    fireEvent.change(d.getByLabelText("Model"), { target: { value: "haiku" } });
    expect(d.queryByLabelText("Effort")).not.toBeInTheDocument();
    fireEvent.change(d.getByLabelText("Model"), { target: { value: "my-own-model" } });
    expect(d.getByLabelText("Effort")).toBeInTheDocument();
  });

  it("Connections: 'Use for all agents' saves the picked model and effort on the selected mode", async () => {
    const user = userEvent.setup();
    location.hash = "#/connections";
    render(<App />);
    const picker = await screen.findByTestId("all-claude-picker");
    await waitFor(() => expect(within(picker).getByTestId("all-claude-source")).toHaveTextContent("from the CLI"));
    expect(within(picker).queryByLabelText("Provider")).not.toBeInTheDocument();
    fireEvent.change(within(picker).getByLabelText("Model"), { target: { value: "opus" } });
    await user.selectOptions(within(picker).getByLabelText("Effort"), "high");
    await user.click(screen.getByRole("button", { name: "Use for all agents" }));
    const m = { provider: "claude", mode: "subscription", model: "opus", effort: "high" };
    await waitFor(() => expect(calls("PUT", "/api/settings/general")[0]?.body).toEqual({ default_model: m, implementer_model: m, reviewer_model: m }));
  });

  it("Settings model rows show the effort picker", async () => {
    location.hash = "#/settings";
    render(<App />);
    const row = await screen.findByTestId("set-reviewer_model-picker");
    await waitFor(() => expect(within(row).getByLabelText("Effort")).toBeInTheDocument());
    expect(within(row).getByLabelText("Runs on")).toHaveValue("subscription");
  });

  it("normalizes the older catalog shape and drops junk", () => {
    const c = normalizeCatalog({ claude: [{ id: "opus", label: "Opus" }], junk: 3, codex: { modes: { api: [{ id: "gpt-5" }] }, efforts: ["low"], source: "cli" } });
    expect(c.claude.modes.subscription?.[0]).toEqual({ id: "opus", label: "Opus" });
    expect(c.claude.source).toBe("builtin");
    expect(c.codex.default).toEqual({ mode: "api", model: "gpt-5" });
    expect(c.codex.source).toBe("cli");
    expect(c.junk).toBeUndefined();
  });
});

// ---------- engine v0.3: "already met" ACs and the "already passes" gate ----------

describe("already met", () => {
  it("shows an already-met AC like done, with its own label, in the list, the AC strip and the graph", async () => {
    db.flows["ludus-engine"].thread!.acs[2].status = "already-met";
    render(<App />);
    const list = (await screen.findByText("Acceptance criteria")).closest(".panel") as HTMLElement;
    const pill = within(list).getByText("already met");
    expect(pill).toHaveClass("p-met");
    const chip = document.querySelector('.acchip[data-status="already-met"]')!;
    expect(chip).toHaveTextContent("AC-003 already met");
    expect(document.querySelector('.acchip[data-status="done"]')).toHaveTextContent("AC-001 done");
    const dot = screen.getByTestId("graph-acs").querySelector('[data-ac="AC-003"]')!;
    expect(dot).toHaveAttribute("data-status", "already-met");
    expect(dot).toHaveTextContent("AC-003 · already met");
  });

  it("an 'already passes' gate offers 'Mark as already met' / 'Send back for a stricter test' (with a why)", async () => {
    const user = userEvent.setup();
    db.flows["ludus-engine"].thread!.waiting = { step: "s8", kind: "gate", title: "AC-003 already passes", detail: "the new test passes before any GREEN code", options: ["approve", "reject"] };
    render(<App />);
    expect(await screen.findByRole("button", { name: "Mark as already met" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send back for a stricter test" }));
    expect(calls("POST", "/api/threads/th_7f3a/resume")).toHaveLength(0);
    await user.type(screen.getByLabelText("Why (needed to send back)"), "assert the 400 body too");
    await user.click(screen.getByRole("button", { name: "Send back for a stricter test" }));
    await waitFor(() => expect(calls("POST", "/api/threads/th_7f3a/resume")[0]?.body).toEqual({ decision: "reject", why: "assert the 400 body too" }));
  });
});

describe("gate detail", () => {
  it("shows the explanation as text and the command output folded", async () => {
    const { GateDetail } = await import("../pages/Flow");
    const out = Array.from({ length: 40 }, (_, i) => `# line ${i}`).join("\n");
    render(<GateDetail text={`AC-4 already passes.\n\nApprove to mark it.\n\n$ npm test -- --test-name-pattern=AC-4\n${out}`} />);
    expect(screen.getByText(/Approve to mark it/)).toBeInTheDocument();
    expect(screen.getByTestId("gate-cmd")).toHaveTextContent("npm test -- --test-name-pattern=AC-4");
    expect(screen.getByRole("button", { name: /Show all \(40 lines\)/ })).toBeInTheDocument();
  });
});

describe("blocking findings gate", () => {
  it("offers Fix them / Go on anyway from the engine's labels", async () => {
    const { gateLabels } = await import("../pages/Flow");
    const l = gateLabels({ step: "ship_review__fix", kind: "gate", title: "ship review x 4 lenses: 2 blocking finding(s)", detail: "- a\n- b",
      options: ["approve", "reject"], labels: { approve: "Fix them", reject: "Go on anyway" } });
    expect(l.approve).toBe("Fix them");
    expect(l.reject).toBe("Go on anyway");
    expect(l.needWhy).toBe(true);
  });
});
