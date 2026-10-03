import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { db, server } from "./setup";

const diagnosis = {
  by: "rules", summary: "4 uncommitted files: 2 setup, 1 code, 1 secret-looking.", note: "Built-in rules.", tokens_in: 0, tokens_out: 0,
  files: [
    { path: ".gitignore", status: "changed", size: 40, kind: "tooling", secret: false, tracked: true },
    { path: ".devcontainer/devcontainer.json", status: "new", size: 60, kind: "tooling", secret: false, tracked: false },
    { path: "src/App.kt", status: "changed", size: 90, kind: "code", secret: false, tracked: true },
    { path: ".env", status: "new", size: 20, kind: "secret", secret: true, tracked: false },
  ],
  plan: [
    { id: "secret", title: "Secret-looking files", why: "Keep them out of git.", action: "ignore", files: [".env"], patterns: [".env"] },
    { id: "tooling", title: "Project setup", why: "Team settings.", action: "commit", files: [".gitignore", ".devcontainer/devcontainer.json"], message: "chore: project tooling" },
    { id: "code", title: "Code in progress", why: "Unfinished work.", action: "stash", files: ["src/App.kt"] },
  ],
};

describe("workspace Doctor", () => {
  it("a dirty start offers the Doctor; its plan can be changed and applied, then the tree is clean", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/projects/:pid/flows", () => HttpResponse.json({ error: "This project has uncommitted changes (4 files): .gitignore, …", hint: "Commit or stash them first." }, { status: 409 })),
      http.post("/api/projects/:pid/doctor/workspace", () => HttpResponse.json(diagnosis)),
      http.post("/api/projects/:pid/doctor/workspace/apply", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        db.calls.push({ method: "POST", path: new URL(request.url).pathname, body });
        return HttpResponse.json({ results: [{ action: "commit", files: [".gitignore"], ok: true, detail: "committed: chore: dev setup" }], remaining: [], clean: true });
      }),
    );
    location.hash = "#/projects";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Start a flow" }));
    await user.type(await screen.findByLabelText("What to build"), "Top 10");
    await user.click(screen.getByRole("button", { name: "Start flow" }));
    await user.click(await screen.findByRole("button", { name: /Ask the Doctor/ }));
    expect(await screen.findByText("4 uncommitted files: 2 setup, 1 code, 1 secret-looking.")).toBeInTheDocument();

    // the secret group cannot be committed
    const secretSelect = screen.getByLabelText("What to do with Secret-looking files");
    expect(within(secretSelect).queryByRole("option", { name: "Commit" })).toBeNull();

    const msg = screen.getByLabelText("Commit message for Project setup");
    await user.clear(msg);
    await user.type(msg, "chore: dev setup");
    await user.selectOptions(screen.getByLabelText("What to do with Code in progress"), "keep");
    await user.click(screen.getByRole("button", { name: "Apply this plan" }));

    await waitFor(() => expect(db.calls.find((c) => c.path.endsWith("/doctor/workspace/apply"))?.body).toEqual({ plan: [
      { action: "ignore", files: [".env"], patterns: [".env"], title: "Secret-looking files" },
      { action: "commit", files: [".gitignore", ".devcontainer/devcontainer.json"], message: "chore: dev setup", title: "Project setup" },
      { action: "keep", files: ["src/App.kt"], title: "Code in progress" },
    ] }));
    expect(await screen.findByText("The working tree is clean. You can start the flow.")).toBeInTheDocument();
  });
});
