// v0.10.0 the Database and Git plugins in the web: install on Tools, Connections › GitHub, the Code page's branch tab,
// KeelBot's blocks of theirs as their cards, and Claude Code's question in the Inbox. Their own pieces are tested with
// them: plugins/db/web/test (Connections › Databases, Code › Database, KeelBot's query button, a db: block's settings on
// the Workflows page; Map › Query: plugins/map/web/test) and plugins/git/web/test (Code › Git, KeelBot's git buttons).

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { db, server } from "./setup";

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

// v0.17.1 plugins are switched only in Control › Plugins (docs/plugins/22-one-plugins-page.md): the Tools page has no
// Plugins section any more; a plugin that is on still brings its server to "Who may use what".
describe("Tools: no Plugins section", () => {
  it("shows servers, recent calls and who may use what, and no plugin cards or switches", async () => {
    location.hash = "#/tools";
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Servers" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recent tool calls" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Plugins" })).toBeNull();
    expect(screen.queryByRole("article", { name: "Database plugin" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Install for / })).toBeNull();
    expect(screen.queryByText(/Plugins keel adds/)).toBeNull();
    // keel 0.28 the redesign: the plugins that bring tools are turned on in Add features
    expect(screen.getByRole("link", { name: "Add features" })).toHaveAttribute("href", "#/features");
    expect(screen.queryByRole("checkbox", { name: "explorer may use keel-db" })).toBeNull();
  });

  it("a plugin that is on for this project still brings its server to who may use what", async () => {
    db.projectPlugins["ludus-engine"] = { db: true };
    location.hash = "#/tools";
    render(<App />);
    expect(await screen.findByRole("checkbox", { name: "explorer may use keel-db" })).toBeEnabled();
  });
});

