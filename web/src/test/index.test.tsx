// Plan 5b: the Code page shows the project's code graph index and can rebuild it.

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { db } from "./setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);

describe("Repo index badge", () => {
  it("shows ready with counts and age, and Rebuild starts indexing", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    render(<App />);
    const badge = await screen.findByText(/^Index: ready/);
    expect(badge).toHaveTextContent("Index: ready · 412 files · 3100 symbols · 5 min ago");
    await user.click(screen.getByRole("button", { name: "Rebuild" }));
    expect(await screen.findByText("Index: indexing…")).toBeInTheDocument();
    expect(calls("POST", "/api/projects/ludus-engine/index/rebuild")).toHaveLength(1);
    await waitFor(() => expect(screen.getByRole("button", { name: "Rebuild" })).toBeDisabled());
  });

  it("shows why the index failed", async () => {
    db.index = { project: "ludus-engine", status: "failed", files: 0, symbols: 0, error: "CodeGraph is not installed" };
    location.hash = "#/repo";
    render(<App />);
    const badge = await screen.findByText("Index: failed: CodeGraph is not installed");
    expect(badge).toHaveAttribute("title", "CodeGraph is not installed");
  });
});
