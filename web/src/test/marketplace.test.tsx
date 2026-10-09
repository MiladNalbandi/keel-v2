// v0.16.0 step 4 in the web (docs/plugins/13-step4-contract.md §9): Control › Plugins with its three tabs (Installed,
// Marketplace, Sources and rules), the install dialog, the restart banner, the Inbox's plugin-install card, and a keel
// with only its core: the Project group offers the sets, and #/plugins?set=<id> installs one.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../App";
import type { InboxItem, InboxView } from "../inboxApi";
import { allPages, registerPage, type PageRegistration } from "../sdk/registry";
import { db, server } from "./setup";

const calls = (method: string, path: string) =>
  db.calls.filter((c) => c.method === method && c.path === path);

async function openPlugins(hash = "#/plugins") {
  location.hash = hash;
  render(<App />);
  return screen.findByRole("heading", { name: "Plugins", level: 1 });
}

function installSome() {
  db.market.installed = [
    {
      name: "code",
      title: "Code",
      version: "1.0.0",
      from: "image",
      on: true,
      parts: ["engine", "api", "web"],
      status: "loaded",
      can_remove: false,
      needed_by: ["db"],
    },
    {
      name: "db",
      title: "Database",
      version: "1.3.0",
      from: "marketplace",
      on: true,
      parts: ["engine", "web"],
      status: "loaded",
      can_remove: true,
      update: "1.4.0",
      previous: "1.2.0",
      revoked: {
        version: "1.3.0",
        why: "it sent errors to a wrong host",
        fixed: "1.4.0",
      },
    },
    {
      name: "hello",
      title: "Hello",
      version: "0.1.0",
      from: "file",
      on: false,
      parts: ["content"],
      status: "off",
      can_remove: true,
    },
    {
      name: "wiki",
      title: "Wiki",
      version: "1.1.0",
      from: "marketplace",
      on: true,
      parts: ["web"],
      status: "removed",
      can_remove: true,
    },
  ];
  db.market.problems = [
    {
      name: "triage",
      version: "0.2.1",
      error: "its files changed since it was installed",
    },
  ];
}

