// keel 0.18.63 the bar over selected lines in the Review page's Files tab: Search opens Code › Search with the first
// selected line, Ask KeelBot and Review hand the lines (with their +/− marks, path:lines and the pull request's commit)
// to KeelBot's page. It runs inside keel's web on the demo data of ./nextDemo, like next.test.tsx.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { SLOTS } from "../../../../web/src/sdk/slots";
import { restorePlugins, withoutSlot } from "../../../../web/src/test/coreOnly";
import { db, server } from "../../../../web/src/test/setup";
import { nextDemo } from "./nextDemo";

afterEach(() => {
  window.getSelection()?.removeAllRanges();
  restorePlugins();
});

/** Pull request #42's Files tab, with orders/refunds.py's changes on screen. */
async function openDiff() {
  server.use(...nextDemo({}).handlers);
  db.plugins.review = true;
  location.hash = "#/review";
  render(<App />);
  const side = await screen.findByRole("region", { name: "Review" });
  await userEvent.click(await within(side).findByRole("button", { name: /^#42 / }));
  const pr = await screen.findByRole("region", { name: "Review of #42" });
  await userEvent.click(await within(pr).findByRole("tab", { name: /^Files/ }));
  const diff = await within(pr).findByRole("region", { name: "Changes of orders/refunds.py" });
  await within(diff).findByText(/return order\.paid_total - already/);
  return diff;
}

/** The diff row whose code is `text`. */
const row = (diff: HTMLElement, text: RegExp) =>
  within(diff).getByText(text).closest<HTMLElement>(".rvn-dl-w")!;

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

describe("the Review page: the bar over selected lines", () => {
  it("Review hands the lines with their marks, where they are and the commit to KeelBot's page", async () => {
    const diff = await openDiff();
    select(row(diff, /already = sum/), row(diff, /return order\.paid_total/));
    const bar = await screen.findByRole("toolbar", { name: "Selected code" });
    fireEvent.click(within(bar).getByRole("button", { name: "Review" }));
    await waitFor(() => expect(location.hash).toBe("#/helper"));
    const input = (await screen.findByRole("textbox", { name: "Ask KeelBot" })) as HTMLTextAreaElement;
    await waitFor(() => expect(input.value).toMatch(/^Review the lines orders\/refunds\.py:12-13 in pull request #42 \(commit [0-9a-f]+/));
    expect(input.value).toContain("  already = sum(r.amount for r in order.refunds)");
    expect(input.value).toContain("-     return order.total - already");
    expect(input.value).toContain("+     return order.paid_total - already");
    expect(input.value).toContain("Change nothing.");
  });

  it("Ask KeelBot hands the line and leaves the question to you", async () => {
    const diff = await openDiff();
    const r = row(diff, /if amount > refundable_amount/);
    select(r, r);
    fireEvent.click(within(await screen.findByRole("toolbar", { name: "Selected code" })).getByRole("button", { name: "Ask KeelBot" }));
    const input = (await screen.findByRole("textbox", { name: "Ask KeelBot" })) as HTMLTextAreaElement;
    await waitFor(() => expect(input.value).toMatch(/^About line orders\/refunds\.py:\d+ in pull request #42/));
    expect(input.value).toContain("if amount > refundable_amount(order):");
    expect(input.value).not.toMatch(/```\n\d/);
    expect(input.value.endsWith("My question: ")).toBe(true);
  });

  it("Search opens Code › Search with the first selected line", async () => {
    const diff = await openDiff();
    select(row(diff, /already = sum/), row(diff, /return order\.paid_total/));
    fireEvent.click(within(await screen.findByRole("toolbar", { name: "Selected code" })).getByRole("button", { name: "Search" }));
    await waitFor(() => expect(location.hash).toBe(`#/repo?search=${encodeURIComponent("already = sum(r.amount for r in order.refunds)")}`));
  });

  it("without KeelBot the bar offers Search only", async () => {
    withoutSlot(SLOTS.assistant);
    const diff = await openDiff();
    select(row(diff, /already = sum/), row(diff, /return order\.paid_total/));
    const bar = await screen.findByRole("toolbar", { name: "Selected code" });
    expect(within(bar).getAllByRole("button").map((b) => b.textContent)).toEqual(["Search"]);
  });
});
