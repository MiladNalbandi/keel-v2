// keel Product's pages in keel's web: the menu and the View, the board, a new idea, an initiative at each kind of stop
// (keel's questions, a gate, the decision, the hand-off), disagreements, follow-ups, and the teams with their pages.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
// keel's own app and test harness: this test runs Product inside keel's web, as keel loads it
import { setAddonImporter } from "../../../web/src/addons";
import { App } from "../../../web/src/App";
import type { Features } from "../../../web/src/api";
import { db, server } from "../../../web/src/test/setup";
import type {
  BoardItem,
  Detail,
  InitiativeJira,
  JiraView,
  LinkedTicket,
  PlanView,
  ProductMap,
  Team,
  TeamKnowledge,
  TeamTickets,
  TicketPreview,
} from "../productApi";
import { allPageNames, sideMenu } from "../../../web/src/test/menu";

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
      id: "product-map",
      label: "Product map",
      group: "Product",
      needs_project: false,
      addon: "product",
    },
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
  plugins: [
    {
      name: "product",
      title: "keel Product",
      version: "0.1.0-beta.1",
      web: {
        entry: "/plugins/product/0.1.0-beta.1/web/index.js",
        css: ["/plugins/product/0.1.0-beta.1/web/style.css"],
      },
    },
  ],
};

/** keel loads Product's web part from its url; here that url is this folder's index.tsx, as it is. */
const loadProduct = (url: string) =>
  url === PRODUCT.plugins[0].web?.entry
    ? import("../index")
    : Promise.reject(new Error(`no module at ${url}`));

const main = () => within(document.getElementById("main")!);
const productCss = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "product.css"), "utf8");
/** The declarations of the first rule whose selector is exactly `sel` (outside media queries: the first one). */
const rule = (sel: string) => {
  const at = productCss.indexOf(`\n${sel} {`);
  expect(at, `no rule for ${sel}`).toBeGreaterThanOrEqual(0);
  return productCss.slice(productCss.indexOf("{", at) + 1, productCss.indexOf("}", at));
};

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
    const v = typeof a === "function" ? (a as (b: unknown) => unknown)(body) : a;
    // an api error ({error, hint} with its status) is answered as it is
    if (v instanceof Response) return v;
    return HttpResponse.json(v as Record<string, unknown>);
  };
  server.use(
    http.all("/api/initiatives*", ({ request }) => answer(request)),
    http.all("/api/teams*", ({ request }) => answer(request)),
    http.all("/api/product*", ({ request }) => answer(request)),
    http.all("/api/jira*", ({ request }) => answer(request)),
  );
  return calls;
}

async function open(hash: string, answers: Record<string, unknown>) {
  db.features = PRODUCT;
  setAddonImporter(loadProduct);
  const calls = productApi(answers);
  location.hash = hash;
  render(<App />);
  return calls;
}

