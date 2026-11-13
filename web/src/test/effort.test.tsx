// 0.18.9 thinking effort: shown next to the model on Jobs and Live, saved per agent (Agents drawer), and a note in the
// model picker when the chosen model has no effort setting (the engine ignores it there).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { Job, Model } from "../api";
import { ModelPicker } from "../components/ModelPicker";
import { jobMatches, searchWords } from "../components/RunSearch";
import * as fx from "./fixtures";
import { db, server } from "./setup";

const main = () => within(document.getElementById("main")!);
const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);
const withEffort: Job[] = [{ ...fx.jobs[0], effort: "high" }, { ...fx.jobs[1], effort: null }];

function jobsWithEffort() {
  server.use(
    http.get("/api/jobs/count", ({ request }) => {
      const st = new URL(request.url).searchParams.get("status");
      return HttpResponse.json({ count: withEffort.filter((j) => (st === "running" ? j.status === "running" : j.status !== "running")).length });
    }),
    http.get("/api/jobs", ({ request }) => {
      const st = new URL(request.url).searchParams.get("status");
      return HttpResponse.json(withEffort.filter((j) => !st || (st === "running" ? j.status === "running" : j.status !== "running")));
    }),
    http.get("/api/jobs/:id", ({ params }) => HttpResponse.json({ ...withEffort.find((j) => j.id === params.id) ?? withEffort[0], steps: fx.steps })),
  );
}

describe("effort next to the model", () => {
  it("Jobs: a running call shows its effort; a call without one shows the model alone", async () => {
    jobsWithEffort();
    location.hash = "#/jobs";
    render(<App />);
    const now = await main().findByRole("region", { name: "Running now" });
    await waitFor(() => expect(within(now).getByText("gpt-5.6-sol · high")).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(await main().findByRole("tab", { name: /^Finished/ }));
    const table = await main().findByRole("table", { name: "Agent calls" });
    await waitFor(() => expect(within(table).getByText("gpt-5")).toBeInTheDocument());
    expect(within(table).queryByText(/gpt-5 ·/)).toBeNull();
  });

  it("Live: the agent card shows the effort", async () => {
    jobsWithEffort();
    location.hash = "#/live";
    render(<App />);
    expect(await main().findByText("gpt-5.6-sol · high")).toBeInTheDocument();
  });

  it("the search finds a call by its effort", () => {
    expect(jobMatches(withEffort[0], searchWords("high"))).toBe(true);
    expect(jobMatches(withEffort[1], searchWords("high"))).toBe(false);
  });
});

describe("Agents: effort per agent", () => {
  it("a row says 'effort: default' only for a model with efforts; the drawer saves model.effort", async () => {
    const user = userEvent.setup();
    location.hash = "#/agents";
    render(<App />);
    const explorer = (await screen.findByText("Maps the code an AC touches, read-only.")).closest("tr")!;
    await waitFor(() => expect(explorer).toHaveTextContent("sonnet · effort: default"));
    const implementer = screen.getByText("Writes the minimum code to make the failing test pass.").closest("tr")!;
    expect(implementer).not.toHaveTextContent("effort");
    await user.click(explorer);
    const d = within(await screen.findByRole("dialog", { name: "explorer" }));
    await waitFor(() => expect(d.getByLabelText("Effort")).toBeInTheDocument());
    await user.selectOptions(d.getByLabelText("Effort"), "high");
    await user.click(d.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT", "/api/projects/ludus-engine/agents/explorer")[0]?.body).toEqual({
      model: { provider: "claude", mode: "api", model: "sonnet", effort: "high" },
    }));
  });

  it("'effort: default' clears a saved effort (the model goes without one)", async () => {
    db.agents[0] = { ...db.agents[0], model: { provider: "claude", mode: "subscription", model: "sonnet", effort: "high" }, overridden: ["model"] };
    const user = userEvent.setup();
    location.hash = "#/agents";
    render(<App />);
    const explorer = (await screen.findByText("Maps the code an AC touches, read-only.")).closest("tr")!;
    expect(explorer).toHaveTextContent("sonnet · high");
    await user.click(explorer);
    const d = within(await screen.findByRole("dialog", { name: "explorer" }));
    await waitFor(() => expect(d.getByLabelText("Effort")).toHaveValue("high"));
    await user.selectOptions(d.getByLabelText("Effort"), "");
    await user.click(d.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT", "/api/projects/ludus-engine/agents/explorer")[0]?.body).toEqual({
      model: { provider: "claude", mode: "api", model: "sonnet" },       // saved as subscription before #101: now the API
    }));
  });
});

describe("model picker: no effort setting", () => {
  function Harness({ start, effort = true }: { start: Model; effort?: boolean }) {
    const [m, setM] = useState<Model>(start);
    return <ModelPicker id="t" value={m} onChange={setM} effort={effort} />;
  }

  it("says so for a model without efforts, and not for one with them or when effort is off", async () => {
    const { unmount } = render(<Harness start={{ provider: "claude", mode: "subscription", model: "haiku" }} />);
    expect(await screen.findByTestId("t-noeffort")).toHaveTextContent("no effort setting");
    expect(screen.queryByLabelText("Effort")).toBeNull();
    unmount();
    render(<Harness start={{ provider: "claude", mode: "subscription", model: "sonnet" }} />);
    await waitFor(() => expect(screen.getByLabelText("Effort")).toBeInTheDocument());
    expect(screen.queryByTestId("t-noeffort")).toBeNull();
  });

  it("shows nothing when the place does not pick an effort", async () => {
    render(<Harness start={{ provider: "claude", mode: "subscription", model: "haiku" }} effort={false} />);
    await waitFor(() => expect(screen.getByTestId("t-source")).toHaveTextContent("built-in list"));
    expect(screen.queryByTestId("t-noeffort")).toBeNull();
  });
});
