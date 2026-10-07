// v0.5.1 the Code page as a small read-only VS Code: tabs (preview / pin / close), quick open, find in file,
// search across files, diffs, deep links with a line, source control, the phone's two screens — and its logic.

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { DiffRow } from "../components/Code";
import {
  closeTab, findAll, findRegExp, fuzzy, markHtml, openTab, parseDeepLink, parseGoto, pinTab, rankFiles, repoHash, retargetTab,
  splitHtmlLines, splitRows, visibleRows, indexTree, webUrl, type Tabs,
} from "../pages/repo/model";
import * as fx from "./fixtures";
import { db } from "./setup";

const at = (hash: string) => {
  location.hash = hash;
};
const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);
const tabs = () => screen.getAllByRole("tab").filter((t) => t.closest(".ed-tabs"));
const row = (n: number) => document.querySelector<HTMLElement>(`.cv-row[data-line="${n}"]`);

describe("Repo IDE logic", () => {
  it("parses deep links and builds them", () => {
    expect(parseDeepLink("src/a.kt:12")).toEqual({ path: "src/a.kt", line: 12 });
    expect(parseDeepLink("src/a.kt")).toEqual({ path: "src/a.kt" });
    expect(parseDeepLink("db/migration/V1__init.sql:3")).toEqual({ path: "db/migration/V1__init.sql", line: 3 });
    expect(parseDeepLink("")).toBeNull();
    expect(repoHash("src/my file.kt", 4)).toBe("#/repo/src/my%20file.kt:4");
  });

  it("opens a preview tab that the next one replaces, pins it, and closes to the neighbour", () => {
    let t: Tabs = { tabs: [], active: null };
    t = openTab(t, { path: "a.kt" });
    expect(t.tabs.map((x) => [x.path, x.preview])).toEqual([["a.kt", true]]);
    t = openTab(t, { path: "b.kt" });
    expect(t.tabs.map((x) => x.path)).toEqual(["b.kt"]);
    t = pinTab(t, "b.kt");
    t = openTab(t, { path: "c.kt" });
    t = openTab(t, { path: "d.kt" }, true);
    expect(t.tabs.map((x) => `${x.path}${x.preview ? "*" : ""}`)).toEqual(["b.kt", "c.kt*", "d.kt"]);
    expect(t.active).toBe("d.kt");
    t = openTab(t, { path: "c.kt" }, true);
    expect(t.tabs.find((x) => x.path === "c.kt")!.preview).toBe(false);
    t = closeTab(t, "c.kt");
    expect(t.active).toBe("d.kt");
    t = { ...t, active: "d.kt" };
    t = closeTab(t, "d.kt");
    expect(t.active).toBe("b.kt");
  });

  it("moves a console tab to another database in its place, or to that database's open console", () => {
    let t: Tabs = { tabs: [], active: null };
    t = openTab(t, { path: "a.kt" }, true);
    t = openTab(t, { kind: "db", path: "local" }, true);
    t = openTab(t, { path: "b.kt" }, true);
    t = { ...t, active: "db:local" };
    t = retargetTab(t, "db:local", { kind: "db", path: "prod" });
    expect(t.tabs.map((x) => x.id)).toEqual(["a.kt", "db:prod", "b.kt"]);
    expect(t.active).toBe("db:prod");
    t = openTab(t, { kind: "db", path: "test" }, true);
    t = retargetTab(t, "db:test", { kind: "db", path: "prod" });
    expect(t.tabs.map((x) => x.id)).toEqual(["a.kt", "db:prod", "b.kt"]);
    expect(t.active).toBe("db:prod");
    expect(retargetTab(t, "db:gone", { kind: "db", path: "x" })).toBe(t);
  });

  it("ranks files fuzzily: the name first, letters in a row and at word starts", () => {
    const files = ["src/main/ScoreController.kt", "src/main/ScoreRepository.kt", "docs/scoring.md", "build.gradle.kts", "src/sc/o/r/e.txt"];
    expect(rankFiles(files, "screp")[0].path).toBe("src/main/ScoreRepository.kt");
    expect(rankFiles(files, "scorecon")[0].path).toBe("src/main/ScoreController.kt");
    expect(rankFiles(files, "zzz")).toEqual([]);
    expect(fuzzy("gradle", "build.gradle.kts")!.hits).toEqual([6, 7, 8, 9, 10, 11]);
  });

  it("splits highlighted HTML per line and marks ranges without breaking the tags", () => {
    const lines = splitHtmlLines('a <span class="c">/* one\ntwo */</span> b\nc');
    expect(lines).toEqual(['a <span class="c">/* one</span>', '<span class="c">two */</span> b', "c"]);
    expect(markHtml('x <span class="k">val</span> y', [[1, 5, "fd"]])).toBe('x<mark class="fd"> </mark><span class="k"><mark class="fd">val</mark></span> y');
    expect(markHtml("a &lt; b", [[2, 3, "fd"]])).toBe('a <mark class="fd">&lt;</mark> b');
  });

  it("finds in a file with case, whole word and regex, and says when a regex is bad", () => {
    const lines = ["score Score", "scoreboard", "x = score;"];
    expect(findAll(lines, findRegExp("score", { matchCase: false, word: false, regex: false }) as RegExp)).toHaveLength(4);
    expect(findAll(lines, findRegExp("score", { matchCase: true, word: true, regex: false }) as RegExp).map((h) => h.line)).toEqual([1, 3]);
    expect(findAll(lines, findRegExp("s\\w+d", { matchCase: false, word: false, regex: true }) as RegExp)).toEqual([{ line: 2, start: 0, end: 10 }]);
    expect(findRegExp("(", { matchCase: false, word: false, regex: true })).toHaveProperty("error");
    expect(parseGoto("12:4", 100)).toEqual({ line: 12, col: 4 });
    expect(parseGoto("500", 100)).toEqual({ line: 100, col: 1 });
    expect(parseGoto("x", 100)).toBeNull();
  });

  it("pairs removed and added lines side by side", () => {
    const rows: DiffRow[] = [
      { kind: "hunk", text: "@@ -1,3 +1,3 @@" },
      { kind: "ctx", text: "a", old: 1, new: 1 },
      { kind: "del", text: "b", old: 2 },
      { kind: "add", text: "B", new: 2 },
      { kind: "add", text: "B2", new: 3 },
      { kind: "ctx", text: "c", old: 3, new: 4 },
    ];
    const s = splitRows(rows);
    expect(s.map((r) => (r.kind === "hunk" ? "hunk" : `${r.left?.text ?? "_"}|${r.right?.text ?? "_"}`))).toEqual(["hunk", "a|a", "b|B", "_|B2", "c|c"]);
  });

  it("filters the tree to matching files and the folders on their way", () => {
    const idx = indexTree(fx.tree);
    // folders first, dot-folders before the rest (like VS Code)
    expect(visibleRows(idx, new Set()).map((r) => r.node.path)).toEqual([".keel", "api", "README.md"]);
    expect(visibleRows(idx, new Set(), "repo").map((r) => r.node.path)).toEqual(["api", "api/ScoreRepository.kt"]);
    expect(webUrl("https://github.com/o/r.git", "feat/x", "src/a.kt", 3)).toBe("https://github.com/o/r/blob/feat/x/src/a.kt#L3");
    expect(webUrl("git@github.com:o/r.git", "main", "a.kt")).toBe("https://github.com/o/r/blob/main/a.kt");
    expect(webUrl("https://example.org/r.git", "main", "a.kt")).toBeNull();
  });
});

