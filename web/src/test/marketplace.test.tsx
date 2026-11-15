// v0.16.0 step 4 in the web (docs/plugins/13-step4-contract.md §9): Control › Plugins with its three tabs (Installed,
// Marketplace, Sources and rules), the install dialog, the restart banner, and a keel with only its core:
// #/settings/plugins?set=<id> installs a set. v0.16.1: the look of docs/plugins/mockup.html: keel core's row, nested rows,
// cards without a catalog, a plugin's own page, ?tab=. The Inbox card and the core welcome: corehome.test.tsx.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../App";
import { setAddonImporter } from "../addons";
import { definePlugin } from "../sdk";
import {
  allPages,
  registerPage,
  registerSlot,
  type PageRegistration,
  type SlotItem,
} from "../sdk/registry";
import { SLOTS } from "../sdk/slots";
import { db, server } from "./setup";
import { allPageNames, allPagesNav } from "./menu";

const calls = (method: string, path: string) =>
  db.calls.filter((c) => c.method === method && c.path === path);

async function openPlugins(hash = "#/settings/plugins") {
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
  it("is the one new page in Control, and lists keel core first, then each plugin with its version, parts, from, status and problems", async () => {
    installSome();
    await openPlugins();
    // keel 0.18.57 a tab of the Settings page: Settings in the menu, Plugins in the settings menu
    const nav = screen.getByRole("navigation", { name: "Screens" });
    expect(within(nav).getByRole("link", { name: /^Settings/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      within(screen.getByRole("navigation", { name: "Settings sections" })).getByRole("link", { name: "Plugins" }),
    ).toHaveAttribute("aria-current", "page");
    // a normal keel has its Project pages: no "No pages yet" box
    expect(
      within(nav).queryByRole("group", { name: "No pages yet" }),
    ).toBeNull();
    expect(screen.getByRole("tab", { name: "Installed (4)" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      screen.getByRole("button", { name: "Check for updates" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Browse the marketplace" }),
    ).toBeInTheDocument();

    // keel core is the first row: its version, its parts, the image, always on
    const code = await screen.findByTestId("plugin-code");
    const rows = within(code.closest("tbody")!).getAllByRole("row");
    expect(rows[0]).toHaveAttribute("data-testid", "keel-core");
    expect(rows[0]).toHaveTextContent("keel core");
    expect(rows[0]).toHaveTextContent("0.4.1");
    expect(rows[0]).toHaveTextContent("engineapiwebcontent");
    expect(rows[0]).toHaveTextContent("the image");
    expect(within(rows[0]).queryByRole("switch")).toBeNull(); // v0.17.1 always on: a pill, no switch
    expect(rows[0]).toHaveTextContent("always on");

    expect(code).toHaveTextContent("in the image");
    expect(code).toHaveTextContent("db needs it");
    expect(code).toHaveTextContent("{ }"); // its icon
    expect(within(code).queryByRole("button", { name: "Remove" })).toBeNull(); // the image's: turn off only
    expect(screen.getByTestId("plugin-wiki")).toHaveTextContent(
      "removed at restart",
    );
    expect(screen.getByTestId("plugin-hello")).toHaveTextContent("Off");
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

  it("nests the plugins that live in another one's page under it (Database in Code), and the host says how many", async () => {
    installSome();
    await openPlugins();
    const code = await screen.findByTestId("plugin-code");
    const order = within(code.closest("tbody")!)
      .getAllByRole("row")
      .map((r) => r.getAttribute("data-testid"));
    // Database lives in the Code page: right under Code; the others after, in keel's order
    expect(order).toEqual([
      "keel-core",
      "plugin-code",
      "plugin-db",
      "plugin-wiki",
      "plugin-hello",
    ]);
    expect(code).toHaveTextContent("1 plugin inside");
    const dbRow = screen.getByTestId("plugin-db");
    expect(dbRow).toHaveClass("child");
    expect(dbRow).toHaveTextContent("in Code");
    expect(screen.getByTestId("plugin-wiki")).not.toHaveClass("child");
    // the title opens the plugin's own page
    expect(within(dbRow).getByRole("link", { name: "Database" })).toHaveAttribute(
      "href",
      "#/settings/plugins/db",
    );
  });

  it("reads 'inside' from the slots too: another publisher's plugin with a piece in the Code page nests under Code", async () => {
    installSome();
    db.market.installed.push({
      name: "acme",
      title: "Acme lint",
      version: "0.2.0",
      from: "marketplace",
      on: true,
      parts: ["web"],
      status: "loaded",
      publisher: "acme",
    });
    const off = registerSlot(SLOTS.codeActivity, {
      id: "acme-lint",
      title: "Lint",
      icon: "search",
      plugin: "acme",
      component: () => null,
    } as SlotItem);
    try {
      await openPlugins();
      const acme = await screen.findByTestId("plugin-acme");
      expect(acme).toHaveClass("child");
      expect(acme).toHaveTextContent("in Code");
      expect(acme).toHaveTextContent("AL"); // its icon: the title's first letters
      expect(screen.getByTestId("plugin-code")).toHaveTextContent(
        "2 plugins inside",
      );
    } finally {
      off();
    }
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
    expect(calls("POST", "/api/plugins/db/update")[0]?.body).toEqual({
      version: "1.4.0",
    });
    expect(
      await screen.findByText("Database is updated. Restart keel to use it."),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Changes wait for a restart"),
    ).toBeInTheDocument();
    expect(screen.getByText(/waiting for restart/)).toBeInTheDocument();

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
    const confirm = screen.getByRole("group", { name: "Confirm" });
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

  it("#/settings/plugins?tab=market opens the Marketplace tab (the core welcome, the menu's + Add pages); tab=sources the sources", async () => {
    await openPlugins("#/settings/plugins?tab=market");
    expect(screen.getByRole("tab", { name: "Marketplace" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      await screen.findByRole("article", { name: "Database plugin" }),
    ).toBeInTheDocument();
    location.hash = "#/settings/plugins?tab=sources";
    await waitFor(() =>
      expect(
        screen.getByRole("tab", { name: "Sources and rules" }),
      ).toHaveAttribute("aria-selected", "true"),
    );
    location.hash = "#/settings/plugins?tab=installed";
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /^Installed/ })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
  });

  it("Check for updates reads the catalogs again and says what is ready; Browse marketplace opens the Marketplace", async () => {
    const user = userEvent.setup();
    installSome();
    await openPlugins();
    await screen.findByTestId("plugin-db");
    await user.click(screen.getByRole("button", { name: "Check for updates" }));
    await waitFor(() =>
      expect(calls("POST", "/api/marketplace/refresh")).toHaveLength(1),
    );
    expect(
      await screen.findByText("1 update ready: Database."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Browse the marketplace" }));
    expect(screen.getByRole("tab", { name: "Marketplace" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      await screen.findByRole("article", { name: "Database plugin" }),
    ).toBeInTheDocument();
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

// v0.17.1 (docs/plugins/22-one-plugins-page.md §5): each row says what its change does; keel core has no switch
describe("Control › Plugins: what a change does now", () => {
  it("keel core is always on: a pill, no switch; the intro says everything is on and how to start with fewer", async () => {
    installSome();
    await openPlugins();
    const core = await screen.findByTestId("keel-core");
    expect(within(core).queryByRole("switch")).toBeNull();
    expect(within(core).getByText("always on")).toBeInTheDocument();
    expect(
      screen.getByText(/Everything is on by default, like 0\.16\.1\. Start with fewer plugins:/),
    ).toHaveTextContent("keel2 start --core");
  });

  it("a change that needs a restart: its row says so, with Restart now; the banner stays", async () => {
    const user = userEvent.setup();
    installSome();
    await openPlugins();
    const code = await screen.findByTestId("plugin-code");
    expect(within(code).queryByText("applies after restart")).toBeNull();
    await user.click(within(code).getByRole("switch", { name: "Code on" }));
    expect(
      await within(screen.getByTestId("plugin-code")).findByText("applies after restart"),
    ).toBeInTheDocument();
    expect(await screen.findByText("Code is off after the next restart.")).toBeInTheDocument();
    expect(screen.getByText("Changes wait for a restart")).toBeInTheDocument();
    // only the row that changed says it
    expect(within(screen.getByTestId("plugin-db")).queryByText("applies after restart")).toBeNull();
    await user.click(
      within(screen.getByTestId("plugin-code")).getByRole("button", { name: "Restart now" }),
    );
    await waitFor(() =>
      expect(calls("POST", "/api/plugins/restart")[0]?.body).toEqual({ now: true }),
    );
    expect(
      await screen.findByText("keel restarts now. This page comes back in about 30 seconds."),
    ).toBeInTheDocument();
  });

  it("Restart now asks first when agent steps run; a keel that keel-start does not run says to restart by hand", async () => {
    const user = userEvent.setup();
    installSome();
    db.market.waiting.add("code");
    db.market.restart = { pending: true, scheduled: false, supervised: true, running: 1 };
    await openPlugins();
    const code = await screen.findByTestId("plugin-code");
    await user.click(await within(code).findByRole("button", { name: "Restart now" }));
    expect(within(code).getByRole("group", { name: "Confirm" })).toHaveTextContent(
      "1 agent step run now. They stop, and go on after the restart.",
    );
    expect(calls("POST", "/api/plugins/restart")).toHaveLength(0);
  });

  it("a web and content-only plugin applies at once: its page appears, and turning it off asks only for a page reload", async () => {
    const user = userEvent.setup();
    const off: (() => void)[] = [];
    try {
      db.market.installed = [
        { name: "notes", title: "Notes", version: "1.0.0", from: "file", on: false, parts: ["web", "content"],
          status: "off", can_remove: true },
      ];
      db.market.liveOnly.add("notes");
      const ENTRY = "/plugins/notes/1.0.0/web/index.js";
      db.market.onLive = (name, on) => {
        db.features = { ...db.features, plugins: on ? [{ name, title: "Notes", version: "1.0.0", web: { entry: ENTRY, css: [] } }] : [] };
      };
      const NotesPage = () => <h1>Notes board</h1>;
      setAddonImporter(async () => ({
        default: definePlugin({
          name: "notes",
          setup: (sdk) => {
            off.push(sdk.registerPage({ id: "notes-board", label: "Notes board", group: "know", order: 90, component: NotesPage }));
          },
        }),
      }));
      await openPlugins();
      const row = await screen.findByTestId("plugin-notes");
      // keel 0.28 a plugin's page shows in All pages (the menu keeps the main places and the pins)
      expect(await allPageNames()).not.toContain("Notes board");
      fireEvent.keyDown(document, { key: "Escape" });

      await user.click(within(row).getByRole("switch", { name: "Notes on" }));
      expect(await within(screen.getByTestId("plugin-notes")).findByText("applied")).toBeInTheDocument();
      expect(await screen.findByText("Notes is on now.")).toBeInTheDocument();
      expect(await within(await allPagesNav()).findByRole("link", { name: "Notes board" })).toBeInTheDocument();
      fireEvent.keyDown(document, { key: "Escape" });
      expect(screen.queryByText("Changes wait for a restart")).toBeNull();
      expect(screen.queryByText("applies after restart")).toBeNull();
      expect(within(screen.getByTestId("plugin-notes")).queryByText("after restart")).toBeNull(); // its status: On

      await user.click(within(screen.getByTestId("plugin-notes")).getByRole("switch", { name: "Notes on" }));
      const note = await within(screen.getByTestId("plugin-notes")).findByText(/^applied —/);
      expect(note).toHaveTextContent("applied — reload the page");
      expect(within(note).getByRole("button", { name: "reload the page" })).toBeInTheDocument();
      expect(screen.getByText("Notes is off now. Reload the page to see it.")).toBeInTheDocument();
      expect(screen.queryByText("Changes wait for a restart")).toBeNull();
    } finally {
      off.forEach((f) => f());
    }
  });
});

describe("Control › Plugins: Marketplace", () => {
  it("searches by words and category, and its cards show the icon, publisher, trust level and what is installed", async () => {
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
    expect(
      await screen.findByRole("heading", { name: "The keel set" }),
    ).toBeInTheDocument();
    const card = await screen.findByRole("article", {
      name: "Database plugin",
    });
    expect(card).toHaveTextContent("DB"); // its icon
    expect(card).toHaveTextContent("Runs code in keel");
    expect(card).toHaveTextContent("keel✓1.4.0");
    expect(card).toHaveTextContent("needs Code");
    expect(
      within(card).getByRole("button", { name: "Install Database" }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("article", { name: "Code plugin" })).getByText(
        "Installed",
      ),
    ).toBeInTheDocument();
    const community = screen
      .getByRole("heading", { name: "From the community" })
      .closest("section")!;
    const notes = within(community).getByRole("article", {
      name: "Release notes plugin",
    });
    expect(notes).toHaveTextContent("Content only");
    expect(notes).toHaveTextContent("community");
    // the trust legend
    expect(screen.getByText("· what a plugin is allowed to bring")).toBeInTheDocument();

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
    const chips = screen.getByRole("group", { name: "Category" });
    expect(
      within(chips)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual([
      "All",
      "Code",
      "Assistant",
      "Knowledge",
      "Tickets",
      "Product",
      "Community",
    ]);
    await user.click(within(chips).getByRole("button", { name: "Knowledge" }));
    expect(
      await screen.findByRole("article", { name: "Wiki plugin" }),
    ).toHaveTextContent("Adds pages");
    await waitFor(() =>
      expect(
        screen.queryByRole("article", { name: "Database plugin" }),
      ).toBeNull(),
    );
    await user.click(within(chips).getByRole("button", { name: "Community" }));
    expect(
      await screen.findByRole("article", { name: "Release notes plugin" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "Wiki plugin" })).toBeNull();
  });

  it("without a catalog, shows keel's own plugins from the image as cards, with a note, never an empty page", async () => {
    const user = userEvent.setup();
    db.market.catalog = [];
    db.market.status = [
      {
        id: "keel",
        ok: false,
        problem: "the catalog is not published yet (404)",
        old: false,
      },
    ];
    db.market.installed = [
      {
        name: "code",
        title: "Code",
        summary: "The Code page.",
        version: "1.0.0",
        from: "image",
        on: true,
        publisher: "keel",
        trust: "code",
      },
      {
        name: "graph",
        title: "Graph",
        version: "1.0.0",
        from: "image",
        on: false,
        publisher: "keel",
        trust: "code",
      },
    ];
    await openPlugins();
    await user.click(screen.getByRole("tab", { name: "Marketplace" }));
    expect(
      await screen.findByLabelText("No catalog yet"),
    ).toHaveTextContent("The catalog is not set up yet.");
    const keelSet = screen
      .getByRole("heading", { name: "The keel set" })
      .closest("section")!;
    const code = within(keelSet).getByRole("article", { name: "Code plugin" });
    expect(code).toHaveTextContent("The Code page.");
    expect(code).toHaveTextContent("Installed");
    expect(code).toHaveTextContent("keel✓1.0.0");
    expect(
      within(keelSet).getByRole("article", { name: "Graph plugin" }),
    ).toHaveTextContent("Installed · off");
    // the search works on them too
    await user.type(
      screen.getByRole("searchbox", { name: /Search plugins/ }),
      "graph",
    );
    await waitFor(() =>
      expect(screen.queryByRole("article", { name: "Code plugin" })).toBeNull(),
    );
    expect(
      screen.getByRole("article", { name: "Graph plugin" }),
    ).toBeInTheDocument();
  });

  it("opens a plugin's own page from its card, and its install dialog lists everything that will be installed with their permissions", async () => {
    const user = userEvent.setup();
    await openPlugins();
    await user.click(screen.getByRole("tab", { name: "Marketplace" }));
    const card = await screen.findByRole("article", { name: "Database plugin" });
    await user.click(within(card).getByRole("link", { name: "Database" }));
    await waitFor(() => expect(location.hash).toBe("#/settings/plugins/db"));
    expect(
      await screen.findByRole("heading", { name: "Database", level: 1 }),
    ).toBeInTheDocument();
    const crumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(crumb).toHaveTextContent("Plugins›Marketplace›Database");
    const perms = await screen.findByRole("region", { name: "Permissions" });
    expect(perms).toHaveTextContent("highRead your database connections");
    expect(perms).toHaveTextContent(
      "mediumTalk to the hosts of your connections",
    );
    expect(perms).toHaveTextContent("mediumRead files in /workspace");
    expect(perms).toHaveTextContent("3 asked");
    expect(screen.getByRole("region", { name: "Needs" })).toHaveTextContent(
      "Code will be installed too",
    );
    const versions = screen.getByRole("region", { name: "Versions" });
    expect(versions).toHaveTextContent("1.3.0");
    expect(versions).toHaveTextContent("keel >=0.15.4");
    expect(versions).toHaveTextContent("2026-09-18");
    expect(
      screen.getByRole("region", { name: "Checks keel runs before install" }),
    ).toHaveTextContent("signed by keel, a key this keel trusts");
    expect(screen.getByRole("region", { name: "About" })).toHaveTextContent(
      "Connect a database.",
    );

    await user.click(screen.getByRole("button", { name: "Install 1.4.0" }));
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
    // now installed: the page offers Turn off (Remove asks first)
    expect(
      await screen.findByRole("button", { name: "Turn off" }),
    ).toBeInTheDocument();
    await user.click(within(crumb).getByRole("link", { name: "Marketplace" }));
    await waitFor(() => expect(location.hash).toBe("#/settings/plugins"));
    expect(screen.getByRole("tab", { name: "Installed (2)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Marketplace" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("an image plugin no catalog lists has its own page with what keel knows, and its buttons", async () => {
    const user = userEvent.setup();
    db.market.installed = [
      {
        name: "graph",
        title: "Graph",
        summary: "A local index of the project's symbols.",
        version: "1.0.0",
        from: "image",
        on: true,
        status: "loaded",
        parts: ["engine", "web"],
        publisher: "keel",
        trust: "code",
        permissions: { workspace: "read" },
        can_remove: false,
      },
    ];
    location.hash = "#/settings/plugins/graph";
    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "Graph", level: 1 }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/No catalog lists Graph yet/),
    ).toBeInTheDocument();
    const about = screen.getByRole("region", { name: "About" });
    expect(about).toHaveTextContent("A local index of the project's symbols.");
    expect(about).toHaveTextContent("engineEngine code");
    expect(about).toHaveTextContent("webPages and panels");
    expect(screen.getByRole("region", { name: "Permissions" })).toHaveTextContent(
      "mediumRead files in /workspace",
    );
    expect(screen.getByRole("region", { name: "Versions" })).toHaveTextContent(
      "The version in keel's image.",
    );
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull(); // the image's
    await user.click(screen.getByRole("button", { name: "Turn off" }));
    await waitFor(() =>
      expect(calls("PUT", "/api/plugins/graph")[0]?.body).toEqual({ on: false }),
    );
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

  it("#/settings/plugins?set=<id> (a set's Install N on the core welcome) installs what is missing and turns on what is off", async () => {
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
    location.hash = "#/settings/plugins?set=developer";
    render(<App />);

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
    expect(
      within(offer).getByRole("button", { name: "Install 1 and turn on 1" }),
    ).toBeInTheDocument();
    // the set's link opens its install question by itself; nothing installs before the person presses Install
    const dialog = await screen.findByRole("dialog", {
      name: "Install the Developer set?",
    });
    expect(calls("POST", "/api/plugins/install")).toEqual([]);
    expect(calls("PUT", "/api/plugins/wiki")).toEqual([]);
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
    await waitFor(() => expect(location.hash).toBe("#/settings/plugins"));
  });

  it("a set's question that was cancelled stays closed, and the offer's button opens it again", async () => {
    const user = userEvent.setup();
    await openPlugins("#/settings/plugins?set=tickets");
    const dialog = await screen.findByRole("dialog", {
      name: "Install the Tickets set?",
    });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Install the Tickets set?" }),
      ).toBeNull(),
    );
    await new Promise((r) => setTimeout(r, 300));
    expect(
      screen.queryByRole("dialog", { name: "Install the Tickets set?" }),
    ).toBeNull();
    expect(calls("POST", "/api/plugins/install")).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Install 2" }));
    expect(
      await screen.findByRole("dialog", { name: "Install the Tickets set?" }),
    ).toBeInTheDocument();
  });

  it("says when a set's link names no set", async () => {
    await openPlugins("#/settings/plugins?set=nope");
    expect(
      await screen.findByText("There is no set called nope."),
    ).toBeInTheDocument();
  });

  it("leaves out what no catalog has, and says why", async () => {
    await openPlugins("#/settings/plugins?set=tickets");
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

describe("Settings › Plugins: the Installed filter (keel 0.18.57)", () => {
  it("filters the rows by name and by state", async () => {
    const user = userEvent.setup();
    installSome();
    await openPlugins();
    expect(await screen.findByTestId("plugin-code")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Filter installed plugins"), "zzz-none");
    expect(screen.queryByTestId("plugin-code")).toBeNull();
    expect(screen.queryByTestId("keel-core")).toBeNull();
    expect(screen.getByText("No installed plugin matches. Try the marketplace.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Filter installed plugins"));
    await user.click(within(screen.getByRole("group", { name: "Show" })).getByRole("button", { name: "Updates" }));
    expect(screen.queryByTestId("keel-core")).toBeNull();
  });
});
