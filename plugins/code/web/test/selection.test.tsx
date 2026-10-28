// keel 0.18.63 the bar over selected code in the Code page's file view: Search the project for the selection, ask
// KeelBot about it (the lines go along), or have KeelBot review it. And #/repo?search=<text> opens Search with the text.
// It runs inside keel's web with every plugin, as the full image has it.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { SLOTS } from "../../../../web/src/sdk/slots";
import { restorePlugins, withoutSlot } from "../../../../web/src/test/coreOnly";

afterEach(() => {
  window.getSelection()?.removeAllRanges();
  restorePlugins();
});

/** The file view's rows, once the file shows. */
async function openFile() {
  location.hash = "#/repo/api/ScoreController.kt";
  render(<App />);
  await waitFor(() => expect(document.querySelectorAll(".ed-body .cv-row").length).toBeGreaterThan(2));
  return [...document.querySelectorAll<HTMLElement>(".ed-body .cv-row")];
}

/** Select from the start of row `a` to the end of row `b`, as a drag that ends with mouse up. */
function select(a: HTMLElement, b: HTMLElement) {
  const r = document.createRange();
  r.setStart(a, 0);
  r.setEnd(b, b.childNodes.length);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(r);
  fireEvent.mouseUp(document);
}

describe("the Code page: the bar over selected code", () => {
  it("Search puts the selection's first line in the search view", async () => {
    const rows = await openFile();
    select(rows[0], rows[2]);
    const bar = await screen.findByRole("toolbar", { name: "Selected code" });
    expect(within(bar).getByRole("button", { name: "Ask KeelBot" })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: "Review" })).toBeInTheDocument();
    fireEvent.click(within(bar).getByRole("button", { name: "Search" }));
    const box = await screen.findByRole("searchbox", { name: "Search in files" });
    await waitFor(() => expect(box).toHaveValue("package scores"));
    expect(screen.queryByRole("toolbar", { name: "Selected code" })).toBeNull();
  });

  it("Review opens KeelBot with the lines attached and a review request to send", async () => {
    const rows = await openFile();
    select(rows[0], rows[2]);
    const bar = await screen.findByRole("toolbar", { name: "Selected code" });
    fireEvent.click(within(bar).getByRole("button", { name: "Review" }));
    const input = await screen.findByRole("textbox", { name: "Ask KeelBot" });
    await waitFor(() => expect((input as HTMLTextAreaElement).value).toMatch(/^Review the selected lines of api\/ScoreController\.kt/));
    expect((input as HTMLTextAreaElement).value).toContain("Change nothing.");
    // the lines go along with the question (the chip above the input)
    expect(screen.getByText(/api\/ScoreController\.kt:1-3/)).toBeInTheDocument();
  });

  it("Ask KeelBot opens KeelBot with the lines attached and the input to type the question in", async () => {
    const rows = await openFile();
    select(rows[0], rows[1]);
    await userEvent.click(within(await screen.findByRole("toolbar", { name: "Selected code" })).getByRole("button", { name: "Ask KeelBot" }));
    const input = await screen.findByRole("textbox", { name: "Ask KeelBot" });
    expect(input).toHaveValue("");
    expect(screen.getByText(/api\/ScoreController\.kt:1-2/)).toBeInTheDocument();
  });

  it("without KeelBot the bar offers Search only", async () => {
    withoutSlot(SLOTS.assistant);
    const rows = await openFile();
    select(rows[0], rows[1]);
    const bar = await screen.findByRole("toolbar", { name: "Selected code" });
    expect(within(bar).getAllByRole("button").map((b) => b.textContent)).toEqual(["Search"]);
  });

  it("#/repo?search=<text> opens Search with that text", async () => {
    location.hash = "#/repo?search=ScoreService";
    render(<App />);
    const box = await screen.findByRole("searchbox", { name: "Search in files" });
    await waitFor(() => expect(box).toHaveValue("ScoreService"));
  });
});
