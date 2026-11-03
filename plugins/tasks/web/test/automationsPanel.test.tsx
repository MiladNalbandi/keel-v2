// 0.18.57 Tasks › Automations in the new Tasks page: three tabs (When Jira moves, Jira statuses, KeelBot's tickets) and
// one Save. The classic drawer's test (automations.test.tsx) adapted, and the Jira statuses tab.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { db, server } from "../../../../web/src/test/setup";

const calls = (method: string, path: string) =>
  db.calls.filter((c) => c.method === method && c.path === path);
const main = () => within(document.getElementById("main")!);

const view = {
  settings: {
    rules: { in_progress: { mode: "propose" }, in_review: { mode: "propose" } },
    keelbot: { account: null, label: null },
    allow_reply: false,
    daily_auto_starts: 5,
    pick_cap_tokens: 8000,
  },
  statuses: ["in_progress", "in_review", "blocked"],
  modes: ["off", "propose", "start"],
  review_watch: true,
  tracker: true,
  auto_starts_today: 1,
};

function automationsApi() {
  server.use(
    http.get("/api/projects/:pid/tasks/automations", () =>
      HttpResponse.json(view),
    ),
    http.put(
      "/api/projects/:pid/tasks/automations",
      async ({ request, params }) => {
        const body = await request.json();
        db.calls.push({
          method: "PUT",
          path: `/api/projects/${params.pid}/tasks/automations`,
          body,
        });
        return HttpResponse.json({ ...view, settings: body });
      },
    ),
    http.get("/api/projects/:pid/jira/users", () =>
      HttpResponse.json([
        {
          name: "KeelBot",
          account_id: "acc-keelbot-1",
          email: "bot@example.com",
        },
      ]),
    ),
  );
}

async function openAutomations() {
  location.hash = "#/tasks";
  render(<App />);
  await main().findByRole("heading", { name: "Tasks", level: 1 });
  await userEvent
    .setup()
    .click(main().getByRole("button", { name: "Automations" }));
  return screen.findByRole("dialog", { name: "Automations" });
}

