// v0.8.0 the Quality page: scores by flow and model with a drop, the run now and its cases, a run with two models,
// and the nightly run.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { QualityRun, QualitySchedule, QualityView } from "../api";
import { db, server } from "./setup";

const haiku = {
  provider: "claude" as const,
  mode: "subscription" as const,
  model: "haiku",
};
const sonnet = {
  provider: "claude" as const,
  mode: "subscription" as const,
  model: "sonnet",
};
const at = (d: number) => new Date(Date.now() - d * 86_400_000).toISOString();

function quality(): { view: QualityView; posted: unknown[] } {
  const done: QualityRun = {
    id: "q_2",
    status: "done",
    trigger: "nightly",
    flows: ["change"],
    models: [haiku, sonnet],
    sets: ["shop-js"],
    created_at: at(0),
    ended_at: at(0),
    scores: [
      {
        workflow_id: "change",
        model: haiku,
        score: 41,
        cases: 2,
        reached_end: 1,
        tokens: 180_000,
        ms: 600_000,
      },
    ],
    cases: [
      {
        id: "q_2-0",
        n: 0,
        eval_set: "shop-js",
        case_id: "euro-format",
        title: "Euro prices",
        workflow_id: "change",
        model: haiku,
        status: "done",
        outcome: "end",
        reached: "the PR gate",
        score: 92,
        tokens: 80_000,
        cost_usd: 0.4,
        ms: 300_000,
        sendbacks: 0,
      },
      {
        id: "q_2-1",
        n: 1,
        eval_set: "shop-js",
        case_id: "quantity-below-one",
        title: "Refuse a quantity below one",
        workflow_id: "change",
        model: haiku,
        status: "done",
        outcome: "stuck",
        reached: "scope check",
        score: 18,
        tokens: 100_000,
        ms: 300_000,
        sendbacks: 2,
      },
    ],
  };
  const view: QualityView = {
    sets: [
      {
        name: "shop-js",
        description: "A tiny JavaScript shop",
        project: "project",
        problems: [],
        cases: [
          {
            id: "euro-format",
            title: "Euro prices",
            workflow: "change",
            request: "…",
            cap_tokens: 300000,
          },
          {
            id: "quantity-below-one",
            title: "Refuse a quantity below one",
            workflow: "change",
            request: "…",
            cap_tokens: 300000,
          },
        ],
      },
      {
        name: "bad",
        description: "",
        project: "",
        problems: ["no case"],
        cases: [],
      },
    ],
    active: null,
    runs: [done],
    lines: [
      {
        workflow_id: "change",
        model: haiku,
        points: [
          { run_id: "q_1", at: at(1), score: 82 },
          { run_id: "q_2", at: at(0), score: 41 },
        ],
        last: 41,
        previous: 82,
        drop: true,
      },
      {
        workflow_id: "change",
        model: sonnet,
        points: [
          { run_id: "q_1", at: at(1), score: 70 },
          { run_id: "q_2", at: at(0), score: 75 },
        ],
        last: 75,
        previous: 70,
        drop: false,
      },
    ],
    schedule: { enabled: false, at: "02:00", flows: ["change"], models: [] },
  };
  const posted: unknown[] = [];
  server.use(
    http.get("/api/quality", () => HttpResponse.json(view)),
    http.post("/api/quality/runs", async ({ request }) => {
      const body = await request.json();
      posted.push(body);
      db.calls.push({ method: "POST", path: "/api/quality/runs", body });
      view.active = {
        ...done,
        id: "q_3",
        status: "running",
        trigger: "manual",
        scores: [],
        cases: done.cases.map((c) => ({
          ...c,
          status: "queued",
          outcome: null,
          score: null,
        })),
      };
      return HttpResponse.json(view.active);
    }),
    http.post("/api/quality/runs/:id/stop", ({ params }) => {
      db.calls.push({
        method: "POST",
        path: `/api/quality/runs/${params.id}/stop`,
        body: null,
      });
      return HttpResponse.json(view.active);
    }),
    http.put("/api/quality/schedule", async ({ request }) => {
      const body = (await request.json()) as QualitySchedule;
      db.calls.push({ method: "PUT", path: "/api/quality/schedule", body });
      view.schedule = body;
      return HttpResponse.json(body);
    }),
  );
  return { view, posted };
}

describe("the Quality page", () => {
  it("is in the Build group and shows each flow and model's score, with a drop in red", async () => {
    quality();
    location.hash = "#/quality";
    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "Quality" }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("navigation", { name: "Screens" })).getByRole(
        "link",
        { name: "Quality" },
      ),
    ).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "A score dropped. change · Claude haiku went from 82 to 41 since the run before.",
    );
    const table = screen.getByRole("table", {
      name: "Scores by flow and model",
    });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("change");
    expect(rows[0]).toHaveTextContent("-41");
    expect(within(rows[0]).getByText("dropped")).toBeInTheDocument();
    expect(
      within(rows[0]).getByRole("img", { name: "Scores: 82, 41" }),
    ).toBeInTheDocument();
    expect(rows[1]).toHaveTextContent("+5");
    // the last run, its cases and why one is stuck
    const cases = screen.getByRole("table", { name: "Cases" });
    expect(
      within(cases)
        .getByText("Refuse a quantity below one")
        .closest('[role="row"]'),
    ).toHaveTextContent("stuck · scope check");
    expect(
      within(cases)
        .getByText("Refuse a quantity below one")
        .closest('[role="row"]'),
    ).toHaveTextContent("2 sent back");
    expect(screen.getByText(/bad is broken: no case/)).toBeInTheDocument();
  });

  it("runs the eval cases with two models, follows the run, stops it, and saves a nightly run", async () => {
    const user = userEvent.setup();
    const { posted } = quality();
    location.hash = "#/quality";
    render(<App />);
    await screen.findByRole("heading", { name: "Quality" });
    await user.click(
      screen.getByRole("checkbox", { name: "Compare with a second model" }),
    );
    expect(
      screen.getByText(/4 cases will run, one at a time/),
    ).toBeInTheDocument(); // 2 cases × 1 flow × 2 models
    await user.click(screen.getByRole("button", { name: "Run now" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({
      flows: ["change"],
      sets: ["shop-js"],
      models: [haiku, sonnet],
    });
    expect(
      await screen.findByText(/The quality run started: 4 cases/),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Stop the run" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "A run is on" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Stop the run" }));
    await waitFor(() =>
      expect(
        db.calls.some((c) => c.path === "/api/quality/runs/q_3/stop"),
      ).toBe(true),
    );

    await user.clear(
      screen.getByRole("textbox", { name: "Nightly time (UTC)" }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "Nightly time (UTC)" }),
      "03:30",
    );
    await user.click(screen.getByRole("checkbox", { name: /Every night at/ }));
    await waitFor(() =>
      expect(
        db.calls.find((c) => c.path === "/api/quality/schedule")?.body,
      ).toMatchObject({
        enabled: true,
        at: "03:30",
        flows: ["change"],
        models: [haiku, sonnet],
      }),
    );
    expect(
      await screen.findByText(
        "Every night at 03:30 UTC keel runs these flows and models.",
      ),
    ).toBeInTheDocument();
  });
});
