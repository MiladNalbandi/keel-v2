// v0.15.2 Code › Source control › Log (like JetBrains' Git log): the graph's lanes, the Log tab, and the tab itself —
// the branch list, the commits with refs and "only on this branch", the filters, uncommitted changes, a commit's files.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { GitLog, LogCommit, RefsView } from "../gitLogApi";
import { authorsOf, byRemote, graphRows, logTitle, openLog, setLogBranch, sortRefs, when } from "../pages/repo/gitLog";
import { openTab, type Tabs } from "../pages/repo/model";
import { server } from "./setup";

const now = new Date().toISOString();
const c = (sha: string, subject: string, parents: string[], more: Partial<LogCommit> = {}): LogCommit => ({
  sha: sha.padEnd(40, "0"), parents: parents.map((p) => p.padEnd(40, "0")), subject, author: "Ann", email: "ann@example.com", at: now,
  refs: [], in_base: false, keel: false, ...more,
});

const refs: RefsView = {
  head: "feat/scores", base: "main",
  local: [
    { name: "feat/scores", sha: "f1", date: now, subject: "refuse a negative score", current: true, ahead: 2, behind: 1 },
    { name: "main", sha: "a3", date: now, subject: "docs: readme", current: false },
  ],
  remote: [{ name: "origin/main", sha: "a2", date: now, subject: "init", current: false }],
  tags: [{ name: "v1.0", sha: "a2", date: now, subject: "init", current: false }],
};

const commits = [
  c("f1", "feat(AC-002): refuse a negative score", ["f0"], { refs: [{ name: "feat/scores", kind: "local", current: true }], keel: true }),
  c("f0", "test(AC-002): a negative score is refused", ["a2"], { author: "Bob", email: "bob@example.com" }),
  c("a2", "init", ["a1"], { in_base: true, refs: [{ name: "v1.0", kind: "tag", current: false }, { name: "origin/main", kind: "remote", current: false }] }),
  c("a1", "first commit", [], { in_base: true, author: "Bob", email: "bob@example.com" }),
];

/** The log and refs routes; every log request's query is kept. */
function serve(more: { log?: (u: URLSearchParams) => Partial<GitLog> } = {}) {
  const asked: URLSearchParams[] = [];
  server.use(
    http.get("/api/projects/:pid/repo/refs", () => HttpResponse.json(refs)),
    http.get("/api/projects/:pid/repo/log", ({ request }) => {
      const u = new URL(request.url).searchParams;
      asked.push(u);
      const branch = u.get("all") ? null : u.get("branch") || "feat/scores";
      const base: GitLog = { branch, head: "feat/scores", base: "main", ahead: branch === "feat/scores" ? 2 : 0, behind: branch === "feat/scores" ? 1 : 0,
        commits: branch === "main" ? commits.slice(2) : commits, has_more: false };
      return HttpResponse.json({ ...base, ...more.log?.(u) });
    }),
  );
  return asked;
}

const tabs = () => screen.getAllByRole("tab").filter((t) => t.closest(".ed-tabs"));

async function openTheLog(user: ReturnType<typeof userEvent.setup>) {
  location.hash = "#/repo";
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Source control" }));
  await user.click(await screen.findByRole("button", { name: "Show the Git log" }));
  return screen.findByRole("region", { name: "Git log" });
}