describe("Repo IDE", () => {
  it("opens files as preview tabs, replaces them, pins on double click and closes (button and middle click)", async () => {
    const user = userEvent.setup();
    at("#/repo");
    render(<App />);
    await user.click(await screen.findByRole("treeitem", { name: /^api/ }));
    await user.click(await screen.findByRole("treeitem", { name: "Open api/ScoreController.kt" }));
    expect(await screen.findByRole("region", { name: "Code of api/ScoreController.kt" })).toBeInTheDocument();
    expect(tabs()).toHaveLength(1);
    expect(tabs()[0]).toHaveClass("preview");
    expect(location.hash).toBe("#/repo/api/ScoreController.kt");

    // a single click on another file replaces the preview tab
    await user.click(screen.getByRole("treeitem", { name: "Open README.md" }));
    await screen.findByRole("region", { name: "Code of README.md" });
    expect(tabs().map((t) => t.textContent)).toEqual([expect.stringContaining("README.md")]);

    // a double click pins it; the next file opens next to it
    await user.dblClick(tabs()[0]);
    expect(tabs()[0]).not.toHaveClass("preview");
    await user.dblClick(screen.getByRole("treeitem", { name: "Open api/ScoreRepository.kt" }));
    await screen.findByRole("region", { name: "Code of api/ScoreRepository.kt" });
    expect(tabs()).toHaveLength(2);
    expect(tabs()[1]).toHaveAttribute("aria-selected", "true");

    // middle click closes a tab; so does its close button
    fireEvent(tabs()[0], new MouseEvent("auxclick", { bubbles: true, button: 1 }));
    expect(tabs()).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Close ScoreRepository.kt" }));
    expect(screen.queryAllByRole("tab").filter((t) => t.closest(".ed-tabs"))).toHaveLength(0);
    expect(screen.getByText("Pick a file in the Explorer, or find one by name.")).toBeInTheDocument();
  });

  it("the explorer moves with the arrow keys, opens folders with →, files with Space (preview) or Enter (pinned), and filters", async () => {
    const user = userEvent.setup();
    at("#/repo");
    render(<App />);
    const tree = await screen.findByRole("tree", { name: "Files" });
    const item = (name: string | RegExp) => within(tree).getByRole("treeitem", { name });
    (await within(tree).findByRole("treeitem", { name: /^\.keel/ })).focus();
    await user.keyboard("{ArrowDown}");
    expect(item(/^api/)).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(item(/^api/)).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{ArrowRight}");
    expect(item("Open api/ScoreController.kt")).toHaveFocus();
    await user.keyboard(" ");
    await screen.findByRole("region", { name: "Code of api/ScoreController.kt" });
    expect(tabs()[0]).toHaveClass("preview");
    item("Open api/ScoreController.kt").focus();
    await user.keyboard("{ArrowDown}{Enter}");
    await screen.findByRole("region", { name: "Code of api/ScoreRepository.kt" });
    // Enter opens it pinned, next to the preview tab
    expect(tabs()).toHaveLength(2);
    expect(tabs()[1]).not.toHaveClass("preview");
    // the frozen file shows its lock; ← goes to the folder, again ← closes it
    expect(within(item("Open api/ScoreRepository.kt")).getByTitle("Frozen in this phase: agents cannot edit it")).toBeInTheDocument();
    item("Open api/ScoreRepository.kt").focus();
    await user.keyboard("{ArrowLeft}");
    expect(item(/^api/)).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(item(/^api/)).toHaveAttribute("aria-expanded", "false");
    // filter as you type: matching files and the folders on their way
    await user.type(screen.getByRole("searchbox", { name: "Filter files" }), "repo");
    expect(within(tree).getAllByRole("treeitem").map((t) => t.dataset.path)).toEqual(["api", "api/ScoreRepository.kt"]);
    // git decorations: M on the changed file, a dot on its folder
    await user.clear(screen.getByRole("searchbox", { name: "Filter files" }));
    expect(within(item(/^\.keel/)).getByLabelText("Has changes")).toBeInTheDocument();
  });

  it("a deep link with a line opens the file there and highlights the line; the URL keeps it", async () => {
    at("#/repo/api/ScoreController.kt:9");
    render(<App />);
    await screen.findByRole("region", { name: "Code of api/ScoreController.kt" });
    await waitFor(() => expect(row(9)).toHaveClass("tgt"));
    expect(row(9)).toHaveClass("cur");
    expect(row(9)!.textContent).toContain("a score is never negative");
    expect(location.hash).toBe("#/repo/api/ScoreController.kt:9");
    // the status bar: the line, which AC changed the file, and what the phase allows
    const bar = document.querySelector<HTMLElement>(".ide-sb")!;
    expect(within(bar).getByText("Ln 9, Col 1")).toBeInTheDocument();
    expect(within(bar).getByText("AC-002")).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "ac-gate: editable" })).toBeInTheDocument();

    // another link to a line of the same file jumps again
    await act(async () => {
      location.hash = "#/repo/api/ScoreController.kt:13";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    await waitFor(() => expect(row(13)).toHaveClass("tgt"));
  });

  it("quick open (Ctrl/⌘+P) finds a file fuzzily and opens it", async () => {
    const user = userEvent.setup();
    at("#/repo");
    render(<App />);
    await screen.findByRole("treeitem", { name: /^api/ });
    await user.keyboard("{Control>}p{/Control}");
    const box = await screen.findByRole("combobox", { name: "Search files by name" });
    await user.type(box, "screp");
    const list = await screen.findByRole("listbox", { name: "Files" });
    await waitFor(() => expect(within(list).getAllByRole("option")).toHaveLength(1));
    const options = within(list).getAllByRole("option");
    expect(options[0]).toHaveTextContent("ScoreRepository.kt");
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("region", { name: "Code of api/ScoreRepository.kt" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Quick open" })).not.toBeInTheDocument();
  });

  it("finds in the file (Ctrl/⌘+F) with next / previous, and goes to a line (Ctrl/⌘+G)", async () => {
    const user = userEvent.setup();
    at("#/repo/api/ScoreController.kt");
    render(<App />);
    await screen.findByRole("region", { name: "Code of api/ScoreController.kt" });
    await user.keyboard("{Control>}f{/Control}");
    const input = await screen.findByRole("textbox", { name: "Find in file" });
    await user.type(input, "score");
    // "scores", "Score…" ×n: case-insensitive by default
    const count = () => document.querySelector(".find-n")!.textContent;
    await waitFor(() => expect(count()).toMatch(/^1 of \d+$/));
    const total = Number(count()!.split(" of ")[1]);
    expect(total).toBeGreaterThan(5);
    expect(document.querySelectorAll("mark.fd").length).toBe(total);
    await user.keyboard("{Enter}");
    expect(count()).toBe(`2 of ${total}`);
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    expect(count()).toBe(`1 of ${total}`);
    await user.click(screen.getByRole("button", { name: "Match case" }));
    await waitFor(() => expect(Number(count()!.split(" of ")[1])).toBeLessThan(total));
    await user.click(screen.getByRole("button", { name: "Use regular expression" }));
    await user.clear(input);
    await user.type(input, "fun \\w+");
    await waitFor(() => expect(count()).toBe("1 of 2"));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("search", { name: "Find in file" })).not.toBeInTheDocument();

    await user.keyboard("{Control>}g{/Control}");
    const go = await screen.findByRole("textbox", { name: "Go to line" });
    await user.type(go, "13{Enter}");
    await waitFor(() => expect(row(13)).toHaveClass("tgt"));
    expect(screen.getByText("Ln 13, Col 1")).toBeInTheDocument();
  });

  it("searches across files (Ctrl/⌘+Shift+F) and opens a result at its line", async () => {
    const user = userEvent.setup();
    at("#/repo");
    render(<App />);
    await screen.findByRole("treeitem", { name: /^api/ });
    await user.keyboard("{Control>}{Shift>}f{/Shift}{/Control}");
    const input = screen.getByRole("searchbox", { name: "Search in files" });
    await waitFor(() => expect(input).toHaveFocus());
    await user.type(input, "negative");
    const hit = await screen.findByRole("treeitem", { name: /api\/ScoreController.kt line 9/ });
    expect(screen.getByText(/2 results in 1 file/)).toBeInTheDocument();
    expect(within(hit).getByText("negative", { selector: "mark" })).toBeInTheDocument();
    const call = calls("GET", "/api/projects/ludus-engine/repo/search").at(-1);
    expect(call).toBeTruthy();
    await user.click(hit);
    await screen.findByRole("region", { name: "Code of api/ScoreController.kt" });
    await waitFor(() => expect(row(9)).toHaveClass("tgt"));
    expect(row(9)!.querySelector("mark.fd.hit")?.textContent).toBe("negative");
    expect(location.hash).toBe("#/repo/api/ScoreController.kt:9");
  });

  it("shows the changes of a file inline and side by side, against HEAD or the base branch", async () => {
    const user = userEvent.setup();
    at("#/repo/api/ScoreController.kt");
    render(<App />);
    await screen.findByRole("region", { name: "Code of api/ScoreController.kt" });
    await user.click(screen.getByRole("button", { name: "Changes" }));
    const diff = await screen.findByRole("region", { name: "Changes in api/ScoreController.kt" });
    expect(diff).toHaveAttribute("data-mode", "inline");
    expect(diff.querySelectorAll('[data-kind="add"]')).toHaveLength(2);
    expect(diff.querySelectorAll('[data-kind="del"]')).toHaveLength(1);
    expect(screen.getByText("+2")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Side by side" }));
    const split = screen.getByRole("region", { name: "Changes in api/ScoreController.kt" });
    expect(split).toHaveAttribute("data-mode", "split");
    expect(split.querySelectorAll('[data-side="old"].del')).toHaveLength(1);
    expect(split.querySelectorAll('[data-side="new"].add')).toHaveLength(2);
    expect(calls("GET", "/api/projects/ludus-engine/repo/diff")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "main" }));
    await waitFor(() => expect(calls("GET", "/api/projects/ludus-engine/repo/diff")).toHaveLength(2));
    await user.click(screen.getByRole("button", { name: "Changes" }));
    expect(await screen.findByRole("region", { name: "Code of api/ScoreController.kt" })).toBeInTheDocument();
  });

  it("source control lists staged, changed and untracked files, the branch's commits with keel's marked, and opens diffs", async () => {
    const user = userEvent.setup();
    at("#/repo");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Source control" }));
    const view = screen.getByRole("complementary", { name: "Source control" });
    expect(await within(view).findByRole("button", { name: /^Staged changes/ })).toHaveTextContent("1");
    expect(within(view).getByRole("button", { name: /^Changes/ })).toHaveTextContent("1");
    expect(within(view).getByRole("button", { name: /^Untracked/ })).toHaveTextContent("1");
    expect(within(view).getByText("3 uncommitted files. A flow starts only on a clean tree.")).toBeInTheDocument();
    expect(within(view).getByRole("button", { name: "Clean up with the Doctor" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Source control" })).toHaveTextContent("3");

    // commits on the branch: keel's own marked, the AC as a chip
    const commit = await within(view).findByRole("button", { name: /refuse a negative score/ });
    expect(within(commit).getByText("keel")).toBeInTheDocument();
    expect(within(commit).getByText("AC-002")).toBeInTheDocument();
    await user.click(commit);
    await user.click(await within(view).findByRole("button", { name: "Open the change of api/ScoreController.kt in a81c3f0" }));
    expect(await screen.findByRole("tab", { name: /ScoreController.kt @ a81c3f0/ })).toBeInTheDocument();
    await waitFor(() => expect(calls("GET", "/api/projects/ludus-engine/repo/diff").length).toBeGreaterThan(0));

    // a changed file opens in its diff
    await user.click(within(view).getByRole("button", { name: "Open the changes of api/ScoreController.kt" }));
    expect(await screen.findByRole("region", { name: "Changes in api/ScoreController.kt" })).toBeInTheDocument();
  });

  it("keel docs and Memory open as tabs from the keel view", async () => {
    const user = userEvent.setup();
    at("#/repo");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "keel" }));
    await user.click(screen.getByRole("button", { name: /^Files keel wrote/ }));
    expect(await screen.findByRole("table", { name: "Files keel wrote" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Memory/ }));
    expect(await screen.findByText("Test needs Docker")).toBeInTheDocument();
    expect(tabs().map((t) => t.textContent)).toEqual([expect.stringContaining("Files keel wrote"), expect.stringContaining("Memory")]);
  });

  it("on a phone the files and the editor are two screens with a back button", async () => {
    const had = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: q.includes("max-width: 720px") || q.includes("max-width: 900px"), media: q, addEventListener() {}, removeEventListener() {}, onchange: null })) as unknown as typeof window.matchMedia;
    try {
      const user = userEvent.setup();
      at("#/repo");
      render(<App />);
      const ide = () => document.querySelector(".ide")!;
      await screen.findByRole("treeitem", { name: /^api/ });
      expect(ide()).toHaveClass("phone", "s-side");
      await user.click(screen.getByRole("treeitem", { name: "Open README.md" }));
      await screen.findByRole("region", { name: "Code of README.md" });
      expect(ide()).toHaveClass("s-editor");
      await user.click(screen.getByRole("button", { name: "Back to the files" }));
      expect(ide()).toHaveClass("s-side");
      // search is a full screen of its own
      await user.click(screen.getByRole("button", { name: "Search" }));
      expect(screen.getByRole("complementary", { name: "Search" })).toBeInTheDocument();
      expect(ide()).toHaveClass("s-side");
    } finally {
      window.matchMedia = had;
    }
  });
});
