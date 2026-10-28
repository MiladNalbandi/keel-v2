// 0.18.65 the stale-base field report (R2, R5): base_branch names a branch this repo has no local copy of, so Start a
// flow asks the person (use the remote's, create a local branch that tracks it, or pick another) and keel writes the
// answer; Settings › General › Base branch shows what keel compares with, the drift, and the canonical remote.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { BaseMissing, BaseView } from "../api";
import { db, server } from "./setup";

const MISSING: BaseMissing = {
  name: "main", remote: "github/main", remotes: ["github/main"], branches: ["master", "feat/x"],
  text: ".keel/config.yml says base_branch: main, and this repo has no local branch main; github/main has it.",
  choices: [
    { id: "remote", ref: "github/main", label: "Use github/main" },
    { id: "track", ref: "github/main", label: "Create a local main that tracks github/main" },
    { id: "pick", label: "Pick another branch", branches: ["master", "feat/x"] },
  ],
};

const VIEW: BaseView = {
  name: "master", local: "master", remote: "github/main", canonical: "github", ref: "github/main", ahead: 2, behind: 3,
  warning: "the local master is 3 commits behind and 2 ahead of github/main; keel compares with github/main",
  configured: "master", setting: "github", remotes: ["github", "origin"], branches: ["master", "feat/x"], missing: null,
  configured_file: true,
};

function answers() {
  const sent: unknown[] = [];
  server.use(
    http.put("/api/projects/:pid/base", async ({ request }) => {
      const b = (await request.json()) as Record<string, string>;
      sent.push(b);
      return HttpResponse.json({ ...VIEW, configured: b.branch ?? VIEW.configured, setting: b.remote ?? VIEW.setting });
    }),
    http.get("/api/projects/:pid/base", () => HttpResponse.json(VIEW)),
  );
  return sent;
}

describe("the base branch (0.18.65)", () => {
  it("Start a flow asks when base_branch names no local branch, and the answer is sent once", async () => {
    const sent = answers();
    const yegi = db.projects.find((p) => p.id === "yegi")!;
    yegi.configured = true;
    localStorage.setItem("keel2.project", "yegi");
    db.startCheck.yegi = { worktree: { ok: true }, base: null, base_missing: MISSING };
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Start a flow" }));
    const dialog = await screen.findByRole("dialog", { name: "Start a flow" });
    const q = await within(dialog).findByTestId("base-missing");
    expect(q).toHaveTextContent("this repo has no local branch main; github/main has it. Which base should keel use?");
    expect(within(q).getByRole("button", { name: "Use github/main" })).toBeInTheDocument();
    db.startCheck.yegi = { worktree: { ok: true }, base: null, base_missing: null };
    await userEvent.click(within(q).getByRole("button", { name: "Create a local main that tracks github/main" }));
    await waitFor(() => expect(sent).toEqual([{ choice: "track", branch: "github/main" }]));
    await waitFor(() => expect(within(dialog).queryByTestId("base-missing")).toBeNull());
  });

  it("Pick another branch sends the branch picked", async () => {
    const sent = answers();
    const yegi = db.projects.find((p) => p.id === "yegi")!;
    yegi.configured = true;
    localStorage.setItem("keel2.project", "yegi");
    db.startCheck.yegi = { worktree: { ok: true }, base: null, base_missing: MISSING };
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Start a flow" }));
    const q = await screen.findByTestId("base-missing");
    await userEvent.selectOptions(within(q).getByRole("combobox", { name: "Another base branch" }), "feat/x");
    await userEvent.click(within(q).getByRole("button", { name: "Use feat/x" }));
    await waitFor(() => expect(sent).toEqual([{ choice: "pick", branch: "feat/x" }]));
  });

  it("Settings › General › Base branch shows what keel compares with and changes the canonical remote", async () => {
    const sent = answers();
    location.hash = "#/settings/general/yegi";
    render(<App />);
    const sec = await screen.findByTestId("sec-base");
    await userEvent.click(within(sec).getByRole("button", { name: /Base branch/ }));
    expect(await within(sec).findByTestId("base-ref")).toHaveTextContent("github/main");
    expect(within(sec).getByTestId("base-warning")).toHaveTextContent("the local master is 3 commits behind and 2 ahead of github/main");
    expect(within(sec).getByRole("combobox", { name: "Canonical remote" })).toHaveValue("github");
    await userEvent.selectOptions(within(sec).getByRole("combobox", { name: "Canonical remote" }), "origin");
    await waitFor(() => expect(sent).toEqual([{ choice: "canonical", remote: "origin" }]));
    await userEvent.selectOptions(within(sec).getByRole("combobox", { name: "Base branch" }), "feat/x");
    await waitFor(() => expect(sent[1]).toEqual({ choice: "pick", branch: "feat/x" }));
  });
});
