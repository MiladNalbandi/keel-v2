// v0.16.0 a keel with only its core and the Inbox's agent install request, as docs/plugins/mockup.html draws them:
// the welcome on a project's Flow page (core only: shown; full keel: not shown), the set cards and their "Install N",
// the menu's "No pages yet" box and the "core" tag, and the install request card (Approve and install / Deny).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { App } from "../App";
import { setAdds } from "../components/CoreOnly";
import type { InboxItem, InboxView } from "../inboxApi";
import { allPages } from "../sdk/registry";
import { coreOnly, restorePlugins, withoutPages } from "./coreOnly";
import { db, server } from "./setup";

afterEach(() => restorePlugins());

const calls = (method: string, path: string) =>
  db.calls.filter((c) => c.method === method && c.path === path);

/** The project yegi's Flow page (it has no flow), with Code on, Wiki in keel but off, and three sets. */
function openFlow() {
  db.market.installed = [
    { name: "code", title: "Code", version: "1.0.0", from: "image", on: true },
    { name: "wiki", title: "Wiki", version: "1.1.0", from: "image", on: false },
  ];
  db.market.sets = [
    { id: "developer", title: "Developer", summary: "Read and change code with agents.", plugins: ["code", "db", "wiki"] },
    { id: "tickets", title: "Tickets", summary: "Start flows from your issues.", plugins: ["tasks", "jira"] },
    { id: "basic", title: "Basic", summary: "Only the Code page.", plugins: ["code"] },
  ];
  localStorage.setItem("keel2.project", "yegi");
  location.hash = "#/flow";
  render(<App />);
}

const brand = () => document.querySelector(".side .brand")!;

