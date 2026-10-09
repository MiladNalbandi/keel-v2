// v0.10.0 the Database and Git plugins in the web: install on Tools, Connections › GitHub, the Code page's branch tab,
// KeelBot's blocks of theirs as their cards, and Claude Code's question in the Inbox. Their own pieces are tested with
// them: plugins/db/web/test (Connections › Databases, Code › Database, KeelBot's query button, a db: block's settings on
// the Workflows page; Map › Query: plugins/map/web/test) and plugins/git/web/test (Code › Git, KeelBot's git buttons).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { db } from "./setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);

function chatWith(text: string) {
  db.helper.sessions.push({ id: "h1", project: "ludus-engine", root: "/w", mode: "ask", title: "Data", status: "idle", busy: false,
    model: { provider: "claude", mode: "subscription", model: "sonnet" }, tokens_in: 0, tokens_out: 0, tokens_cached: 0, cost_usd: 0, turns: 1,
    created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    messages: [{ n: 1, role: "user", text: "q", data: {}, at: new Date().toISOString() },
      { n: 2, role: "helper", text, call_id: "c1", data: { status: "done", provider: "claude", model: "sonnet", tokens_in: 1, tokens_out: 1, cost_usd: 0, ms: 1 },
        at: new Date().toISOString() }] } as never);
  localStorage.setItem("keel2.helper.ludus-engine.session", "h1");
  location.hash = "#/keelbot";
  render(<App />);
  return screen.findByRole("complementary", { name: "KeelBot" });
}

describe("Tools › Plugins", () => {
  it("installs a plugin for this project, and its tools join who may use what", async () => {
    const user = userEvent.setup();
    location.hash = "#/tools";
    render(<App />);
    const card = await screen.findByRole("article", { name: "Database plugin" });
    expect(card).toHaveTextContent("not installed");
    expect(card).toHaveTextContent("db:check, db:query");
    expect(screen.queryByRole("checkbox", { name: "explorer may use keel-db" })).toBeNull();
    await user.click(within(card).getByRole("button", { name: "Install for ludus-engine" }));
    await waitFor(() => expect(calls("PUT", "/api/projects/ludus-engine/plugins/db")[0]?.body).toEqual({ enabled: true, scope: "project" }));
    expect(await within(card).findByText("on for ludus-engine")).toBeInTheDocument();
    expect(await screen.findByRole("checkbox", { name: "explorer may use keel-db" })).toBeEnabled();
  });
});

describe("Connections › GitHub", () => {
  it("keeps the GitHub token", async () => {
    const user = userEvent.setup();
    location.hash = "#/connections";
    render(<App />);
    await user.type(await screen.findByLabelText("GitHub token"), "ghp_123456789");
    await user.click(within(screen.getByLabelText("GitHub token").closest(".gh-row") as HTMLElement).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT", "/api/secrets/GITHUB_REPO_TOKEN")[0]?.body).toEqual({ value: "ghp_123456789" }));
    expect(await screen.findByText("token set …789")).toBeInTheDocument();
  });
});

describe("Code › Source control › a branch", () => {
  it("opens a branch as a tab with its changed files, a file's diff, its commits, and switches to another", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Source control" }));
    // without the Git plugin a branch is only a line
    await user.click(await screen.findByRole("button", { name: /^Branches/ }));
    expect(await screen.findByText("this flow")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open the branch feat/scores" })).not.toBeInTheDocument();
  });

  it("with the Git plugin: a click on a branch shows what it changed and lets the person switch", async () => {
    const user = userEvent.setup();
    db.plugins.git = true;
    location.hash = "#/repo";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Source control" }));
    await user.click(await screen.findByRole("button", { name: "Open the branch feat/scores" }));
    const tab = await screen.findByRole("region", { name: "Branch feat/scores" });
    expect(within(tab).getByText("current")).toBeInTheDocument();
    expect(within(tab).queryByRole("button", { name: /Switch to/ })).not.toBeInTheDocument();
    const files = within(tab).getByRole("region", { name: "Changed files" });
    expect(within(files).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual([
      "Show the changes of api/ScoreController.kt on feat/scores", "Show the changes of api/Euro.kt on feat/scores"]);
    // the first file's diff shows at once, against base...branch; another file on a click
    expect(await within(tab).findByText("main…feat/scores")).toBeInTheDocument();
    await user.click(within(files).getByRole("button", { name: "Show the changes of api/Euro.kt on feat/scores" }));
    expect(within(files).getByRole("button", { name: "Show the changes of api/Euro.kt on feat/scores" })).toHaveAttribute("aria-pressed", "true");
    expect(within(tab).getByRole("region", { name: "Commits" })).toHaveTextContent("feat(AC-002): refuse a negative score");

    // main: the base, and Switch
    await user.click(screen.getByRole("button", { name: "Open the branch main" }));
    const main = await screen.findByRole("region", { name: "Branch main" });
    expect(main).toHaveTextContent("the base branch");
    await user.click(within(main).getByRole("button", { name: "Switch to main" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/git/switch").at(-1)?.body).toEqual({ branch: "main", create: false }));
    expect(await screen.findByText("On main now.")).toBeInTheDocument();
  });
});

describe("KeelBot's query and git buttons", () => {
  // KeelBot reads them among its other blocks: plugins/keelbot/web/test (keelbot-cards.test.tsx)
  it("a plugin's block shows its card (slot keelbot.card; their buttons: plugins/db/web/test, plugins/git/web/test)", async () => {
    const p = await chatWith('Zero them:\n```keel-query\n{"sql": "update scores set value = 0", "connection": "local"}\n```\n'
      + '```keel-git\n{"op": "commit", "message": "fix: zero scores"}\n```');
    expect(await within(p).findByRole("region", { name: "Query on local" })).toBeInTheDocument();
    expect(within(p).getByRole("region", { name: "Git: Commit" })).toBeInTheDocument();
  });

  it("a keel block of a kind keel does not know shows as code", async () => {
    const p = await chatWith('Look:\n```keel-nope\n{"op": "x"}\n```\nDone.');
    expect(await within(p).findByText('{"op": "x"}')).toBeInTheDocument();
    expect(within(p).getByText("Done.")).toBeInTheDocument();
  });
});
