// 0.21.4 (issue #64) the GitHub Issues plugin's card inside keel's web, as the full image has it (web/src/test/setup.ts
// runs every plugin's setup()): Settings › Connections › GitHub Issues, one card per project.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
// keel's own app and test harness: this test runs the plugin's parts inside keel's web, as keel loads them
import { App } from "../../../../web/src/App";
import { db } from "../../../../web/src/test/setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);

describe("Connections › GitHub Issues", () => {
  it("connects a project to its origin's issues, with a status label and the closing line, then removes it", async () => {
    const user = userEvent.setup();
    location.hash = "#/connections";
    render(<App />);
    const card = await screen.findByTestId("github-issues-platform");
    expect(await within(card).findByText("not connected")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Set up GitHub Issues" }));
    expect(within(card).getByLabelText("Repository")).toHaveAttribute("placeholder", "acme/platform");
    expect(within(card).getByText("Empty: this project's origin, acme/platform.")).toBeInTheDocument();
    expect(within(card).getByLabelText("GitHub issue for Done")).toHaveValue("closed");
    expect(within(card).getByLabelText("GitHub issue for Cancelled")).toHaveValue("closed:not planned");
    expect(within(card).getByLabelText("GitHub issue for In review")).toHaveValue("-");

    await user.click(within(card).getByRole("button", { name: "Test" }));
    expect(await within(card).findByText("Test OK: acme/platform as mili-dev.")).toBeInTheDocument();

    await user.click(within(within(card).getByRole("group", { name: "Which issues" })).getByRole("button", { name: "With a label" }));
    await user.type(within(card).getByLabelText("Label"), "keel");
    await user.selectOptions(within(card).getByLabelText("GitHub issue for In review"), "label");
    const label = within(card).getByLabelText("Label for In review");
    await user.clear(label);
    await user.type(label, "in review");
    await user.click(within(card).getByRole("checkbox", { name: /Comment on the issue/ }));
    await user.click(within(card).getByRole("button", { name: "Connect" }));
    expect(await within(card).findByText("connected")).toBeInTheDocument();
    const put = calls("PUT", "/api/projects/platform/github-issues")[0].body as { status_map: Record<string, string> };
    expect(put).toMatchObject({ repo: "", follow: "label", label: "keel", comment: false, closes_on_merge: true, poll_minutes: 5 });
    expect(put.status_map).toMatchObject({ in_review: "label:in review", done: "closed", cancelled: "closed:not planned", todo: "-" });
    expect(card).toHaveTextContent("GitHub Issues · acme/platform");

    await user.click(within(card).getByRole("button", { name: "Remove" }));
    await user.click(within(within(card).getByRole("group", { name: "Confirm" })).getByRole("button", { name: "Remove" }));
    expect(await within(card).findByText("not connected")).toBeInTheDocument();
    expect(calls("DELETE", "/api/projects/platform/github-issues")).toHaveLength(1);
  });

  it("a project that follows Jira says so and cannot connect GitHub Issues too", async () => {
    const user = userEvent.setup();
    location.hash = "#/connections";
    render(<App />);
    const card = await screen.findByTestId("github-issues-ludus-engine");
    expect(await within(card).findByText("Follows Jira.")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Set up GitHub Issues" }));
    expect(within(card).getByText(/This project follows Jira\. A project follows one tracker/)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Connect" })).toBeDisabled();
  });
});
