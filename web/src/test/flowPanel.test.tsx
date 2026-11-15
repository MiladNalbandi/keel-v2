// A part's panel under a flow's steps (the slot flow.panel): the Flow page shows each one, for the flow it shows.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../App";
import { registerSlot, SLOTS, type FlowPanelItem, type FlowPanelProps } from "../sdk";

function Panel({ pid, thread, readOnly }: FlowPanelProps) {
  return <section aria-label="A part's panel">{`${pid} ${thread.thread_id} ${readOnly ? "read only" : "live"}`}</section>;
}

describe("the slot flow.panel", () => {
  let off: (() => void) | undefined;
  afterEach(() => off?.());

  it("the Flow page shows a part's panel with the flow it shows", async () => {
    off = registerSlot<FlowPanelItem>(SLOTS.flowPanel, { id: "test-panel", component: Panel });
    location.hash = "#/flow";
    render(<App />);
    // keel 0.28: the add-ons' panels are the Details drawer's More tab
    fireEvent.click(await screen.findByRole("tab", { name: "More" }, { timeout: 5000 }));
    const panel = await within(document.getElementById("main")!).findByRole("region", { name: "A part's panel" }, { timeout: 5000 });
    expect(panel.textContent).toContain("live");
    expect(screen.getAllByRole("region", { name: "A part's panel" })).toHaveLength(1);
  });
});
