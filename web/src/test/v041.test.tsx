// v0.4.1: the Stacks page lists each stack's tools; Start a flow sends the lint flow's scope.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { normalizeStack } from "../api";
import * as fx from "./fixtures";
import { db } from "./setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);

describe("tools", () => {
  it("normalizeStack keeps a tool's description, kind and off switch", () => {
    const s = normalizeStack({ name: "go", tools: [
      { name: "gofmt", on: "edit", fail: "fix", description: "Format the Go", match: "\\.go$" },
      { name: "sonar", on: "manual", fail: "warn", kind: "status", off: true }] });
    expect(s.tools).toEqual([
      { name: "gofmt", on: "edit", fail: "fix", description: "Format the Go", match: "\\.go$" },
      { name: "sonar", on: "manual", fail: "warn", kind: "status", off: true }]);
  });

  it("the Stacks page shows each tool: when it runs, what a failure means, what it does", async () => {
    location.hash = "#/stacks";
    render(<App />);
    const table = await screen.findByRole("table", { name: "Tools of ts-react" });
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("eslint");
    expect(rows[1]).toHaveTextContent("pre-commit");
    expect(rows[1]).toHaveTextContent("block: refuse the commit");
    expect(rows[2]).toHaveTextContent("prettier");
    expect(rows[2]).toHaveTextContent("fix: keep its changes");
    expect(rows[2]).toHaveTextContent("Format the files an agent just changed");
    expect(rows[3]).toHaveTextContent("turned off");
  });

  it("Start a flow lists the lint flow and sends its scope", async () => {
    db.workflows.push({ ...fx.featureWorkflow, id: "lint", name: "lint (keel)", steps: [] });
    const user = userEvent.setup();
    location.hash = "#/projects";
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Start a flow" }));
    const dlg = await screen.findByRole("dialog", { name: "Start a flow" });
    await waitFor(() => expect(within(dlg).getByRole("option", { name: "lint (keel)" })).toBeInTheDocument());
    await user.selectOptions(within(dlg).getByLabelText("Workflow"), "lint");
    await user.selectOptions(within(dlg).getByLabelText("What to check"), "all");
    await user.type(within(dlg).getByLabelText("What to build"), "Tidy the code");
    await user.click(within(dlg).getByRole("button", { name: "Start flow" }));
    await waitFor(() => expect(calls("POST", "/api/projects/ludus-engine/flows")[0]?.body).toMatchObject(
      { workflow_id: "lint", title: "Tidy the code", options: { scope: "all" } }));
  });
});
