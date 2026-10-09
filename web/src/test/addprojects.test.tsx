// v0.15.7 Add projects from your folders (All projects → Add projects: the repos in KEEL_FOLDERS, search, select,
// add many at once, Browse… a folder's tree) and the menu's searchable project switcher (open, filter, keys, switch,
// "Add projects…", the folded rail); ⌘K still switches projects.

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { BrowseDir, Folders, FolderWalk, Project } from "../api";
import { App } from "../App";
import { initials } from "../components/ProjectSwitcher";
import { db, server } from "./setup";

const FOLDERS: Folders = {
  roots: [
    { path: "/workspace", exists: true },
    { path: "/folders/work", exists: true },
    { path: "/folders/gone", exists: false },
  ],
  repos: [
    {
      path: "/workspace/ludus-engine",
      name: "ludus-engine",
      root: "/workspace",
      project_id: "ludus-engine",
    },
    {
      path: "/folders/work/api",
      name: "api",
      root: "/folders/work",
      project_id: null,
    },
    {
      path: "/folders/work/web",
      name: "web",
      root: "/folders/work",
      project_id: null,
    },
    {
      path: "/folders/work/tools/cli",
      name: "cli",
      root: "/folders/work",
      project_id: null,
    },
  ],
  truncated: false,
};

const dir = (
  path: string,
  repo: boolean,
  project_id: string | null = null,
): BrowseDir => ({ path, name: path.split("/").pop()!, repo, project_id });
const WALKS: Record<string, FolderWalk> = {
  "/folders/work": {
    path: "/folders/work",
    root: "/folders/work",
    parent: null,
    dirs: [dir("/folders/work/api", true), dir("/folders/work/tools", false)],
  },
  "/folders/work/tools": {
    path: "/folders/work/tools",
    root: "/folders/work",
    parent: "/folders/work",
    dirs: [
      dir("/folders/work/tools/cli", true),
      dir("/folders/work/tools/lib", true, "lib"),
    ],
  },
};

const newProject = (root: string): Project => ({
  id: root.split("/").pop()!,
  name: root.split("/").pop()!,
  root,
  branch: "main",
  flow: null,
  phase: "none",
  acs: [0, 0],
  waiting: 0,
  running: 0,
});

/** The folder routes, like the api: the bulk add adds every folder but /folders/work/web, which it skips. */
function folders(data: Folders = FOLDERS) {
  const bulk: unknown[] = [];
  const browsed: string[] = [];
  server.use(
    http.get("/api/folders", () => HttpResponse.json(data)),
    http.get("/api/folders/browse", ({ request }) => {
      const path =
        new URL(request.url).searchParams.get("path") ?? "/folders/work";
      browsed.push(path);
      return WALKS[path]
        ? HttpResponse.json(WALKS[path])
        : HttpResponse.json(
            { error: `${path} is not inside the folders keel may look in` },
            { status: 403 },
          );
    }),
    http.post("/api/projects/bulk", async ({ request }) => {
      const body = (await request.json()) as { roots: string[] };
      bulk.push(body);
      const added = body.roots
        .filter((r) => r !== "/folders/work/web")
        .map(newProject);
      db.projects.push(...added);
      return HttpResponse.json({
        added,
        skipped: body.roots.includes("/folders/work/web")
          ? [{ root: "/folders/work/web", why: "already a project (web)" }]
          : [],
      });
    }),
  );
  return { bulk, browsed };
}

async function openPanel() {
  location.hash = "#/projects";
  render(<App />);
  await screen.findByText("/workspace/platform");
  await userEvent.click(
    screen.getAllByRole("button", { name: "Add projects" })[0],
  );
  return screen.findByRole("dialog", { name: "Add projects" });
}

const group = (dlg: HTMLElement, root: string) =>
  within(dlg).getByRole("group", { name: root });
const switcher = () => screen.getByRole("button", { name: /^Project: / });

