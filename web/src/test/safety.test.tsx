import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { db, server } from "./setup";

describe("flow safety", () => {
  it("a start refused for the fake model offers 'run anyway' and retries with allow_fake", async () => {
    const user = userEvent.setup();
    let tries = 0;
    server.use(http.post("/api/projects/:pid/flows", async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      db.calls.push({ method: "POST", path: new URL(request.url).pathname, body });
      tries++;
      if (!body.allow_fake) {
        return HttpResponse.json({ error: "These agents would use the fake model: explorer. It writes example files, not real code.", hint: "Pick a real model." }, { status: 409 });
      }
      return HttpResponse.json({ thread_id: "t1", status: "running" });
    }));
    location.hash = "#/projects";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Start a flow" }));
    await user.type(await screen.findByLabelText("What to build"), "Top 10");
    await user.click(screen.getByRole("button", { name: "Start flow" }));
    const box = await screen.findByLabelText(/Run with the fake model anyway/);
    await user.click(box);
    await user.click(screen.getByRole("button", { name: "Start flow" }));
    await waitFor(() => expect(tries).toBe(2));
    expect(db.calls.filter((c) => c.path.endsWith("/flows")).at(-1)?.body).toMatchObject({ allow_fake: true });
  });

  it("'Use for all agents' saves that provider as the model of every agent", async () => {
    const user = userEvent.setup();
    location.hash = "#/connections";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Use for all agents" }));
    await waitFor(() => expect(db.calls.find((c) => c.method === "PUT" && c.path === "/api/settings/general")?.body).toEqual({
      default_model: { provider: "claude", mode: "subscription", model: "sonnet" },
      implementer_model: { provider: "claude", mode: "subscription", model: "sonnet" },
      reviewer_model: { provider: "claude", mode: "subscription", model: "sonnet" },
    }));
  });
});
