// 0.16.1 plugin track: the marketplace for workflows in the web (docs/plugins/21-workflow-marketplace.md §4):
// Settings › Plugins › Marketplace's kind switch (Plugins · Workflows, ?kind=workflows), the Workflows view's cards and
// its note when no catalog answers, a workflow's page (#/settings/plugins/workflow/<name>), the install dialog (this project or
// all, 409 missing plugins / agents), and on Build › Workflows the badge, Update (keep my copy / replace), Remove and
// Publish to marketplace. keel's own workflows look as before.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { Workflow } from "../api";
import * as fx from "./fixtures";
import { fromMarket } from "./marketWorkflowHandlers";
import { db } from "./setup";

const calls = (method: string, path: string) =>
  db.calls.filter((c) => c.method === method && c.path === path);

function open(hash: string) {
  location.hash = hash;
  render(<App />);
}

const card = (title: string) =>
  screen.findByRole("article", { name: `${title} workflow` });

describe("Settings › Plugins › Marketplace: the kind switch", () => {
  it("shows plugins first; Workflows keeps ?tab=market and adds ?kind=workflows", async () => {
    const user = userEvent.setup();
    open("#/settings/plugins?tab=market");
    const kinds = await screen.findByRole("group", { name: "What to find" });
    expect(within(kinds).getByRole("button", { name: "Plugins" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByRole("article", { name: "Database plugin" })).toBeInTheDocument();
    await user.click(within(kinds).getByRole("button", { name: "Workflows" }));
    await waitFor(() => expect(location.hash).toBe("#/settings/plugins?tab=market&kind=workflows"));
    expect(await card("Release notes")).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Database plugin" })).toBeNull();
    expect(screen.getByRole("tab", { name: "Marketplace" })).toHaveAttribute("aria-selected", "true");
    // and back
    await user.click(within(kinds).getByRole("button", { name: "Plugins" }));
    await waitFor(() => expect(location.hash).toBe("#/settings/plugins?tab=market"));
    expect(await screen.findByRole("article", { name: "Database plugin" })).toBeInTheDocument();
  });

  it("?kind=workflows opens the Workflows view, and the search names the project", async () => {
    open("#/settings/plugins?tab=market&kind=workflows");
    expect(await card("Hotfix")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Workflows" })).toHaveAttribute("aria-pressed", "true");
    expect(db.mw.searches[0]).toEqual({ q: "", category: "", project: "ludus-engine" });
  });
});

describe("Marketplace › Workflows: the cards", () => {
  it("shows the review in short (red when it runs commands), what it needs, and Install / Installed here / Update", async () => {
    open("#/settings/plugins?tab=market&kind=workflows");
    const rel = await card("Release notes");
    expect(rel).toHaveTextContent("ana-k");
    expect(rel).toHaveTextContent("community");
    expect(rel).toHaveTextContent("1.2.0");
    expect(rel).toHaveTextContent("Writes release notes from the commits");
    const short = within(rel).getByText("6 steps · 2 gates · runs 1 command");
    expect(short).toHaveClass("wfm-short", "bad");
    expect(within(rel).getByLabelText("Runs shell commands")).toBeInTheDocument();
    // the git plugin is not here: its chip warns
    const needs = within(rel).getByLabelText("Needs");
    expect(within(needs).getByText("needs git")).toHaveClass("warn");
    expect(within(needs).getByText("agent release-writer")).toBeInTheDocument();
    expect(within(rel).getByRole("button", { name: "Install Release notes" })).toBeEnabled();
    expect(within(rel).getByRole("link", { name: "Release notes" })).toHaveAttribute("href", "#/settings/plugins/workflow/release-notes");

    const hot = await card("Hotfix");
    const shortHot = within(hot).getByText("4 steps · 1 gate");
    expect(shortHot).not.toHaveClass("bad");
    expect(within(hot).queryByLabelText("Runs shell commands")).toBeNull();
    expect(within(hot).getByTitle("Verified publisher")).toBeInTheDocument();
    // keel's own come first, under their own heading
    const keelSection = screen.getByRole("region", { name: "From keel" });
    expect(within(keelSection).getByRole("article", { name: "Hotfix workflow" })).toBeInTheDocument();

    const tidy = await card("PR tidy");
    expect(within(tidy).getByRole("button", { name: "Update to 1.1.0" })).toBeInTheDocument();
  });

  it("searches, and the category chips narrow the list", async () => {
    const user = userEvent.setup();
    open("#/settings/plugins?tab=market&kind=workflows");
    await card("Release notes");
    // the category chips come with the catalog's answer, a moment after the cards on a slow machine
    await user.click(await screen.findByRole("button", { name: "Docs" }));
    await waitFor(() => expect(screen.queryByRole("article", { name: "Hotfix workflow" })).toBeNull());
    expect(screen.getByRole("article", { name: "Release notes workflow" })).toBeInTheDocument();
    expect(db.mw.searches.at(-1)?.category).toBe("docs");
    await user.click(screen.getByRole("button", { name: "All" }));
    await user.type(screen.getByRole("searchbox", { name: "Search workflows" }), "hotfix");
    await waitFor(() => expect(screen.queryByRole("article", { name: "Release notes workflow" })).toBeNull());
    expect(await card("Hotfix")).toBeInTheDocument();
  });

  it("with no workflow catalog: a clear note, never an empty page", async () => {
    db.mw.down = true;
    open("#/settings/plugins?tab=market&kind=workflows");
    const note = await screen.findByRole("note", { name: "No workflow catalog" });
    expect(note).toHaveTextContent("No workflow catalog answers yet.");
    expect(note).toHaveTextContent("make your own in Build › Workflows and publish it");
    expect(within(note).getByRole("link", { name: "Build › Workflows" })).toHaveAttribute("href", "#/workflows");
    expect(within(note).getByRole("link", { name: "Sources and rules" })).toBeInTheDocument();
  });

  it("a catalog that lists no workflows says so too", async () => {
    db.mw.catalog = [];
    db.mw.sources = [];
    open("#/settings/plugins?tab=market&kind=workflows");
    expect(await screen.findByRole("note", { name: "No workflow catalog" })).toHaveTextContent(
      "The catalogs list no workflows yet.",
    );
  });
});

describe("A workflow's page", () => {
  it("shows the steps as a graph, the YAML folded, the review, what it needs, versions and checks", async () => {
    db.market.installed = [{ name: "git", title: "Git", version: "1.0.0", on: false, status: "off", from: "image" }];
    open("#/settings/plugins/workflow/release-notes");
    expect(await screen.findByRole("heading", { name: "Release notes", level: 1 })).toBeInTheDocument();
    const crumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumb).getByRole("link", { name: "Workflows" })).toHaveAttribute("href", "#/settings/plugins?tab=market&kind=workflows");
    expect(screen.getByText("Content only")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install 1.2.0" })).toBeEnabled();

    // the builder's own graph, read-only: no step to edit, remove or insert
    const graph = screen.getByRole("img", { name: "Steps of Release notes" });
    expect(within(graph).getByText("draft notes")).toBeInTheDocument();
    expect(within(graph).queryByRole("button")).toBeNull();
    // the YAML, folded
    const yaml = screen.getByText("The YAML").closest("details")!;
    expect(yaml).not.toHaveAttribute("open");
    expect(within(yaml).getByLabelText("YAML of Release notes")).toHaveTextContent("agent: release-writer");
    // keel's code editor, read only (0.17.4)
    expect(within(yaml).getByLabelText("YAML of Release notes")).toHaveClass("ce-input");
    expect(within(yaml).getByLabelText("YAML of Release notes")).toHaveAttribute("readonly");
    expect(yaml.querySelector(".ce-gutter")).not.toBeNull();

    const review = screen.getByRole("region", { name: "Install review" });
    const list = within(review).getByRole("list", { name: "Install review" });
    expect(list).toHaveTextContent("Runs release-writer, reviewer");
    expect(list).toHaveTextContent("Uses no MCP server");
    expect(list).toHaveTextContent("Runs 1 shell command");
    expect(within(list).getByText("git log --oneline v1.1.0..HEAD").tagName).toBe("CODE");
    expect(list).toHaveTextContent("Edits files in the project");
    expect(list).toHaveTextContent("keel rules on");
    expect(list).toHaveTextContent("It runs shell commands."); // keel's warning

    const needs = screen.getByRole("region", { name: "Needs" });
    const git = within(needs).getByTestId("needs-plugin-git");
    expect(git).toHaveTextContent("Git >=1.0.0 plugin · off");
    expect(within(git).getByRole("link", { name: "turn on" })).toHaveAttribute("href", "#/settings/plugins/git");
    expect(await within(needs).findByText("agent · missing")).toBeInTheDocument();

    const versions = screen.getByRole("region", { name: "Versions" });
    expect(within(versions).getAllByRole("row").map((r) => r.textContent)).toEqual([
      "VersionWorks withReleasedWhat it does",
      "1.2.0keel >=0.16.12026-10-15!6 steps · 2 gates · runs 1 command",
      "1.1.0keel >=0.16.12026-09-305 steps · 1 gate",
    ]);
    expect(screen.getByRole("region", { name: "Checks keel runs before install" })).toHaveTextContent(
      "Signed by ana-k, a key the catalog lists",
    );
  });

  it("an agent this keel has shows as here; a plugin not installed links to its page to install it", async () => {
    db.agents.push({ ...db.agents[0], id: "release-writer", label: "release-writer", custom: true });
    open("#/settings/plugins/workflow/release-notes");
    const needs = await screen.findByRole("region", { name: "Needs" });
    expect(await within(needs).findByText("agent · here")).toBeInTheDocument();
    expect(within(needs).getByRole("link", { name: "install it" })).toHaveAttribute("href", "#/settings/plugins/git");
  });

  it("a workflow keel refuses: its error, with the way back", async () => {
    db.mw.catalog[0].refused = "Its publisher's key was revoked.";
    open("#/settings/plugins/workflow/release-notes");
    expect(await screen.findByRole("alert")).toHaveTextContent("Its publisher's key was revoked.");
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toBeInTheDocument();
  });
});

describe("Installing a workflow", () => {
  it("asks this project (the default) or all projects, shows the review again, and installs", async () => {
    const user = userEvent.setup();
    open("#/settings/plugins/workflow/release-notes");
    await user.click(await screen.findByRole("button", { name: "Install 1.2.0" }));
    const dlg = await screen.findByRole("dialog", { name: "Install Release notes 1.2.0?" });
    expect(within(dlg).getByRole("radio", { name: /This project/ })).toBeChecked();
    expect(within(dlg).getByRole("radio", { name: /This project/ }).closest("label")).toHaveTextContent("ludus-engine only");
    expect(within(dlg).getByRole("list", { name: "Install review" })).toHaveTextContent("Runs 1 shell command");
    await user.click(within(dlg).getByRole("radio", { name: /All projects/ }));
    await user.click(within(dlg).getByRole("button", { name: "Install" }));
    await waitFor(() =>
      expect(calls("POST", "/api/projects/ludus-engine/workflows/market/install")[0]?.body).toEqual({ name: "release-notes", scope: "all" }),
    );
    const done = await screen.findByRole("dialog", { name: "Release notes is installed" });
    expect(within(done).getByRole("link", { name: "Open in Build › Workflows" })).toHaveAttribute("href", "#/workflows/release-notes");
  });

  it("an older version can be picked", async () => {
    const user = userEvent.setup();
    open("#/settings/plugins/workflow/release-notes");
    await user.click(await screen.findByRole("button", { name: "Install 1.2.0" }));
    const dlg = await screen.findByRole("dialog", { name: "Install Release notes 1.2.0?" });
    await user.selectOptions(within(dlg).getByLabelText("Version"), "1.1.0");
    expect(within(dlg).getByRole("list", { name: "Install review" })).toHaveTextContent("Runs no shell command");
    await user.click(within(dlg).getByRole("button", { name: "Install" }));
    await waitFor(() =>
      expect(calls("POST", "/api/projects/ludus-engine/workflows/market/install")[0]?.body).toEqual({
        name: "release-notes", version: "1.1.0", scope: "project",
      }),
    );
  });

  it("from a card: a 409 with missing plugins offers their pages (never installs them); missing agents are named", async () => {
    const user = userEvent.setup();
    db.mw.refuse = {
      error: "Release notes needs what this keel does not have.",
      missing_plugins: ["git"],
      missing_agents: ["release-writer"],
    };
    open("#/settings/plugins?tab=market&kind=workflows");
    await user.click(within(await card("Release notes")).getByRole("button", { name: "Install Release notes" }));
    const dlg = await screen.findByRole("dialog", { name: "Install Release notes 1.2.0?" });
    await user.click(within(dlg).getByRole("button", { name: "Install" }));
    const why = await within(dlg).findByRole("alert", { name: "Missing to install" });
    expect(why).toHaveTextContent("Release notes needs what this keel does not have.");
    expect(within(why).getByRole("link", { name: "Open the git plugin's page" })).toHaveAttribute("href", "#/settings/plugins/git");
    expect(why).toHaveTextContent("keel has no agent named release-writer");
    expect(calls("POST", "/api/plugins/install")).toHaveLength(0);
    // the plugin path: its page
    await user.click(within(why).getByRole("link", { name: "Open the git plugin's page" }));
    await waitFor(() => expect(location.hash).toBe("#/settings/plugins/git"));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Install Release notes 1.2.0?" })).toBeNull());
  });
});

describe("Build › Workflows: a workflow from the marketplace", () => {
  it("shows the badge, the update that is ready, and Update keeps my copy by default", async () => {
    const user = userEvent.setup();
    db.workflows.push(fromMarket());
    open("#/workflows/pr-tidy");
    const top = await screen.findByRole("region", { name: "This workflow" });
    expect(await within(top).findByText("from marketplace · 1.0.0")).toBeInTheDocument();
    expect(within(top).getByText("1.1.0 ready")).toBeInTheDocument();
    // the badge says where it came from: no second "from marketplace/pr-tidy" tag
    expect(within(top).queryByText("from marketplace/pr-tidy")).toBeNull();
    expect(screen.getByRole("navigation", { name: "Workflows" })).toHaveTextContent("marketplace 1.0.0");
    await user.click(within(top).getByRole("button", { name: "Update" }));
    const dlg = await screen.findByRole("dialog", { name: "Update PR tidy to 1.1.0?" });
    expect(within(dlg).getByRole("radio", { name: /Keep my copy/ })).toBeChecked();
    await user.click(within(dlg).getByRole("button", { name: "Update" }));
    await waitFor(() =>
      expect(calls("POST", "/api/projects/ludus-engine/workflows/market/pr-tidy/update")[0]?.body).toEqual({ version: "1.1.0", mode: "keep" }),
    );
    expect(await screen.findByText("PR tidy is updated to 1.1.0. Your changes stay in a copy.")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /^PR tidy \(mine\)/ })).toBeInTheDocument();
  });

  it("the menu has Update and Remove; Replace replaces; Remove deletes it and keeps its runs", async () => {
    const user = userEvent.setup();
    db.workflows.push(fromMarket());
    open("#/workflows/pr-tidy");
    const top = await screen.findByRole("region", { name: "This workflow" });
    await user.click(await within(top).findByRole("button", { name: "More for PR tidy" }));
    const menu = within(top).getByRole("menu", { name: "PR tidy menu" });
    expect(within(menu).getAllByRole("menuitem").map((b) => b.textContent)).toEqual(["Update to 1.1.0", "Remove"]);
    await user.click(within(menu).getByRole("menuitem", { name: "Update to 1.1.0" }));
    const dlg = await screen.findByRole("dialog", { name: "Update PR tidy to 1.1.0?" });
    await user.click(within(dlg).getByRole("radio", { name: /Replace/ }));
    await user.click(within(dlg).getByRole("button", { name: "Update" }));
    await waitFor(() =>
      expect(calls("POST", "/api/projects/ludus-engine/workflows/market/pr-tidy/update")[0]?.body).toEqual({ version: "1.1.0", mode: "replace" }),
    );
    expect(await within(top).findByText("from marketplace · 1.1.0")).toBeInTheDocument();
    expect(within(top).queryByText("1.1.0 ready")).toBeNull();

    await user.click(within(top).getByRole("button", { name: "More for PR tidy" }));
    await user.click(within(top).getByRole("menuitem", { name: "Remove" }));
    const confirm = screen.getByRole("alertdialog", { name: "Delete PR tidy?" });
    expect(confirm).toHaveTextContent("Flows that ran with it keep their history.");
    await user.click(within(confirm).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls("DELETE", "/api/workflows/pr-tidy")).toHaveLength(1));
    await waitFor(() => expect(db.workflows.some((w) => w.id === "pr-tidy")).toBe(false));
    expect(await screen.findByText(/^PR tidy is deleted\. Flows that ran with it keep their history\./)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: /^PR tidy/ })).toBeNull());
  });
});