describe("core only: the welcome on the Flow page", () => {
  it("shows the hero, the four parts of the mockup and the project's own empty flow", async () => {
    coreOnly();
    openFlow();
    const hello = (await screen.findByRole("heading", { name: "Welcome to keel core", level: 1 })).closest("section")!;
    expect(hello).toHaveTextContent("keel core 0.4.1");
    expect(hello).toHaveTextContent("This keel has only the core. Flows, workflows, agents, stacks, the Inbox, the budget and your settings work now.");
    expect(within(hello).getByRole("button", { name: "Open the marketplace" })).toBeInTheDocument();
    expect(within(hello).getByRole("button", { name: "How plugins work" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Start with a set" })).toBeInTheDocument();
    expect(screen.getByText("One click installs the set. Then keel restarts once.")).toBeInTheDocument();
    const core = screen.getByRole("list", { name: "What core does without plugins" });
    expect(within(core).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "✓Runs flows and workflows", "✓Agents, skills and stacks", "✓Guard and gates", "✓Inbox and notifications",
      "✓Budget and usage", "✓Connections and secrets", "✓Projects", "✓Plugin host and marketplace",
    ]);
    expect(screen.getByText("A flow can ask for a plugin it needs. The request waits in the Inbox until you approve it.")).toBeInTheDocument();
    // the project's flow is still one click away
    expect(screen.getByText("Nothing running here yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start a flow" })).toBeInTheDocument();
    // the tag next to the logo
    expect(brand()).toHaveTextContent("keelcore");
  });

  it("each set card: its title, one line, its plugins, and Install N = what is missing or off", async () => {
    coreOnly();
    openFlow();
    const dev = await screen.findByRole("group", { name: "Developer set" });
    expect(dev).toHaveTextContent("Read and change code with agents.");
    expect(dev).toHaveTextContent("Code, Database, Wiki");
    // db is missing (downloaded), wiki is only off (turned on): 2
    expect(within(dev).getByRole("button", { name: "Install 2" })).toBeEnabled();
    const tickets = screen.getByRole("group", { name: "Tickets set" });
    expect(within(tickets).getByRole("button", { name: "Install 2" })).toBeInTheDocument();
    const basic = screen.getByRole("group", { name: "Basic set" });
    expect(within(basic).getByRole("button", { name: "All here" })).toBeDisabled();
  });

  it("Install N opens the set's offer in Settings › Plugins and installs nothing by itself", async () => {
    const user = userEvent.setup();
    coreOnly();
    openFlow();
    const dev = await screen.findByRole("group", { name: "Developer set" });
    await user.click(within(dev).getByRole("button", { name: "Install 2" }));
    await waitFor(() => expect(location.hash).toBe("#/settings/plugins?set=developer"));
    expect(await screen.findByRole("heading", { name: "Start with the Developer set" })).toBeInTheDocument();
    expect(calls("POST", "/api/plugins/install")).toEqual([]);
    expect(calls("PUT", "/api/plugins/wiki")).toEqual([]);
  });

  it("Open the marketplace goes to Settings › Plugins (Marketplace); How plugins work explains in a dialog", async () => {
    const user = userEvent.setup();
    coreOnly();
    openFlow();
    await user.click(await screen.findByRole("button", { name: "How plugins work" }));
    const how = await screen.findByRole("dialog", { name: "How plugins work" });
    expect(how).toHaveTextContent("Plugins come from their own repos as signed files.");
    expect(how).toHaveTextContent("Agents only ask");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Open the marketplace" }));
    await waitFor(() => expect(location.hash).toBe("#/settings/plugins?tab=market"));
  });

  it("full keel: no welcome, the Flow page's own empty state and the usual tag", async () => {
    openFlow();
    expect(await screen.findByText("Nothing running here yet")).toBeInTheDocument();
    expect(screen.queryByText("Welcome to keel core")).toBeNull();
    expect(screen.queryByTestId("core-welcome")).toBeNull();
    expect(brand()).toHaveTextContent("keelv2 studio");
  });

  it("a plugin's page still there (Tasks, no Project page): the menu box, but no welcome", async () => {
    withoutPages(...allPages().filter((p) => p.group === "know").map((p) => p.id));
    openFlow();
    const nav = screen.getByRole("navigation", { name: "Screens" });
    expect(await within(nav).findByRole("group", { name: "No pages yet" })).toBeInTheDocument();
    expect(await screen.findByText("Nothing running here yet")).toBeInTheDocument();
    expect(screen.queryByText("Welcome to keel core")).toBeNull();
    expect(brand()).toHaveTextContent("keelv2 studio");
  });
});

describe("core only: the menu's Project group", () => {
  it("is one small box: No pages yet, and + Add pages opens the marketplace", async () => {
    const user = userEvent.setup();
    coreOnly();
    openFlow();
    const nav = screen.getByRole("navigation", { name: "Screens" });
    const box = await within(nav).findByRole("group", { name: "No pages yet" });
    expect(box).toHaveTextContent("No pages yet");
    expect(box).toHaveTextContent("Code, KeelBot, Map, Graph and Wiki are plugins now.");
    // no link in the box: ⌃1–⌃9 count the menu's links, as before
    expect(within(box).queryAllByRole("link")).toEqual([]);
    expect(box.closest(".nav-sec")).toHaveTextContent(/^Project/);
    await user.click(within(box).getByRole("button", { name: "+ Add pages" }));
    await waitFor(() => expect(location.hash).toBe("#/settings/plugins?tab=market"));
  });

  it("full keel: the Project pages, no box", async () => {
    openFlow();
    const nav = screen.getByRole("navigation", { name: "Screens" });
    expect(await within(nav).findByRole("link", { name: "Wiki" })).toBeInTheDocument();
    expect(within(nav).queryByRole("group", { name: "No pages yet" })).toBeNull();
  });
});

describe("setAdds: a set's N", () => {
  it("counts the missing and the off plugins of the set once each", () => {
    expect(setAdds({ id: "a", plugins: ["x", "y", "z"], missing: ["x"], off: ["y"] })).toBe(2);
    expect(setAdds({ id: "a", plugins: ["x"], missing: ["x"], off: ["x"] })).toBe(1);
    expect(setAdds({ id: "a", plugins: ["x"], missing: ["other"] })).toBe(0);
    expect(setAdds({ id: "a", plugins: ["x", "y"] })).toBe(0);
  });
});

describe("Inbox: an agent install request", () => {
  const request = (id: string, title = "Database", extra: Partial<Record<string, unknown>> = {}): InboxItem => ({
    project_id: "ludus-engine",
    project_name: "ludus-engine",
    thread_id: id,
    flow: "keelbot",
    workflow_id: null,
    step: "approval",
    kind: "plugin-install",
    title: `Install ${title} 1.4.0?`,
    detail: "I need the sessions table's schema.",
    more: false,
    options: ["approve", "deny"],
    id,
    since: new Date(Date.now() - 60_000).toISOString(),
    payload: {
      name: "db",
      title,
      version: "1.4.0",
      trust: "code",
      publisher: "keel",
      verified: true,
      summary: "Connect a database.",
      permissions: { secrets: ["database"], workspace: "read" },
      needs: ["code"],
      installs: [{ name: "code", title: "Code", version: "1.0.0" }],
      source: "keelbot",
      reasons: [
        { reason: "I need the sessions table's schema.", source: "keelbot" },
        { reason: "The flow fix-login needs SQL.", source: "agent" },
      ],
      ...extra,
    },
  });

  let state: { list: InboxItem[] };
  let decided: { id: string; body: unknown }[];
  beforeEach(() => {
    state = { list: [] };
    decided = [];
    server.use(
      http.get("/api/inbox", () =>
        HttpResponse.json({
          items: state.list,
          count: state.list.length,
          kinds: ["plugin-install"],
          projects: [{ id: "ludus-engine", name: "ludus-engine", count: state.list.length }],
        } satisfies InboxView),
      ),
      http.post("/api/approvals/:id/decide", async ({ request: req, params }) => {
        decided.push({ id: String(params.id), body: await req.json() });
        state.list = state.list.filter((x) => x.id !== params.id);
        return HttpResponse.json({ id: params.id, status: "approved" });
      }),
    );
  });

  const openInbox = () => {
    location.hash = "#/inbox";
    render(<App />);
  };

  it("shows who asks, the plugin, the reason, the permissions with their levels and what happens; Approve and install / Deny answer it", async () => {
    const user = userEvent.setup();
    const update: InboxItem = {
      ...request("a_3", "Git"),
      title: "Update Git to 1.2.0?",
      payload: {
        name: "git",
        title: "Git",
        version: "1.2.0",
        installed: "1.1.0",
        update: true,
        trust: "code",
        more: ["+ secrets: gitlab"],
        permissions: { secrets: ["github", "gitlab"] },
        source: "keel",
        reasons: [{ reason: "The new version 1.2.0 asks for more permissions: + secrets: gitlab.", source: "keel" }],
      },
    };
    state.list = [request("a_1"), request("a_2", "Wiki"), update];
    openInbox();
    const card = await screen.findByRole("article", { name: "Install Database 1.4.0?" });
    // the header: waiting, what it is, who asks and where
    expect(card).toHaveTextContent("Waiting for you");
    expect(card).toHaveTextContent("Install request");
    expect(card).toHaveTextContent("from KeelBot · project ludus-engine");
    expect(card).toHaveTextContent(/waiting \d+/);
    // the plugin: icon, name, version, publisher, trust, summary
    expect(card.querySelector(".pl-ico")).toHaveTextContent("DB");
    expect(within(card).getByRole("heading", { level: 2 })).toHaveTextContent("Database1.4.0✓by keel");
    expect(card).toHaveTextContent("Runs code in keel");
    expect(card).toHaveTextContent("Connect a database.");
    // the reason, in the askers' words
    const why = within(card).getByLabelText("Why it asks");
    expect(why).toHaveTextContent("“I need the sessions table's schema.”");
    expect(why).toHaveTextContent("“The flow fix-login needs SQL.” · An agent");
    // the permissions, the highest level first
    const perms = within(card).getByRole("list", { name: "Permissions" });
    expect(within(perms).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "highRead your database connections",
      "mediumRead files in /workspace",
    ]);
    // what happens
    const what = within(card).getByRole("list", { name: "What happens" });
    expect(what).toHaveTextContent("Also installs Code: Database needs it.");
    expect(what).toHaveTextContent("Signed by keel");
    expect(what).toHaveTextContent("It loads when keel restarts the api and the engine, about 30 s.");
    expect(card).toHaveTextContent("An agent cannot install anything by itself.");

    await user.click(within(card).getByRole("button", { name: "Approve and install" }));
    await waitFor(() => expect(decided[0]).toEqual({ id: "a_1", body: { decision: "approve", why: "" } }));
    expect(await screen.findByText("Database is installed. Restart keel to use it.")).toBeInTheDocument();

    const wiki = await screen.findByRole("article", { name: "Install Wiki 1.4.0?" });
    await user.type(within(wiki).getByLabelText("Note (optional)"), "not now");
    await user.click(within(wiki).getByRole("button", { name: "Deny" }));
    await waitFor(() => expect(decided[1]).toEqual({ id: "a_2", body: { decision: "deny", why: "not now" } }));
    expect(await screen.findByText("Denied: Wiki is not installed.")).toBeInTheDocument();

    // an update that asks for more: the new permissions, the versions, then Approve and update
    const git = await screen.findByRole("article", { name: "Update Git to 1.2.0?" });
    expect(git).toHaveTextContent("Update request");
    expect(within(git).getByRole("heading", { level: 2 })).toHaveTextContent("Git1.1.0 → 1.2.0");
    expect(within(git).getByRole("list", { name: "New permissions" })).toHaveTextContent("+ secrets: gitlab");
    expect(git).toHaveTextContent("keel updates it from 1.1.0 to 1.2.0");
    expect(git).toHaveTextContent("from keel");
    expect(within(git).getByRole("button", { name: "Keep 1.1.0" })).toBeInTheDocument();
    await user.click(within(git).getByRole("button", { name: "Approve and update" }));
    await waitFor(() => expect(decided[2]).toEqual({ id: "a_3", body: { decision: "approve", why: "" } }));
    expect(await screen.findByText("Git is updated. Restart keel to use it.")).toBeInTheDocument();
  });

  it("shows three permissions, then all of them; a need that is here is checked; an unverified publisher is said", async () => {
    const user = userEvent.setup();
    state.list = [
      request("a_9", "Database", {
        verified: false,
        publisher: "ops-collective",
        installs: [],
        source: "investigator",
        reasons: [{ reason: "I need to read the sessions table.", source: "investigator" }],
        permissions:{ secrets: ["database"], network: ["from-connections"], workspace: "read", agent_tools: "read", tables: ["db_connection"] },
      }),
    ];
    openInbox();
    const card = await screen.findByRole("article", { name: "Install Database 1.4.0?" });
    // a flow's agent asks
    expect(card).toHaveTextContent("from agent investigator");
    expect(within(card).getByLabelText("Why it asks")).toHaveTextContent("The agent says“I need to read the sessions table.”");
    const perms = within(card).getByRole("list", { name: "Permissions" });
    expect(within(perms).getAllByRole("listitem")).toHaveLength(3);
    await user.click(within(card).getByRole("button", { name: "See all 5 permissions" }));
    expect(within(perms).getAllByRole("listitem")).toHaveLength(5);
    expect(perms).toHaveTextContent("lowKeep its own tables: db_connection");
    const what = within(card).getByRole("list", { name: "What happens" });
    expect(what).toHaveTextContent("Needs Code · installed");
    expect(what).not.toHaveTextContent("Also installs");
    expect(what).toHaveTextContent("keel checks its signature first");
    expect(within(card).getByRole("heading", { level: 2 })).toHaveTextContent("by ops-collective (not verified)");
  });
});
