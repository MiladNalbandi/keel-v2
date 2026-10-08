// keel Product's pages in keel's web: the menu and the View, the board, a new idea, an initiative at each kind of stop
// (keel's questions, a gate, the decision, the hand-off), disagreements, follow-ups, and the teams with their pages.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../../../web/src/App";
import type { Features } from "../../../web/src/api";
import { db, server } from "../../../web/src/test/setup";
import type { BoardItem, Detail, Team, TeamKnowledge } from "../productApi";

const PRODUCT: Features = {
  mode: "both",
  modes: ["dev", "product", "both"],
  parts: { dev: true, product: true },
  addons: [
    {
      name: "product",
      version: "0.1.0-beta.1",
      title: "keel Product",
      part: "product",
      on: true,
    },
  ],
  screens: [
    {
      id: "initiatives",
      label: "Initiatives",
      group: "Product",
      needs_project: false,
      addon: "product",
    },
    {
      id: "teams",
      label: "Teams",
      group: "Product",
      needs_project: false,
      addon: "product",
    },
  ],
};

const main = () => within(document.getElementById("main")!);

const board: BoardItem[] = [
  {
    id: "INI-2",
    title: "Euro prices",
    stage: "decision",
    status: "waiting",
    waiting: true,
    teams: ["payments", "web"],
    next: "Go or not now",
    updated_at: "2026-10-08T10:00:00Z",
  },
  {
    id: "INI-1",
    title: "Gift cards",
    stage: "delivery",
    status: "ready",
    waiting: false,
    teams: ["web"],
    next: "The teams build it",
    progress: { done: 1, total: 4 },
    updated_at: "2026-10-07T10:00:00Z",
  },
  {
    id: "INI-3",
    title: "Dark mode",
    stage: "brief",
    status: "parked",
    waiting: false,
    teams: [],
    next: "Comes back 2027-01-01",
    revisit_at: "2027-01-01T09:00:00Z",
    updated_at: "2026-10-06T10:00:00Z",
  },
];

function detail(
  over: Partial<Detail["initiative"]> = {},
  rest: Partial<Detail> = {},
): Detail {
  const initiative = {
    id: "INI-2",
    n: 2,
    title: "Euro prices",
    idea: "Show prices in euro to EU visitors.",
    owner: "Mia",
    stage: "brief",
    status: "running",
    repos: ["web-shop"],
    created_at: "2026-10-08T09:00:00Z",
    updated_at: "2026-10-08T10:00:00Z",
    ...over,
  };
  return {
    initiative,
    stage: {
      stage: initiative.stage,
      status: initiative.status,
      thread_id: "t-1",
      waiting: null,
      next: "keel works on the brief",
    },
    docs: {},
    questions: [],
    disagreements: [],
    runs: [],
    history: [],
    follow_ups: [],
    plan: null,
    repos: [
      {
        project_id: "web-shop",
        name: "web-shop",
        root: "/r/web",
        teams: ["web"],
      },
    ],
    teams: ["web"],
    ...rest,
  };
}

type Call = { method: string; path: string; body: unknown };

/** keel Product's endpoints: answers per path, and every call written down. */
function productApi(answers: Record<string, unknown>): Call[] {
  const calls: Call[] = [];
  const answer = async (req: Request) => {
    const url = new URL(req.url);
    let body: unknown = null;
    try {
      body = await req.clone().json();
    } catch {
      /* no body */
    }
    calls.push({ method: req.method, path: url.pathname, body });
    const key = `${req.method} ${url.pathname}`;
    const a = answers[key] ?? answers[url.pathname];
    if (a === undefined)
      return HttpResponse.json(
        { error: `no answer for ${key}` },
        { status: 404 },
      );
    return HttpResponse.json(
      (typeof a === "function" ? (a as (b: unknown) => unknown)(body) : a) as Record<string, unknown>,
    );
  };
  server.use(
    http.all("/api/initiatives*", ({ request }) => answer(request)),
    http.all("/api/teams*", ({ request }) => answer(request)),
    http.all("/api/product*", ({ request }) => answer(request)),
  );
  return calls;
}

async function open(hash: string, answers: Record<string, unknown>) {
  db.features = PRODUCT;
  const calls = productApi(answers);
  location.hash = hash;
  render(<App />);
  return calls;
}

