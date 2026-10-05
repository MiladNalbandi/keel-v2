// v0.4 (plan 5c): each agent chooses the project knowledge it uses, in the Agents drawer.

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { db } from "./setup";

const calls = (method: string, path: string) => db.calls.filter((c) => c.method === method && c.path === path);
const PUT = "/api/projects/ludus-engine/agents/implementer";

async function openImplementer(user: ReturnType<typeof userEvent.setup>) {
  location.hash = "#/agents";
  render(<App />);
  await user.click(await screen.findByText("Writes the minimum code to make the failing test pass."));
  return screen.findByRole("dialog", { name: "implementer" });
}

describe("Agents drawer: Knowledge this agent uses", () => {
  it("shows the sections with their cost, saves the change as an override, and a reload shows it", async () => {
    const user = userEvent.setup();
    let dlg = await openImplementer(user);
    const box = within(within(dlg).getByRole("group", { name: "Knowledge this agent uses" }));
    expect(box.getByRole("checkbox", { name: /^architecture/ })).toBeChecked();
    expect(box.getByRole("checkbox", { name: /^domain/ })).not.toBeChecked();
    expect(box.getByRole("checkbox", { name: /^architecture/ }).closest("label")).toHaveTextContent("~1k tokens");
    expect(box.getByRole("checkbox", { name: /^journeys/ }).closest("label")).toHaveTextContent("not written yet");
    expect(box.getByText(/About 2k tokens/)).toBeInTheDocument();
    expect(box.getByRole("checkbox", { name: /Code graph/ })).toBeChecked();
    expect(box.getByRole("checkbox", { name: /Strict/ }).closest("label")).toHaveTextContent("the agent is only told");
    expect(box.queryByRole("button", { name: "Use defaults" })).toBeNull();

    await user.click(box.getByRole("checkbox", { name: /^data/ }));
    await user.click(box.getByRole("checkbox", { name: /^domain/ }));
    await user.click(box.getByRole("checkbox", { name: /Strict/ }));
    await user.click(within(dlg).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls("PUT", PUT)[0]?.body).toEqual({
      knowledge: { sections: ["architecture", "domain", "conventions"], code_graph: true, memory: true, strict: true },
    }));

    // reload: the drawer shows what was saved
    cleanup();
    dlg = await openImplementer(user);
    const again = within(within(dlg).getByRole("group", { name: "Knowledge this agent uses" }));
    expect(again.getByRole("checkbox", { name: /^domain/ })).toBeChecked();
    expect(again.getByRole("checkbox", { name: /^data/ })).not.toBeChecked();
    expect(again.getByRole("checkbox", { name: /Strict/ })).toBeChecked();

    // Use defaults clears the project's change
    await user.click(again.getByRole("button", { name: "Use defaults" }));
    await waitFor(() => expect(calls("PUT", PUT).at(-1)?.body).toEqual({ knowledge: null }));
    await waitFor(() => expect(again.getByRole("checkbox", { name: /^data/ })).toBeChecked());
    expect(again.getByRole("checkbox", { name: /Strict/ })).not.toBeChecked();
  });
});