describe("Add projects", () => {
  it("lists the repos of every folder, marks the added ones, searches, selects and adds many at once", async () => {
    const user = userEvent.setup();
    const { bulk } = folders();
    const dlg = await openPanel();
    const work = await waitFor(() => group(dlg, "/folders/work"));
    expect(
      within(work)
        .getAllByRole("checkbox")
        .map((c) => c.getAttribute("aria-label")),
    ).toEqual(["api", "web", "cli"]);
    expect(
      within(work).getByText("work/tools/cli"),
    ).toBeInTheDocument();
    // a repo that is a project already: ticked, disabled, "added"
    const ws = group(dlg, "/workspace");
    expect(
      within(ws).getByRole("checkbox", { name: "ludus-engine" }),
    ).toBeDisabled();
    expect(within(ws).getByText("added")).toBeInTheDocument();
    expect(
      within(group(dlg, "/folders/gone")).getByText("not there"),
    ).toBeInTheDocument();
    const addBtn = () =>
      within(dlg).getByRole("button", {
        name: /^Add (\d+ projects?|projects)$/,
      });
    expect(addBtn()).toBeDisabled();

    // search, then "Select all shown" ticks only what shows
    await user.type(
      within(dlg).getByRole("searchbox", { name: "Search repos" }),
      "we",
    );
    expect(
      within(group(dlg, "/folders/work"))
        .getAllByRole("checkbox")
        .map((c) => c.getAttribute("aria-label")),
    ).toEqual(["web"]);
    expect(
      within(dlg).queryByRole("checkbox", { name: "api" }),
    ).not.toBeInTheDocument();
    await user.click(
      within(dlg).getByRole("button", { name: "Select all shown" }),
    );
    expect(addBtn()).toHaveTextContent("Add 1 project");
    await user.clear(
      within(dlg).getByRole("searchbox", { name: "Search repos" }),
    );
    await user.type(
      within(dlg).getByRole("searchbox", { name: "Search repos" }),
      "zzz",
    );
    expect(
      within(dlg).getAllByText("No repo matches “zzz”.").length,
    ).toBeGreaterThan(0);
    await user.clear(
      within(dlg).getByRole("searchbox", { name: "Search repos" }),
    );
    await user.click(within(dlg).getByRole("checkbox", { name: "api" }));
    expect(addBtn()).toHaveTextContent("Add 2 projects");

    await user.click(addBtn());
    const result = await within(dlg).findByRole("status", { name: "Result" });
    expect(bulk).toEqual([
      { roots: ["/folders/work/web", "/folders/work/api"] },
    ]);
    expect(result).toHaveTextContent("Added 1: api");
    expect(result).toHaveTextContent("Skipped 1:");
    expect(result).toHaveTextContent(
      "/folders/work/web — already a project (web)",
    );
    // the project list reloads: the new project is on All projects
    await waitFor(() =>
      expect(
        screen
          .getAllByTestId("project-row")
          .map((r) => r.querySelector(".proj-name")?.textContent),
      ).toContain("api"),
    );
    expect(addBtn()).toHaveTextContent("Add projects");
  });

  it("walks a folder's tree with breadcrumbs and ticks repos there", async () => {
    const user = userEvent.setup();
    const { bulk, browsed } = folders();
    const dlg = await openPanel();
    await waitFor(() => group(dlg, "/folders/work"));
    expect(
      within(dlg).getByRole("button", { name: "Browse /folders/gone" }),
    ).toBeDisabled();
    await user.click(
      within(dlg).getByRole("button", { name: "Browse /folders/work" }),
    );
    const walk = await within(dlg).findByRole("list", {
      name: "Folders in /folders/work",
    });
    expect(browsed).toEqual(["/folders/work"]);
    expect(
      within(walk).getByRole("checkbox", { name: "api" }),
    ).toBeInTheDocument();
    expect(within(walk).getByText("repo")).toBeInTheDocument();

    await user.click(within(walk).getByRole("button", { name: "Open tools" }));
    const tools = await within(dlg).findByRole("list", {
      name: "Folders in /folders/work/tools",
    });
    const crumbs = within(dlg).getByRole("navigation", { name: "Folder path" });
    expect(crumbs).toHaveTextContent("/folders/work/tools");
    expect(within(tools).getByRole("checkbox", { name: "lib" })).toBeDisabled();
    await user.click(within(tools).getByRole("checkbox", { name: "cli" }));
    // the root crumb goes back up
    await user.click(
      within(crumbs).getByRole("button", { name: "/folders/work" }),
    );
    await within(dlg).findByRole("list", { name: "Folders in /folders/work" });

    await user.click(
      within(dlg).getByRole("button", { name: "← Back to the list" }),
    );
    expect(
      within(group(dlg, "/folders/work")).getByRole("checkbox", {
        name: "cli",
      }),
    ).toBeChecked();
    await user.click(
      within(dlg).getByRole("button", { name: "Add 1 project" }),
    );
    await within(dlg).findByRole("status", { name: "Result" });
    expect(bulk).toEqual([{ roots: ["/folders/work/tools/cli"] }]);
  });

  it("says how to add folders when none is mounted, and keeps Add by path", async () => {
    const user = userEvent.setup();
    folders({ roots: [], repos: [] });
    const dlg = await openPanel();
    expect(
      await within(dlg).findByText(/keel2 add <folder> \[<folder>…\]/),
    ).toBeInTheDocument();
    await user.click(within(dlg).getByText("Add by path"));
    await user.clear(within(dlg).getByLabelText("Folder inside the container"));
    await user.type(
      within(dlg).getByLabelText("Folder inside the container"),
      "/workspace/new-one",
    );
    await user.click(within(dlg).getByRole("button", { name: "Add repo" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Add projects" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("status")).toHaveTextContent("added.");
  });

  it("opens from #/projects/add, and closing it goes back to #/projects", async () => {
    folders();
    location.hash = "#/projects/add";
    render(<App />);
    const dlg = await screen.findByRole("dialog", { name: "Add projects" });
    await within(dlg).findByRole("group", { name: "/folders/work" });
    await userEvent.click(
      within(dlg).getAllByRole("button", { name: "Close" })[0],
    );
    await waitFor(() => expect(location.hash).toBe("#/projects"));
    expect(
      screen.queryByRole("dialog", { name: "Add projects" }),
    ).not.toBeInTheDocument();
  });
});

describe("the project switcher", () => {
  it("opens a searchable list with each project's waiting count and branch, and switches with the keys", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(/AC gate — AC-002/);
    expect(switcher()).toHaveAccessibleName("Project: ludus-engine, 1 waiting");
    expect(switcher()).toHaveTextContent("ac-gate · ⎇ feat/scores");
    await user.click(switcher());
    const list = screen.getByRole("listbox", { name: "Projects" });
    expect(
      within(list)
        .getAllByRole("option")
        .map((o) => o.getAttribute("aria-label")),
    ).toEqual(["ludus-engine", "platform", "YegiResearcher", "Add projects…"]);
    const ludus = within(list).getByRole("option", { name: "ludus-engine" });
    expect(ludus).toHaveAttribute("aria-selected", "true");
    expect(ludus).toHaveTextContent("◆ 1");
    expect(
      within(list).getByRole("option", { name: "platform" }),
    ).toHaveTextContent("bug-investigate · ⎇ fix/tally-queue");
    const box = screen.getByRole("combobox", { name: "Find a project" });
    expect(box).toHaveFocus();

    // type to filter (name or branch), ↓ moves, Enter switches
    await user.type(box, "main");
    expect(
      within(list)
        .getAllByRole("option")
        .map((o) => o.getAttribute("aria-label")),
    ).toEqual(["YegiResearcher", "Add projects…"]);
    await user.clear(box);
    await user.keyboard("{ArrowDown}");
    expect(within(list).getByRole("option", { name: "platform" })).toHaveClass(
      "is-active",
    );
    expect(box).toHaveAttribute(
      "aria-activedescendant",
      within(list).getByRole("option", { name: "platform" }).id,
    );
    await user.keyboard("{Enter}");
    expect(
      screen.queryByRole("listbox", { name: "Projects" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Now showing platform",
    );
    expect(localStorage.getItem("keel2.project")).toBe("platform");
    expect(switcher()).toHaveAccessibleName("Project: platform");
    expect(switcher()).toHaveFocus();
  });

  it("types to open, Esc clears the search and then closes", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(/AC gate — AC-002/);
    switcher().focus();
    await user.keyboard("p");
    const box = screen.getByRole("combobox", { name: "Find a project" });
    expect(box).toHaveValue("p");
    expect(
      within(screen.getByRole("listbox", { name: "Projects" }))
        .getAllByRole("option")
        .map((o) => o.getAttribute("aria-label")),
    ).toEqual(["platform", "Add projects…"]);
    await user.type(box, "zz");
    expect(screen.getByText("No project matches “pzz”.")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(box).toHaveValue("");
    await user.keyboard("{Escape}");
    expect(
      screen.queryByRole("listbox", { name: "Projects" }),
    ).not.toBeInTheDocument();
    expect(switcher()).toHaveFocus();
    expect(localStorage.getItem("keel2.project")).toBeNull();
  });

  it("goes to Flow when you switch from All projects", async () => {
    const user = userEvent.setup();
    location.hash = "#/projects";
    render(<App />);
    await screen.findByText("/workspace/platform");
    await user.click(switcher());
    await user.click(screen.getByRole("option", { name: "YegiResearcher" }));
    await waitFor(() => expect(location.hash).toBe("#/flow"));
    expect(screen.getByRole("status")).toHaveTextContent(
      "Now showing YegiResearcher",
    );
  });

  it('"Add projects…" opens the Add projects panel on All projects', async () => {
    const user = userEvent.setup();
    folders();
    render(<App />);
    await screen.findByText(/AC gate — AC-002/);
    await user.click(switcher());
    // ↑ at the top stays there; ↓ goes down to the last row
    await user.keyboard("{ArrowUp}");
    for (let i = 0; i < 4; i++) await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("option", { name: "Add projects…" })).toHaveClass(
      "is-active",
    );
    await user.keyboard("{Enter}");
    await waitFor(() => expect(location.hash).toBe("#/projects/add"));
    expect(
      await screen.findByRole("dialog", { name: "Add projects" }),
    ).toBeInTheDocument();
  });

  it("works in the folded rail", async () => {
    const user = userEvent.setup();
    localStorage.setItem("keel2.nav.hidden", "1");
    render(<App />);
    await screen.findByText(/AC gate — AC-002/);
    const rail = document.getElementById("ps-rail-btn")!;
    expect(rail).toHaveAccessibleName("Project: ludus-engine, 1 waiting");
    expect(rail).toHaveTextContent("LE");
    await user.click(rail);
    await user.click(
      within(screen.getByRole("listbox", { name: "Projects" })).getByRole(
        "option",
        { name: "platform" },
      ),
    );
    expect(localStorage.getItem("keel2.project")).toBe("platform");
    expect(document.getElementById("ps-rail-btn")).toHaveTextContent("PL");
  });

  it("makes two letters for the rail", () => {
    expect(initials("ludus-engine")).toBe("LE");
    expect(initials("YegiResearcher")).toBe("YR");
    expect(initials("platform")).toBe("PL");
  });
});

describe("⌘K", () => {
  function inbox() {
    server.use(
      http.get("/api/inbox", () =>
        HttpResponse.json({ items: [], count: 0, kinds: [], projects: [] }),
      ),
    );
  }
  async function launcher() {
    fireEvent.keyDown(window, { key: "k", code: "KeyK", ctrlKey: true });
    return screen.findByRole("dialog", { name: "Launcher" });
  }

  it("still switches projects", async () => {
    inbox();
    render(<App />);
    await screen.findByText(/AC gate — AC-002/);
    const dlg = await launcher();
    await userEvent.type(within(dlg).getByRole("combobox"), "Switch to plat");
    await userEvent.click(
      await within(dlg).findByRole("option", { name: /Switch to platform/ }),
    );
    await waitFor(() =>
      expect(localStorage.getItem("keel2.project")).toBe("platform"),
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Now showing platform",
    );
  });

  it('has an "Add projects…" action', async () => {
    inbox();
    folders();
    render(<App />);
    await screen.findByText(/AC gate — AC-002/);
    const dlg = await launcher();
    await userEvent.type(within(dlg).getByRole("combobox"), "Add projects");
    await userEvent.click(
      await within(dlg).findByRole("option", { name: /Add projects…/ }),
    );
    await waitFor(() => expect(location.hash).toBe("#/projects/add"));
    await act(async () => undefined);
    expect(
      await screen.findByRole("dialog", { name: "Add projects" }),
    ).toBeInTheDocument();
  });
});
