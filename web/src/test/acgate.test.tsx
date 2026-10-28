// 0.18.64 the AC gate shows its criterion's code: RED / GREEN / Both tabs, a card per file with its diff coloured (added
// and removed lines), a long file folded to its first lines, lock files folded behind Show, a missing commit said in its
// tab's place, and the Markdown's Commits section left out (the same commits as text).

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { dropSection, FILE_KEEP } from "../pages/flow/AcCode";
import * as fx from "./fixtures";
import { db } from "./setup";

const section = async () => screen.findByRole("region", { name: "The code of this criterion" });

describe("the AC gate's code", () => {
  it("shows the commits as tabs and the RED test folded to its first lines", async () => {
    render(<App />);
    const s = await section();
    expect(within(s).getByTestId("acc-sum")).toHaveTextContent("2 commits · 3 files · +46 −2");
    const tabs = within(s).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual([
      "RED · 4be12d9 test(AC-002): refuse a negative score",
      "GREEN · a81c3f0 feat(AC-002): refuse a negative score with 400",
      "Both",
    ]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    const panel = within(s).getByRole("tabpanel");
    const card = panel.querySelector('[data-path="api/src/test/kotlin/ScoreApiTest.kt"]') as HTMLElement;
    expect(card).toHaveTextContent("+40");
    expect(card).toHaveTextContent("new file");
    // 41 rows (the hunk and 40 added lines): folded to the first FILE_KEEP
    expect(card.querySelectorAll(".dl")).toHaveLength(FILE_KEEP);
    expect(card.querySelector(".dl.hunk")).toHaveTextContent("@@ -0,0 +1,40 @@");
    expect(card.querySelectorAll(".dl.add").length).toBe(FILE_KEEP - 1);
    await userEvent.click(within(card).getByRole("button", { name: `… ${41 - FILE_KEEP} more lines · show all` }));
    expect(card.querySelectorAll(".dl")).toHaveLength(41);
  });

  it("switches to GREEN with removed and added lines, and shows a folded lock file on Show", async () => {
    render(<App />);
    const s = await section();
    await userEvent.click(within(s).getByRole("tab", { name: /^GREEN/ }));
    expect(within(s).getByRole("tab", { name: /^GREEN/ })).toHaveAttribute("aria-selected", "true");
    expect(within(s).getByRole("tab", { name: /^RED/ })).toHaveAttribute("aria-selected", "false");
    const panel = within(s).getByRole("tabpanel");
    expect(panel.querySelector('[data-path="api/src/test/kotlin/ScoreApiTest.kt"]')).toBeNull();
    const ctrl = panel.querySelector('[data-path="api/ScoreController.kt"]') as HTMLElement;
    expect([...ctrl.querySelectorAll(".dl.del")].map((r) => r.textContent)).toEqual([expect.stringContaining("return store(score)")]);
    expect(ctrl.querySelectorAll(".dl.add")).toHaveLength(4);
    expect(ctrl.querySelector(".dl.hunk")).toHaveTextContent("@@ -10,3 +10,6 @@");

    const lock = panel.querySelector('[data-path="gradle.lockfile"]') as HTMLElement;
    expect(lock).toHaveTextContent("lock file · folded");
    expect(lock.querySelector(".dl")).toBeNull();
    await userEvent.click(within(lock).getByRole("button", { name: "Show the diff of gradle.lockfile" }));
    expect(await within(lock).findByText(/shop-money/)).toBeInTheDocument();
    expect(lock.querySelectorAll(".dl.add")).toHaveLength(2);
    expect(lock.querySelectorAll(".dl.del")).toHaveLength(1);
  });

  it("shows both commits' files under Both, and a missing commit in its tab's place", async () => {
    db.acCode.th_7f3a = { ...structuredClone(fx.acCode), red: null, commits: 1, files: 2 };
    render(<App />);
    const s = await section();
    const red = within(s).getByRole("tab", { name: /^RED/ });
    expect(red).toHaveTextContent("RED · no test(AC-002) commit found");
    expect(red).toBeDisabled();
    // GREEN is the first tab with a commit
    expect(within(s).getByRole("tab", { name: /^GREEN/ })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(within(s).getByRole("tab", { name: "Both" }));
    const panel = within(s).getByRole("tabpanel");
    expect(panel).toHaveTextContent("RED · no test(AC-002) commit found");
    expect(panel.querySelector('[data-path="api/ScoreController.kt"]')).not.toBeNull();
  });

  it("leaves the Markdown's Commits section out once the code is there, and links to Code and Review", async () => {
    db.flows["ludus-engine"].thread!.waiting!.detail =
      "## Criterion\n\n`AC-002` [API] refuse\n\n## Commits\n\n**RED**: `4be12d9` `test(AC-002)`\n\n## Review\n\n**AC-REVIEW: pass**";
    render(<App />);
    const s = await section();
    const card = screen.getByRole("region", { name: "Gate waits for you" });
    expect(within(card).getByRole("heading", { name: "Review" })).toBeInTheDocument();
    expect(within(card).queryByRole("heading", { name: "Commits" })).toBeNull();
    expect(within(s).getByRole("link", { name: "Open in Code ›" })).toHaveAttribute("href",
      `#/repo/api/src/test/kotlin/ScoreApiTest.kt?ref=${fx.acCode.red!.sha}`);
    expect(within(s).getByRole("link", { name: "Open in Review ›" })).toHaveAttribute("href", "#/review/branch%3Afeat%2Fscores");
  });

  it("drops one Markdown section and keeps the rest", () => {
    expect(dropSection("## A\n\na\n\n## Commits\n\n```text\nx\n```\n\n## B\n\nb", "Commits")).toBe("## A\n\na\n\n## B\n\nb");
    expect(dropSection("## A\n\na", "Commits")).toBe("## A\n\na");
  });
});
