// v0.11.0 the CI/CD plugin in keel's core web (KeelBot's CI button, Settings › When CI fails; Run › Jobs › Pipelines is
// the plugin's own page part, tested in plugins/ci/web/test). The Database plugin's IntelliJ-style tool on the Code
// page moved with the plugin (plugins/db/web/test).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { db } from "./setup";

const calls = (method: string, path: string) =>
  db.calls.filter((c) => c.method === method && c.path === path);

describe("KeelBot's CI button and the setting", () => {
  it("fix starts the flow from KeelBot's answer", async () => {
    const user = userEvent.setup();
    db.helper.sessions.push({
      id: "h1",
      project: "ludus-engine",
      root: "/w",
      mode: "ask",
      title: "CI",
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
          role: "helper",
          text: 'The test assert is wrong.\n```keel-ci\n{"op": "fix", "run": 12}\n```',
          call_id: "c1",
          data: {
            status: "done",
            provider: "claude",
            model: "sonnet",
            tokens_in: 1,
            tokens_out: 1,
            cost_usd: 0,
            ms: 1,
          },
          at: new Date().toISOString(),
        },
      ],
    } as never);
    localStorage.setItem("keel2.helper.ludus-engine.session", "h1");
    location.hash = "#/keelbot";
    render(<App />);
    const card = await screen.findByRole("region", { name: "CI: Fix it" });
    await user.click(within(card).getByRole("button", { name: "Fix it" }));
    await waitFor(() =>
      expect(
        calls("POST", "/api/projects/ludus-engine/ci/fix")[0]?.body,
      ).toEqual({ run: 12 }),
    );
    expect(await within(card).findByRole("status")).toHaveTextContent(
      "The fix flow started",
    );
  });

  it("Settings › When CI fails saves the choice", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings";
    render(<App />);
    await user.selectOptions(
      await screen.findByLabelText("When CI fails"),
      "fix",
    );
    await waitFor(() =>
      expect(db.calls.at(-1)?.body).toEqual({ ci_on_failure: "fix" }),
    );
  });
});
