// B8a Connections says when a provider is ready but agents still run on the fake model, and fixes it in one click.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import * as fx from "./fixtures";
import { db, server } from "./setup";

const keySaved = () => ({
  ...fx.connections,
  providers: [{ ...fx.connections.providers[0], key_set: true, key_hint: "…9KQ",
    modes: [{ id: "api", label: "Anthropic API key", ready: true, detail: "ANTHROPIC_API_KEY" }] }],
});

describe("Connections: agents still on the fake model (B8a)", () => {
  it("a Claude with its key and a fake default model says so and Use Claude for all agents sets the three models", async () => {
    server.use(http.get("/api/connections", () => HttpResponse.json(keySaved())));
    const user = userEvent.setup();
    location.hash = "#/settings/models";
    render(<App />);
    const note = await screen.findByRole("note");
    expect(note).toHaveTextContent("Claude is ready, but the default model still uses the fake model");
    await user.click(screen.getByRole("button", { name: "Use Claude for all agents" }));
    await waitFor(() => expect(db.calls.find((c) => c.method === "PUT" && c.path === "/api/settings/general")).toBeTruthy());
    const body = db.calls.find((c) => c.method === "PUT" && c.path === "/api/settings/general")!.body as Record<string, { provider: string }>;
    expect(body.default_model.provider).toBe("claude");
    expect(body.implementer_model.provider).toBe("claude");
    expect(body.reviewer_model.provider).toBe("claude");
    expect(body.default_model).toMatchObject({ provider: "claude", mode: "api" });
    await waitFor(() => expect(screen.queryByRole("note")).toBeNull());
  });

  it("no note while the provider has no key", async () => {
    render(<App />);
    location.hash = "#/settings/models";
    await screen.findByLabelText(/^Anthropic API key/);
    expect(screen.queryByRole("note")).toBeNull();
  });
});