describe("Build › Workflows: the others look as before", () => {
  it("keel's templates have no badge and no menu; the list reads as before", async () => {
    open("#/workflows/feature");
    const top = await screen.findByRole("region", { name: "This workflow" });
    expect(await within(top).findByText("keel template")).toBeInTheDocument();
    expect(within(top).queryByText(/from marketplace/)).toBeNull();
    expect(within(top).queryByRole("button", { name: /More for/ })).toBeNull();
    expect(within(top).queryByRole("button", { name: "Update" })).toBeNull();
    const rail = screen.getByRole("navigation", { name: "Workflows" });
    expect(within(rail).getByRole("button", { name: /^feature \(keel\)/ })).toHaveTextContent("feature (keel)12 steps · v3");
    expect(rail).not.toHaveTextContent("marketplace");
    // no marketplace call on Build › Workflows
    expect(db.mw.searches).toHaveLength(0);
  });

  it("the person's own workflow: Publish to marketplace and Delete in its menu", async () => {
    const user = userEvent.setup();
    const mine: Workflow = { ...fx.fixWorkflow, id: "mine", name: "My flow", based_on: "keel/fix", source: "yours" };
    db.workflows.push(mine);
    open("#/workflows/mine");
    const top = await screen.findByRole("region", { name: "This workflow" });
    expect(await within(top).findByText("from keel/fix")).toBeInTheDocument();
    expect(within(top).queryByText(/from marketplace/)).toBeNull();
    await user.click(within(top).getByRole("button", { name: "More for My flow" }));
    expect(within(top).getAllByRole("menuitem").map((b) => b.textContent)).toEqual(["Publish to marketplace", "Delete"]);
  });
});