describe("Control › Plugins: a plugin each project turns on", () => {
  function installDbGit() {
    db.market.installed = [
      { name: "db", title: "Database", version: "1.4.0", from: "image", on: true, parts: ["engine", "api", "web"], status: "loaded", can_remove: false },
      { name: "git", title: "Git", version: "1.0.0", from: "image", on: true, parts: ["engine", "api", "web"], status: "loaded", can_remove: false },
      { name: "wiki", title: "Wiki", version: "1.1.0", from: "image", on: true, parts: ["web"], status: "loaded", can_remove: false },
    ];
  }

  // v0.17.1 one control per row: [ Off | This project | All projects ] (PluginProjects.tsx: what each sends)
  const choices = (group: HTMLElement) =>
    within(group).getAllByRole("radio").map((r) => `${r.textContent}${r.getAttribute("aria-checked") === "true" ? "*" : ""}`);
  const putsOf = (pid: string, name: string) => calls("PUT", `/api/projects/${pid}/plugins/${name}`).map((c) => c.body);

  it("Installed: one control with three named choices, each with its api call", async () => {
    const user = userEvent.setup();
    installDbGit();
    location.hash = "#/settings/plugins";
    render(<App />);
    const row = await screen.findByTestId("plugin-db");
    const group = await within(row).findByRole("radiogroup", { name: "Database: where it is on" });
    expect(choices(group)).toEqual(["Off*", "This project", "All projects"]);
    expect(within(row).queryByRole("switch")).toBeNull(); // the control is the only one: no second switch
    // the status says keel loaded it (keel-wide), not "On" beside a control that says Off for this project
    expect(within(row).getByText("Loaded")).toHaveAttribute("title", expect.stringContaining("Where it is on"));
    expect(within(row).queryByText("On")).toBeNull();
    expect(screen.getByRole("columnheader", { name: "Where it is on" })).toBeInTheDocument();

    await user.click(within(group).getByRole("radio", { name: "This project" }));
    await waitFor(() => expect(putsOf("ludus-engine", "db")).toEqual([{ enabled: true, scope: "project" }]));
    await waitFor(() => expect(choices(group)).toEqual(["Off", "This project*", "All projects"]));
    expect(db.projectPlugins["ludus-engine"]).toEqual({ db: true });

    await user.click(within(group).getByRole("radio", { name: "All projects" }));
    await waitFor(() => expect(putsOf("ludus-engine", "db").at(-1)).toEqual({ enabled: true, scope: "all" }));
    await waitFor(() => expect(choices(group)).toEqual(["Off", "This project", "All projects*"]));
    expect(db.plugins.db).toBe(true);

    // back to only this project: the every-project row goes off first, then the project's own row
    await user.click(within(group).getByRole("radio", { name: "This project" }));
    await waitFor(() => expect(putsOf("ludus-engine", "db").slice(2)).toEqual([
      { enabled: false, scope: "all" }, { enabled: true, scope: "project" }]));
    await waitFor(() => expect(choices(group)).toEqual(["Off", "This project*", "All projects"]));
    expect(db.plugins.db).toBe(false);

    await user.click(within(group).getByRole("radio", { name: "Off" }));
    await waitFor(() => expect(putsOf("ludus-engine", "db").at(-1)).toEqual({ enabled: false, scope: "project" }));
    await waitFor(() => expect(choices(group)).toEqual(["Off*", "This project", "All projects"]));
    expect(calls("PUT", "/api/plugins/db")).toHaveLength(0); // on keel-wide already: not touched
  });

  it("Installed: All projects to This project puts All projects back when the second call fails", async () => {
    const user = userEvent.setup();
    installDbGit();
    location.hash = "#/settings/plugins";
    render(<App />);
    const row = await screen.findByTestId("plugin-db");
    const group = await within(row).findByRole("radiogroup", { name: "Database: where it is on" });
    await user.click(within(group).getByRole("radio", { name: "All projects" }));
    await waitFor(() => expect(choices(group)).toEqual(["Off", "This project", "All projects*"]));
    server.use(http.put("/api/projects/:pid/plugins/:name", async ({ request }) => {
      const b = (await request.clone().json()) as { enabled: boolean; scope?: string };
      if (b.enabled && b.scope === "project") return HttpResponse.json({ error: "The project file is read-only" }, { status: 500 });
      if (b.scope === "all") db.plugins.db = b.enabled;
      return HttpResponse.json([]);
    }));
    await user.click(within(group).getByRole("radio", { name: "This project" }));
    await waitFor(() => expect(db.plugins.db).toBe(true)); // off for all first, then put back
    expect(await within(row).findByText("The project file is read-only")).toBeInTheDocument();
  });

  it("Installed: a project's choice for a plugin keel has loaded says applied (no restart)", async () => {
    const user = userEvent.setup();
    installDbGit();
    location.hash = "#/settings/plugins";
    render(<App />);
    const row = await screen.findByTestId("plugin-db");
    const group = await within(row).findByRole("radiogroup", { name: "Database: where it is on" });
    await user.click(within(group).getByRole("radio", { name: "This project" }));
    expect(await within(row).findByText("applied")).toBeInTheDocument();
    expect(within(row).queryByText("applies after restart")).toBeNull();
    expect(screen.queryByText("Changes wait for a restart")).toBeNull();
  });

  it("Installed: a plugin that is off keel-wide shows Off; This project turns it on keel-wide too", async () => {
    const user = userEvent.setup();
    installDbGit();
    db.market.installed[0].on = false;
    db.projectPlugins["ludus-engine"] = { db: true }; // its own row says on, but nothing loads while keel has it off
    location.hash = "#/settings/plugins";
    render(<App />);
    const group = await within(await screen.findByTestId("plugin-db")).findByRole("radiogroup", { name: "Database: where it is on" });
    await waitFor(() => expect(choices(group)).toEqual(["Off*", "This project", "All projects"]));
    await user.click(within(group).getByRole("radio", { name: "This project" }));
    await waitFor(() => expect(calls("PUT", "/api/plugins/db")[0]?.body).toEqual({ on: true }));
    await waitFor(() => expect(putsOf("ludus-engine", "db")).toEqual([{ enabled: true, scope: "project" }]));
    await waitFor(() => expect(choices(group)).toEqual(["Off", "This project*", "All projects"]));
    // turning it on keel-wide waits for the restart: the row says so, not "applied"
    const row = screen.getByTestId("plugin-db");
    expect(await within(row).findByText("applies after restart")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Restart now" })).toBeInTheDocument();
    expect(within(row).queryByText("applied")).toBeNull();
  });

  it("Installed: on in other projects but not this one: a hint that links to the plugin page's Projects", async () => {
    const user = userEvent.setup();
    installDbGit();
    db.projectPlugins.platform = { db: true };
    db.projectPlugins.yegi = { db: true };
    location.hash = "#/settings/plugins";
    render(<App />);
    const row = await screen.findByTestId("plugin-db");
    const hint = await within(row).findByRole("link", { name: "on in 2 other projects" });
    expect(hint).toHaveAttribute("href", "#/settings/plugins/db?at=projects");
    // git is on nowhere: no hint
    expect(within(screen.getByTestId("plugin-git")).queryByRole("link", { name: /other project/ })).toBeNull();

    await user.click(within(row).getByRole("radio", { name: "This project" }));
    expect(await within(row).findByRole("link", { name: "also on in 2 other projects" })).toBeInTheDocument();
    await user.click(within(row).getByRole("radio", { name: "All projects" }));
    await waitFor(() => expect(within(row).queryByRole("link", { name: /other project/ })).toBeNull());
  });

  it("Installed: the arrow keys move the choice and apply it; Home and End; one tab stop", async () => {
    const user = userEvent.setup();
    installDbGit();
    location.hash = "#/settings/plugins";
    render(<App />);
    const group = await within(await screen.findByTestId("plugin-git")).findByRole("radiogroup", { name: "Git: where it is on" });
    const radio = (name: string) => within(group).getByRole("radio", { name });
    expect(within(group).getAllByRole("radio").map((r) => r.tabIndex)).toEqual([0, -1, -1]);
    radio("Off").focus();

    await user.keyboard("{ArrowRight}");
    expect(radio("This project")).toHaveFocus();
    await waitFor(() => expect(putsOf("ludus-engine", "git")).toEqual([{ enabled: true, scope: "project" }]));
    await waitFor(() => expect(radio("This project")).toHaveAttribute("aria-checked", "true"));
    expect(within(group).getAllByRole("radio").map((r) => r.tabIndex)).toEqual([-1, 0, -1]);

    await user.keyboard("{End}");
    expect(radio("All projects")).toHaveFocus();
    await waitFor(() => expect(putsOf("ludus-engine", "git").at(-1)).toEqual({ enabled: true, scope: "all" }));
    await waitFor(() => expect(radio("All projects")).toHaveAttribute("aria-checked", "true"));

    await user.keyboard("{ArrowRight}"); // wraps to the first
    expect(radio("Off")).toHaveFocus();
    await waitFor(() => expect(putsOf("ludus-engine", "git").at(-1)).toEqual({ enabled: false, scope: "project" }));
    await waitFor(() => expect(radio("Off")).toHaveAttribute("aria-checked", "true"));

    await user.keyboard("{ArrowLeft}"); // wraps to the last
    expect(radio("All projects")).toHaveFocus();
    await waitFor(() => expect(radio("All projects")).toHaveAttribute("aria-checked", "true"));

    await user.keyboard("{Home}");
    expect(radio("Off")).toHaveFocus();
    await waitFor(() => expect(putsOf("ludus-engine", "git").at(-1)).toEqual({ enabled: false, scope: "project" }));
    await waitFor(() => expect(radio("Off")).toHaveAttribute("aria-checked", "true"));
  });

  it("Installed: a plugin with no per-project choice has one switch with On/Off beside it", async () => {
    const user = userEvent.setup();
    installDbGit();
    location.hash = "#/settings/plugins";
    render(<App />);
    const wiki = await screen.findByTestId("plugin-wiki");
    // the per-project plugins get their control once the project's list is read; wiki never does
    await within(screen.getByTestId("plugin-db")).findByRole("radiogroup");
    expect(within(wiki).queryByRole("radiogroup")).toBeNull();
    const sw = within(wiki).getByRole("switch", { name: "Wiki on" });
    expect(sw).toBeChecked();
    expect(sw.closest("label")).toHaveTextContent("On");
    await user.click(sw);
    await waitFor(() => expect(calls("PUT", "/api/plugins/wiki")[0]?.body).toEqual({ on: false }));
    await waitFor(() => expect(within(screen.getByTestId("plugin-wiki")).getByRole("switch", { name: "Wiki on" }).closest("label")).toHaveTextContent("Off"));
  });

  it("Installed: the On column never wraps its control (a min-width rule; jsdom draws no CSS)", async () => {
    const css = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "styles", "plugins-page.css"), "utf8");
    const rule = (selector: string) => {
      const at = css.indexOf(`\n${selector} {`);
      return at < 0 ? "" : css.slice(at, css.indexOf("}", at));
    };
    expect(rule(".mk-installed td.mk-oncell")).toMatch(/min-width: max-content;[\s\S]*white-space: nowrap;/);
    expect(rule(".mk-seg")).toMatch(/min-width: max-content;[\s\S]*white-space: nowrap;/);
    expect(rule(".mk-seg-opt")).toContain("white-space: nowrap;");
    installDbGit();
    location.hash = "#/settings/plugins";
    render(<App />);
    const group = await within(await screen.findByTestId("plugin-db")).findByRole("radiogroup");
    expect(group.closest("td")).toHaveClass("mk-oncell");
    expect(group).toHaveClass("mk-seg");
  });

  it("the plugin's page: what it adds, and Projects with the same control per project", async () => {
    const user = userEvent.setup();
    installDbGit();
    db.projectPlugins.platform = { db: true };
    const seen: string[] = [];
    Element.prototype.scrollIntoView = function (this: Element) { seen.push(this.id); };
    location.hash = "#/settings/plugins/db?at=projects";
    render(<App />);
    const about = await screen.findByRole("region", { name: "About" });
    await waitFor(() => expect(about).toHaveTextContent("KeelBot toolsdb_connections, db_schema, db_query"));
    expect(about).toHaveTextContent("Workflow blocksdb:check, db:query");
    expect(about).toHaveTextContent("Commands/sql");
    expect(about).toHaveTextContent("Shows inConnections · Map › Query · Workflows blocks · KeelBot · Inbox");

    const projects = await screen.findByRole("region", { name: "Projects" });
    const list = await within(projects).findByRole("list", { name: "Projects with Database" });
    expect(within(list).getAllByRole("radiogroup").map((s) => s.getAttribute("aria-label"))).toEqual([
      "Database in ludus-engine: where it is on", "Database in platform: where it is on", "Database in YegiResearcher: where it is on"]);
    const group = (p: string) => within(list).getByRole("radiogroup", { name: `Database in ${p}: where it is on` });
    expect(choices(group("platform"))).toEqual(["Off", "This project*", "All projects"]);
    expect(choices(group("ludus-engine"))).toEqual(["Off*", "This project", "All projects"]);
    await waitFor(() => expect(seen).toContain("projects")); // the hint's link scrolls here

    await user.click(within(group("YegiResearcher")).getByRole("radio", { name: "This project" }));
    await waitFor(() => expect(putsOf("yegi", "db")).toEqual([{ enabled: true, scope: "project" }]));
    await waitFor(() => expect(choices(group("YegiResearcher"))).toEqual(["Off", "This project*", "All projects"]));

    await user.click(within(group("ludus-engine")).getByRole("radio", { name: "All projects" }));
    await waitFor(() => expect(putsOf("ludus-engine", "db")).toEqual([{ enabled: true, scope: "all" }]));
    // every project follows it, except the ones with their own choice
    await waitFor(() => expect(choices(group("ludus-engine"))).toEqual(["Off", "This project", "All projects*"]));
    expect(choices(group("platform"))).toEqual(["Off", "This project*", "All projects"]);
    expect(projects).not.toHaveTextContent("On for every project");
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  });

  it("a plugin no project switches has no Projects section", async () => {
    installDbGit();
    location.hash = "#/settings/plugins/wiki";
    render(<App />);
    expect(await screen.findByRole("region", { name: "About" })).toBeInTheDocument();
    // the projects' lists are read; wiki is in none of them
    await waitFor(() => expect(db.calls.length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("region", { name: "Projects" })).toBeNull();
  });
});

