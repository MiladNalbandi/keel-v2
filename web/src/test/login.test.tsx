import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { db, server } from "./setup";

describe("login helper", () => {
  it("logs Claude in from the dashboard: open the page, paste its code, saved and tested", async () => {
    const user = userEvent.setup();
    let polls = 0;
    server.use(
      http.post("/api/logins", () => HttpResponse.json({ id: "L1", provider: "claude", status: "code_needed",
        url: "https://claude.com/cai/oauth/authorize?code=true", message: "Open the link, sign in, then paste the code the page shows." })),
      http.post("/api/logins/L1/code", async ({ request }) => {
        db.calls.push({ method: "POST", path: "/api/logins/L1/code", body: await request.json() });
        return HttpResponse.json({ id: "L1", provider: "claude", status: "waiting", url: "https://claude.com/cai/oauth/authorize?code=true", message: "Checking the code…" });
      }),
      http.get("/api/logins/L1", () => {
        polls++;
        return HttpResponse.json({ id: "L1", provider: "claude", status: "done", message: "Logged in. Saved encrypted in keel's database as CLAUDE_CODE_OAUTH_TOKEN.", hint: "…gAA" });
      }),
    );
    location.hash = "#/connections";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Set up login" }));
    await user.click(await screen.findByRole("button", { name: "Start login" }));
    expect(await screen.findByRole("link", { name: "Open claude.com" })).toHaveAttribute("href", "https://claude.com/cai/oauth/authorize?code=true");
    await user.type(screen.getByLabelText(/the page shows a code/), "abc#def");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(db.calls.find((c) => c.path === "/api/logins/L1/code")?.body).toEqual({ code: "abc#def" }));
    expect(await screen.findByText(/Logged in\. Saved encrypted/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(await screen.findByText(/Test: OK/)).toBeInTheDocument();
    expect(polls).toBeGreaterThan(0);
  });

  it("the paste tab explains how to get each token", async () => {
    const user = userEvent.setup();
    location.hash = "#/connections";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Set up login" }));
    await user.click(screen.getByRole("tab", { name: "Paste a token" }));
    const dlg = screen.getByRole("dialog", { name: "Set up Claude login" });
    expect(within(dlg).getByText(/On your computer run `claude setup-token`/)).toBeInTheDocument();
  });
});