describe("Publish to marketplace", () => {
  it("prefills the package, shows the review, downloads it with the fields, and says the next steps", async () => {
    const user = userEvent.setup();
    localStorage.setItem("keel2.publisher", "ana-k");
    const mine: Workflow = {
      ...fx.fixWorkflow, id: "mine", name: "My flow", based_on: "keel/fix", source: "yours",
      yaml: 'name: My flow\nsummary: Fixes a bug fast.\nkeel_rules: true\nsteps:\n  - id: look\n    kind: agent\n    agent: reproducer\n  - id: run\n    kind: code\n    action: "run: npm test"\n',
    };
    db.workflows.push(mine);
    open("#/workflows/mine");
    const top = await screen.findByRole("region", { name: "This workflow" });
    await user.click(await within(top).findByRole("button", { name: "More for My flow" }));
    await user.click(within(top).getByRole("menuitem", { name: "Publish to marketplace" }));
    const dlg = await screen.findByRole("dialog", { name: "Publish to marketplace" });
    expect(within(dlg).getByLabelText("Name")).toHaveValue("my-flow");
    expect(within(dlg).getByLabelText("Version")).toHaveValue("1.0.0");
    expect(within(dlg).getByLabelText("Summary")).toHaveValue("Fixes a bug fast.");
    expect(within(dlg).getByLabelText("Publisher")).toHaveValue("ana-k");
    // keel's check of the YAML is the review
    const review = await within(dlg).findByRole("list", { name: "Publish review" });
    expect(review).toHaveTextContent("Runs reproducer");
    expect(within(review).getByText("npm test").tagName).toBe("CODE");
    expect(calls("POST", "/api/projects/ludus-engine/workflows/check")[0]?.body).toEqual({ yaml: mine.yaml });

    await user.selectOptions(within(dlg).getByLabelText("Category"), "code");
    await user.clear(within(dlg).getByLabelText("Version"));
    await user.type(within(dlg).getByLabelText("Version"), "1.2.0");
    const link = within(dlg).getByRole("link", { name: "Download the package" });
    const href = new URL(link.getAttribute("href")!, "http://keel.test");
    expect(href.pathname).toBe("/api/workflows/mine/publish");
    expect(Object.fromEntries(href.searchParams)).toEqual({
      name: "my-flow", version: "1.2.0", summary: "Fixes a bug fast.", category: "code", publisher: "ana-k",
    });
    expect(link).toHaveAttribute("download", "my-flow-1.2.0.zip");
    const next = within(dlg).getByRole("region", { name: "Next steps" });
    expect(next).toHaveTextContent("keel-plugin sign my-flow-1.2.0.yaml --key-file you.key");
    expect(within(next).getByRole("link", { name: "keel-studio/keel-marketplace" })).toHaveAttribute(
      "href", "https://github.com/keel-studio/keel-marketplace",
    );
    expect(next).toHaveTextContent("workflows/my-flow.yml");

    // a bad name or no publisher: no download
    await user.clear(within(dlg).getByLabelText("Publisher"));
    expect(within(dlg).queryByRole("link", { name: "Download the package" })).toBeNull();
    expect(within(dlg).getByRole("button", { name: "Download the package" })).toBeDisabled();
  });
});
