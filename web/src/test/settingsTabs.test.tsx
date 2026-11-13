// keel 0.18.57 one smoke test per Settings tab (with the MSW handlers), and a sign-in that fails (Connections ›
// GitHub; #101 the AI providers take an API key: their CLI logins are gone).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
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
      within(screen.getByText(/^Models are in/)).getByRole("link", { name: "Models & keys" }),
    ).toHaveAttribute("href", "#/settings/models");
  });

  it("Models & keys: a row per provider with its key, the models with their effort, this machine", async () => {
    await open("#/settings/models", "Models & keys");
    const claude = await screen.findByTestId("conn-claude");
    expect(within(claude).getByText("No API key")).toBeInTheDocument();
    // the row that needs something is open: how agents reach it (the provider's API, no other way) and its key
    expect(within(claude).getByTestId("way-claude")).toHaveTextContent("Anthropic API key");
    expect(within(claude).getByLabelText(/^Anthropic API key/)).toHaveAttribute("id", "key-claude");
    expect(within(claude).queryByRole("radiogroup")).toBeNull();
    expect(within(claude).queryByRole("button", { name: /log ?in/i })).toBeNull();
    expect(screen.getByRole("region", { name: "Models" })).toBeInTheDocument();
    expect(await screen.findByTestId("row-default_model")).toBeInTheDocument();
    const machine = screen.getByRole("region", { name: "This machine" });
    // whatever the api lists: node, git, java and docker (no agent CLI any more)
    const chips = within(machine).getByLabelText("Installed programs");
    expect(chips.querySelectorAll(".ss-tag")).toHaveLength(4);
    for (const program of ["node", "git", "java", "docker"]) expect(chips).toHaveTextContent(program);
    expect(within(chips).getByText("not installed")).toBeInTheDocument();
    // the menu's hint: one provider needs a key
    await waitFor(() =>
      expect(
        screen.getByRole("navigation", { name: "Settings sections" }),
      ).toHaveTextContent("Models & keys"),
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

  it("Budget: what keel counted with the day chart, the limits; a click on a day says its numbers", async () => {
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

describe("the sign-in steps (Connections › GitHub)", () => {
  it("a sign-in that fails says why at its step, and Start again starts a new one", async () => {
    const user = userEvent.setup();
    let starts = 0;
    server.use(
      http.post("/api/github/login", () => {
        starts++;
        return HttpResponse.json({
          id: `F${starts}`,
          status: "failed",
          host: "github.com",
          url: "https://github.com/login/device",
          code: null,
          message: "GitHub refused the device code.",
          login: null,
          scopes: [],
          via: null,
        });
      }),
    );
    location.hash = "#/settings/connections";
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: "Sign in with GitHub" }),
    );
    const dlg = screen.getByRole("dialog", { name: "Set up GitHub login" });
    await user.click(within(dlg).getByRole("button", { name: "Start login" }));
    const err = await within(dlg).findByRole("alert");
    expect(err).toHaveTextContent("GitHub refused the device code.");
    expect(err.closest("li")).toHaveClass("s-failed");
    await user.click(within(err).getByRole("button", { name: "Start again" }));
    await waitFor(() => expect(starts).toBe(2));
  });
});