describe("Git log logic", () => {
  it("draws lanes: a branch tip, a merge that opens a lane, and lanes that end in the same parent", () => {
    // five ← merge(three, four); four ← two; three ← two; two ← one
    const rows = graphRows([
      { sha: "five", parents: ["merge"] },
      { sha: "merge", parents: ["three", "four"] },
      { sha: "four", parents: ["two"] },
      { sha: "three", parents: ["two"] },
      { sha: "two", parents: ["one"] },
      { sha: "one", parents: [] },
    ]);
    expect(rows.map((r) => r.lane)).toEqual([0, 0, 1, 0, 0, 0]);
    expect(rows.map((r) => r.width)).toEqual([1, 2, 2, 2, 2, 1]);
    // a tip has no line from above; the merge's second parent gets lane 1
    expect(rows[0].segs).toEqual([{ x1: 0, y1: 1, x2: 0, y2: 2, color: 0 }]);
    expect(rows[1].segs).toContainEqual({ x1: 0, y1: 1, x2: 1, y2: 2, color: 1 });
    // "three" waits in lane 0 while "four" is drawn: a line straight through
    expect(rows[2].segs).toContainEqual({ x1: 0, y1: 0, x2: 0, y2: 2, color: 0 });
    // both lanes wait for "two": lane 1 ends in it
    expect(rows[4].segs).toContainEqual({ x1: 1, y1: 0, x2: 0, y2: 1, color: 1 });
    expect(rows[5].segs).toEqual([{ x1: 0, y1: 0, x2: 0, y2: 1, color: 0 }]);
  });

  it("gives a second branch tip a lane of its own, without a line from above", () => {
    const rows = graphRows([{ sha: "a", parents: ["c"] }, { sha: "b", parents: ["c"] }, { sha: "c", parents: [] }]);
    expect(rows.map((r) => r.lane)).toEqual([0, 1, 0]);
    expect(rows[1].segs.filter((s) => s.y1 === 0 && s.x1 === 1)).toEqual([]);
    expect(rows[1].color).not.toBe(rows[0].color);
    expect(rows[2].segs).toContainEqual({ x1: 1, y1: 0, x2: 0, y2: 1, color: rows[1].color });
  });

  it("keeps one Log tab and moves it to another branch", () => {
    let t: Tabs = openTab({ tabs: [], active: null }, { path: "a.kt" }, true);
    t = openLog(t);
    expect(t.tabs.map((x) => [x.id, x.path, x.preview])).toEqual([["a.kt", "a.kt", false], ["keel:log", "", false]]);
    expect(t.active).toBe("keel:log");
    t = { ...t, active: "a.kt" };
    t = openLog(t, "main");
    expect(t.tabs).toHaveLength(2);
    expect(t.tabs[1].path).toBe("main");
    expect(t.active).toBe("keel:log");
    expect(setLogBranch(t, "*").tabs[1].path).toBe("*");
    expect([logTitle(""), logTitle("*"), logTitle("feat/x")]).toEqual(["Git log", "Log: all branches", "Log: feat/x"]);
  });

  it("sorts badges, groups remote branches, names authors and dates", () => {
    expect(sortRefs([{ name: "v1", kind: "tag", current: false }, { name: "origin/x", kind: "remote", current: false }, { name: "x", kind: "local", current: true }])
      .map((r) => r.name)).toEqual(["x", "origin/x", "v1"]);
    expect(byRemote(refs.remote.concat({ ...refs.remote[0], name: "upstream/dev" })).map(([r, l]) => [r, l.map((x) => x.short)]))
      .toEqual([["origin", ["main"]], ["upstream", ["dev"]]]);
    expect(authorsOf(commits)).toEqual(["Ann", "Bob"]);
    const d = new Date(2026, 9, 8, 14, 5);
    expect(when(d.toISOString(), new Date(2026, 9, 8, 20, 0))).toBe("14:05");
    expect(when(new Date(2023, 2, 4).toISOString(), d)).toContain("2023");
    expect(when(new Date(2026, 2, 4).toISOString(), d)).not.toContain("2026");
  });
});

