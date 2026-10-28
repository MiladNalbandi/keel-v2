// 0.18.64 the gate's title row is a flex row: a title with `code` in it fell apart into one narrow column per piece
// (text, code, text ...). The title is one span now, next to the pill.

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "../App";

describe("the gate's title row", () => {
  it("holds the pill and one title span, whatever the title's code pieces", async () => {
    render(<App />);
    const card = await screen.findByRole("region", { name: "Gate waits for you" });
    const h3 = within(card).getAllByRole("heading", { level: 3 })[0];
    const kids = Array.from(h3.childNodes).filter((n) => n.nodeType === 1 || (n.textContent ?? "").trim());
    expect(kids).toHaveLength(2);
    expect(kids[0]).toHaveTextContent("waits for you");
    expect((kids[1] as HTMLElement).className).toBe("gate-title");
    expect(kids[1]).toHaveTextContent(/AC-002/);
  });
});
