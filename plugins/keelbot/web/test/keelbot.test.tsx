// v0.9.0 KeelBot's buttons (start a flow it suggests, save a workflow it wrote), inside keel's web as the full image has
// it (web/src/test/setup.ts runs every plugin's setup()). Moved with the plugin; the Workflows page's folders and run
// counts, the Flow page's tabs and history and the pages' names stay keel's (web/src/test/runs.test.tsx).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
// keel's own app and test harness: the panel and its buttons run inside keel's web, as keel loads them
import { App } from "../../../../web/src/App";
import * as fx from "../../../../web/src/test/fixtures";
import { db } from "../../../../web/src/test/setup";
import { splitActions } from "../Actions";

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
async function chatWith(text: string) {
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
        },
        at: new Date().toISOString(),
      },
    ],
  } as never);
  localStorage.setItem("keel2.helper.ludus-engine.session", "h1");
  location.hash = "#/keelbot";
  render(<App />);
  return panel();
}

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
