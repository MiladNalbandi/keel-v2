// keel 0.18.63 the bar over selected code (components/SelectionBar.tsx): it shows when a selection inside the code is
// finished, runs an action while the selection is still there, and goes on Esc, an empty selection or a press.

import { fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { searchTextOf, SelectionBar, selectionReviewRequest, type SelectionAction } from "../components/SelectionBar";

function Page({ actions }: { actions: SelectionAction[] }) {
  const ref = useRef<HTMLDivElement>(null);
  return (
    <div>
      <p data-testid="outside">Text of the page around the code</p>
      <div ref={ref} data-testid="code">
        <div className="row">  val total = order.total</div>
        <div className="row">  return total - refunded</div>
      </div>
      <SelectionBar within={ref} actions={actions} />
    </div>
  );
}

/** Select the text of `from` through `to` and finish the selection with a mouse up. */
function select(from: Element, to: Element = from) {
  const r = document.createRange();
  r.setStart(from.firstChild!, 0);
  r.setEnd(to.firstChild!, to.textContent!.length);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(r);
  fireEvent.mouseUp(document);
}

const bar = () => screen.queryByRole("toolbar", { name: "Selected code" });

afterEach(() => window.getSelection()?.removeAllRanges());

describe("the selection bar", () => {
  it("shows its actions over a selection in the code; a press runs one with the selection still there, then it goes", async () => {
    let seen = "";
    const run = vi.fn(() => (seen = window.getSelection()!.toString()));
    render(<Page actions={[{ id: "search", label: "Search", run }, { id: "ask", label: "Ask KeelBot", run: () => undefined }]} />);
    const rows = screen.getByTestId("code").querySelectorAll(".row");
    select(rows[0], rows[1]);
    expect(await screen.findByRole("toolbar", { name: "Selected code" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask KeelBot" })).toBeInTheDocument();
    // pressing a button keeps the selection (the bar takes no focus on mouse down)
    const press = fireEvent.mouseDown(screen.getByRole("button", { name: "Search" }));
    expect(press).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(run).toHaveBeenCalledOnce();
    expect(seen).toContain("return total - refunded");
    expect(bar()).toBeNull();
  });

  it("does not show for a selection outside the code, and Esc or an empty selection hides it", async () => {
    render(<Page actions={[{ id: "search", label: "Search", run: () => undefined }]} />);
    select(screen.getByTestId("outside"));
    await new Promise((r) => setTimeout(r, 5));
    expect(bar()).toBeNull();

    select(screen.getByTestId("code").querySelector(".row")!);
    expect(await screen.findByRole("toolbar", { name: "Selected code" })).toBeInTheDocument();
    fireEvent.keyUp(document, { key: "Escape" });
    expect(bar()).toBeNull();

    select(screen.getByTestId("code").querySelector(".row")!);
    expect(await screen.findByRole("toolbar", { name: "Selected code" })).toBeInTheDocument();
    window.getSelection()!.removeAllRanges();
    fireEvent(document, new Event("selectionchange"));
    expect(bar()).toBeNull();
  });

  it("shows nothing when the page gives no actions", async () => {
    render(<Page actions={[]} />);
    select(screen.getByTestId("code").querySelector(".row")!);
    await new Promise((r) => setTimeout(r, 5));
    expect(bar()).toBeNull();
  });

  it("searches for the first line with text, and asks KeelBot for a review that changes nothing", () => {
    expect(searchTextOf("\n\n   val total = order.total\n  return total")).toBe("val total = order.total");
    expect(searchTextOf("x".repeat(500))).toHaveLength(200);
    const r = selectionReviewRequest("the lines a.py:3-4", "+ x = 1");
    expect(r).toMatch(/^Review the lines a\.py:3-4:\n\n```\n\+ x = 1\n```/);
    expect(r).toContain("file:line");
    expect(r).toContain("Change nothing.");
    expect(selectionReviewRequest("the selected lines of a.py")).not.toContain("```");
  });
});
