// The Git plugin's web part inside keel's web, as the full image has it (web/src/test/setup.ts runs every plugin's
// setup()): Code › Source control's Git panel (push, the pull request, the comments for KeelBot) and KeelBot's git
// buttons through the slot keelbot.card. Moved from web/src/test/plugins.test.tsx with the code. The Code page's branch
// tab and Connections › GitHub stay keel's core (web/src/test/plugins.test.tsx).

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
// keel's own app and test harness: this test runs the panel and the buttons inside keel's web, as keel loads them
import { App } from "../../../../web/src/App";
import { slotItems, SLOTS } from "../../../../web/src/sdk";
import { db } from "../../../../web/src/test/setup";
import plugin from "../index";

const calls = (method: string, path: string) =>
  db.calls.filter((c) => c.method === method && c.path === path);

function chatWith(text: string) {
  db.helper.sessions.push({
    id: "h1",
    project: "ludus-engine",
    root: "/w",
    mode: "ask",
    title: "Git",
    status: "idle",
    busy: false,
    model: { provider: "claude", mode: "subscription", model: "sonnet" },
    tokens_in: 0,
    tokens_out: 0,
    tokens_cached: 0,
    cost_usd: 0,
    turns: 1,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    messages: [
      { n: 1, role: "user", text: "q", data: {}, at: new Date().toISOString() },
      {
        n: 2,
        role: "helper",
        text,
        call_id: "c1",
        data: {
          status: "done",
          provider: "claude",
          model: "sonnet",
          tokens_in: 1,
          tokens_out: 1,
          cost_usd: 0,
          ms: 1,
        },
        at: new Date().toISOString(),
      },
    ],
  } as never);
  localStorage.setItem("keel2.helper.ludus-engine.session", "h1");
  location.hash = "#/keelbot";
  render(<App />);
  return screen.findByRole("complementary", { name: "KeelBot" });
}

describe("the Git plugin's web part", () => {
  it("is the plugin git, and puts its pieces where keel 0.15.1 had them", () => {
    expect(plugin.name).toBe("git");
    const scm = slotItems<{ id: string; place?: string; plugin?: string }>(
      SLOTS.codeActivity,
    ).filter((a) => a.place === "scm");
    expect(scm).toMatchObject([{ id: "git", place: "scm", plugin: "git" }]);
    expect(
      slotItems<{ id: string; prefix: string; plugin?: string }>(
        SLOTS.workflowActions,
      ).find((a) => a.id === "git"),
    ).toMatchObject({ prefix: "git:", plugin: "git" });
    expect(
      slotItems<{ id: string; kind: string }>(SLOTS.keelbotCard).find(
        (c) => c.id === "git",
      ),
    ).toMatchObject({ kind: "keel-git" });
    // the GitHub token and the branch tab stay keel's core
    expect(
      slotItems<{ id: string; order?: number }>(SLOTS.connectionsKind).find(
        (k) => k.id === "github",
      ),
    ).toMatchObject({ order: 20 });
    expect(slotItems<{ id: string }>(SLOTS.codeTab).map((t) => t.id)).toContain(
      "branch",
    );
  });
});

describe("Code › Git", () => {
  it("pushes, and hands the review comments to KeelBot", async () => {
    const user = userEvent.setup();
    db.plugins.git = true;
    db.pr = {
      number: 7,
      title: "Euro prices",
      url: "https://github.com/o/r/pull/7",
      state: "OPEN",
      review: "CHANGES_REQUESTED",
      checks: [
        { name: "ci / web", state: "success", url: "" },
        { name: "ci / api", state: "failure", url: "" },
      ],
      checks_done: 2,
      checks_failed: 1,
      comments: [
        {
          author: "rev",
          body: "round half up",
          path: "src/money.ts",
          line: 14,
        },
      ],
    };
    location.hash = "#/repo";
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: "Source control" }),
    );
    const git = await screen.findByLabelText("Git");
    expect(within(git).getByText("↑ 2 to push")).toBeInTheDocument();
    await user.click(within(git).getByRole("button", { name: "Push" }));
    await waitFor(() =>
      expect(calls("POST", "/api/projects/ludus-engine/git/push")).toHaveLength(
        1,
      ),
    );
    const pr = await within(git).findByRole("group", {
      name: "Pull request #7",
    });
    expect(pr).toHaveTextContent("1 check failed");
    await user.click(
      within(pr).getByRole("button", {
        name: "Ask KeelBot to address the comments",
      }),
    );
    const box = await screen.findByRole("textbox", { name: "Ask KeelBot" });
    await waitFor(() =>
      expect((box as HTMLTextAreaElement).value).toContain(
        "rev (src/money.ts:14): round half up",
      ),
    );
  });

  it("is read only while the plugin is off", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    await user.click(
      await screen.findByRole("button", { name: "Source control" }),
    );
    expect(
      await screen.findByText(/Read-only: keel never stages, commits/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Git")).toBeNull();
  });
});

describe("KeelBot's git buttons", () => {
  it("a commit uses the message the person may edit", async () => {
    const user = userEvent.setup();
    const p = await chatWith(
      'Commit it:\n```keel-git\n{"op": "commit", "message": "fix: zero scores"}\n```',
    );
    const g = await within(p).findByRole("region", { name: "Git: Commit" });
    await user.type(
      within(g).getByRole("textbox", { name: "Commit message" }),
      " for the reset",
    );
    await user.click(within(g).getByRole("button", { name: "Commit" }));
    await waitFor(() =>
      expect(
        calls("POST", "/api/projects/ludus-engine/git/commit")[0]?.body,
      ).toEqual({ message: "fix: zero scores for the reset" }),
    );
    expect(await within(g).findByRole("status")).toHaveTextContent(
      "Committed abc1234 fix: zero scores for the reset (1 file).",
    );
  });

  it("opens the pull request with the title the person may change, and says when a button cannot be read", async () => {
    const user = userEvent.setup();
    const p = await chatWith(
      'Here:\n```keel-git\n{"op": "pr", "title": "Euro prices", "body": "Why: **euros**."}\n```\n' +
        '```keel-git\n{"op": "rebase"}\n```',
    );
    const g = await within(p).findByRole("region", {
      name: "Git: Open the pull request",
    });
    const title = within(g).getByRole("textbox", {
      name: "Pull request title",
    });
    await user.clear(title);
    await user.type(title, "Prices in euros");
    await user.click(
      within(g).getByRole("button", { name: "Open the pull request" }),
    );
    await waitFor(() =>
      expect(
        calls("POST", "/api/projects/ludus-engine/git/pr")[0]?.body,
      ).toEqual({
        title: "Prices in euros",
        body: "Why: **euros**.",
        draft: false,
      }),
    );
    expect(await within(g).findByRole("status")).toHaveTextContent(
      "Opened the pull request: https://github.com/o/r/pull/7",
    );
    expect(
      within(p).getByText(
        "KeelBot's git button could not be read. Ask it to give it again.",
      ),
    ).toBeInTheDocument();
  });
});
