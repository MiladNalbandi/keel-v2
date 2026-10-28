// keel 0.18.57 Settings: one page with its own menu (General · Models & logins · Connections · Budget · Plugins ·
// Databases · Devices) at #/settings/<tab>; the old pages' links open their tab. General shows Every project or one
// project (#/settings/general/<id>): a changed row says so, with Every project's value and Reset.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { parseHash } from "../routes";
import { db, server } from "./setup";

const row = (key: string) => screen.getByTestId(`row-${key}`);
const open = (hash: string) => {
  location.hash = hash;
  render(<App />);
};
/** A section's fold button, named by its title. */
const fold = (name: string) => screen.findByRole("button", { name });

describe("Settings: the tabs and their links", () => {
  it("has one menu with the eight tabs (Review since 0.18.65); each opens at #/settings/<tab>", async () => {
    const user = userEvent.setup();
    open("#/settings");
    const menu = await screen.findByRole("navigation", {
      name: "Settings sections",
    });
    expect(
      within(menu)
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual([
      "General",
      "Models & logins",
      "Review",
      "Connections",
      "Budget",
      "Plugins",
      "Databases",
      "Devices",
    ]);
    expect(within(menu).getByRole("link", { name: "General" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await user.click(within(menu).getByRole("link", { name: "Budget" }));
    expect(location.hash).toBe("#/settings/budget");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Budget" }),
    ).toBeInTheDocument();
    expect(
      within(
        screen.getByRole("navigation", { name: "Settings sections" }),
      ).getByRole("link", { name: "Budget" }),
    ).toHaveAttribute("aria-current", "page");
  });

  it("the old pages' links open their tab and the address bar shows the new link", async () => {
    open("#/connections");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Connections" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(location.hash).toBe("#/settings/connections"));
  });

  it.each([
    ["#/budget", { page: "settings", arg: "budget" }, "#/settings/budget"],
    [
      "#/connections",
      { page: "settings", arg: "connections" },
      "#/settings/connections",
    ],
    ["#/plugins", { page: "settings", arg: "plugins" }, "#/settings/plugins"],
    [
      "#/plugins/git?at=projects",
      { page: "settings", arg: "plugins/git", query: "at=projects" },
      "#/settings/plugins/git?at=projects",
    ],
    [
      "#/plugins?tab=market&kind=skills",
      { page: "settings", arg: "plugins", query: "tab=market&kind=skills" },
      "#/settings/plugins?tab=market&kind=skills",
    ],
    [
      "#/databases",
      { page: "settings", arg: "databases" },
      "#/settings/databases",
    ],
    [
      "#/projects/ludus-engine/budget",
      { page: "settings", arg: "budget", project: "ludus-engine" },
      "#/projects/ludus-engine/settings/budget",
    ],
  ])("%s opens the tab and becomes %s", (old, route, now) => {
    const r = parseHash(old);
    expect(r).toMatchObject(route);
    expect(r.redirect).toBe(now);
    expect(parseHash(now).redirect).toBeUndefined();
  });

  it("a notice's link to /budget (the api's form) opens Settings › Budget", async () => {
    open("#/budget");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Budget" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(location.hash).toBe("#/settings/budget"));
  });

  it("the menu has one Settings entry in Control, not Budget, Connections or Plugins", async () => {
    open("#/settings/general");
    const nav = await screen.findByRole("navigation", { name: "Screens" });
    expect(
      within(nav).getByRole("link", { name: /^Settings/ }),
    ).toHaveAttribute("aria-current", "page");
    for (const name of [/^Budget/, /^Connections/, /^Plugins/, /^Databases/])
      expect(within(nav).queryByRole("link", { name })).toBeNull();
  });

  it("a tab of a part that is not there says so", async () => {
    open("#/settings/nothing-here");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Not here" }),
    ).toBeInTheDocument();
  });
});

describe("Settings › General", () => {
  it("shows Every project at first; the switch shows the project's own and back", async () => {
    const user = userEvent.setup();
    open("#/settings/general");
    const scope = await screen.findByRole("group", {
      name: "Show settings for",
    });
    expect(
      within(scope).getByRole("button", { name: "Every project" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText(/changed for ludus-engine/)).toBeNull();
    await user.click(
      within(scope).getByRole("button", { name: "ludus-engine" }),
    );
    expect(location.hash).toBe("#/settings/general/ludus-engine");
    expect(
      await screen.findByText(
        /A value you do not change here comes from Every project/,
      ),
    ).toBeInTheDocument();
    await user.click(
      within(
        screen.getByRole("group", { name: "Show settings for" }),
      ).getByRole("button", { name: "Every project" }),
    );
    expect(location.hash).toBe("#/settings/general");
  });

  it("marks a changed setting with a badge and Every project's value; Reset removes it", async () => {
    const user = userEvent.setup();
    db.overrides["ludus-engine"] = { gates_mode: "end" };
    open("#/settings/general/ludus-engine");
    expect(
      await screen.findByText(/1 setting changed for ludus-engine\./),
    ).toBeInTheDocument();
    const gate = row("gates_mode");
    expect(gate).toHaveClass("is-changed");
    expect(
      within(gate).getByText("changed for ludus-engine"),
    ).toBeInTheDocument();
    expect(
      within(gate).getByText(/Every project: every AC/),
    ).toBeInTheDocument();
    expect(
      within(row("fix_attempts")).getByText("from Every project"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("sec-flow")).getByText("1 changed"),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Reset Gate mode to Every project" }),
    );
    await waitFor(() =>
      expect(
        db.calls.find(
          (c) =>
            c.method === "PUT" &&
            c.path === "/api/projects/ludus-engine/settings",
        )?.body,
      ).toEqual({ gates_mode: null }),
    );
    expect(
      await screen.findByText(/Everything follows Every project\./),
    ).toBeInTheDocument();
    expect(
      within(row("gates_mode")).getByText("from Every project"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("save-state")).toHaveTextContent("Saved");
    expect(
      await screen.findByText("Saved: Gate mode follows Every project again"),
    ).toBeInTheDocument();
  });

  it("says that the project changes settings of other tabs too, with a link to them", async () => {
    // the fixture's project changes its cap per flow (Budget's rule)
    open("#/settings/general/ludus-engine");
    const bar = await screen.findByText(/Everything follows Every project\./);
    expect(bar).toHaveTextContent("1 more is changed on Budget.");
    expect(within(bar).getByRole("link", { name: "Budget" })).toHaveAttribute(
      "href",
      "#/settings/budget",
    );
  });

  it("changing a value in the project's scope makes it the project's own", async () => {
    const user = userEvent.setup();
    open("#/settings/general/ludus-engine");
    await screen.findByText(/Everything follows Every project\./);
    await user.click(
      within(screen.getByRole("group", { name: "Gate mode" })).getByRole(
        "button",
        { name: "end of flow" },
      ),
    );
    await waitFor(() =>
      expect(db.calls.at(-1)?.body).toEqual({ gates_mode: "end" }),
    );
    expect(
      await screen.findByText(/1 setting changed for ludus-engine\./),
    ).toBeInTheDocument();
    expect(
      within(row("gates_mode")).getByText("changed for ludus-engine"),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        "Saved: Gate mode is end of flow for ludus-engine",
      ),
    ).toBeInTheDocument();
  });

  it("Enter and the blur after it save a text value once", async () => {
    const user = userEvent.setup();
    const puts: unknown[] = [];
    server.use(
      http.put("/api/projects/:pid/settings", async ({ request }) => {
        puts.push(await request.json());
        await new Promise((r) => setTimeout(r, 150));
        return HttpResponse.json({});
      }),
    );
    open("#/settings/general/ludus-engine");
    await screen.findByText(/Everything follows Every project\./);
    await user.click(await fold("Git"));
    const branch = screen.getByLabelText("Branch name");
    await user.clear(branch);
    await user.type(branch, "team/{slug}{Enter}");
    await user.tab();
    await new Promise((r) => setTimeout(r, 300));
    expect(puts).toHaveLength(1);
  });

  it("Every project saves for every project", async () => {
    const user = userEvent.setup();
    open("#/settings/general");
    await user.click(
      within(await screen.findByRole("group", { name: "Gate mode" })).getByRole(
        "button",
        { name: "end of lane" },
      ),
    );
    await waitFor(() =>
      expect(
        db.calls.find((c) => c.path === "/api/settings/general")?.body,
      ).toEqual({ gates_mode: "end-of-lane" }),
    );
    expect(
      await screen.findByText(
        "Saved: Gate mode is end of lane for every project that does not change it",
      ),
    ).toBeInTheDocument();
  });

  it("every section says what it is for and folds; the run mode line follows the choice", async () => {
    const user = userEvent.setup();
    open("#/settings/general");
    const flow = await screen.findByRole("region", { name: "Flow and gates" });
    expect(
      within(flow).getByText(
        "How much a flow does by itself, and when it stops for you.",
      ),
    ).toBeInTheDocument();
    for (const name of [
      "Git",
      "Notifications",
      "Environment",
      "Code Review threads",
      "This browser",
    ])
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    // the models and the budget rules have their own tabs now
    expect(screen.queryByRole("region", { name: "Models" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Budget" })).toBeNull();
    expect(row("run_mode")).toHaveTextContent("Stops at every gate.");
    await user.click(
      within(
        screen.getByRole("group", { name: "Run mode (new flows)" }),
      ).getByRole("button", { name: "Important only" }),
    );
    await waitFor(() =>
      expect(row("run_mode")).toHaveTextContent(
        "Approves a criterion's AC gate by itself",
      ),
    );
    // a folded section opens with its button
    const git = await fold("Git");
    expect(git).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText("Commit author")).toBeNull();
    await user.click(git);
    expect(git).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Commit author")).toBeInTheDocument();
  });

  it("names KeelBot as co-author of keel's commits by default; the author can be set as Name <email>", async () => {
    const user = userEvent.setup();
    open("#/settings/general");
    await user.click(await fold("Git"));
    const co = screen.getByRole("group", { name: "KeelBot as co-author" });
    expect(within(co).getByRole("button", { name: "yes" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(row("commit_coauthor")).toHaveTextContent(
      "Co-Authored-By: KeelBot <keel.dev.bot@gmail.com>",
    );
    const author = screen.getByLabelText("Commit author");
    expect(author).toHaveValue("");
    expect(author).toHaveAttribute(
      "placeholder",
      "Ada Lovelace <ada@example.com>",
    );
    await user.type(author, "Ada Lovelace <ada@example.com>{Enter}");
    await waitFor(() =>
      expect(db.calls.at(-1)?.body).toEqual({
        commit_author: "Ada Lovelace <ada@example.com>",
      }),
    );
    await user.click(within(co).getByRole("button", { name: "no" }));
    await waitFor(() =>
      expect(db.calls.at(-1)?.body).toEqual({ commit_coauthor: false }),
    );
    await waitFor(() =>
      expect(row("commit_coauthor")).toHaveTextContent(
        "keel's commits name only their author.",
      ),
    );
  });

  it("a branch name idea saves with one click", async () => {
    const user = userEvent.setup();
    open("#/settings/general");
    await user.click(await fold("Git"));
    await user.click(
      within(
        screen.getByRole("group", { name: "Ideas for Branch name" }),
      ).getByRole("button", { name: "{user}/{slug}" }),
    );
    await waitFor(() =>
      expect(db.calls.at(-1)?.body).toEqual({
        branch_pattern: "{user}/{slug}",
      }),
    );
  });

  it("'Only changed' leaves the changed rows; a failed save says so", async () => {
    const user = userEvent.setup();
    db.overrides["ludus-engine"] = { gates_mode: "end" };
    open("#/settings/general/ludus-engine");
    await screen.findByText(/1 setting changed for ludus-engine\./);
    await user.click(screen.getByRole("switch", { name: "Only changed" }));
    expect(
      screen.getByRole("group", { name: "Gate mode" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("group", { name: "Fix attempts per ladder rung" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "This browser" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "Only changed" }));
    server.use(
      http.put("/api/projects/:pid/settings", () =>
        HttpResponse.json(
          { error: "The settings file is read-only." },
          { status: 500 },
        ),
      ),
    );
    await user.click(
      within(
        screen.getByRole("group", { name: "Fix attempts per ladder rung" }),
      ).getByRole("button", { name: "5" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("save-state")).toHaveTextContent(
        "Not saved: The settings file is read-only.",
      ),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The settings file is read-only.",
    );
  });

  it("the bell's settings are in Notifications and save to the api", async () => {
    const user = userEvent.setup();
    open("#/settings/general");
    const bell = await screen.findByTestId("bell-settings");
    await user.click(
      within(within(bell).getByRole("group", { name: "Sound" })).getByRole(
        "button",
        { name: "Silent" },
      ),
    );
    await waitFor(() =>
      expect(
        db.calls.find((c) => c.path === "/api/notification-settings")?.body,
      ).toMatchObject({ tone: "off" }),
    );
    await user.click(
      within(bell).getByRole("switch", { name: "Do not disturb" }),
    );
    await waitFor(() =>
      expect(
        db.calls.filter((c) => c.path === "/api/notification-settings").at(-1)
          ?.body,
      ).toMatchObject({ quiet: true }),
    );
  });

  it("This browser switches the theme", async () => {
    const user = userEvent.setup();
    open("#/settings/general");
    await user.click(await fold("This browser"));
    const theme = () => within(screen.getByRole("group", { name: "Theme" }));
    // jsdom has no dark preference: Light is chosen at first
    expect(theme().getByRole("button", { name: "Light" })).toHaveAttribute("aria-pressed", "true");
    await user.click(theme().getByRole("button", { name: "Dark" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("keel2.theme")).toBe("dark");
    await user.click(theme().getByRole("button", { name: "Light" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    delete document.documentElement.dataset.theme;
  });
});

describe("Settings › Models & logins: the models", () => {
  it("says when the cheaper model is the fake model (a real flow pauses instead), and a project's own model", async () => {
    db.overrides["ludus-engine"] = {
      cheaper_model: { provider: "fake", mode: "api", model: "fake" },
    };
    db.general.cheaper_model = { provider: "fake", mode: "api", model: "fake" };
    open("#/settings/models");
    const cheap = await screen.findByTestId("row-cheaper_model");
    expect(
      await within(cheap).findByText(
        /Not set: the fake model never runs a real flow/,
      ),
    ).toHaveClass("is-warn");
    expect(
      within(cheap).getByText("changed for ludus-engine"),
    ).toBeInTheDocument();
  });

  it("Local models only saves for every project", async () => {
    const user = userEvent.setup();
    open("#/settings/models");
    const local = await screen.findByRole("group", {
      name: "Local models only",
    });
    await user.click(within(local).getByRole("button", { name: "yes" }));
    await waitFor(() =>
      expect(
        db.calls.find((c) => c.path === "/api/settings/general")?.body,
      ).toEqual({ local_only: true }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("row-local_only")).toHaveTextContent(
        "keel refuses a cloud model",
      ),
    );
  });
});