describe("Code › Source control › Log", () => {
  it("shows the branches, the commits with their refs, what only this branch has, and uncommitted changes on top", async () => {
    const user = userEvent.setup();
    serve();
    const log = await openTheLog(user);
    expect(tabs().map((t) => t.textContent)).toContain("Git log");

    const branches = within(log).getByRole("navigation", { name: "Branches" });
    const current = within(branches).getByRole("button", { name: "Show the log of feat/scores" });
    expect(current).toHaveTextContent("current");
    expect(current).toHaveTextContent("↑2");
    expect(within(within(branches).getByRole("group", { name: "origin" })).getByRole("button", { name: "Show the log of origin/main" })).toBeInTheDocument();
    expect(within(branches).getByRole("button", { name: "Show the log of HEAD" })).toHaveAttribute("aria-pressed", "true");

    const list = within(log).getByRole("listbox", { name: "Commits" });
    const rows = await within(list).findAllByRole("option");
    expect(rows[0]).toHaveTextContent("Uncommitted changes (3 files)");
    expect(rows.slice(1).map((r) => r.getAttribute("aria-label"))).toEqual([
      "feat(AC-002): refuse a negative score, Ann, f100000, only on feat/scores",
      "test(AC-002): a negative score is refused, Bob, f000000, only on feat/scores",
      "init, Ann, a200000, already on main",
      "first commit, Bob, a100000, already on main",
    ]);
    expect(rows[1]).toHaveTextContent("feat/scores");
    expect(rows[1]).toHaveTextContent("keel");
    expect(rows[3].querySelector(".lg-ref.k-tag")).toHaveTextContent("v1.0");
    expect(rows[3].querySelector(".lg-ref.k-remote")).toHaveTextContent("origin/main");
    expect(within(log).getByRole("search", { name: "Log filters" })).toHaveTextContent("2 commits only on feat/scores · 1 behind main");
    // it only reads: nothing here commits, checks out or resets
    expect(within(log).queryByRole("button", { name: /commit$|checkout|switch|reset|push/i })).not.toBeInTheDocument();

    // the uncommitted files, each with its diff against HEAD
    await user.click(rows[0]);
    const wt = await within(log).findByRole("region", { name: "Uncommitted changes" });
    expect(within(wt).getAllByRole("button", { name: /Show the uncommitted change of/ })).toHaveLength(3);
    expect(await within(wt).findByText("HEAD")).toBeInTheDocument();
  });

  it("a commit shows its files and a diff; a double click opens the commit tab; arrows move", async () => {
    const user = userEvent.setup();
    serve();
    const log = await openTheLog(user);
    const list = within(log).getByRole("listbox", { name: "Commits" });
    const row = await within(list).findByRole("option", { name: /^init, Ann/ });
    await user.click(row);
    expect(row).toHaveAttribute("aria-selected", "true");
    const detail = await within(log).findByRole("region", { name: "Commit a200000" });
    expect(detail).toHaveTextContent("Already on main");
    expect(detail).toHaveTextContent("ann@example.com");
    const file = await within(detail).findByRole("button", { name: "Show the change of api/ScoreController.kt in a200000" });
    expect(file).toHaveAttribute("aria-pressed", "true");
    expect(await within(detail).findByRole("region", { name: "Changes in api/ScoreController.kt" })).toBeInTheDocument();

    await user.dblClick(file);
    await waitFor(() => expect(tabs().map((t) => t.textContent)).toContainEqual(expect.stringContaining("ScoreController.kt @ a200000")));
    await user.click(tabs().find((t) => t.textContent === "Git log")!);

    // the keyboard: down to the next commit
    const again = within(screen.getByRole("listbox", { name: "Commits" }));
    screen.getByRole("listbox", { name: "Commits" }).focus();
    await user.keyboard("{ArrowDown}");
    expect(again.getByRole("option", { name: /^first commit/ })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("region", { name: "Commit a100000" })).toHaveTextContent("Already on main");
  });

  it("filters by branch, author, text and path, shows all branches, and reads more", async () => {
    const user = userEvent.setup();
    const asked = serve({ log: (u) => ({ has_more: Number(u.get("limit")) < 200 }) });
    const log = await openTheLog(user);
    await within(log).findAllByRole("option");
    expect(asked.at(-1)!.get("branch")).toBeNull();
    expect(asked.at(-1)!.get("limit")).toBe("100");

    await user.click(within(log).getByRole("button", { name: "Show the log of main" }));
    await waitFor(() => expect(asked.at(-1)!.get("branch")).toBe("main"));
    expect(tabs().map((t) => t.textContent)).toContain("Log: main");
    await waitFor(() => expect(within(log).getByRole("search", { name: "Log filters" })).toHaveTextContent("main is the base branch"));
    // not the current branch: no uncommitted row
    await waitFor(() => expect(within(log).queryByRole("option", { name: /Uncommitted/ })).not.toBeInTheDocument());

    await user.selectOptions(within(log).getByRole("combobox", { name: "Branch" }), "*");
    await waitFor(() => expect(asked.at(-1)!.get("all")).toBe("true"));
    expect(tabs().map((t) => t.textContent)).toContain("Log: all branches");

    await user.type(within(log).getByRole("combobox", { name: "Author" }), "ann");
    await waitFor(() => expect(asked.at(-1)!.get("author")).toBe("ann"));
    await user.type(within(log).getByRole("searchbox", { name: "Text or commit id" }), "negative{Enter}");
    await waitFor(() => expect(asked.at(-1)!.get("q")).toBe("negative"));
    await user.type(within(log).getByRole("textbox", { name: "Path" }), "api/{Enter}");
    await waitFor(() => expect(asked.at(-1)!.get("path")).toBe("api/"));
    expect(asked.at(-1)!.get("all")).toBe("true");
    expect(asked.at(-1)!.get("author")).toBe("ann");

    await user.click(within(log).getByRole("button", { name: "Show 100 more" }));
    await waitFor(() => expect(asked.at(-1)!.get("limit")).toBe("200"));
    await waitFor(() => expect(within(log).queryByRole("button", { name: /more/ })).not.toBeInTheDocument());

    await user.click(within(log).getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(asked.at(-1)!.get("author")).toBeNull());
  });

  it("a branch in Source control opens the log on it (no Git plugin needed)", async () => {
    const user = userEvent.setup();
    const asked = serve();
    location.hash = "#/repo";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Source control" }));
    await user.click(await screen.findByRole("button", { name: /^Branches/ }));
    await user.click(await screen.findByRole("button", { name: "Show the log of main" }));
    const log = await screen.findByRole("region", { name: "Git log" });
    await waitFor(() => expect(asked.at(-1)!.get("branch")).toBe("main"));
    expect((within(log).getByRole("combobox", { name: "Branch" }) as HTMLSelectElement).value).toBe("main");
  });
});
