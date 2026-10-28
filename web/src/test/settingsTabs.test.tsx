// keel 0.18.57 one smoke test per Settings tab (with the MSW handlers), and the login steps of a device code (Codex)
// and of "Paste a token".

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import * as fx from "./fixtures";
import { db, server } from "./setup";

const open = async (hash: string, title: string) => {
  location.hash = hash;
  render(<App />);
  expect(
    await screen.findByRole("heading", { level: 1, name: title }),
  ).toBeInTheDocument();
  expect(
    within(
      screen.getByRole("navigation", { name: "Settings sections" }),
    ).getByRole("link", { name: title }),
  ).toHaveAttribute("aria-current", "page");
};

describe("each Settings tab renders with its parts", () => {
  it("General: the folding sections, the scope switch, the links to the other tabs", async () => {
    await open("#/settings/general", "General");
    expect(screen.getByText("Settings ›")).toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: "Show settings for" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("region", { name: "Flow and gates" }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByText(/^Models are in/)).getByRole("link", { name: "Models & logins" }),
    ).toHaveAttribute("href", "#/settings/models");
  });

  it("Models & logins: a row per provider, the models with their effort, this machine", async () => {
    await open("#/settings/models", "Models & logins");
    const claude = await screen.findByTestId("conn-claude");
    expect(within(claude).getByText("No login saved")).toBeInTheDocument();
    // the row that needs something is open: how agents reach it, as radio cards
    expect(
      within(claude).getByRole("radiogroup", {
        name: "How agents reach Claude",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Models" })).toBeInTheDocument();
    expect(await screen.findByTestId("row-default_model")).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "This machine" }),
    ).toBeInTheDocument();
    // the menu's hint: one provider needs a login
    await waitFor(() =>
      expect(
        screen.getByRole("navigation", { name: "Settings sections" }),
      ).toHaveTextContent("Models & logins"),
    );
  });

  it("Connections: GitHub and GitLab for every project, then Jira per project; no providers, no Databases", async () => {
    await open("#/settings/connections", "Connections");
    const gh = await screen.findByTestId("github");
    expect(within(gh).getByText("every project")).toBeInTheDocument();
    expect(await screen.findByTestId("gitlab")).toBeInTheDocument();
    const jira = await screen.findByTestId("jira");
    expect(within(jira).getByText("per project")).toBeInTheDocument();
    const order = [
      ...document.querySelectorAll(
        '[data-testid="github"], [data-testid="gitlab"], [data-testid="jira"]',
      ),
    ].map((e) => e.getAttribute("data-testid"));
    expect(order).toEqual(["github", "gitlab", "jira"]);
    expect(screen.queryByTestId("conn-claude")).toBeNull();
    expect(
      screen.queryByRole("complementary", { name: "Database connections" }),
    ).toBeNull();
  });

  it("Budget: the providers, what keel counted with the day chart, the limits; a click on a day says its numbers", async () => {
    const user = userEvent.setup();
    await open("#/settings/budget", "Budget");
    expect(screen.getAllByRole("button", { name: "Add cap" })[0]).toHaveClass("p"); // the tab's main button
    const chart = await screen.findByRole("group", {
      name: /Tokens per day, last/,
    });
    const bars = within(chart).getAllByRole("button");
    expect(bars.at(-1)).toHaveAttribute("aria-pressed", "true");
    await user.click(bars[0]);
    expect(bars[0]).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("chart-picked")).toHaveTextContent(
      bars[0].getAttribute("aria-label")!.replace(/:.*/, ""),
    );
    expect(screen.getByTestId("for-every-flow")).toBeInTheDocument();
    expect(
      await screen.findByRole("table", { name: "Caps" }),
    ).toBeInTheDocument();
  });

  it("Budget: a rule for every flow saves for every project", async () => {
    const user = userEvent.setup();
    await open("#/settings/budget", "Budget");
    const on = within(await screen.findByTestId("for-every-flow")).getByRole(
      "group",
      { name: "When a cap is hit" },
    );
    await user.click(within(on).getByRole("button", { name: "stop" }));
    await waitFor(() =>
      expect(
        db.calls.find((c) => c.path === "/api/settings/general")?.body,
      ).toEqual({ on_cap: "stop" }),
    );
  });

  it("Plugins: the head's buttons, the restart banner's place, the three tabs", async () => {
    await open("#/settings/plugins", "Plugins");
    expect(
      screen.getByRole("button", { name: "Check for updates" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Browse the marketplace" }),
    ).toBeInTheDocument();
    const tabs = screen.getByRole("tablist", { name: "Plugins" });
    expect(
      within(tabs)
        .getAllByRole("tab")
        .map((t) => t.textContent?.replace(/ \(\d+\)/, "")),
    ).toEqual(["Installed", "Marketplace", "Sources and rules"]);
  });

  it("Databases: the Database plugin's tab, its list and editor", async () => {
    server.use(
      http.get("/api/databases", () => HttpResponse.json([])),
      http.get("/api/projects/:pid/databases", () => HttpResponse.json([])),
    );
    await open("#/settings/databases", "Databases");
    expect(
      await screen.findByRole("complementary", {
        name: "Database connections",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/keeps each password encrypted; agents never see it/),
    ).toBeInTheDocument();
  });

  it("Access › Devices: Pair a phone is the part's button; the pairing card replaces it", async () => {
    const user = userEvent.setup();
    await open("#/settings/access", "Access");
    expect(
      await screen.findByText("No phone is paired yet."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Pair a phone (QR code)" }));
    expect(
      await screen.findByRole("group", { name: "Pair a phone" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pair a phone (QR code)" })).toBeNull();
  });
});

describe("the login steps", () => {
  it("a device code (Codex): start, open the page, type the code there, saved", async () => {
    const user = userEvent.setup();
    let done = false;
    server.use(
      http.get("/api/connections", () =>
        HttpResponse.json({
          ...fx.connections,
          providers: [
            {
              ...fx.connections.providers[0],
              id: "codex",
              label: "GPT / Codex",
              login_secret: "CODEX_AUTH_JSON",
            },
          ],
        }),
      ),
      http.post("/api/logins", () =>
        HttpResponse.json({
          id: "C1",
          provider: "codex",
          status: "waiting",
          url: "https://auth.openai.com/codex/device",
          code: "WDJB-MJHT",
          message: "Waiting for you to sign in.",
        }),
      ),
      http.get("/api/logins/C1", () =>
        HttpResponse.json(
          done
            ? {
                id: "C1",
                provider: "codex",
                status: "done",
                message: "Logged in. Saved encrypted.",
                hint: "…Z9e",
              }
            : {
                id: "C1",
                provider: "codex",
                status: "waiting",
                url: "https://auth.openai.com/codex/device",
                code: "WDJB-MJHT",
                message: "Waiting for you to sign in.",
              },
        ),
      ),
    );
    location.hash = "#/settings/models";
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: "Set up login" }),
    );
    const dlg = screen.getByRole("dialog", {
      name: "Set up GPT / Codex login",
    });
    const steps = () => within(dlg).getAllByRole("listitem");
    expect(steps()[0]).toHaveAttribute("aria-current", "step");
    await user.click(within(dlg).getByRole("button", { name: "Start login" }));
    const link = await within(dlg).findByRole("link", {
      name: "Open auth.openai.com",
    });
    expect(steps()[0]).toHaveClass("s-done");
    expect(steps()[1]).toHaveTextContent("Open this page: auth.openai.com");
    await user.click(link);
    expect(await within(dlg).findByText("WDJB-MJHT")).toBeInTheDocument();
    expect(steps()[2]).toHaveTextContent("Type the code on that page");
    expect(within(steps()[2]).getByRole("status")).toHaveTextContent(
      "Waiting for you to sign in.",
    );
    done = true;
    expect(
      await within(dlg).findByText(
        /Logged in\. Saved encrypted\. \(…Z9e\)/,
        {},
        { timeout: 4000 },
      ),
    ).toBeInTheDocument();
    expect(steps()[3]).toHaveClass("s-done");
  });

  it("a login that fails says why at its step, and Start again starts a new one", async () => {
    const user = userEvent.setup();
    let starts = 0;
    server.use(
      http.post("/api/logins", () => {
        starts++;
        return HttpResponse.json({
          id: `F${starts}`,
          provider: "claude",
          status: "failed",
          url: "https://claude.com/cai/oauth/authorize",
          message: "claude setup-token stopped: no browser.",
        });
      }),
    );
    location.hash = "#/settings/models";
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: "Set up login" }),
    );
    const dlg = screen.getByRole("dialog", { name: "Set up Claude login" });
    await user.click(within(dlg).getByRole("button", { name: "Start login" }));
    const err = await within(dlg).findByRole("alert");
    expect(err).toHaveTextContent("claude setup-token stopped: no browser.");
    expect(err.closest("li")).toHaveClass("s-failed");
    await user.click(within(err).getByRole("button", { name: "Start again" }));
    await waitFor(() => expect(starts).toBe(2));
  });

  it("Paste a token saves the login and shows step 4 done", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings/models";
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: "Set up login" }),
    );
    const dlg = screen.getByRole("dialog", { name: "Set up Claude login" });
    await user.click(
      within(
        within(dlg).getByRole("group", { name: "How to log in" }),
      ).getByRole("button", { name: "Paste a token" }),
    );
    expect(
      within(dlg).getByText(/On your computer run `claude setup-token`/),
    ).toBeInTheDocument();
    await user.type(
      within(dlg).getByLabelText(/Claude login token/),
      "sk-ant-oat01-secret-9KQ",
    );
    await user.click(within(dlg).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(
        db.calls.find((c) => c.path === "/api/secrets/CLAUDE_CODE_OAUTH_TOKEN")
          ?.body,
      ).toEqual({ value: "sk-ant-oat01-secret-9KQ" }),
    );
    expect(
      await within(dlg).findByText(/Claude login saved \(…9KQ\)/),
    ).toBeInTheDocument();
    expect(within(dlg).getAllByRole("listitem").at(-1)).toHaveClass("s-done");
  });
});