describe("keel Product in keel's web", () => {
  it("adds a Product group to the menu, and the View shows Product, Dev or both", async () => {
    await open("#/initiatives", { "/api/initiatives": board });
    await main().findByRole("heading", { name: "Initiatives", level: 1 });
    const nav = screen.getByRole("navigation", { name: "Screens" });
    const links = within(nav)
      .getAllByRole("link")
      .map((a) => a.textContent ?? "");
    expect(links.indexOf("Initiatives")).toBeLessThan(links.indexOf("Flow"));
    expect(links).toContain("Teams");
    const view = screen.getByRole("group", { name: "View" });
    await userEvent.click(
      within(view).getByRole("button", { name: "Product" }),
    );
    await waitFor(() =>
      expect(
        within(nav).queryByRole("link", { name: "Flow" }),
      ).not.toBeInTheDocument(),
    );
    expect(
      within(nav).getByRole("link", { name: /^Inbox/ }),
    ).toBeInTheDocument();
    expect(localStorage.getItem("keel2.view")).toBe("product");
    await userEvent.click(within(view).getByRole("button", { name: "Dev" }));
    await waitFor(() =>
      expect(
        within(nav).queryByRole("link", { name: "Initiatives" }),
      ).not.toBeInTheDocument(),
    );
    expect(within(nav).getByRole("link", { name: "Flow" })).toBeInTheDocument();
  });

  it("shows the initiatives on a board by stage, the parked ones apart, and opens one", async () => {
    await open("#/initiatives", {
      "/api/initiatives": board,
      "/api/initiatives/INI-2": detail({
        stage: "decision",
        status: "waiting",
      }),
    });
    const decision = await main().findByTestId("stage-decision");
    const card = within(decision).getByRole("button", {
      name: "INI-2: Euro prices",
    });
    expect(card).toHaveTextContent("waits for you");
    expect(card).toHaveTextContent("payments");
    expect(card).toHaveTextContent("Go or not now");
    expect(
      within(main().getByTestId("stage-delivery")).getByRole("button", {
        name: "INI-1: Gift cards",
      }),
    ).toHaveTextContent("The teams build it");
    expect(main().getByRole("region", { name: "Not now" })).toHaveTextContent(
      "Dark mode · back 2027-01-01",
    );
    expect(
      within(main().getByTestId("stage-brief")).queryByText("Dark mode"),
    ).not.toBeInTheDocument();
    await userEvent.click(
      main().getByRole("tab", { name: "All, with history" }),
    );
    expect(main().getByRole("table")).toHaveTextContent("Gift cards");
    await userEvent.click(main().getByRole("tab", { name: "Board" }));
    await userEvent.click(
      within(main().getByTestId("stage-decision")).getByRole("button", {
        name: "INI-2: Euro prices",
      }),
    );
    await waitFor(() => expect(location.hash).toBe("#/initiatives/INI-2"));
  });

  it("writes a new idea with its repos and starts discovery", async () => {
    const calls = await open("#/initiatives", {
      "GET /api/initiatives": [],
      "/api/initiatives/repos": [
        {
          project_id: "web-shop",
          name: "web-shop",
          root: "/r/web",
          teams: ["web"],
        },
        { project_id: "pay", name: "payments-api", root: "/r/pay", teams: [] },
      ],
      "POST /api/initiatives": detail({ id: "INI-4" }),
      "/api/initiatives/INI-4": detail({ id: "INI-4" }),
    });
    expect(await main().findByText(/No initiative yet/)).toBeInTheDocument();
    await userEvent.click(
      main().getByRole("button", { name: "New initiative" }),
    );
    const d = await screen.findByRole("dialog", { name: "New initiative" });
    const start = within(d).getByRole("button", { name: "Start discovery" });
    expect(start).toBeDisabled();
    await userEvent.type(within(d).getByLabelText("Title"), "Euro prices");
    await userEvent.type(
      within(d).getByLabelText("The idea"),
      "Show prices in euro.",
    );
    await userEvent.click(
      await within(d).findByRole("checkbox", { name: /payments-api/ }),
    );
    expect(within(d).getByText("no team yet")).toBeInTheDocument();
    await userEvent.click(start);
    await waitFor(() => expect(location.hash).toBe("#/initiatives/INI-4"));
    const post = calls.find(
      (c) => c.method === "POST" && c.path === "/api/initiatives",
    )!;
    expect(post.body).toMatchObject({
      title: "Euro prices",
      idea: "Show prices in euro.",
      repos: ["pay"],
      start: true,
    });
  });

  it("answers keel's questions at the brief, and sends a brief back only with a note", async () => {
    const asking = detail(
      { status: "waiting" },
      {
        stage: {
          stage: "brief",
          status: "waiting",
          thread_id: "t-1",
          next: "Answer keel's questions",
          waiting: {
            kind: "clarify",
            title: "product manager has 1 question",
            questions: [
              {
                id: "who",
                question: "Who sees euro?",
                why: "scope",
                options: [
                  { label: "EU only", recommended: true },
                  { label: "EU and UK" },
                ],
              },
            ],
          },
        },
      },
    );
    const gate = detail(
      { status: "waiting" },
      {
        stage: {
          stage: "brief",
          status: "waiting",
          thread_id: "t-1",
          next: "Approve the brief",
          waiting: { kind: "gate", title: "Approve the brief" },
        },
        docs: {
          brief: {
            kind: "brief",
            version: 2,
            versions: [1, 2],
            path: "initiatives/INI-2/brief-v2.md",
            text: "# Brief\n\nEuro for the EU and the UK.",
            created_at: "2026-10-08T10:00:00Z",
          },
        },
        questions: [
          {
            id: "q1",
            initiative_id: "INI-2",
            stage: "brief",
            role: "keel-asked",
            asked_to: "you",
            text: "Who sees euro?",
            answer: "EU and UK",
            answered_by: "you",
            status: "answered",
            created_at: "",
            answered_at: "",
          },
        ],
      },
    );
    const calls = await open("#/initiatives/INI-2", {
      "GET /api/initiatives/INI-2": asking,
      "POST /api/initiatives/INI-2/approve": (b: { answers?: unknown }) =>
        b.answers ? gate : detail({ status: "running" }),
      "POST /api/initiatives/INI-2/send-back": detail({ status: "running" }, { questions: gate.questions }),
      "GET /api/initiatives/INI-2/docs/brief/1": {
        initiative_id: "INI-2",
        kind: "brief",
        version: 1,
        path: "initiatives/INI-2/brief-v1.md",
        text: "# Brief\n\nEuro for the EU.",
        created_at: "",
      },
    });
    const next = await main().findByRole("region", { name: "Next" });
    expect(next).toHaveTextContent("Answer keel's questions");
    await userEvent.click(
      within(next).getByRole("radio", { name: /EU and UK/ }),
    );
    await userEvent.click(
      within(next).getByRole("button", { name: "Send my answers" }),
    );
    await waitFor(() =>
      expect(
        calls.some((c) => c.path === "/api/initiatives/INI-2/approve"),
      ).toBe(true),
    );
    expect(
      calls.find((c) => c.path === "/api/initiatives/INI-2/approve")!.body,
    ).toEqual({ answers: { who: "EU and UK" } });

    const gateCard = await main().findByRole("region", { name: "Next" });
    await waitFor(() =>
      expect(gateCard).toHaveTextContent("Approve the brief"),
    );
    expect(main().getByText("Euro for the EU and the UK.")).toBeInTheDocument();
    await userEvent.selectOptions(main().getByLabelText("Version"), "1");
    expect(await main().findByText("Euro for the EU.")).toBeInTheDocument();
    const back = within(gateCard).getByRole("button", { name: "Send back" });
    expect(back).toBeDisabled();
    await userEvent.type(
      within(gateCard).getByLabelText("What to change"),
      "Add the price rounding",
    );
    await userEvent.click(back);
    await waitFor(() =>
      expect(
        calls.find((c) => c.path === "/api/initiatives/INI-2/send-back")?.body,
      ).toEqual({ note: "Add the price rounding" }),
    );
    await userEvent.click(main().getByRole("tab", { name: "Questions" }));
    expect(main().getByRole("tabpanel")).toHaveTextContent(/Who sees euro\?.*keel asked · Brief.*EU and UK — you/);
  });

  it("decides Go with an option, writes a disagreement down and settles it", async () => {
    const waiting = detail(
      { stage: "decision", status: "waiting" },
      {
        stage: {
          stage: "decision",
          status: "waiting",
          thread_id: "t-3",
          next: "Go or not now",
          waiting: { kind: "gate", title: "Go or not now" },
        },
        docs: {
          decision: {
            kind: "decision",
            version: 1,
            versions: [1],
            path: "initiatives/INI-2/decision-v1.md",
            text: "# Memo\n\nOption B.",
            created_at: "",
          },
          deck: {
            kind: "deck",
            version: 1,
            versions: [1],
            path: "initiatives/INI-2/deck-v1.html",
            text: "",
            created_at: "",
          },
        },
      },
    );
    let disagreements: Detail["disagreements"] = [];
    const calls = await open("#/initiatives/INI-2", {
      "GET /api/initiatives/INI-2": () => ({ ...waiting, disagreements }),
      "POST /api/initiatives/INI-2/disagreements": (b: { reason: string }) => {
        disagreements = [
          {
            id: "dis-1",
            initiative_id: "INI-2",
            stage: "decision",
            author: "Bo",
            reason: b.reason,
            decider: "po",
            status: "open",
            created_at: "",
          },
        ];
        return disagreements[0];
      },
      "POST /api/initiatives/INI-2/disagreements/dis-1/settle": (b: {
        outcome: string;
      }) => {
        disagreements = [
          { ...disagreements[0], status: "settled", outcome: b.outcome },
        ];
        return disagreements[0];
      },
      "POST /api/initiatives/INI-2/decide": detail({
        stage: "plan",
        status: "running",
        option: "B",
      }),
    });
    expect(
      await main().findByRole("link", { name: "Presentation v1" }),
    ).toHaveAttribute("href", "/api/initiatives/INI-2/deck");
    await userEvent.click(main().getByRole("button", { name: "I disagree…" }));
    await userEvent.type(main().getByLabelText("Who disagrees"), "Bo");
    await userEvent.type(
      main().getByLabelText("Why you disagree"),
      "Option A is cheaper",
    );
    await userEvent.click(
      main().getByRole("button", { name: "Write it down" }),
    );
    const dis = await main()
      .findByRole("region", { name: "Disagreements" })
      .catch(() => main().findByText(/Option A is cheaper/));
    expect(dis).toBeInTheDocument();
    await userEvent.type(
      await main().findByLabelText("Decision on: Option A is cheaper"),
      "B stays: A can not do the UK",
    );
    await userEvent.click(main().getByRole("button", { name: "Decide" }));
    expect(
      await main().findByText("B stays: A can not do the UK"),
    ).toBeInTheDocument();

    const next = main().getByRole("region", { name: "Next" });
    await userEvent.type(within(next).getByLabelText("Option"), "B");
    await userEvent.type(within(next).getByLabelText("Why"), "the UK too");
    await userEvent.click(within(next).getByRole("button", { name: "Go" }));
    await waitFor(() =>
      expect(
        calls.find((c) => c.path === "/api/initiatives/INI-2/decide")?.body,
      ).toEqual({ choice: "go", option: "B", why: "the UK too" }),
    );
  });

  it("sends the agreed plan's stories to keel Tasks and shows the plan, the delivery and the history", async () => {
    const plan = {
      version: 1,
      ok: true,
      problems: [],
      critical_path: ["PAY-S1", "PAY-S2"],
      critical_days: 5,
      teams: { payments: [3, 5] },
      total_days: [5, 7],
      epics: [
        {
          id: "PAY",
          team: "payments",
          title: "Prices API v2",
          stories: [
            {
              id: "PAY-S1",
              epic: "PAY",
              team: "payments",
              repo: "payments-api",
              project_id: "pay",
              title: "Prices in euro",
              criteria: ["AC-1 [API] currency"],
              tasks: ["Add currency column"],
              depends_on: [],
              estimate_days: [2, 3],
              task_id: null,
              jira_key: null,
            },
            {
              id: "PAY-S2",
              epic: "PAY",
              team: "payments",
              repo: "payments-api",
              project_id: "pay",
              title: "Rates job",
              criteria: ["AC-1 [API] daily"],
              tasks: [],
              depends_on: ["PAY-S1"],
              estimate_days: [1, 2],
              task_id: null,
              jira_key: null,
            },
          ],
        },
      ],
    };
    let sent = false;
    const ready = () =>
      detail(
        { stage: "delivery", status: "ready", option: "B" },
        {
          stage: {
            stage: "delivery",
            status: "ready",
            next: sent
              ? "The teams build it"
              : "Send the stories to Tasks or Jira",
          },
          plan: sent
            ? {
                ...plan,
                progress: { done: 0, total: 2 },
                epics: plan.epics.map((e) => ({
                  ...e,
                  stories: e.stories.map((s, n) => ({
                    ...s,
                    task_id: `tk_${n}`,
                    task_status: "todo",
                  })),
                })),
              }
            : plan,
          history: [
            {
              id: 2,
              initiative_id: "INI-2",
              at: "2026-10-08T11:00:00Z",
              actor: "you",
              kind: "decision",
              text: "Go with option B",
            },
            {
              id: 1,
              initiative_id: "INI-2",
              at: "2026-10-08T09:00:00Z",
              actor: "you",
              kind: "created",
              text: "Created the idea",
            },
          ],
        },
      );
    const calls = await open("#/initiatives/INI-2", {
      "GET /api/initiatives/INI-2": () => ready(),
      "POST /api/initiatives/INI-2/handoff": () => {
        sent = true;
        return { tasks: ["tk_0", "tk_1"], jira: [], skipped: [], notes: [] };
      },
      "POST /api/initiatives/INI-2/released": () =>
        detail({ stage: "outcome", status: "ready" }),
    });
    const next = await main().findByRole("region", { name: "Next" });
    expect(next).toHaveTextContent("Send the stories to Tasks or Jira");
    expect(
      main().getByRole("tab", { name: "Delivery", selected: true }),
    ).toBeInTheDocument();
    await userEvent.click(
      within(next).getByRole("button", { name: "Send to keel Tasks" }),
    );
    await waitFor(() =>
      expect(
        calls.find((c) => c.path === "/api/initiatives/INI-2/handoff")?.body,
      ).toEqual({ target: "tasks" }),
    );
    expect(
      await within(main().getByRole("region", { name: "Next" })).findByRole(
        "button",
        { name: "It is released" },
      ),
    ).toBeInTheDocument();
    expect(main().getByText("0 of 2 stories done")).toBeInTheDocument();
    expect(main().getAllByRole("link", { name: "todo" })[0]).toHaveAttribute(
      "href",
      "#/tasks/tk_0",
    );

    await userEvent.click(main().getByRole("tab", { name: "Plan" }));
    expect(
      main().getByText(/critical path PAY-S1 → PAY-S2/),
    ).toBeInTheDocument();
    expect(main().getByText("AC-1 [API] currency")).toBeInTheDocument();
    expect(main().getByText("payments: 3–5 d")).toBeInTheDocument();
    await userEvent.click(main().getByRole("tab", { name: "History" }));
    const items = within(main().getByRole("tabpanel"))
      .getAllByRole("listitem")
      .map((l) => l.textContent);
    expect(items[0]).toContain("Go with option B");
    expect(items[1]).toContain("Created the idea");
  });

  it("adds and finishes follow-ups, and parks an idea until a date", async () => {
    let fus: Detail["follow_ups"] = [
      {
        id: "fu-1",
        initiative_id: "INI-2",
        kind: "gate",
        text: "Answer: Approve the brief",
        due_at: "2026-10-10T09:00:00Z",
        created_at: "",
      },
    ];
    const calls = await open("#/initiatives/INI-2", {
      "GET /api/initiatives/INI-2": () =>
        detail({ status: "ready" }, { follow_ups: fus }),
      "POST /api/initiatives/INI-2/follow-ups": (b: {
        text: string;
        due_at: string;
      }) => {
        const f = {
          id: "fu-2",
          initiative_id: "INI-2",
          kind: "manual",
          text: b.text,
          due_at: `${b.due_at}T09:00:00Z`,
          created_at: "",
        };
        fus = [...fus, f];
        return f;
      },
      "POST /api/initiatives/INI-2/follow-ups/fu-1/done": () => {
        fus = fus.map((f) => (f.id === "fu-1" ? { ...f, done_at: "now" } : f));
        return fus[0];
      },
      "POST /api/initiatives/INI-2/park": detail({
        status: "parked",
        revisit_at: "2027-01-15T09:00:00Z",
      }),
    });
    const panel = (
      await main().findByRole("heading", { name: "Follow-ups" })
    ).closest("section")!;
    expect(panel).toHaveTextContent("Answer: Approve the brief");
    await userEvent.type(
      within(panel).getByLabelText("Follow up on"),
      "Ask the shop team",
    );
    await userEvent.type(within(panel).getByLabelText("When"), "2026-11-01");
    await userEvent.click(within(panel).getByRole("button", { name: "Add" }));
    expect(
      await within(panel).findByText("Ask the shop team"),
    ).toBeInTheDocument();
    await userEvent.click(
      within(panel).getByRole("button", {
        name: "Done: Answer: Approve the brief",
      }),
    );
    await waitFor(() =>
      expect(
        within(panel).queryByText("Answer: Approve the brief"),
      ).not.toBeInTheDocument(),
    );

    await userEvent.click(main().getByRole("button", { name: "Not now…" }));
    await userEvent.type(main().getByLabelText("Look again on"), "2027-01-15");
    await userEvent.click(main().getByRole("button", { name: "Park it" }));
    await waitFor(() =>
      expect(
        calls.find((c) => c.path === "/api/initiatives/INI-2/park")?.body,
      ).toEqual({ revisit: "2027-01-15" }),
    );
    expect(
      await main().findByRole("button", { name: "Back from Not now" }),
    ).toBeInTheDocument();
  });

  it("lists the teams, reads CODEOWNERS, and a team accepts keel's lesson into its pages", async () => {
    const team: Team = {
      id: "payments",
      name: "payments",
      lead: "Bo",
      jira_project: "PAY",
      capacity_days: 12,
      members: ["bo", "eli"],
      source: "manual",
      owns: [
        {
          team_id: "payments",
          project_id: "pay",
          glob: "**",
          source: "manual",
        },
      ],
      created_at: "",
      updated_at: "",
    };
    let k: TeamKnowledge = {
      team,
      pages: [
        {
          name: "definition-of-ready",
          title: "Definition of ready",
          text: "Criteria first.",
        },
        { name: "lessons", title: "Lessons from past work", text: "" },
      ],
      suggestions: [
        {
          id: "sug-1",
          team_id: "payments",
          page: "lessons",
          text: "INI-1: rates need a cache.",
          source: "INI-1",
          status: "open",
          created_at: "",
        },
      ],
    };
    const calls = await open("#/teams", {
      "GET /api/teams": [team],
      "/api/initiatives/repos": [
        {
          project_id: "pay",
          name: "payments-api",
          root: "/r/pay",
          teams: ["payments"],
        },
        { project_id: "web-shop", name: "web-shop", root: "/r/web", teams: [] },
      ],
      "POST /api/teams/import-codeowners": {
        teams: ["web"],
        paths: 2,
        read: ["web-shop/.github/CODEOWNERS"],
        skipped: [],
      },
      "GET /api/teams/payments": () => k,
      "POST /api/teams/payments/suggestions/sug-1": () => {
        k = {
          ...k,
          pages: k.pages.map((p) =>
            p.name === "lessons"
              ? { ...p, text: "INI-1: rates need a cache." }
              : p,
          ),
          suggestions: [{ ...k.suggestions[0], status: "accepted" }],
        };
        return k;
      },
      "PUT /api/teams/payments/pages/definition-of-ready": (b: {
        text: string;
      }) => ({
        name: "definition-of-ready",
        title: "Definition of ready",
        text: b.text,
      }),
    });
    const card = await main().findByRole("button", { name: "Team payments" });
    expect(card).toHaveTextContent("lead Bo · Jira PAY · 12 d free");
    expect(
      await main().findByText(
        /keel cannot say who does the work in these: web-shop/,
      ),
    ).toBeInTheDocument();
    await userEvent.click(
      main().getByRole("button", { name: "Read CODEOWNERS" }),
    );
    expect(await main().findByRole("status")).toHaveTextContent(
      "Read web-shop/.github/CODEOWNERS: 1 team(s), 2 path(s).",
    );

    await userEvent.click(card);
    await waitFor(() => expect(location.hash).toBe("#/teams/payments"));
    expect(
      await main().findByRole("heading", { name: "payments", level: 1 }),
    ).toBeInTheDocument();
    await userEvent.click(
      main().getByRole("button", { name: "Add to the page" }),
    );
    await waitFor(() =>
      expect(main().getByLabelText("Lessons from past work")).toHaveValue(
        "INI-1: rates need a cache.",
      ),
    );
    const dor = main().getByLabelText("Definition of ready");
    await userEvent.type(dor, " Designs linked.");
    expect(main().getByText("not saved")).toBeInTheDocument();
    await userEvent.click(
      within(dor.closest("section")!).getByRole("button", { name: "Save" }),
    );
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
        text: "Criteria first. Designs linked.",
      }),
    );
    // deleting a team asks first
    await userEvent.click(main().getByRole("button", { name: "Delete" }));
    const ask = main().getByRole("group", { name: "Confirm" });
    expect(ask).toHaveTextContent("Delete the team payments?");
    await userEvent.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });
});
