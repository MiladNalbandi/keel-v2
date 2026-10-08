// v0.5.0: the Jira plugin's parts inside keel's web, as the full image has it (web/src/test/setup.ts runs every
// plugin's setup()): Connections › Jira (the slot connections.kind) and the MCP catalog in Tools (tools.card). Moved
// from web/src/test/tasks.test.tsx with the Jira card.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
// keel's own app and test harness: this test runs the Jira parts inside keel's web, as keel loads them
import { App } from "../../../../web/src/App";
import { db } from "../../../../web/src/test/setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);

describe("Connections › Jira", () => {
  it("shows a card per project; a connected one tests, finds the statuses and saves the mapping", async () => {
    const user = userEvent.setup();
    location.hash = "#/connections";
    render(<App />);
    const card = await screen.findByTestId("jira-ludus-engine");
    expect(await within(card).findByText("connected")).toBeInTheDocument();
    expect(card).toHaveTextContent("Jira Cloud · https://acme.atlassian.net");
    expect(screen.getByTestId("jira-platform")).toHaveTextContent("not connected");
    await user.click(within(card).getByRole("button", { name: "Edit" }));
    expect(within(card).getByLabelText("Site URL")).toHaveValue("https://acme.atlassian.net");
    expect(within(card).getByLabelText("API token")).toHaveAttribute("placeholder", "…XYZ (saved)");
    await user.type(within(card).getByLabelText("API token"), "bad");
    await user.click(within(card).getByRole("button", { name: "Test" }));
    expect(await within(card).findByText(/Test failed: Jira refused the login \(401\)\./)).toBeInTheDocument();
    expect(calls("POST", "/api/projects/ludus-engine/jira/test")[0].body).toMatchObject({ kind: "cloud", base_url: "https://acme.atlassian.net", token: "bad" });

    await user.click(within(card).getByRole("button", { name: "Find statuses in Jira" }));
    const review = await within(card).findByLabelText("Jira status for In review");
    expect(review).toHaveValue("Code Review");
    expect(within(card).getByLabelText("Jira status for Blocked")).toHaveValue("");
    await user.selectOptions(within(card).getByLabelText("Jira status for Blocked"), "-");
    await user.selectOptions(within(card).getByLabelText("Jira reviewer field"), "customfield_10010");
    await user.type(within(card).getByLabelText("Jira reviewers"), "rev@acme.com");
    await user.clear(within(card).getByLabelText("GitHub reviewers"));
    await user.type(within(card).getByLabelText("GitHub reviewers"), "ana, org/team");
    await user.click(within(card).getByRole("button", { name: "Save mapping and reviewers" }));
    await waitFor(() => expect(calls("PUT", "/api/projects/ludus-engine/jira")).toHaveLength(1));
    expect(calls("PUT", "/api/projects/ludus-engine/jira")[0].body).toEqual({
      status_map: { todo: "To Do", in_progress: "In Progress", in_review: "Code Review", testing_pp: "QA on PP", ready_prod: "Ready for Release", done: "Done", blocked: "-" },
      reviewer_field: "customfield_10010", jira_reviewers: ["rev@acme.com"], github_reviewers: ["ana", "org/team"],
    });
  });

  it("connects Jira Server for another project with a personal access token", async () => {
    const user = userEvent.setup();
    location.hash = "#/connections";
    render(<App />);
    const card = await screen.findByTestId("jira-platform");
    await user.click(await within(card).findByRole("button", { name: "Set up Jira" }));
    await user.click(within(card).getByRole("tab", { name: "Server / Data Center" }));
    expect(within(card).queryByLabelText("Email")).toBeNull();
    await user.type(within(card).getByLabelText("Jira URL"), "https://jira.example.com");
    await user.type(within(card).getByLabelText("Personal access token"), "pat-123");
    await user.type(within(card).getByLabelText("Board id (optional)"), "7");
    await user.click(within(card).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(calls("PUT", "/api/projects/platform/jira")).toHaveLength(1));
    expect(calls("PUT", "/api/projects/platform/jira")[0].body).toEqual({
      kind: "server", base_url: "https://jira.example.com", email: "", project_key: "", board_id: "7", jql: "", poll_minutes: 5, token: "pat-123",
    });
    expect(await within(card).findByText("connected")).toBeInTheDocument();
    expect(within(card).getByLabelText("Personal access token")).toHaveValue("");     // never shown again
  });
});

describe("Tools › Catalog", () => {
  it("offers Jira (mcp-atlassian) from the project's connection, added turned off", async () => {
    const user = userEvent.setup();
    location.hash = "#/tools";
    render(<App />);
    const cat = await screen.findByTestId("mcp-catalog");
    expect(cat).toHaveTextContent("Jira (mcp-atlassian)");
    expect(cat).toHaveTextContent("MIT");
    expect(cat).toHaveTextContent("uvx mcp-atlassian");
    await user.click(within(cat).getByRole("button", { name: "Add (turned off)" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/mcp-catalog/jira")).toHaveLength(1));
    expect(await within(cat).findByText("added")).toBeInTheDocument();
    expect(cat).toHaveTextContent("jira-ludus-engine");
  });
});
