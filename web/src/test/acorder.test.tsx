// v0.17.4 the criteria list is the build order, and says where the order came from.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { db } from "./setup";

describe("Acceptance criteria in build order", () => {
  it("numbers the criteria as they are built and names the order's source", async () => {
    const t = db.flows["ludus-engine"].thread!;
    t.acs = [t.acs[2], t.acs[0], t.acs[1]];
    t.ac_order = { ids: t.acs.map((a) => a.id), from: "spec" };
    render(<App />);
    // keel 0.28 the criteria are a tab of the Details drawer
    fireEvent.click(await screen.findByRole("tab", { name: "Criteria" }));
    const list = (await screen.findByText("Acceptance criteria")).closest(".panel") as HTMLElement;
    expect(within(list).getByTestId("ac-order")).toHaveTextContent("Built in this order, from the spec's ORDER line.");
    expect([...list.querySelectorAll(".ac b")].map((b) => b.textContent)).toEqual(t.acs.map((a, n) => `${n + 1}. ${a.id}`));
  });
});