describe("Tasks › Automations (new page)", () => {
  it("has three tabs; the rules take a mode, how it runs and a token cap; KeelBot's account is found in Jira; one Save", async () => {
    automationsApi();
    const user = userEvent.setup();
    const d = await openAutomations();
    const tabs = within(d).getByRole("tablist", { name: "Automations" });
    expect(
      within(tabs)
        .getAllByRole("tab")
        .map((t) => t.textContent),
    ).toEqual(["When Jira moves", "Jira statuses", "KeelBot's tickets"]);
    expect(
      within(tabs).getByRole("tab", { name: "When Jira moves" }),
    ).toHaveAttribute("aria-selected", "true");

    const progress = await within(d).findByLabelText(
      "Ticket moves to In progress",
    );
    expect(progress).toHaveValue("propose");
    await user.selectOptions(progress, "start");
    const rule = within(d).getByTestId("rule-in_progress");
    await user.selectOptions(
      within(rule).getByLabelText("How it runs"),
      "auto",
    );
    await user.type(within(rule).getByLabelText("Token cap"), "40000");
    expect(within(d).getByText(/1 today/)).toBeInTheDocument();

    await user.click(
      within(tabs).getByRole("tab", { name: "KeelBot's tickets" }),
    );
    expect(
      within(tabs).getByRole("tab", { name: "KeelBot's tickets" }),
    ).toHaveAttribute("aria-selected", "true");
    const bot = within(d).getByRole("tabpanel", { name: "KeelBot's tickets" });
    await user.type(within(bot).getByLabelText("Find a Jira user"), "keel");
    await user.click(within(bot).getByRole("button", { name: "Find" }));
    await user.click(
      await within(bot).findByRole("button", {
        name: "KeelBot · bot@example.com",
      }),
    );
    expect(within(bot).getByLabelText("KeelBot's Jira account")).toHaveValue(
      "acc-keelbot-1",
    );
    await user.type(
      within(bot).getByLabelText("Or a label on the ticket"),
      "keel",
    );
    await user.click(
      within(bot).getByRole("checkbox", {
        name: /post its reply on the pull request without asking/,
      }),
    );
    await user.click(within(d).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(
        calls("PUT", "/api/projects/ludus-engine/tasks/automations"),
      ).toHaveLength(1),
    );
    const body = calls("PUT", "/api/projects/ludus-engine/tasks/automations")[0]
      .body as Record<string, any>;
    expect(body.rules.in_progress).toEqual({
      mode: "start",
      run_mode: "auto",
      cap_tokens: 40000,
    });
    expect(body.keelbot).toEqual({ account: "acc-keelbot-1", label: "keel" });
    expect(body.allow_reply).toBe(true);
    expect(calls("PUT", "/api/projects/ludus-engine/jira")).toHaveLength(0); // the status map did not change
    expect(await screen.findByText("Automations saved.")).toBeInTheDocument();
  });

  it("Jira statuses: finds the board's statuses, maps them and saves the map with the automations", async () => {
    automationsApi();
    const user = userEvent.setup();
    const d = await openAutomations();
    await user.click(within(d).getByRole("tab", { name: "Jira statuses" }));
    const tab = within(d).getByRole("tabpanel", { name: "Jira statuses" });
    expect(
      await within(tab).findByLabelText("Jira status for In review"),
    ).toHaveValue("");
    expect(tab).toHaveTextContent("Jira Cloud as Dev One");
    expect(
      within(tab).getByRole("link", { name: "Settings › Connections ›" }),
    ).toHaveAttribute("href", "#/settings/connections");
    await user.click(
      within(tab).getByRole("button", { name: "Find statuses in Jira" }),
    );
    await waitFor(() =>
      expect(
        within(tab).getByLabelText("Jira status for In review"),
      ).toHaveValue("Code Review"),
    );
    expect(
      within(tab).getByLabelText("Jira status for Ready for release"),
    ).toHaveValue("Ready for Release");
    await user.selectOptions(
      within(tab).getByLabelText("Jira status for Blocked"),
      "-",
    );
    await user.click(within(d).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(calls("PUT", "/api/projects/ludus-engine/jira")).toHaveLength(1),
    );
    expect(calls("PUT", "/api/projects/ludus-engine/jira")[0].body).toEqual({
      status_map: {
        todo: "To Do",
        in_progress: "In Progress",
        in_review: "Code Review",
        testing_pp: "QA on PP",
        ready_prod: "Ready for Release",
        done: "Done",
        blocked: "-",
      },
    });
  });

  it("Jira statuses: a keel status with no match on the board becomes do not move, and says so (B4)", async () => {
    automationsApi();
    server.use(http.get("/api/projects/:pid/jira/discover", () => HttpResponse.json({
      statuses: [{ name: "To Do", category: "new" }, { name: "Doing", category: "indeterminate" }, { name: "Done", category: "done" }],
      transitions: [], fields: [],
      suggested: { todo: "To Do", in_progress: "Doing", in_review: "-", testing_pp: "-", ready_prod: "-", done: "Done" },
      keel_statuses: ["todo", "in_progress", "in_review", "testing_pp", "ready_prod", "done", "cancelled", "blocked"],
    })));
    const user = userEvent.setup();
    const d = await openAutomations();
    await user.click(within(d).getByRole("tab", { name: "Jira statuses" }));
    const tab = within(d).getByRole("tabpanel", { name: "Jira statuses" });
    await user.click(await within(tab).findByRole("button", { name: "Find statuses in Jira" }));
    await waitFor(() => expect(within(tab).getByLabelText("Jira status for In review")).toHaveValue("-"));
    expect(within(tab).getByTestId("jira-note-in_review")).toHaveTextContent("No match on the board (no “In Review”): keel will not move the ticket.");
    expect(within(tab).queryByTestId("jira-note-in_progress")).toBeNull();
  });

  it("Jira statuses without a Jira connection points to Settings › Connections", async () => {
    automationsApi();
    delete db.tk.jira["ludus-engine"];
    const user = userEvent.setup();
    const d = await openAutomations();
    await user.click(within(d).getByRole("tab", { name: "Jira statuses" }));
    const off = await within(d).findByTestId("jira-off");
    expect(off).toHaveTextContent("This project has no Jira connection");
    expect(
      within(off).getByRole("link", {
        name: "Connect Jira in Settings › Connections",
      }),
    ).toHaveAttribute("href", "#/settings/connections");
  });

  it("Jira statuses when the Jira plugin is off (its api answers 404) says so too", async () => {
    automationsApi();
    server.use(
      http.get("/api/projects/:pid/jira", () =>
        HttpResponse.json({ error: "Not found" }, { status: 404 }),
      ),
    );
    const user = userEvent.setup();
    const d = await openAutomations();
    await user.click(within(d).getByRole("tab", { name: "Jira statuses" }));
    expect(await within(d).findByTestId("jira-off")).toBeInTheDocument();
  });
});
