// Rule packs in the marketplace (docs/plugins/23-rules-in-marketplace.md §3): the kind switch's third kind (Plugins ·
// Workflows · Rules, ?kind=rules), the Rules view's cards (the review in short, Install / Installed here / Update / the
// "edited" note) and its note when no catalog answers, a pack's page (#/settings/plugins/rules/<name>: every rule in a table and
// who enforces it, from the api), install into the project, update (asks before it replaces a copy changed here),
// remove, Open in Rules, and a pack whose signature keel refuses.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { RulePack } from "../rulesApi";
import { db } from "./setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);

function open(hash: string) {
  location.hash = hash;
  render(<App />);
}

const card = (title: string) => screen.findByRole("article", { name: `${title} rule pack` });

/** strict-git 1.0.0 in the project, changed here on the Rules page */
const installed = (edited: boolean): RulePack => ({
  id: "strict-git", name: "strict-git", title: "Strict git", version: "1.0.0", publisher: "bo", summary: "x",
  source: "marketplace", edited, rules: [],
});

describe("Control › Plugins › Marketplace: Plugins · Workflows · Rules", () => {
  it("Rules keeps ?tab=market and adds ?kind=rules; the search names the project", async () => {
    const user = userEvent.setup();
    open("#/settings/plugins?tab=market");
    const kinds = await screen.findByRole("group", { name: "What to find" });
    // #92 keel's kinds, then a plugin's own (slot market.kind: the Org plugin's Orgs)
    expect(within(kinds).getAllByRole("button").map((b) => b.textContent)).toEqual(["Plugins", "Workflows", "Rules", "Skills", "Stacks", "Orgs"]);
    await user.click(within(kinds).getByRole("button", { name: "Rules" }));
    await waitFor(() => expect(location.hash).toBe("#/settings/plugins?tab=market&kind=rules"));
    expect(await card("No new dependencies")).toBeInTheDocument();
    expect(within(kinds).getByRole("button", { name: "Rules" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("article", { name: "Database plugin" })).toBeNull();
    expect(screen.getByText(/Rule packs made by keel and the community/)).toBeInTheDocument();
    expect(db.mr.searches[0]).toEqual({ q: "", category: "", project: "ludus-engine" });
    await user.click(within(kinds).getByRole("button", { name: "Workflows" }));
    await waitFor(() => expect(location.hash).toBe("#/settings/plugins?tab=market&kind=workflows"));
  });
});

describe("Marketplace › Rules: the cards", () => {
  it("shows the publisher, the version, the summary, the review in short, and Install / Installed here / Update / edited", async () => {
    db.rules.installed = [installed(true)];
    open("#/settings/plugins?tab=market&kind=rules");
    const deps = await card("No new dependencies");
    expect(deps).toHaveTextContent("keel");
    expect(within(deps).getByTitle("Verified publisher")).toBeInTheDocument();
    expect(deps).toHaveTextContent("1.1.0");
    expect(deps).toHaveTextContent("Agents never add a package");
    expect(within(deps).getByText("3 rules · bash 1 · edit 2 · every phase")).toBeInTheDocument();
    expect(within(deps).getByText("web lane")).toBeInTheDocument();
    expect(within(deps).getByRole("button", { name: "Install No new dependencies" })).toBeEnabled();
    expect(within(deps).getByRole("link", { name: "No new dependencies" })).toHaveAttribute("href", "#/settings/plugins/rules/no-new-dependencies");

    const git = await card("Strict git");
    expect(git).toHaveTextContent("community");
    expect(within(git).getByText("2 rules · bash 1 · mcp 1 · every phase")).toBeInTheDocument();
    expect(within(git).getByRole("button", { name: "Update to 1.2.0" })).toBeInTheDocument();
    expect(within(git).getByText("edited")).toHaveAttribute("title", expect.stringContaining("asks before it replaces"));

    const future = await card("Future pack");
    expect(within(future).getByText("refused")).toBeInTheDocument();
    expect(within(future).getByRole("button", { name: "Install Future pack" })).toBeDisabled();
    // keel's own first
    const keelSection = screen.getByRole("region", { name: "From keel" });
    expect(within(keelSection).getByRole("article", { name: "No new dependencies rule pack" })).toBeInTheDocument();
  });

  it("installs from a card into this project, and the card says Installed here", async () => {
    const user = userEvent.setup();
    open("#/settings/plugins?tab=market&kind=rules");
    await user.click(within(await card("No new dependencies")).getByRole("button", { name: "Install No new dependencies" }));
    const dlg = await screen.findByRole("dialog", { name: "Install No new dependencies 1.1.0?" });
    expect(dlg).toHaveTextContent(".keel/rules/no-new-dependencies.yaml");
    const blocks = within(dlg).getByRole("list", { name: "What it blocks" });
    expect(blocks).toHaveTextContent("npm install, pnpm add");
    expect(blocks).toHaveTextContent("glob · green, refactor · web lane");
    await user.click(within(dlg).getByRole("button", { name: "Install" }));
    await waitFor(() =>
      expect(calls("POST", "/api/projects/ludus-engine/rule-packs/market/install")[0]?.body).toEqual({ name: "no-new-dependencies" }),
    );
    expect(await within(await card("No new dependencies")).findByText("Installed here")).toBeInTheDocument();
    // the rules api has it, from the marketplace
    expect(db.rules.installed.map((p) => [p.id, p.source, p.version])).toEqual([["no-new-dependencies", "marketplace", "1.1.0"]]);
  });

  it("with no rule pack catalog: a clear note, never an empty page", async () => {
    db.mr.down = true;
    open("#/settings/plugins?tab=market&kind=rules");
    const note = await screen.findByRole("note", { name: "No rule pack catalog" });
    expect(note).toHaveTextContent("No rule pack catalog answers yet.");
    expect(within(note).getByRole("link", { name: "Build › Safety & History" })).toHaveAttribute("href", "#/rules");
    expect(within(note).getByRole("link", { name: "Sources and rules" })).toBeInTheDocument();
  });

  it("searches, and the category chips narrow the list", async () => {
    const user = userEvent.setup();
    open("#/settings/plugins?tab=market&kind=rules");
    await card("No new dependencies");
    await user.click(screen.getByRole("button", { name: "Git" }));
    await waitFor(() => expect(screen.queryByRole("article", { name: "No new dependencies rule pack" })).toBeNull());
    expect(screen.getByRole("article", { name: "Strict git rule pack" })).toBeInTheDocument();
    expect(db.mr.searches.at(-1)?.category).toBe("git");
    await user.click(screen.getByRole("button", { name: "All" }));
    await user.type(screen.getByRole("searchbox", { name: "Search rule packs" }), "dependencies");
    await waitFor(() => expect(screen.queryByRole("article", { name: "Strict git rule pack" })).toBeNull());
  });
});

describe("A rule pack's page", () => {
  it("lists every rule (kind, match, how, phases, lanes, except, reason) and who enforces it, from the api", async () => {
    open("#/settings/plugins/rules/no-new-dependencies");
    expect(await screen.findByRole("heading", { name: "No new dependencies", level: 1 })).toBeInTheDocument();
    const crumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumb).getByRole("link", { name: "Rules" })).toHaveAttribute("href", "#/settings/plugins?tab=market&kind=rules");
    expect(screen.getByText("rule pack")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Rules of No new dependencies" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    const lock = rows[1];
    for (const text of ["no-lockfile", "edit", "**/package-lock.json", "glob", "green, refactor", "web", "web/fixtures/**",
      "The lock file follows package.json."]) expect(lock).toHaveTextContent(text);
    // who enforces it: the api's mechanisms, never a list of the page's own; #101 one engine (keel's harness), so no
    // coding-agent app is named after a mechanism
    const bash = screen.getByTestId("who-no-install");
    expect(bash).toHaveTextContent("before the action keel's ToolBox");
    expect(bash).not.toHaveTextContent("after the step");
    expect(screen.getByTestId("who-no-lockfile")).toHaveTextContent("before the action keel's ToolBox");
    expect(screen.getByTestId("who-no-lockfile")).toHaveTextContent("after the step the diff guard");
    const who = screen.getByRole("region", { name: "Who enforces it" });
    expect(who).toHaveTextContent("keel's ToolBox · every rule");
    expect(who).toHaveTextContent("the diff guard · no-lockfile, no-manifest");
    expect(who).toHaveTextContent("keel's harness");
    expect(who).toHaveTextContent("every edit, shell command, read and MCP call is checked before it runs");
    expect(who).not.toHaveTextContent(/Claude Code|OpenCode|Codex|Copilot|API-key models|PreToolUse|wrappers|harness\)/);
    expect(table).not.toHaveTextContent(/Claude Code|OpenCode|Copilot|PreToolUse|wrappers/);
    // the YAML, folded; requires; versions; checks; Open in Rules
    const yaml = screen.getByText("The YAML (installed as it is)").closest("details")!;
    expect(yaml).not.toHaveAttribute("open");
    expect(within(yaml).getByLabelText("YAML of No new dependencies")).toHaveTextContent("name: no-new-dependencies");
    expect(screen.getByRole("region", { name: "Requires" })).toHaveTextContent("keel core >=0.17.2");
    expect(screen.getByRole("region", { name: "Requires" })).toHaveTextContent(".keel/rules/no-new-dependencies.yaml");
    const versions = screen.getByRole("region", { name: "Versions" });
    expect(within(versions).getAllByRole("row").map((r) => r.textContent)).toEqual([
      "VersionWorks withReleasedWhat it blocks",
      "1.1.0keel >=0.17.22026-10-163 rules · bash 1 · edit 2 · every phase",
      "1.0.0keel >=0.17.22026-10-011 rule · bash 1 · every phase",
    ]);
    expect(screen.getByRole("region", { name: "Checks keel runs before install" })).toHaveTextContent("it must be the catalog's");
    expect(screen.getByRole("link", { name: "Open in Rules" })).toHaveAttribute("href", "#/rules");
    expect(screen.getByRole("button", { name: "Install 1.1.0" })).toBeEnabled();
  });

  it("installs an older version into this project", async () => {
    const user = userEvent.setup();
    open("#/settings/plugins/rules/no-new-dependencies");
    await user.click(await screen.findByRole("button", { name: "Install 1.1.0" }));
    const dlg = await screen.findByRole("dialog", { name: "Install No new dependencies 1.1.0?" });
    await user.selectOptions(within(dlg).getByLabelText("Version"), "1.0.0");
    await user.click(within(dlg).getByRole("button", { name: "Install" }));
    await waitFor(() =>
      expect(calls("POST", "/api/projects/ludus-engine/rule-packs/market/install")[0]?.body).toEqual({ name: "no-new-dependencies", version: "1.0.0" }),
    );
    expect(await screen.findByText("Installed here 1.0.0")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update to 1.1.0" })).toBeInTheDocument();
  });

  it("an update of a copy changed here asks: keep my copy (the default) or replace it", async () => {
    const user = userEvent.setup();
    db.rules.installed = [installed(true)];
    open("#/settings/plugins/rules/strict-git");
    expect(await screen.findByText("Installed here 1.0.0")).toBeInTheDocument();
    expect(screen.getByText("edited")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Update to 1.2.0" }));
    const dlg = await screen.findByRole("dialog", { name: "Update Strict git to 1.2.0?" });
    expect(within(dlg).getByRole("radio", { name: /Keep my copy/ })).toBeChecked();
    // keep: nothing is sent
    await user.click(within(dlg).getByRole("button", { name: "Keep my copy" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls("POST", "/api/projects/ludus-engine/rule-packs/market/install")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Update to 1.2.0" }));
    const again = await screen.findByRole("dialog", { name: "Update Strict git to 1.2.0?" });
    await user.click(within(again).getByRole("radio", { name: /Replace it with 1.2.0/ }));
    await user.click(within(again).getByRole("button", { name: "Update to 1.2.0" }));
    await waitFor(() =>
      expect(calls("POST", "/api/projects/ludus-engine/rule-packs/market/install")[0]?.body).toEqual({ name: "strict-git", version: "1.2.0", replace: true }),
    );
    expect(await screen.findByText("Installed here 1.2.0")).toBeInTheDocument();
  });

  it("an unchanged copy updates with replace at once; Remove deletes the pack file", async () => {
    const user = userEvent.setup();
    db.rules.installed = [installed(false)];
    open("#/settings/plugins/rules/strict-git");
    await user.click(await screen.findByRole("button", { name: "Update to 1.2.0" }));
    const dlg = await screen.findByRole("dialog", { name: "Update Strict git to 1.2.0?" });
    expect(within(dlg).queryByRole("radio")).toBeNull();
    await user.click(within(dlg).getByRole("button", { name: "Update to 1.2.0" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/rule-packs/market/install")[0]?.body).toMatchObject({ replace: true }));
    await user.click(await screen.findByRole("button", { name: "Remove" }));
    expect(screen.getByText(/This deletes/)).toHaveTextContent(".keel/rules/strict-git.yaml");
    const confirm = screen.getAllByRole("button", { name: "Remove" }).at(-1)!;
    await user.click(confirm);
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/rule-packs/uninstall")[0]?.body).toEqual({ name: "strict-git" }));
    expect(await screen.findByRole("button", { name: "Install 1.2.0" })).toBeInTheDocument();
    expect(db.rules.installed).toEqual([]);
  });

  it("a pack whose signature keel refuses: the page says why and Install is off", async () => {
    db.mr.badSignature = "no-new-dependencies";
    open("#/settings/plugins/rules/no-new-dependencies");
    expect(await screen.findByRole("alert")).toHaveTextContent("does not match its publisher's key");
    expect(screen.getByRole("button", { name: "Install 1.1.0" })).toBeDisabled();
    expect(screen.queryByRole("table", { name: /Rules of/ })).toBeNull();
  });

  it("a pack for a newer keel is refused, with the reason", async () => {
    open("#/settings/plugins/rules/future-pack");
    expect(await screen.findByRole("note")).toHaveTextContent("needs keel >=9.0.0");
    expect(screen.getByRole("button", { name: /^Install/ })).toBeDisabled();
  });
});
