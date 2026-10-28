// 0.18.57 one task in the new Tasks page: keel's suggestion (Start / Use another flow / how it runs / Not now), the start's
// warnings (uncommitted files → the Doctor, the fake model, a project keel does not know yet), keel's proposal and
// "Pick a flow for me", what waits for you (PP, the release, the PR link, the flow at a gate, a review comment with keel's
// check), the running flow, the pull request ("Review it"), and More actions. The classic drawer's tests (tasks.test.tsx,
// automations.test.tsx) adapted to the new one.

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { db, server } from "../../../../web/src/test/setup";

const calls = (method: string, path: string) =>
  db.calls.filter((c) => c.method === method && c.path === path);
const main = () => within(document.getElementById("main")!);

async function openTask(id: string, name: string) {
  location.hash = `#/tasks/${id}`;
  render(<App />);
  await main().findByRole("heading", { name: "Tasks", level: 1 });
  return screen.findByRole("dialog", { name });
}

/** The task with its open items (as GET /api/tasks/{id} answers). */
const withWaiting = (id: string) => {
  const t = db.tk.tasks.find((x) => x.id === id)!;
  return {
    ...t,
    waiting: db.tk.items.filter((i) => i.task_id === id && !i.done_at),
  };
};

describe("Task drawer (new page): keel suggests", () => {
  it("suggests the flow the type starts, says why and its steps, and Start starts it with how it runs", async () => {
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    expect(
      within(d).getByRole("link", { name: "Open in Jira ↗" }),
    ).toHaveAttribute("href", "https://acme.atlassian.net/browse/ABC-1");
    const s = within(d).getByRole("region", { name: "Start a flow" });
    expect(await within(s).findByTestId("suggested-flow")).toHaveTextContent(
      "feature flow",
    ); // a story → feature
    expect(s).toHaveTextContent("Why: New behaviour for users.");
    expect(s).toHaveTextContent(
      "a short spec → you approve → build each criterion test-first",
    );
    await user.selectOptions(
      within(s).getByLabelText("How it runs"),
      "important",
    );
    await user.click(
      within(s).getByRole("button", { name: "Start feature flow" }),
    );
    await waitFor(() =>
      expect(calls("POST", "/api/tasks/tk_1/start")).toHaveLength(1),
    );
    expect(calls("POST", "/api/tasks/tk_1/start")[0].body).toEqual({
      workflow_id: "feature",
      run_mode: "important",
    });
    expect(
      await within(d).findByText("In progress", { selector: ".pill" }),
    ).toBeInTheDocument();
    expect(
      within(d).queryByRole("region", { name: "Start a flow" }),
    ).toBeNull(); // the flow runs: no second start
    const flow = within(d).getByRole("region", { name: "Flow" });
    expect(flow).toHaveTextContent("feature flow");
    expect(flow).toHaveTextContent("running");
    expect(
      within(flow).getByRole("button", { name: "Open flow ▸" }),
    ).toBeInTheDocument();
  });

  it("Use another flow shows the flows as chips, and Start starts the picked one", async () => {
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const s = within(d).getByRole("region", { name: "Start a flow" });
    await within(s).findByTestId("suggested-flow");
    await user.click(
      within(s).getByRole("button", { name: "Use another flow" }),
    );
    const chips = within(s).getByRole("group", { name: "Pick another flow" });
    expect(
      within(chips).getByRole("button", { name: "feature (keel's pick)" }),
    ).toHaveAttribute("aria-pressed", "true");
    await user.click(within(chips).getByRole("button", { name: "fix" }));
    expect(within(chips).getByRole("button", { name: "fix" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(s).getByTestId("suggested-flow")).toHaveTextContent(
      "fix flow",
    );
    expect(s).toHaveTextContent(
      "You picked this flow instead of keel's feature.",
    );
    await user.click(within(s).getByRole("button", { name: "Start fix flow" }));
    await waitFor(() =>
      expect(calls("POST", "/api/tasks/tk_1/start")[0]?.body).toEqual({
        workflow_id: "fix",
        run_mode: "manual",
      }),
    );
  });

  it("Not now closes the task without starting anything", async () => {
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    await user.click(
      within(within(d).getByRole("region", { name: "Start a flow" })).getByRole(
        "button",
        { name: "Not now" },
      ),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls("POST", "/api/tasks/tk_1/start")).toHaveLength(0);
    await waitFor(() => expect(location.hash).toBe("#/tasks"));
  });

  it("suggests the address flow for a ticket that changes only test code (0.18.56)", async () => {
    db.workflows.push({
      id: "address",
      name: "address (keel)",
      keel_rules: true,
      version: 1,
      yaml: "",
      steps: [],
    });
    db.tk.tasks[0].description =
      "The flaky case in src/test/RankTest.kt waits too long; make it use the fake clock.";
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const s = within(d).getByRole("region", { name: "Start a flow" });
    expect(await within(s).findByTestId("suggested-flow")).toHaveTextContent(
      "address flow",
    );
    expect(s).toHaveTextContent(
      "Only test code changes (src/test/RankTest.kt)",
    );
    expect(
      within(s).getByRole("button", { name: "Start address flow" }),
    ).toBeInTheDocument();
  });

  it("offers the workspace Doctor when the start is refused for uncommitted files, then Start anyway", async () => {
    server.use(
      http.post("/api/tasks/:id/start", async ({ request }) => {
        const b = (await request.json()) as Record<string, unknown>;
        db.calls.push({
          method: "POST",
          path: "/api/tasks/tk_1/start",
          body: b,
        });
        return b.allow_dirty
          ? HttpResponse.json(withWaiting("tk_1"))
          : HttpResponse.json(
              {
                error:
                  "This project has uncommitted changes (2 files): .serena/project.yml, docs/x.md",
                hint: "Commit or stash them first.",
              },
              { status: 409 },
            );
      }),
      http.post("/api/projects/:pid/doctor/workspace", () =>
        HttpResponse.json({
          by: "rules",
          summary: "2 files",
          files: [],
          note: null,
          plan: [
            {
              id: "local",
              title: "Build output, editor and tool folders",
              why: "Made by tools on this computer.",
              action: "exclude",
              files: [".serena/project.yml"],
              patterns: [".serena/"],
            },
          ],
        }),
      ),
    );
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const s = within(d).getByRole("region", { name: "Start a flow" });
    await within(s).findByTestId("suggested-flow");
    await user.click(
      within(s).getByRole("button", { name: "Start feature flow" }),
    );
    await user.click(
      await within(s).findByRole("button", {
        name: "Ask the Doctor what to do with these files",
      }),
    );
    expect(
      await within(s).findByText("Build output, editor and tool folders"),
    ).toBeInTheDocument();
    await user.click(within(s).getByRole("checkbox", { name: /Start anyway/ }));
    await user.click(
      within(s).getByRole("button", { name: "Start feature flow" }),
    );
    await waitFor(() =>
      expect(calls("POST", "/api/tasks/tk_1/start")[1]?.body).toMatchObject({
        workflow_id: "feature",
        allow_dirty: true,
      }),
    );
  });

  it("offers to run with the fake model when the start is refused for it", async () => {
    server.use(
      http.post("/api/tasks/:id/start", () =>
        HttpResponse.json(
          { error: "The fake model is in use: agents only pretend." },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const s = within(d).getByRole("region", { name: "Start a flow" });
    await within(s).findByTestId("suggested-flow");
    await user.click(
      within(s).getByRole("button", { name: "Start feature flow" }),
    );
    expect(
      await within(s).findByRole("checkbox", {
        name: "Run with the fake model anyway",
      }),
    ).toBeInTheDocument();
  });

  it("offers Start anyway when another flow already works on the task, and sends force", async () => {
    let n = 0;
    server.use(
      http.post("/api/tasks/:id/start", async ({ request }) => {
        const body = (await request.clone().json()) as Record<string, unknown>;
        n += 1;
        if (!body.force)
          return HttpResponse.json(
            { error: "Someone already works on this: flow Export orders as CSV at verify works on ticket ABC-1" },
            { status: 409 },
          );
        return undefined; // force: the default handler starts it
      }),
    );
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const s = within(d).getByRole("region", { name: "Start a flow" });
    await within(s).findByTestId("suggested-flow");
    await user.click(within(s).getByRole("button", { name: "Start feature flow" }));
    const box = await within(s).findByRole("checkbox", { name: /two agents on one task/ });
    await user.click(box);
    await user.click(within(s).getByRole("button", { name: "Start feature flow" }));
    await waitFor(() => expect(n).toBe(2));
  });

  it("a project keel does not know yet: asks once, Run init first starts init, Start anyway starts the task's flow", async () => {
    db.projects[0].configured = false;
    localStorage.setItem("keel2.init-offered.ludus-engine", "true");
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const s = within(d).getByRole("region", { name: "Start a flow" });
    await within(s).findByTestId("suggested-flow");
    await user.click(
      within(s).getByRole("button", { name: "Start feature flow" }),
    );
    const ask = await within(s).findByRole("group", {
      name: "keel does not know this project yet",
    });
    expect(calls("POST", "/api/tasks/tk_1/start")).toHaveLength(0);
    await user.click(within(ask).getByRole("button", { name: "Start anyway" }));
    await waitFor(() =>
      expect(calls("POST", "/api/tasks/tk_1/start")).toHaveLength(1),
    );
    expect(calls("POST", "/api/tasks/tk_1/start")[0].body).toMatchObject({
      workflow_id: "feature",
    });
  });

  it("Run init first starts the init flow, not the task's", async () => {
    db.projects[0].configured = false;
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const s = within(d).getByRole("region", { name: "Start a flow" });
    await within(s).findByTestId("suggested-flow");
    await user.click(
      within(s).getByRole("button", { name: "Start feature flow" }),
    );
    const ask = await within(s).findByRole("group", {
      name: "keel does not know this project yet",
    });
    await user.click(
      within(ask).getByRole("button", { name: "Run init first (recommended)" }),
    );
    await waitFor(() =>
      expect(calls("POST", "/api/projects/ludus-engine/flows")).toHaveLength(1),
    );
    expect(
      calls("POST", "/api/projects/ludus-engine/flows")[0].body,
    ).toMatchObject({ workflow_id: "init" });
    expect(calls("POST", "/api/tasks/tk_1/start")).toHaveLength(0);
  });
});

describe("Task drawer (new page): keel's proposal", () => {
  const proposal = (id: number, data: Record<string, unknown> = {}) => ({
    id,
    task_id: "tk_1",
    project_id: "ludus-engine",
    kind: "task-proposal" as const,
    stage: "flow",
    title: "I want to run the small flow on ABC-1",
    detail: "Why: a small change.",
    created_at: "",
    done_at: null,
    data: {
      kind: "flow",
      workflow_id: "small",
      why: "a small change.",
      title: "Rank players",
      trigger: "Jira moved ABC-1 to In Progress",
      run_mode: "important",
      questions: [],
      ...data,
    },
  });

  it("Pick a flow for me says keel is picking, then shows the proposal and links it in the Inbox; Start answers it and the card closes", async () => {
    const user = userEvent.setup();
    let answer: () => void = () => undefined;
    server.use(
      http.post("/api/tasks/:id/pick", async ({ params }) => {
        db.calls.push({
          method: "POST",
          path: `/api/tasks/${params.id}/pick`,
          body: null,
        });
        await new Promise<void>((done) => {
          answer = done;
        });
        db.tk.items.push(proposal(301) as never);
        return HttpResponse.json(withWaiting(String(params.id)));
      }),
    );
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const s = within(d).getByRole("region", { name: "Start a flow" });
    await within(s).findByTestId("suggested-flow");
    expect(within(d).queryByRole("button", { name: "Review it" })).toBeNull(); // no PR yet
    await user.click(
      within(s).getByRole("button", { name: "Pick a flow for me" }),
    );
    expect(await within(d).findByTestId("pick-status")).toHaveTextContent(
      "keel is picking a flow… the proposal will be in your Inbox.",
    );
    expect(within(d).getByRole("button", { name: "Picking…" })).toBeDisabled();
    answer();
    const p = await within(d).findByRole("region", {
      name: "I want to run the small flow on ABC-1",
    });
    expect(p).toHaveTextContent("small flow");
    expect(p).toHaveTextContent("Why: a small change.");
    expect(p).toHaveTextContent("Because Jira moved ABC-1 to In Progress");
    expect(p).toHaveTextContent(
      "How it runs: Ask me only at the important gates",
    );
    expect(
      within(d).queryByRole("region", { name: "Start a flow" }),
    ).toBeNull(); // keel's proposal takes its place
    expect(
      within(d).getByRole("link", { name: "Open it in the Inbox ▸" }),
    ).toHaveAttribute("href", "#/inbox/ludus-engine?item=task-item-301");
    await user.click(within(p).getByRole("button", { name: "Start" }));
    await waitFor(() =>
      expect(calls("POST", "/api/inbox/tasks/301/act")[0]?.body).toEqual({
        action: "start",
      }),
    );
    await waitFor(() =>
      expect(
        within(d).queryByRole("region", {
          name: "I want to run the small flow on ABC-1",
        }),
      ).toBeNull(),
    );
    expect(within(d).queryByTestId("pick-status")).toBeNull();
  });

  it("answers keel's questions, picks another flow, or puts the proposal aside", async () => {
    db.tk.items.push(
      proposal(302, {
        questions: [
          {
            id: "q1",
            question: "Mobile too?",
            options: [{ label: "yes" }, { label: "no" }],
          },
        ],
      }) as never,
    );
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const p = await within(d).findByRole("region", {
      name: "I want to run the small flow on ABC-1",
    });
    await user.click(within(p).getByRole("radio", { name: /yes/ }));
    await user.click(
      within(p).getByRole("button", { name: "Use another flow" }),
    );
    const chips = within(p).getByRole("group", { name: "Pick another flow" });
    await user.click(within(chips).getByRole("button", { name: "fix" }));
    await user.click(
      within(p).getByRole("button", { name: "Start fix instead" }),
    );
    await waitFor(() =>
      expect(calls("POST", "/api/inbox/tasks/302/act")[0]?.body).toEqual({
        action: "other",
        workflow_id: "fix",
        answers: { q1: "yes" },
      }),
    );
  });

  it("0.18.64 an unsent answer is kept after the drawer closes, and goes when the flow starts", async () => {
    db.tk.items.push(
      proposal(304, {
        questions: [
          {
            id: "q1",
            question: "Cover the empty list too?",
            options: [{ label: "yes" }, { label: "no" }],
          },
        ],
      }) as never,
    );
    const user = userEvent.setup();
    let d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    let p = await within(d).findByRole("region", {
      name: "I want to run the small flow on ABC-1",
    });
    await user.click(within(p).getByRole("radio", { name: /no/ }));
    expect(within(p).queryByTestId("draft-kept")).toBeNull();
    cleanup();
    d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    p = await within(d).findByRole("region", {
      name: "I want to run the small flow on ABC-1",
    });
    expect(within(p).getByRole("radio", { name: /no/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(within(p).getByTestId("draft-kept")).toBeInTheDocument();
    await user.click(within(p).getByRole("button", { name: "Start" }));
    await waitFor(() =>
      expect(calls("POST", "/api/inbox/tasks/304/act")[0]?.body).toEqual({
        action: "start",
        answers: { q1: "no" },
      }),
    );
    await waitFor(() =>
      expect(
        Object.keys(localStorage).filter((k) => k.startsWith("keel2.answer.")),
      ).toEqual([]),
    );
  });

  it("Not now puts it aside", async () => {
    db.tk.items.push(proposal(303) as never);
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const p = await within(d).findByRole("region", {
      name: "I want to run the small flow on ABC-1",
    });
    await user.click(within(p).getByRole("button", { name: "Not now" }));
    await waitFor(() =>
      expect(calls("POST", "/api/inbox/tasks/303/act")[0]?.body).toEqual({
        action: "later",
      }),
    );
  });
});

describe("Task drawer (new page): what waits for you, the flow, the PR and More actions", () => {
  it("takes the PR link, Mark approved, PP works and Shipped walk it to Done, with the history", async () => {
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const s = within(d).getByRole("region", { name: "Start a flow" });
    await within(s).findByTestId("suggested-flow");
    await user.click(
      within(s).getByRole("button", { name: "Start feature flow" }),
    );
    await within(d).findByText("In progress", { selector: ".pill" });

    await user.type(
      within(d).getByLabelText("PR link"),
      "https://github.com/acme/app/pull/12",
    );
    await user.click(within(d).getByRole("button", { name: "Save link" }));
    expect(
      await within(d).findByText("In review", { selector: ".pill" }),
    ).toBeInTheDocument();
    expect(calls("POST", "/api/tasks/tk_1/pr")[0].body).toEqual({
      url: "https://github.com/acme/app/pull/12",
    });
    expect(
      within(d).getByRole("region", { name: "Pull request" }),
    ).toHaveTextContent("PR #12");

    await user.click(within(d).getByRole("button", { name: "More actions" }));
    await user.click(within(d).getByRole("button", { name: "Mark approved" }));
    const pp = await within(d).findByRole("group", {
      name: "Confirm PP testing for ABC-1",
    });
    expect(pp).toHaveTextContent("waits for you");
    await user.click(
      within(pp).getByRole("button", { name: "PP works, confirm" }),
    );
    await waitFor(() =>
      expect(calls("POST", "/api/inbox/tasks/100/act")).toHaveLength(1),
    );
    expect(
      await within(d).findByText("Ready for release", { selector: ".pill" }),
    ).toBeInTheDocument();
    const ship = await within(d).findByRole("group", {
      name: "Ship ABC-1 to production",
    });
    await user.click(
      within(ship).getByRole("button", { name: "Shipped, confirm" }),
    );
    expect(
      await within(d).findByText("Done", { selector: ".pill" }),
    ).toBeInTheDocument();
    const history = within(d).getByRole("list", { name: "History" });
    expect(
      within(history).getAllByRole("listitem").length,
    ).toBeGreaterThanOrEqual(5);
    expect(history).toHaveTextContent("In progress → In review");
    expect(history).toHaveTextContent("Testing on PP → Ready for release");
  });

  it("a PP test is sent back only with a reason", async () => {
    db.tk.tasks[1].status = "testing_pp";
    db.tk.items.push({
      id: 130,
      task_id: "tk_2",
      project_id: "ludus-engine",
      kind: "task",
      stage: "pp",
      title: "Confirm PP testing for ABC-2",
      detail: "",
      created_at: "",
      done_at: null,
    });
    const user = userEvent.setup();
    const d = await openTask("tk_2", "ABC-2 · Fix the tally queue");
    const pp = await within(d).findByRole("group", {
      name: "Confirm PP testing for ABC-2",
    });
    await user.click(within(pp).getByRole("button", { name: "Send back" }));
    expect(calls("POST", "/api/inbox/tasks/130/act")).toHaveLength(0); // a reason first
    await user.type(
      within(pp).getByLabelText("Why does it go back?"),
      "wrong totals",
    );
    await user.click(within(pp).getByRole("button", { name: "Send back" }));
    await waitFor(() =>
      expect(calls("POST", "/api/inbox/tasks/130/act")[0]?.body).toEqual({
        action: "send_back",
        note: "wrong totals",
      }),
    );
  });

  it("a flow that finished without a PR asks for its link (B11), and a Jira move keel could not make is confirmed by hand", async () => {
    db.tk.tasks[0].status = "in_progress";
    db.tk.items.push(
      {
        id: 140,
        task_id: "tk_1",
        project_id: "ludus-engine",
        kind: "task",
        stage: "pr",
        title: "ABC-1: the flow finished, open its pull request",
        detail: "Open the PR, then paste its link.",
        created_at: "",
        done_at: null,
      },
      {
        id: 141,
        task_id: "tk_1",
        project_id: "ludus-engine",
        kind: "jira-manual",
        stage: "In Progress",
        title: "Move ABC-1 to In Progress in Jira",
        detail: "keel could not.",
        created_at: "",
        done_at: null,
      },
    );
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const pr = await within(d).findByRole("group", {
      name: "ABC-1: the flow finished, open its pull request",
    });
    expect(
      within(pr).getByRole("button", { name: "Save the PR link" }),
    ).toBeDisabled();
    await user.type(
      within(pr).getByLabelText("Pull request link"),
      "https://github.com/acme/app/pull/7",
    );
    await user.click(
      within(pr).getByRole("button", { name: "Save the PR link" }),
    );
    await waitFor(() =>
      expect(calls("POST", "/api/inbox/tasks/140/act")[0]?.body).toEqual({
        action: "pr",
        note: "https://github.com/acme/app/pull/7",
      }),
    );
    const jira = within(d).getByRole("group", {
      name: "Move ABC-1 to In Progress in Jira",
    });
    await user.click(
      within(jira).getByRole("button", { name: "Done, I moved it" }),
    );
    await waitFor(() =>
      expect(calls("POST", "/api/inbox/tasks/141/act")[0]?.body).toEqual({
        action: "done",
      }),
    );
  });

  it("a flow at a gate waits for you, and a running flow shows how far it is", async () => {
    db.tk.tasks[0].status = "in_progress";
    db.tk.tasks[0].flow = {
      thread_id: "th_9",
      workflow_id: "feature",
      status: "waiting",
      phase: "ac-gate",
      current: "s8",
      title: "ABC-1",
    };
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const w = within(d).getByRole("group", { name: "The flow waits for you" });
    expect(w).toHaveTextContent(
      "The feature flow waits for your answer at AC gate.",
    );
    expect(
      within(w).getByRole("link", { name: "Open the Inbox" }),
    ).toHaveAttribute("href", "#/inbox/ludus-engine");
    const bar = await within(d).findByRole("progressbar", {
      name: "How far the flow is",
    });
    expect(bar).toHaveAttribute("aria-valuenow", "67"); // step 8 of 12
    expect(
      within(d).queryByRole("region", { name: "Start a flow" }),
    ).toBeNull();
  });

  it("a review comment on a KeelBot ticket shows keel's check and answers with the edited reply", async () => {
    db.tk.items.push({
      id: 150,
      task_id: "tk_2",
      project_id: "ludus-engine",
      kind: "task-review",
      stage: "th2",
      title: "ana commented on ABC-2's pull request: the reviewer is right",
      detail: "",
      created_at: "",
      done_at: null,
      data: {
        thread_id: "th2",
        author: "ana",
        said: "This returns null.",
        where: "src/cart.kt:12",
        verdict: "reviewer_right",
        why: "no empty check",
        reply: "Good catch.",
      },
    } as never);
    const user = userEvent.setup();
    const d = await openTask("tk_2", "ABC-2 · Fix the tally queue");
    const c = await within(d).findByRole("region", {
      name: "ana commented on ABC-2's pull request: the reviewer is right",
    });
    expect(c).toHaveTextContent("the reviewer is right — no empty check");
    expect(c).toHaveTextContent("src/cart.kt:12");
    expect(
      within(c).getByRole("button", { name: "Address it (small flow)" }),
    ).toBeInTheDocument();
    const box = within(c).getByLabelText(/keel's reply/);
    await user.clear(box);
    await user.type(box, "Fixed in the next commit.");
    await user.click(
      within(c).getByRole("button", { name: "Use keel's reply" }),
    );
    await waitFor(() =>
      expect(calls("POST", "/api/inbox/tasks/150/act")[0]?.body).toEqual({
        action: "reply",
        note: "Fixed in the next commit.",
      }),
    );
  });

  it("the PR shows its reviewers; Review it asks keel for a review; Send back needs a reason; a deep link opens the task", async () => {
    server.use(
      http.post("/api/tasks/:id/review", async ({ params }) => {
        db.calls.push({
          method: "POST",
          path: `/api/tasks/${params.id}/review`,
          body: null,
        });
        return HttpResponse.json(withWaiting(String(params.id)));
      }),
    );
    const user = userEvent.setup();
    const d = await openTask("tk_2", "ABC-2 · Fix the tally queue");
    const pr = within(d).getByRole("region", { name: "Pull request" });
    expect(await within(pr).findByText("@ana · approved")).toBeInTheDocument();
    expect(within(pr).getByText("@bo · requested")).toBeInTheDocument();
    expect(within(pr).getByRole("link", { name: /PR #7/ })).toHaveAttribute(
      "href",
      "https://github.com/acme/app/pull/7",
    );
    expect(
      within(d).queryByRole("button", { name: "Pick a flow for me" }),
    ).toBeNull(); // in review: no new flow
    await user.click(within(pr).getByRole("button", { name: "Review it" }));
    await waitFor(() =>
      expect(calls("POST", "/api/tasks/tk_2/review")).toHaveLength(1),
    );

    await user.click(within(d).getByRole("button", { name: "More actions" }));
    await user.click(within(d).getByRole("button", { name: "Send back" }));
    const box = within(d).getByRole("group", { name: "Send back" });
    expect(
      within(box).getByRole("button", { name: "Send back" }),
    ).toBeDisabled();
    await user.type(
      within(box).getByLabelText("Why does it go back?"),
      "the totals are off",
    );
    await user.click(within(box).getByRole("button", { name: "Send back" }));
    await waitFor(() =>
      expect(calls("POST", "/api/tasks/tk_2/status")).toHaveLength(1),
    );
    expect(calls("POST", "/api/tasks/tk_2/status")[0].body).toEqual({
      to: "in_progress",
      note: "the totals are off",
    });
  });

  it("cancels and deletes only after a second click; a done task reopens", async () => {
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    await user.click(
      await within(d).findByRole("button", { name: "More actions" }),
    );
    await user.click(within(d).getByRole("button", { name: "Cancel task" }));
    expect(calls("POST", "/api/tasks/tk_1/status")).toHaveLength(0);
    await user.click(
      within(within(d).getByRole("group", { name: "Confirm" })).getByRole(
        "button",
        { name: "Cancel task" },
      ),
    );
    await waitFor(() =>
      expect(calls("POST", "/api/tasks/tk_1/status")[0]?.body).toEqual({
        to: "cancelled",
      }),
    );
    await user.click(await within(d).findByRole("button", { name: "Reopen" }));
    await waitFor(() =>
      expect(calls("POST", "/api/tasks/tk_1/status")[1]?.body).toEqual({
        to: "todo",
      }),
    );
    await user.click(await within(d).findByRole("button", { name: "Delete" }));
    await user.click(
      within(within(d).getByRole("group", { name: "Confirm" })).getByRole(
        "button",
        { name: "Delete" },
      ),
    );
    await waitFor(() =>
      expect(calls("DELETE", "/api/tasks/tk_1")).toHaveLength(1),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("folds a long description", async () => {
    db.tk.tasks[0].description = "Rank the players. ".repeat(30);
    const user = userEvent.setup();
    const d = await openTask("tk_1", "ABC-1 · Rank players weekly");
    const desc = within(d).getByRole("region", { name: "Description" });
    expect(desc.textContent!.length).toBeLessThan(400);
    await user.click(within(desc).getByRole("button", { name: "Show more" }));
    expect(desc.textContent!.length).toBeGreaterThan(500);
  });
});