describe("Workflows: a block of a plugin that is off", () => {
  it("says to turn it on in Settings › Plugins, with a link to the plugin's page", async () => {
    const user = userEvent.setup();
    location.hash = "#/workflows/feature";
    render(<App />);
    await user.click((await screen.findAllByRole("button", { name: /^verify_red, plain code/ }))[0]);
    const act = await screen.findByLabelText("What it runs");
    await user.clear(act);
    await user.type(act, "db:check");
    expect(await screen.findByText(/This block belongs to a plugin that is off for this project/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings › Plugins" })).toHaveAttribute("href", "#/settings/plugins/db");
  });
});

describe("Connections › GitHub", () => {
  it("keeps the GitHub token", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings/connections";
    render(<App />);
    await user.type(await screen.findByLabelText("GitHub token"), "ghp_123456789");
    await user.click(within(screen.getByTestId("github")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT", "/api/secrets/GITHUB_REPO_TOKEN")[0]?.body).toEqual({ value: "ghp_123456789" }));
    expect(await screen.findByText("token set …789")).toBeInTheDocument();
  });
});

describe("Connections › GitHub › Sign in with GitHub", () => {
  it("opens the shared login drawer, shows the code and the link, then who is connected, and signs out", async () => {
    const user = userEvent.setup();
    location.hash = "#/settings/connections";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Sign in with GitHub" }));
    const dlg = screen.getByRole("dialog", { name: "Set up GitHub login" });
    expect(within(dlg).getByText(/authorize it when GitHub asks/)).toBeInTheDocument();
    await user.clear(within(dlg).getByLabelText("GitHub host"));
    await user.type(within(dlg).getByLabelText("GitHub host"), "ghe.example.com");
    await user.click(within(dlg).getByRole("button", { name: "Start login" }));
    // step 2: open (or copy) the link; step 3: the code to type there
    const link = await within(dlg).findByRole("link", { name: "Open ghe.example.com" });
    expect(link).toHaveAttribute("href", "https://ghe.example.com/login/device");
    expect(calls("POST", "/api/github/login")[0]?.body).toEqual({ host: "ghe.example.com" });
    expect(within(dlg).queryByText("AB12-CD34")).not.toBeInTheDocument();
    await user.click(within(dlg).getByRole("button", { name: "Copy link" }));
    expect(await within(dlg).findByText("AB12-CD34")).toBeInTheDocument();
    expect(within(dlg).getByRole("button", { name: "Copy code" })).toBeInTheDocument();
    // the poll finds it done: who is connected, with the scopes and the host (in the drawer and on the card)
    expect(await within(dlg).findByText("✓ connected as mili-dev · repo, workflow · github.com", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(within(dlg).queryByText("AB12-CD34")).not.toBeInTheDocument();
    await user.click(within(dlg).getAllByRole("button", { name: "Close" })[0]);
    expect(screen.queryByRole("dialog", { name: "Set up GitHub login" })).not.toBeInTheDocument();
    expect(calls("DELETE", "/api/github/login/gl-1")).toHaveLength(0);   // it was saved: nothing to cancel
    expect(await screen.findByText("connected as mili-dev · repo, workflow · github.com")).toBeInTheDocument();
    // the card keeps no code UI; pasting a token stays as the second way
    expect(screen.queryByLabelText("GitHub host")).not.toBeInTheDocument();
    expect(screen.getByLabelText("GitHub token")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(calls("DELETE", "/api/github")).toHaveLength(1));
    expect(await screen.findByText("no token")).toBeInTheDocument();
  });

  it("closing the drawer while it waits cancels the sign-in", async () => {
    const user = userEvent.setup();
    server.use(http.get("/api/github/login/:id", () => HttpResponse.json({ id: "gl-1", status: "waiting", host: "github.com", url: "https://github.com/login/device",
      code: "AB12-CD34", message: "Waiting", login: null, scopes: [], via: null })));
    location.hash = "#/settings/connections";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Sign in with GitHub" }));
    await user.click(screen.getByRole("button", { name: "Start login" }));
    await user.click(await screen.findByRole("button", { name: "Copy link" }));
    expect(await screen.findByText("AB12-CD34")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Close" })[0]);
    await waitFor(() => expect(calls("DELETE", "/api/github/login/gl-1")).toHaveLength(1));
    expect(screen.queryByRole("dialog", { name: "Set up GitHub login" })).not.toBeInTheDocument();
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