describe("Control › Plugins: Installed", () => {
  it("is the one new page in Control, and lists each plugin with its version, parts, from, status and problems", async () => {
    installSome();
    await openPlugins();
    const nav = screen.getByRole("navigation", { name: "Screens" });
    expect(within(nav).getByRole("link", { name: "Plugins" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    // a normal keel has its Project pages: no "Start with a set"
    expect(
      within(nav).queryByRole("group", { name: "Start with a set" }),
    ).toBeNull();
    expect(screen.getByRole("tab", { name: "Installed (4)" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    const code = await screen.findByTestId("plugin-code");
    expect(code).toHaveTextContent("in the image");
    expect(code).toHaveTextContent("db needs it");
    expect(within(code).queryByRole("button", { name: "Remove" })).toBeNull(); // the image's: turn off only
    expect(screen.getByTestId("plugin-wiki")).toHaveTextContent(
      "removed at restart",
    );
    expect(screen.getByTestId("plugin-hello")).toHaveTextContent("off");
    const dbRow = screen.getByTestId("plugin-db");
    expect(dbRow).toHaveTextContent("1.4.0 ready");
    expect(dbRow).toHaveTextContent(
      "This version was revoked: it sent errors to a wrong host · 1.4.0 fixes it",
    );
    expect(dbRow).toHaveTextContent("marketplace");
    const hello = screen.getByTestId("plugin-hello");
    expect(hello).toHaveTextContent("a file (unsigned)");
    expect(
      within(hello).getByRole("switch", { name: "Hello on" }),
    ).not.toBeChecked();
    expect(
      screen
        .getByRole("heading", { name: "Left out at start" })
        .closest("section"),
    ).toHaveTextContent(
      "triage 0.2.1: its files changed since it was installed",
    );
    // nothing waits for a restart yet
    expect(screen.queryByText("Changes wait for a restart")).toBeNull();
  });

  it("updates, rolls back, turns off and removes, and then the banner offers the restart", async () => {
    const user = userEvent.setup();
    installSome();
    await openPlugins();
    const dbRow = await screen.findByTestId("plugin-db");
    await user.click(within(dbRow).getByRole("button", { name: "Update" }));
    await waitFor(() =>
      expect(calls("POST", "/api/plugins/db/update")).toHaveLength(1),
    );
    expect(
      await screen.findByText("Database is updated. Restart keel to use it."),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Changes wait for a restart"),
    ).toBeInTheDocument();

    await user.click(
      within(screen.getByTestId("plugin-db")).getByRole("button", {
        name: "Roll back",
      }),
    );
    await waitFor(() =>
      expect(calls("POST", "/api/plugins/db/rollback")).toHaveLength(1),
    );

    await user.click(
      within(screen.getByTestId("plugin-code")).getByRole("switch", {
        name: "Code on",
      }),
    );
    await waitFor(() =>
      expect(calls("PUT", "/api/plugins/code")[0]?.body).toEqual({ on: false }),
    );

    const hello = screen.getByTestId("plugin-hello");
    await user.click(within(hello).getByRole("button", { name: "Remove" }));
    const confirm = within(hello).getByRole("group", { name: "Confirm" });
    expect(confirm).toHaveTextContent("Its tables in keel's database stay.");
    await user.click(
      within(confirm).getByRole("checkbox", {
        name: "Also delete its files in /data",
      }),
    );
    await user.click(within(confirm).getByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(db.market.removed).toEqual([{ name: "hello", data: "delete" }]),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("plugin-hello")).toBeNull(),
    );

    // the banner: restart now, or when no agent step runs
    db.market.restart = { ...db.market.restart, running: 0 };
    await user.click(
      screen.getByRole("button", { name: "Restart when no agent runs" }),
    );
    await waitFor(() =>
      expect(calls("POST", "/api/plugins/restart")[0]?.body).toEqual({}),
    );
    expect(
      await screen.findByText(
        "keel restarts by itself when no agent step runs.",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Restart keel" }));
    await waitFor(() =>
      expect(calls("POST", "/api/plugins/restart")[1]?.body).toEqual({
        now: true,
      }),
    );
    expect(await screen.findByText("keel is restarting…")).toBeInTheDocument();
  });

  it("asks first when agent steps run, and shows the api's refusal", async () => {
    const user = userEvent.setup();
    db.market.restart = {
      pending: true,
      scheduled: false,
      supervised: true,
      running: 2,
    };
    server.use(
      http.post("/api/plugins/restart", () =>
        HttpResponse.json(
          {
            error:
              "keel can restart itself only when keel-start runs it (KEEL_SUPERVISED=1)",
            hint: "Restart keel by hand: keel2 restart",
          },
          { status: 409 },
        ),
      ),
    );
    await openPlugins();
    await user.click(
      await screen.findByRole("button", { name: "Restart keel" }),
    );
    const confirm = screen.getByRole("group", { name: "Confirm" });
    expect(confirm).toHaveTextContent(
      "2 agent steps run now. They stop, and go on after the restart.",
    );
    await user.click(
      within(confirm).getByRole("button", { name: "Restart anyway" }),
    );
    expect(
      await screen.findByText("Restart keel by hand: keel2 restart"),
    ).toBeInTheDocument();
  });

  it("tells a keel that keel-start does not run to restart by hand", async () => {
    db.market.restart = {
      pending: true,
      scheduled: false,
      supervised: false,
      running: 0,
    };
    await openPlugins();
    expect(
      await screen.findByText(/restart it by hand with keel2 restart/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restart keel" })).toBeNull();
  });
});

describe("Control › Plugins: Marketplace", () => {
  it("searches by words and category, and its cards show the trust level and what is installed", async () => {
    const user = userEvent.setup();
    db.market.installed = [
      {
        name: "code",
        title: "Code",
        version: "1.0.0",
        from: "image",
        on: true,
      },
    ];
    await openPlugins();
    await user.click(screen.getByRole("tab", { name: "Marketplace" }));
    const card = await screen.findByRole("article", {
      name: "Database plugin",
    });
    expect(card).toHaveTextContent("Runs code in keel");
    expect(card).toHaveTextContent("keel ✓ · 1.4.0");
    expect(
      within(screen.getByRole("article", { name: "Code plugin" })).getByText(
        "Installed 1.0.0",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("article", { name: "Release notes plugin" }),
    ).toHaveTextContent("Content only");
    expect(
      screen.getByRole("article", { name: "Release notes plugin" }),
    ).toHaveTextContent("community");

    await user.type(
      screen.getByRole("searchbox", { name: /Search plugins/ }),
      "sql",
    );
    await waitFor(() =>
      expect(screen.queryByRole("article", { name: "Code plugin" })).toBeNull(),
    );
    expect(
      screen.getByRole("article", { name: "Database plugin" }),
    ).toBeInTheDocument();
    expect(db.market.searches.at(-1)).toEqual({ q: "sql", category: "" });

    await user.clear(screen.getByRole("searchbox", { name: /Search plugins/ }));
    await user.click(
      within(screen.getByRole("group", { name: "Category" })).getByRole(
        "button",
        { name: "Knowledge" },
      ),
    );
    expect(
      await screen.findByRole("article", { name: "Wiki plugin" }),
    ).toHaveTextContent("Adds pages");
    await waitFor(() =>
      expect(
        screen.queryByRole("article", { name: "Database plugin" }),
      ).toBeNull(),
    );
  });

  it("shows a plugin's details, and its install dialog lists everything that will be installed with their permissions", async () => {
    const user = userEvent.setup();
    await openPlugins();
    await user.click(screen.getByRole("tab", { name: "Marketplace" }));
    await user.click(
      await screen.findByRole("button", { name: "Details of Database" }),
    );
    const detail = await screen.findByRole("dialog", { name: "Database" });
    const perms = await within(detail).findByRole("region", {
      name: "Permissions",
    });
    expect(perms).toHaveTextContent("highRead your database connections");
    expect(perms).toHaveTextContent(
      "mediumTalk to the hosts of your connections",
    );
    expect(perms).toHaveTextContent("mediumRead files in /workspace");
    expect(
      within(detail).getByRole("region", { name: "Needs" }),
    ).toHaveTextContent("code will be installed too");
    expect(
      within(detail).getByRole("region", { name: "Versions" }),
    ).toHaveTextContent("1.3.0");
    expect(
      within(detail).getByRole("region", { name: "Checks keel runs" }),
    ).toHaveTextContent("signed by keel, a key this keel trusts");

    await user.click(
      within(detail).getByRole("button", { name: "Install 1.4.0" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Install Database 1.4.0?",
    });
    const list = await within(dialog).findByRole("list", {
      name: "What will be installed",
    });
    // the list's own items (each holds its permissions' list)
    expect(
      [...list.querySelectorAll(":scope > li")].map(
        (li) => li.querySelector("b")?.textContent,
      ),
    ).toEqual(["Code", "Database"]);
    expect(list).toHaveTextContent("Read and change files in /workspace"); // Code's
    expect(list).toHaveTextContent("Read your database connections"); // Database's
    expect(dialog).toHaveTextContent("Also installs Code");
    expect(dialog).toHaveTextContent(
      "Code from these plugins runs inside keel.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Install" }));
    await waitFor(() =>
      expect(calls("POST", "/api/plugins/install")[0]?.body).toEqual({
        name: "db",
        version: "1.4.0",
      }),
    );
    expect(
      await screen.findByText("Database is installed. Restart keel to use it."),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Install Database 1.4.0?" }),
      ).toBeNull(),
    );
    expect(
      await screen.findByText("Changes wait for a restart"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: "Installed (2)" }),
    ).toBeInTheDocument();
  });

  it("says when the catalog is old or has a problem", async () => {
    const user = userEvent.setup();
    db.market.status = [
      {
        id: "keel",
        ok: false,
        problem: "the signature does not match the catalog key",
        old: true,
      },
    ];
    await openPlugins();
    await user.click(screen.getByRole("tab", { name: "Marketplace" }));
    expect(await screen.findByRole("note")).toHaveTextContent(
      "the catalog is old",
    );
    expect(screen.getByRole("note")).toHaveTextContent(
      "the signature does not match the catalog key",
    );
  });
});

describe("Control › Plugins: Sources and rules", () => {
  it("adds a catalog with its key and reads the catalogs again, changes the four rules, and installs a file", async () => {
    const user = userEvent.setup();
    db.market.status = [
      { id: "keel", ok: true, problem: null, old: false },
      {
        id: "acme",
        ok: false,
        problem: "acme.test does not answer",
        old: false,
      },
    ];
    await openPlugins();
    await user.click(screen.getByRole("tab", { name: "Sources and rules" }));
    const list = await screen.findByRole("list", { name: "Catalogs" });
    expect(list).toHaveTextContent("keel marketplace");
    expect(list).toHaveTextContent("built in");
    expect(within(list).queryByRole("button", { name: "Remove" })).toBeNull(); // the official one stays

    const form = screen.getByRole("form", { name: "Add a catalog" });
    await user.type(within(form).getByLabelText("Name"), "Acme");
    await user.type(
      within(form).getByLabelText("Catalog URL"),
      "https://acme.test/v1/index.json",
    );
    await user.type(within(form).getByLabelText("Its public key"), "RWQacme");
    await user.click(
      within(form).getByRole("button", { name: "Add the catalog" }),
    );
    await waitFor(() =>
      expect(calls("PUT", "/api/plugins/sources")[0]?.body).toEqual({
        sources: [
          expect.objectContaining({ id: "keel" }),
          {
            id: "acme",
            title: "Acme",
            url: "https://acme.test/v1/index.json",
            key: "RWQacme",
            on: true,
          },
        ],
      }),
    );
    await waitFor(() =>
      expect(calls("POST", "/api/marketplace/refresh")).toHaveLength(1),
    );
    expect(
      await within(screen.getByRole("list", { name: "Catalogs" })).findByText(
        "acme.test does not answer",
      ),
    ).toBeInTheDocument();
    await user.click(
      within(screen.getByRole("list", { name: "Catalogs" })).getByRole(
        "button",
        { name: "Remove" },
      ),
    );
    await waitFor(() =>
      expect(calls("PUT", "/api/plugins/sources")[1]?.body).toEqual({
        sources: [expect.objectContaining({ id: "keel" })],
      }),
    );

    const ask = await screen.findByRole("checkbox", {
      name: /Agents may ask to install plugins/,
    });
    expect(ask).toBeChecked();
    for (const name of [
      /Check for updates every day/,
      /Allow unverified publishers/,
      /Restart by itself after an approved install/,
    ]) {
      expect(screen.getByRole("checkbox", { name })).toBeInTheDocument();
    }
    await user.click(ask);
    await waitFor(() =>
      expect(calls("PUT", "/api/plugins/rules")[0]?.body).toEqual({
        agents_may_ask: false,
        allow_unverified: false,
        check_daily: true,
        restart_when_idle: false,
      }),
    );

    await user.type(
      screen.getByLabelText("The file's path in /data"),
      "/data/hello-0.1.0.kplug",
    );
    await user.click(screen.getByRole("button", { name: "Install the file" }));
    await waitFor(() =>
      expect(calls("POST", "/api/plugins/install-file")[0]?.body).toEqual({
        path: "/data/hello-0.1.0.kplug",
      }),
    );
    expect(
      await screen.findByText(
        "The file is installed, as unsigned. Restart keel to use it.",
      ),
    ).toBeInTheDocument();
  });
});

describe("Inbox: a plugin-install request", () => {
  const request = (id: string, title = "Database"): InboxItem => ({
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
    },
  });

  it("shows what it is, why, what it may do and what comes with it, and Approve and install / Deny answer the approval", async () => {
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
        reasons: [
          {
            reason:
              "The new version 1.2.0 asks for more permissions: + secrets: gitlab.",
            source: "keel",
          },
        ],
      },
    };
    const state = { list: [request("a_1"), request("a_2", "Wiki"), update] };
    const decided: { id: string; body: unknown }[] = [];
    server.use(
      http.get("/api/inbox", () =>
        HttpResponse.json({
          items: state.list,
          count: state.list.length,
          kinds: ["plugin-install"],
          projects: [
            {
              id: "ludus-engine",
              name: "ludus-engine",
              count: state.list.length,
            },
          ],
        } satisfies InboxView),
      ),
      http.post(
        "/api/approvals/:id/decide",
        async ({ request: req, params }) => {
          decided.push({ id: String(params.id), body: await req.json() });
          state.list = state.list.filter((x) => x.id !== params.id);
          return HttpResponse.json({ id: params.id, status: "approved" });
        },
      ),
    );
    location.hash = "#/inbox";
    render(<App />);
    const card = (
      await screen.findByRole("heading", { name: "Install Database 1.4.0?" })
    ).closest("article")!;
    expect(card).toHaveTextContent("install request");
    expect(card).toHaveTextContent("from KeelBot");
    expect(card).toHaveTextContent("Runs code in keel");
    expect(card).toHaveTextContent("by keel ✓");
    expect(within(card).getByLabelText("Why it asks")).toHaveTextContent(
      "“I need the sessions table's schema.”",
    );
    expect(within(card).getByLabelText("Why it asks")).toHaveTextContent(
      "“The flow fix-login needs SQL.” · An agent",
    );
    expect(
      within(card).getByRole("list", { name: "Permissions" }),
    ).toHaveTextContent("highRead your database connections");
    expect(card).toHaveTextContent("Also installs Code: Database needs it.");

    await user.click(
      within(card).getByRole("button", { name: "Approve and install" }),
    );
    await waitFor(() =>
      expect(decided[0]).toEqual({
        id: "a_1",
        body: { decision: "approve", why: "" },
      }),
    );
    expect(
      await screen.findByText("Database is installed. Restart keel to use it."),
    ).toBeInTheDocument();

    const wiki = (
      await screen.findByRole("heading", { name: "Install Wiki 1.4.0?" })
    ).closest("article")!;
    await user.type(within(wiki).getByLabelText("Note (optional)"), "not now");
    await user.click(within(wiki).getByRole("button", { name: "Deny" }));
    await waitFor(() =>
      expect(decided[1]).toEqual({
        id: "a_2",
        body: { decision: "deny", why: "not now" },
      }),
    );
    expect(
      await screen.findByText("Denied: Wiki is not installed."),
    ).toBeInTheDocument();

    // an update that asks for more: the new permissions, then Approve and update
    const git = (
      await screen.findByRole("heading", { name: "Update Git to 1.2.0?" })
    ).closest("article")!;
    expect(
      within(git).getByRole("list", { name: "New permissions" }),
    ).toHaveTextContent("+ secrets: gitlab");
    expect(git).toHaveTextContent("keel updates it from 1.1.0 to 1.2.0");
    expect(git).toHaveTextContent("from keel");
    await user.click(
      within(git).getByRole("button", { name: "Approve and update" }),
    );
    await waitFor(() =>
      expect(decided[2]).toEqual({
        id: "a_3",
        body: { decision: "approve", why: "" },
      }),
    );
    expect(
      await screen.findByText("Git is updated. Restart keel to use it."),
    ).toBeInTheDocument();
  });
});

describe("a keel with only its core", () => {
  let hidden: PageRegistration[] = [];
  afterEach(() => {
    hidden.forEach((p) => registerPage(p));
    hidden = [];
  });

  /** Take the Project pages away, as a keel whose image has no plugins. */
  function coreOnly() {
    hidden = allPages().filter((p) => p.group === "know");
    for (const p of hidden) registerPage({ ...p })();
  }

  it("offers the sets in the Project group, and a set installs what is missing and turns on what is off", async () => {
    const user = userEvent.setup();
    coreOnly();
    db.market.installed = [
      {
        name: "code",
        title: "Code",
        version: "1.0.0",
        from: "image",
        on: true,
      },
      {
        name: "wiki",
        title: "Wiki",
        version: "1.1.0",
        from: "image",
        on: false,
      },
    ];
    location.hash = "#/plugins";
    render(<App />);
    const nav = screen.getByRole("navigation", { name: "Screens" });
    const start = await within(nav).findByRole("group", {
      name: "Start with a set",
    });
    expect(start).toHaveTextContent(
      "Code, KeelBot, Map, Graph and Wiki are plugins now.",
    );
    await user.click(
      await within(start).findByRole("button", { name: "Developer" }),
    );
    await waitFor(() => expect(location.hash).toBe("#/plugins?set=developer"));

    const offer = (
      await screen.findByRole("heading", {
        name: "Start with the Developer set",
      })
    ).closest("section")!;
    const list = await within(offer).findByRole("list", {
      name: "The Developer set",
    });
    expect(list).toHaveTextContent("Code here");
    expect(list).toHaveTextContent("db will be installed");
    expect(list).toHaveTextContent("Wiki off: turns on");
    await user.click(
      within(offer).getByRole("button", { name: "Install 1 and turn on 1" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Install the Developer set?",
    });
    expect(
      await within(dialog).findByRole("list", {
        name: "What will be installed",
      }),
    ).toHaveTextContent("Database");
    expect(dialog).toHaveTextContent(
      "Turns on (they came in keel's image): wiki",
    );
    await user.click(within(dialog).getByRole("button", { name: "Install" }));
    await waitFor(() =>
      expect(calls("POST", "/api/plugins/install").map((c) => c.body)).toEqual([
        { name: "db", version: "1.4.0" },
      ]),
    );
    await waitFor(() =>
      expect(calls("PUT", "/api/plugins/wiki")[0]?.body).toEqual({ on: true }),
    );
    expect(
      await screen.findByText(
        "The plugins are ready. Restart keel to use them.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(location.hash).toBe("#/plugins"));
  });

  it("says when a set's link names no set", async () => {
    await openPlugins("#/plugins?set=nope");
    expect(
      await screen.findByText("There is no set called nope."),
    ).toBeInTheDocument();
  });

  it("leaves out what no catalog has, and says why", async () => {
    const user = userEvent.setup();
    await openPlugins("#/plugins?set=tickets");
    await user.click(await screen.findByRole("button", { name: "Install 2" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Install the Tickets set?",
    });
    expect(
      await within(dialog).findByText(
        "Left out: No plugin tasks in the catalogs.",
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Install" }),
    ).toBeDisabled();
  });
});

describe("a workflow that needs a plugin", () => {
  it("says so when it starts, and that the request waits in the Inbox", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/projects/:pid/flows", () =>
        HttpResponse.json(
          {
            error: "This workflow needs Database.",
            hint: "keel asked you in the Inbox to install it. Approve the request, restart keel, then start the flow again.",
            missing: ["db"],
            requests: ["a_1"],
          },
          { status: 409 },
        ),
      ),
    );
    location.hash = "#/projects";
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: "Start a flow" }),
    );
    await user.type(await screen.findByLabelText("What to build"), "Top 10");
    await user.click(screen.getByRole("button", { name: "Start flow" }));
    expect(
      await screen.findByText("This workflow needs Database."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/keel asked you in the Inbox to install it/),
    ).toBeInTheDocument();
  });
});