describe("keel Product in keel's web", () => {
  it("adds a Product group to the menu, and the View shows Product, Dev or both", async () => {
    await open("#/initiatives", { "/api/initiatives": board });
    await main().findByRole("heading", { name: "Initiatives", level: 1 });
    // keel 0.28 All pages lists Product's group before keel's own; the menu's main places follow the view
    const links = await allPageNames();
    expect(links.indexOf("Initiatives")).toBeLessThan(links.indexOf("Flow"));
    expect(links).toContain("Teams");
    fireEvent.keyDown(document, { key: "Escape" });
    const nav = sideMenu();
    const view = screen.getByRole("group", { name: "View" });
    await userEvent.click(
      within(view).getByRole("button", { name: "Product" }),
    );
    await waitFor(() =>
      expect(
        within(nav).queryByRole("link", { name: /^Flow/ }),
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
    // keel 0.28 a main place's link names its count too ("Flow 1 running now")
    expect(within(nav).getByRole("link", { name: /^Flow/ })).toBeInTheDocument();
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
    // the note is a box of several lines, not a one-line input
    const what = within(gateCard).getByLabelText("What to change");
    expect(what.tagName).toBe("TEXTAREA");
    await userEvent.type(what, "Add the price rounding{enter}and the VAT line");
    await userEvent.click(back);
    await waitFor(() =>
      expect(
        calls.find((c) => c.path === "/api/initiatives/INI-2/send-back")?.body,
      ).toEqual({ note: "Add the price rounding\nand the VAT line" }),
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
    expect(
      calls.find((c) => c.path === "/api/initiatives/INI-2/disagreements")?.body,
    ).toMatchObject({ author: "Bo" });
    // the next disagreement is someone else's: the form starts empty, not with "Bo"
    await userEvent.click(main().getAllByRole("button", { name: "I disagree…" })[0]);
    expect(main().getByLabelText("Who disagrees")).toHaveValue("");
    expect(main().getByLabelText("Why you disagree")).toHaveValue("");
    await userEvent.click(main().getByRole("button", { name: "Cancel" }));
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
    // the stories are keel tasks now, but not Jira tickets yet: only Jira is left to send to
    const delivery = main().getByRole("tabpanel");
    expect(
      within(delivery).getByRole("button", { name: "Preview Jira tickets" }),
    ).toBeInTheDocument();
    expect(
      within(delivery).queryByRole("button", { name: "Send to keel Tasks" }),
    ).toBeNull();
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

  it("a team gets its projects, its own Jira and a ticket-key pattern it can try first", async () => {
    let team: Team = {
      id: "erp",
      name: "Orders",
      lead: "Sara",
      jira_project: "SHOPS",
      members: [],
      source: "manual",
      owns: [
        { team_id: "erp", project_id: "erp-core", glob: "**", source: "manual" },
      ],
      created_at: "",
      updated_at: "",
      jira: { connection: null, labels: [] },
      key_pattern: null,
      keys: "SHOPS-\\d+",
      projects: ["erp-core"],
    };
    const k = (): TeamKnowledge => ({ team, pages: [], suggestions: [] });
    const owns = (pids: string[]) =>
      pids.map((p) => ({
        team_id: "erp",
        project_id: p,
        glob: "**",
        source: "manual",
      }));
    const calls = await open("#/teams/erp", {
      "GET /api/teams/erp": () => k(),
      "/api/initiatives/repos": [
        { project_id: "erp-core", name: "erp-core", root: "/r/c", teams: ["erp"] },
        { project_id: "erp-ui", name: "erp-ui", root: "/r/u", teams: [] },
      ],
      "PUT /api/teams/erp/projects/erp-ui": () =>
        (team = { ...team, owns: owns(["erp-core", "erp-ui"]) }),
      "DELETE /api/teams/erp/projects/erp-core": () =>
        (team = { ...team, owns: owns(["erp-ui"]) }),
      "PATCH /api/teams/erp": (b: Partial<Team>) =>
        (team = { ...team, ...b, keys: b.key_pattern || team.keys }),
      "POST /api/teams/key-pattern/test": (b: { pattern: string; text: string }) =>
        b.pattern === "SHOPS-("
          ? HttpResponse.json(
              { error: "The pattern does not compile", hint: "Close the bracket." },
              { status: 400 },
            )
          : { pattern: b.pattern, keys: ["SHOPS-1234"] },
    });
    await userEvent.click(
      await main().findByRole("tab", { name: "Projects and Jira" }),
    );
    // projects: add one, take one out
    const projects = main().getByRole("heading", { name: "Projects" }).closest("section")!;
    expect(projects).toHaveTextContent("erp-core");
    await userEvent.selectOptions(
      within(projects).getByLabelText("Add a project"),
      "erp-ui",
    );
    await userEvent.click(within(projects).getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(within(projects).getByRole("list")).toHaveTextContent("erp-ui"),
    );
    await userEvent.click(
      within(projects).getByRole("button", { name: "Remove erp-core" }),
    );
    await waitFor(() =>
      expect(within(projects).getByRole("list")).not.toHaveTextContent(
        "erp-core",
      ),
    );
    expect(
      calls.filter((c) => c.path.startsWith("/api/teams/erp/projects")).map((c) => `${c.method} ${c.path}`),
    ).toEqual([
      "PUT /api/teams/erp/projects/erp-ui",
      "DELETE /api/teams/erp/projects/erp-core",
    ]);

    // its Jira
    const jira = main().getByRole("heading", { name: "Jira" }).closest("section")!;
    await userEvent.selectOptions(
      within(jira).getByLabelText("Jira connection"),
      "erp-ui",
    );
    await userEvent.type(within(jira).getByLabelText("Sub-task type"), "Subtask");
    await userEvent.type(
      within(jira).getByLabelText("Epic Link field (Jira Server)"),
      "customfield_10014",
    );
    await userEvent.type(within(jira).getByLabelText("Labels"), "erp, euro");
    await userEvent.type(within(jira).getByLabelText("Component"), "Prices");
    await userEvent.click(
      within(jira).getByRole("button", { name: "Save the Jira settings" }),
    );
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
        jira_project: "SHOPS",
        jira: {
          connection: "erp-ui",
          initiative_type: null,
          epic_type: null,
          story_type: null,
          subtask_type: "Subtask",
          epic_link_field: "customfield_10014",
          labels: ["erp", "euro"],
          component: "Prices",
        },
      }),
    );
    expect(await within(jira).findByText("Saved.")).toBeInTheDocument();

    // its ticket keys: the placeholder is the pattern keel uses now; try a branch name, then a broken pattern
    const keys = main().getByRole("heading", { name: "Ticket keys" }).closest("section")!;
    const pattern = within(keys).getByLabelText("Ticket-key pattern");
    expect(pattern).toHaveAttribute("placeholder", "SHOPS-\\d+");
    await userEvent.type(
      within(keys).getByLabelText("A branch name or commit message"),
      "feature/shops-1234-vat",
    );
    await userEvent.click(within(keys).getByRole("button", { name: "Try it" }));
    expect(await within(keys).findByLabelText("Keys found")).toHaveTextContent(
      "Found SHOPS-1234",
    );
    expect(
      calls.find((c) => c.path === "/api/teams/key-pattern/test")?.body,
    ).toEqual({ pattern: "SHOPS-\\d+", text: "feature/shops-1234-vat" });
    await userEvent.type(pattern, "SHOPS-(");
    await userEvent.click(within(keys).getByRole("button", { name: "Try it" }));
    expect(await within(keys).findByRole("alert")).toHaveTextContent(
      "The pattern does not compile",
    );
  });

  it("a team's Tickets list the keys keel found in git, what has no key, and scan again", async () => {
    const team: Team = {
      id: "erp",
      name: "Orders",
      members: [],
      source: "manual",
      owns: [],
      created_at: "",
      updated_at: "",
    };
    const link = (kind: string, ref: string, key: string, title?: string) => ({
      project_id: "erp-core",
      kind,
      ref,
      key,
      team_id: "erp",
      title,
      at: "2026-10-20T10:00:00Z",
    });
    const tickets: TeamTickets = {
      team: "erp",
      tickets: [
        {
          key: "SHOPS-1234",
          team: "erp",
          branches: [link("branch", "feature/shops-1234-vat", "SHOPS-1234")],
          commits: [
            link("commit", "abc12345ff", "SHOPS-1234", "SHOPS-1234 add vat"),
            link("commit", "def67890aa", "SHOPS-1234", "SHOPS-1234 tests"),
          ],
          last_at: "2026-10-20T10:00:00Z",
        },
      ],
      unlinked: [link("branch", "fix-typo", "")],
      scans: [
        {
          project_id: "erp-core",
          at: "2026-10-21T08:00:00Z",
          branches: 2,
          commits: 3,
          linked: 3,
        },
      ],
    };
    const calls = await open("#/teams/erp", {
      "GET /api/teams/erp": { team, pages: [], suggestions: [] },
      "/api/initiatives/repos": [],
      "GET /api/product/tickets": tickets,
      "POST /api/product/tickets/scan": { projects: tickets.scans },
    });
    await userEvent.click(await main().findByRole("tab", { name: "Tickets" }));
    const table = await main().findByRole("table", { name: "Tickets" });
    expect(table).toHaveTextContent("SHOPS-1234");
    expect(table).toHaveTextContent("feature/shops-1234-vat");
    const none = main().getByRole("region", { name: "No ticket key" });
    expect(none).toHaveTextContent("No ticket key (1)");
    expect(none).toHaveTextContent("branch fix-typo in erp-core");
    expect(main().getByLabelText("Scans")).toHaveTextContent(
      "erp-core: 2 branches, 3 commits, 3 with a key",
    );
    const loads = () =>
      calls.filter((c) => c.path === "/api/product/tickets").length;
    const before = loads();
    await userEvent.click(main().getByRole("button", { name: "Scan now" }));
    await waitFor(() => expect(loads()).toBe(before + 1));
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/product/tickets/scan")).toBe(true);
  });

  it("B5: with no keel-wide Jira, a team and the initiatives default to a project's own connection, and no second token is asked", async () => {
    const team: Team = {
      id: "erp", name: "Orders", lead: "Sara", jira_project: "SHOPS", members: [], source: "manual",
      owns: [{ team_id: "erp", project_id: "shop", glob: "**", source: "manual" }],
      created_at: "", updated_at: "", jira: { connection: null, labels: [] }, key_pattern: null, keys: "SHOPS-\\d+", projects: ["shop"],
    };
    const conns = {
      keel_wide: { project_id: null, name: "keel-wide (Jira for every project)", connected: false, kind: null, base_url: null, project_key: null },
      projects: [{ project_id: "shop", name: "shop", connected: true, kind: "cloud", base_url: "https://shop.example.net", project_key: "SHOPS" }],
      suggested: "shop",
    };
    const repos = [
      { project_id: "shop", name: "shop", root: "/r/shop", teams: ["erp"] },
      { project_id: "admin", name: "admin", root: "/r/admin", teams: [] },
    ];
    const notConnected: JiraView = { connected: false, settings: { kind: "cloud", base_url: "", email: null, project_key: null }, token_set: false, token_hint: null, me: null };
    const calls = await open("#/teams", {
      "GET /api/teams": [team],
      "/api/initiatives/repos": repos,
      "GET /api/jira": () => notConnected,
      "GET /api/product/jira": { connection: null, project: null, type: null },
      "PUT /api/product/jira": (b: unknown) => b,
      "GET /api/product/jira/connections": conns,
    });
    // Jira for every project: the project's connection works, so no token form until asked
    const card = (await main().findByRole("heading", { name: "Jira for every project" })).closest("section")!;
    expect(await within(card).findByTestId("keel-jira-reuse")).toHaveTextContent("Not needed now: shop (https://shop.example.net) has a Jira connection");
    expect(within(card).queryByLabelText("API token")).toBeNull();
    // Initiatives in Jira: the project's own connection, not "keel-wide (not connected)"
    const ini = main().getByRole("heading", { name: "Initiatives in Jira" }).closest("section")!;
    const conn = await within(ini).findByLabelText("Connection");
    await waitFor(() => expect(conn).toHaveValue("shop"));
    expect(within(ini).getByRole("option", { name: "keel-wide (Jira for every project) · not connected" })).toBeInTheDocument();
    expect(within(ini).queryByRole("option", { name: /admin/ })).toBeNull();          // no connection there
    expect(within(ini).getByTestId("conn-note")).toHaveTextContent("keel picked shop's own connection (no second token)");
    await userEvent.type(within(ini).getByLabelText("Initiative project key"), "PORT");
    await userEvent.click(within(ini).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT" && c.path === "/api/product/jira")?.body).toEqual({ connection: "shop", project: "PORT", type: null }),
    );
    // asked anyway: the token form
    await userEvent.click(within(card).getByRole("button", { name: "Set up a keel-wide connection" }));
    expect(within(card).getByLabelText("API token")).toBeInTheDocument();
  });

  it("B5: a team's Jira defaults to its project's own connection when keel-wide has none", async () => {
    const team: Team = {
      id: "erp", name: "Orders", lead: "Sara", jira_project: "SHOPS", members: [], source: "manual",
      owns: [{ team_id: "erp", project_id: "shop", glob: "**", source: "manual" }],
      created_at: "", updated_at: "", jira: { connection: null, labels: [] }, key_pattern: null, keys: "SHOPS-\\d+", projects: ["shop"],
    };
    const calls = await open("#/teams/erp", {
      "GET /api/teams/erp": { team, pages: [], suggestions: [] },
      "/api/initiatives/repos": [{ project_id: "shop", name: "shop", root: "/r/shop", teams: ["erp"] }],
      "GET /api/product/jira/connections": {
        keel_wide: { project_id: null, name: "keel-wide", connected: false },
        projects: [{ project_id: "shop", name: "shop", connected: true, base_url: "https://shop.example.net" }],
        suggested: "shop",
      },
      "PATCH /api/teams/erp": (b: Partial<Team>) => ({ ...team, ...b }),
    });
    await userEvent.click(await main().findByRole("tab", { name: "Projects and Jira" }));
    const jira = main().getByRole("heading", { name: "Jira" }).closest("section")!;
    await waitFor(() => expect(within(jira).getByLabelText("Jira connection")).toHaveValue("shop"));
    await userEvent.click(within(jira).getByRole("button", { name: "Save the Jira settings" }));
    await waitFor(() =>
      expect((calls.find((c) => c.method === "PATCH")?.body as { jira: { connection: string } }).jira.connection).toBe("shop"),
    );
  });

  it("saves the keel-wide Jira connection after a test, never shows its token, and where initiatives go", async () => {
    let view: JiraView = {
      connected: false,
      settings: { kind: "cloud", base_url: "", email: null, project_key: null },
      token_set: false,
      token_hint: null,
      me: null,
    };
    let ij: InitiativeJira = { connection: null, project: null, type: null };
    const calls = await open("#/teams", {
      "GET /api/teams": [],
      "/api/initiatives/repos": [],
      "GET /api/jira": () => view,
      "POST /api/jira/test": { ok: true, user: { name: "Sara" } },
      "PUT /api/jira": (b: { base_url: string; email: string; project_key: string }) =>
        (view = {
          connected: true,
          settings: { kind: "cloud", base_url: b.base_url, email: b.email, project_key: b.project_key },
          token_set: true,
          token_hint: "…x9Q",
          me: null,
        }),
      "GET /api/product/jira": () => ij,
      "PUT /api/product/jira": (b: typeof ij) => (ij = b),
    });
    const card = (await main().findByRole("heading", { name: "Jira for every project" })).closest("section")!;
    expect(await within(card).findByText("Not connected.")).toBeInTheDocument();
    await userEvent.type(within(card).getByLabelText("Site URL"), "https://acme.atlassian.net");
    await userEvent.type(within(card).getByLabelText("Email"), "sara@acme.io");
    await userEvent.type(within(card).getByLabelText("API token"), "tok-123");
    await userEvent.type(within(card).getByLabelText("Project key"), "SHOPS");
    await userEvent.click(within(card).getByRole("button", { name: "Test" }));
    expect(await within(card).findByText("Test OK: logged in as Sara.")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Save" }));
    const body = {
      kind: "cloud",
      base_url: "https://acme.atlassian.net",
      email: "sara@acme.io",
      project_key: "SHOPS",
      token: "tok-123",
    };
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT" && c.path === "/api/jira")?.body).toEqual(body),
    );
    expect(calls.find((c) => c.path === "/api/jira/test")?.body).toEqual(body);
    expect(
      await within(card).findByText("Connected: Jira Cloud · https://acme.atlassian.net"),
    ).toBeInTheDocument();
    const token = within(card).getByLabelText("API token");
    expect(token).toHaveValue("");
    expect(token).toHaveAttribute("placeholder", "…x9Q (saved)");
    expect(within(card).getByRole("button", { name: "Remove" })).toBeInTheDocument();

    const ini = main().getByRole("heading", { name: "Initiatives in Jira" }).closest("section")!;
    expect(ini).toHaveTextContent("the initiative id goes on every ticket as a label");
    await userEvent.type(await within(ini).findByLabelText("Initiative project key"), "PORT");
    await userEvent.click(within(ini).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT" && c.path === "/api/product/jira")?.body).toEqual({
        connection: null,
        project: "PORT",
        type: null,
      }),
    );
  });

  const draftPlan = (): PlanView => ({
    version: 2,
    ok: true,
    problems: [],
    critical_path: [],
    edited: true,
    initiative_key: "PORT-7",
    epics: [
      {
        id: "PAY",
        team: "payments",
        title: "Prices API v2",
        jira_key: "PAY-100",
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
            jira_key: "PAY-101",
            work: {
              key: "PAY-101",
              branches: [
                {
                  project_id: "pay",
                  kind: "branch",
                  ref: "feature/pay-101",
                  key: "PAY-101",
                },
              ],
              commits: [],
            },
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
          },
        ],
      },
    ],
  });

  it("keeps the plan table inside its column at 1280 px: it scrolls in its own box and Edit stays clickable", async () => {
    // keel 0.18.57 at 1280 px the plan table ran under the side panel and its Edit buttons could not be clicked
    await open("#/initiatives/INI-2", {
      "GET /api/initiatives/INI-2": () =>
        detail(
          { stage: "plan", status: "waiting" },
          {
            stage: { stage: "plan", status: "waiting", next: "The leads agree the plan", waiting: { kind: "gate", title: "Agree the plan" } },
            plan: draftPlan(),
          },
        ),
      "GET /api/teams": [],
    });
    await userEvent.click(await main().findByRole("tab", { name: "Plan" }));
    const epic = within(main().getByRole("tabpanel")).getByRole("region", { name: "Epic PAY" });
    const table = epic.querySelector("table.pd-table")!;
    expect(table.parentElement).toHaveClass("pd-table-wrap");
    const edit = within(epic).getByRole("button", { name: "Edit PAY-S2" });
    expect(edit.closest("td")).toHaveClass("pd-act");
    expect(table.querySelector("thead th:last-child")).toHaveClass("pd-act");
    // the plan's column may shrink below the table's width (grid items default to min-width: auto)
    expect(rule(".pd-main")).toMatch(/grid-template-columns: minmax\(0, 1fr\) 320px/);
    expect(rule(".pd-tab")).toMatch(/min-width: 0/);
    expect(rule(".pd-doc")).toMatch(/grid-template-columns: minmax\(0, 1fr\)/);
    expect(rule(".pd-epic")).toMatch(/min-width: 0/);
    // the table scrolls in its own box, and the actions stay at its right edge
    expect(rule(".pd-table-wrap")).toMatch(/overflow-x: auto/);
    expect(rule(".pd-table-wrap")).toMatch(/max-width: 100%/);
    expect(rule(".pd-table .pd-act")).toMatch(/position: sticky;.*right: 0/);
    expect(rule(".pd-table .pd-act")).toMatch(/background: var\(--panel\)/);
    await userEvent.click(edit);
    expect(within(epic).getByRole("group", { name: "Edit PAY-S2" })).toBeInTheDocument();
  });

  it("edits the plan's drafts while the leads look at it; a sent story stays as it is", async () => {
    const d = () =>
      detail(
        { stage: "plan", status: "waiting" },
        {
          stage: {
            stage: "plan",
            status: "waiting",
            next: "The leads agree the plan",
            waiting: { kind: "gate", title: "Agree the plan" },
          },
          plan: draftPlan(),
        },
      );
    const calls = await open("#/initiatives/INI-2", {
      "GET /api/initiatives/INI-2": () => d(),
      "GET /api/teams": [],
      "PATCH /api/initiatives/INI-2/plan/stories/PAY-S2": () => {
        const n = d();
        n.plan!.epics[0].stories[1].title = "Rates job, daily";
        return n;
      },
      "POST /api/initiatives/INI-2/plan/stories": () => {
        const n = d();
        n.plan!.epics[0].stories.push({
          ...n.plan!.epics[0].stories[1],
          id: "PAY-S3",
          title: "Rounding rule",
          jira_key: null,
        });
        return n;
      },
    });
    await userEvent.click(await main().findByRole("tab", { name: "Plan" }));
    const tab = main().getByRole("tabpanel");
    expect(within(tab).getByText("edited by hand")).toBeInTheDocument();
    expect(within(tab).getByText("PORT-7")).toBeInTheDocument();
    const epic = within(tab).getByRole("region", { name: "Epic PAY" });
    expect(within(epic).getByText("PAY-100")).toBeInTheDocument();
    // the sent story shows its key and cannot change here
    expect(within(epic).queryByRole("button", { name: "Edit PAY-S1" })).toBeNull();
    expect(within(epic).getByText("PAY-101")).toBeInTheDocument();

    await userEvent.click(within(epic).getByRole("button", { name: "Edit PAY-S2" }));
    const form = within(epic).getByRole("group", { name: "Edit PAY-S2" });
    const title = within(form).getByLabelText("Title");
    await userEvent.clear(title);
    await userEvent.type(title, "Rates job, daily");
    await userEvent.type(
      within(form).getByLabelText("Criteria (one per line)"),
      "{enter}AC-2 [[API] at 06:00",
    );
    await userEvent.type(within(form).getByLabelText("Tasks (one per line)"), "Cron job");
    const high = within(form).getByLabelText("Days, at most");
    await userEvent.clear(high);
    await userEvent.type(high, "3");
    await userEvent.click(within(form).getByRole("button", { name: "Save the story" }));
    await waitFor(() =>
      expect(
        calls.find((c) => c.method === "PATCH")?.body,
      ).toEqual({
        title: "Rates job, daily",
        criteria: ["AC-1 [API] daily", "AC-2 [API] at 06:00"],
        tasks: ["Cron job"],
        estimate_days: [1, 3],
      }),
    );
    expect(await within(epic).findByText("Rates job, daily")).toBeInTheDocument();

    // a new story in the epic
    await userEvent.click(within(epic).getByRole("button", { name: "Add a story to PAY" }));
    const add = within(epic).getByRole("group", { name: "New story in PAY" });
    await userEvent.type(within(add).getByLabelText("Title"), "Rounding rule");
    await userEvent.click(within(add).getByRole("button", { name: "Add the story" }));
    await waitFor(() =>
      expect(
        calls.find((c) => c.method === "POST" && c.path === "/api/initiatives/INI-2/plan/stories")?.body,
      ).toEqual({ epic: "PAY", title: "Rounding rule" }),
    );
    expect(await within(epic).findByText("Rounding rule")).toBeInTheDocument();
  });

  it("P5: each save of the plan is a version: the picker shows an older one as it was", async () => {
    const d = () =>
      detail(
        { stage: "delivery", status: "ready" },
        {
          docs: {
            plan: { kind: "plan", version: 2, versions: [1, 2], path: "initiatives/INI-2/plan-v2.json", text: "", created_at: "2026-10-08T10:00:00Z" },
          },
          plan: { ...draftPlan(), total_days: [8, 11], critical_days: 9, critical_path: ["PAY-S1", "PAY-S2"] },
        },
      );
    await open("#/initiatives/INI-2", {
      "GET /api/initiatives/INI-2": () => d(),
      "GET /api/teams": [],
      "GET /api/initiatives/INI-2/docs/plan/1": {
        initiative_id: "INI-2", kind: "plan", version: 1, path: "initiatives/INI-2/plan-v1.json", text: "", created_at: "2026-10-08T09:00:00Z",
        data: {
          plan: { epics: [{ id: "PAY", title: "Prices API v2", team: "payments", stories: [
            { id: "PAY-S1", title: "Prices in euro", repo: "payments-api", estimate_days: [2, 3], depends_on: [], criteria: ["AC-1 [API] currency"] },
            { id: "PAY-S2", title: "Rates job", repo: "payments-api", estimate_days: [1, 2], depends_on: ["PAY-S1"], criteria: [] },
          ] }] },
          counts: { epics: 1, stories: 2, tasks: 1 }, total_days: [3, 5], critical_path: ["PAY-S1", "PAY-S2"], critical_days: 5, problems: [],
        },
      },
    });
    await userEvent.click(await main().findByRole("tab", { name: "Plan" }));
    const tab = main().getByRole("tabpanel");
    expect(within(tab).getByText(/keel checked it again/)).toBeInTheDocument();
    const pick = within(tab).getByRole("combobox", { name: "Plan version" });
    expect(within(pick).getAllByRole("option").map((o) => o.textContent)).toEqual(["v2 (latest)", "v1"]);
    await userEvent.selectOptions(pick, "1");
    expect(await within(tab).findByText(/3–5 d in total/)).toBeInTheDocument();
    expect(within(tab).getByText(/Critical path: PAY-S1 → PAY-S2 \(5 days\)/)).toBeInTheDocument();
    expect(within(tab).getByText("an older version, read only")).toBeInTheDocument();
    expect(within(tab).queryByRole("button", { name: "Edit PAY-S2" })).toBeNull();
  });

  it("previews the Jira tickets, then sends the new ones after a confirm", async () => {
    let sent = false;
    const plan = () => {
      const p = draftPlan();
      if (sent) p.epics[0].stories[1].jira_key = "PAY-102";
      return p;
    };
    const preview: TicketPreview = {
      rows: [
        { level: "initiative", ref: "INI-2", title: "INI-2 · Euro prices", project: "PORT", type: "Initiative", key: "PORT-7", action: "exists" },
        { level: "epic", ref: "PAY", title: "INI-2 · PAY · Prices API v2", project: "PAY", type: "Epic", key: "PAY-100", action: "exists" },
        { level: "story", ref: "PAY-S2", title: "PAY-S2 · Rates job", project: "PAY", type: "Story", action: "create" },
        { level: "subtask", ref: "PAY-S2/1", title: "Cron job", project: "PAY", type: null, action: "blocked", problem: "the project PAY has no sub-task type" },
      ],
      problems: ["the project PAY has no sub-task type"],
      notes: ["PAY-S2: no acceptance criteria"],
    };
    const calls = await open("#/initiatives/INI-2", {
      "GET /api/initiatives/INI-2": () =>
        detail(
          { stage: "delivery", status: "ready" },
          {
            stage: { stage: "delivery", status: "ready", next: "The teams build it" },
            plan: plan(),
          },
        ),
      "GET /api/initiatives/INI-2/tickets/preview": preview,
      "POST /api/initiatives/INI-2/tickets/send": () => {
        sent = true;
        return { created: [], jira: ["PAY-102"], notes: ["PAY-S2 sub-task: no type"], preview };
      },
    });
    const tab = await main().findByRole("tabpanel");
    // a sent story shows its key and the work keel found for it
    expect(await within(tab).findByText("1 branches · 0 commits")).toBeInTheDocument();
    expect(within(tab).queryByRole("button", { name: "Send to Jira" })).toBeNull();
    await userEvent.click(within(tab).getByRole("button", { name: "Preview Jira tickets" }));
    const table = await within(tab).findByRole("table", { name: "Ticket preview" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      "INI-2 · Euro pricesPORTInitiativePORT-7",
      "INI-2 · PAY · Prices API v2PAYEpicPAY-100",
      "PAY-S2 · Rates jobPAYStorycreate",
      "Cron jobPAY—blockedthe project PAY has no sub-task type",
    ]);
    expect(within(rows[3]).getByRole("cell", { name: "Cron job" })).toHaveClass("pd-lv-subtask");
    expect(within(tab).getByLabelText("Problems")).toHaveTextContent("the project PAY has no sub-task type");
    expect(within(tab).getByText("PAY-S2: no acceptance criteria")).toBeInTheDocument();

    await userEvent.click(within(tab).getByRole("button", { name: "Send 1 tickets to Jira" }));
    const ask = within(tab).getByRole("group", { name: "Confirm" });
    expect(calls.some((c) => c.path.endsWith("/tickets/send"))).toBe(false);
    await userEvent.click(within(ask).getByRole("button", { name: "Send them" }));
    expect(await within(tab).findByText("Made 1 tickets in Jira: PAY-102")).toBeInTheDocument();
    expect(within(tab).getByText("PAY-S2 sub-task: no type")).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/tickets/send"))?.body).toEqual({ stories: [] });
    // reloaded: every story is in Jira now
    expect(await within(tab).findByText("PAY-102")).toBeInTheDocument();
    expect(within(tab).queryByRole("button", { name: "Preview Jira tickets" })).toBeNull();
  });

  it("B6: an initiative links tickets already on the Tasks boards, shows their status, and unlinks one", async () => {
    const coupon: LinkedTicket = { key: "SHOPS-1234", project_id: "web-shop", task_id: "tk_1", title: "Coupon applies once", status: "in_progress",
      external_status: "In Review", url: "https://shop.example.net/browse/SHOPS-1234", assignee: "ana", source: "jira", on_board: true,
      initiatives: ["INI-2"], branches: 2, commits: 5 };
    const report: LinkedTicket = { key: "SHOPS-1252", project_id: "web-shop", task_id: "tk_2", title: "Coupon report", status: "todo",
      external_status: "To Do", source: "jira", on_board: true, initiatives: [], branches: 0, commits: 0 };
    const banner: LinkedTicket = { key: "tk_9", project_id: "admin", task_id: "tk_9", title: "Euro banner", status: "todo", source: "local",
      on_board: true, initiatives: [], branches: 0, commits: 0 };
    let linked: LinkedTicket[] = [coupon];
    const asked: string[] = [];
    const calls = await open("#/initiatives/INI-2", {
      "/api/initiatives/INI-2": () => detail({}, { linked }),
      "GET /api/initiatives/INI-2/tickets/candidates": () => [report],
      "POST /api/initiatives/INI-2/tickets": (b: { tasks: string[] }) =>
        (linked = [...linked, ...[report, banner].filter((t) => b.tasks.includes(t.task_id!)).map((t) => ({ ...t, initiatives: ["INI-2"] }))]),
      "DELETE /api/initiatives/INI-2/tickets/SHOPS-1234": () => (linked = linked.filter((t) => t.key !== "SHOPS-1234")),
    });
    server.use(http.get("/api/initiatives/INI-2/tickets/candidates", ({ request }) => {
      const u = new URL(request.url);
      asked.push(u.search);
      return HttpResponse.json(u.searchParams.get("all") === "true" ? [report, banner] : [report]);
    }));
    const panel = (await main().findByRole("heading", { name: "Tickets" })).closest("section")!;
    const list = await within(panel).findByRole("list", { name: "Linked tickets" });
    expect(list).toHaveTextContent("SHOPS-1234In progressCoupon applies onceJira: In Review · web-shop · ana · 2 branches, 5 commits");
    expect(within(list).getByRole("link", { name: "SHOPS-1234" })).toHaveAttribute("href", "https://shop.example.net/browse/SHOPS-1234");

    // Add tickets: the initiative's repos first, every project when the box is off
    await userEvent.click(within(panel).getByRole("button", { name: "Add tickets" }));
    const add = within(panel).getByRole("group", { name: "Add tickets" });
    const found = await within(add).findByRole("list", { name: "Tickets found" });
    expect(within(found).getAllByRole("listitem")).toHaveLength(1);
    await userEvent.click(within(add).getByLabelText("only this initiative's repos"));
    await waitFor(() => expect(within(add).getAllByRole("checkbox", { name: /^Pick / })).toHaveLength(2));
    expect(asked.at(-1)).toContain("all=true");
    await userEvent.type(within(add).getByLabelText("Search tickets"), "coupon");
    await waitFor(() => expect(asked.at(-1)).toContain("q=coupon"));
    await userEvent.click(await within(add).findByRole("checkbox", { name: "Pick SHOPS-1252" }));
    await userEvent.click(within(add).getByRole("button", { name: "Link 1 ticket" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path === "/api/initiatives/INI-2/tickets")?.body).toEqual({ tasks: ["tk_2"] }),
    );
    await waitFor(() => expect(within(panel).getByRole("list", { name: "Linked tickets" })).toHaveTextContent("SHOPS-1252"));
    expect(within(panel).queryByRole("group", { name: "Add tickets" })).toBeNull();

    // Unlink
    await userEvent.click(within(panel).getByRole("button", { name: "Unlink SHOPS-1234" }));
    await waitFor(() => expect(within(panel).getByRole("list", { name: "Linked tickets" })).not.toHaveTextContent("SHOPS-1234"));
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/initiatives/INI-2/tickets/SHOPS-1234")).toBe(true);
  });

  it("the Product map shows the teams, their flows with the tickets that changed them, the work, and finds the flows again", async () => {
    let running = false;
    const map = (): ProductMap => ({
      teams: [
        { id: "erp", name: "Orders", lead: "Sara", projects: ["erp-core", "erp-ui"], jira_project: "SHOPS", keys: "SHOPS-\\d+" },
        { id: "sw", name: "SW", lead: "Tom", projects: ["web-shop"], jira_project: "SW", keys: "SW-\\d+" },
      ],
      flows: [
        {
          project_id: "erp-core",
          id: "checkout",
          name: "Checkout",
          summary: "From the cart to a paid order.",
          team: "erp",
          steps: [
            { text: "The cart totals the prices", cite: "src/Cart.kt:40" },
            { text: "The customer pays", cite: null },
          ],
          endpoints: ["POST /orders"],
          tables: ["orders", "payments"],
          edited: false,
          hidden: false,
          updated_at: "",
          tickets: ["SHOPS-1234"],
        },
      ],
      tickets: {
        team: null,
        tickets: [
          {
            key: "SHOPS-1234",
            branches: [{ project_id: "erp-core", kind: "branch", ref: "feature/shops-1234-vat", key: "SHOPS-1234" }],
            commits: [
              { project_id: "erp-core", kind: "commit", ref: "abc", key: "SHOPS-1234" },
              { project_id: "erp-core", kind: "commit", ref: "def", key: "SHOPS-1234" },
            ],
            last_at: "2026-10-20T10:00:00Z",
          },
        ],
        unlinked: [{ project_id: "erp-ui", kind: "branch", ref: "fix-typo", key: "" }],
        scans: [],
      },
      runs: running
        ? [{ thread_id: "t-9", projects: ["erp-core", "erp-ui", "web-shop"], status: "running", started_at: "" }]
        : [],
      not_analysed: ["erp-ui", "web-shop"],
      board: [
        { key: "SHOPS-1234", project_id: "erp-core", task_id: "tk_1", title: "Coupon applies once", status: "in_progress", external_status: "In Review",
          url: "https://shop.example.net/browse/SHOPS-1234", source: "jira", on_board: true, initiatives: ["INI-2"], branches: 1, commits: 2 },
        { key: "SHOPS-1300", project_id: "erp-ui", task_id: "tk_2", title: "Fix the login page", status: "todo", external_status: "To Do",
          source: "jira", on_board: true, initiatives: [], branches: 0, commits: 0 },
      ],
    });
    const calls = await open("#/product-map", {
      "GET /api/product/map": () => map(),
      "POST /api/product/flows/run": () => {
        running = true;
        return map().runs[0];
      },
      "PATCH /api/product/flows/erp-core/checkout": (b: { hidden?: boolean }) => ({
        ...map().flows[0],
        ...b,
      }),
    });
    const teams = await main().findByRole("region", { name: "Teams" });
    expect(within(teams).getByRole("button", { name: "Team Orders" })).toHaveTextContent(
      "Jira SHOPS · keys SHOPS-\\d+",
    );
    expect(main().getByText("No flows yet for: erp-ui, web-shop.")).toBeInTheDocument();
    const flows = main().getByRole("region", { name: "Business flows" });
    const flow = within(flows).getByRole("button", { name: "Flow Checkout" });
    expect(flow).toHaveTextContent("2 steps · 1 endpoint · 2 tables");
    expect(within(flow).getByLabelText("Tickets")).toHaveTextContent("SHOPS-1234");
    const work = main().getByRole("region", { name: "Tickets and work" });
    expect(work).toHaveTextContent("SHOPS-1234");
    expect(work).toHaveTextContent("branch feature/shops-1234-vat");
    expect(work).toHaveTextContent("2 commits · last 2026-10-20");
    expect(work).toHaveTextContent("1 branches or commits with no ticket key");
    // B6: the Jira tickets on the Tasks boards, with their initiative, not only the keys found in git
    const board = within(work).getByRole("list", { name: "On the Tasks boards" });
    expect(within(board).getAllByRole("listitem").map((l) => l.textContent)).toEqual([
      "SHOPS-1234In ReviewCoupon applies onceINI-2 · erp-core · 2 commits",
      "SHOPS-1300To DoFix the login pageno initiative · erp-ui",
    ]);
    expect(within(board).getByRole("link", { name: "SHOPS-1234" })).toHaveAttribute("href", "https://shop.example.net/browse/SHOPS-1234");

    // a flow's steps, with their file:line
    await userEvent.click(flow);
    const steps = within(flows).getAllByRole("listitem").map((l) => l.textContent);
    expect(steps).toEqual([
      "The cart totals the prices src/Cart.kt:40",
      "The customer pays no file:line",
    ]);
    // + New tickets opens the new idea with the flow's repo chosen
    await userEvent.click(within(flows).getByRole("button", { name: "+ New tickets" }));
    const d = await screen.findByRole("dialog", { name: "New initiative" });
    expect(within(d).getByLabelText("The idea")).toHaveValue('About the flow "Checkout": ');
    await userEvent.click(within(d).getByRole("button", { name: "Close" }));
    // hiding a flow puts it under "Show 1 hidden"
    await userEvent.click(within(flows).getByRole("button", { name: "Hide" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ hidden: true }),
    );
    expect(await within(flows).findByRole("button", { name: "Show 1 hidden" })).toBeInTheDocument();


    await userEvent.click(main().getByRole("button", { name: "Find flows again" }));
    await waitFor(() =>
      expect(calls.find((c) => c.path === "/api/product/flows/run")?.body).toEqual({ projects: [] }),
    );
    expect(await main().findByLabelText("Flow run")).toHaveTextContent(
      "runningkeel finds the flows of 3 repos…",
    );
    expect(main().getByRole("button", { name: "Find flows again" })).toBeDisabled();
  });
});
