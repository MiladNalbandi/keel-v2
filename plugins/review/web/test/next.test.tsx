// 0.18.57 the new Review view (the redesign's Work › Review), next to the classic one; 0.18.62 its own page in the
// menu (#/review), and Code › Review the classic view only (its hint opens the page). The list with its filters, search and "Review #N", a pull request
// in an editor tab with its tabs (Comments · Files · Problems · Commits · Checklist, and registered ones), keel's
// verdict on a comment and Address it with its confirmation, Files (viewed, a pending comment on a line, a comment's
// marker), Problems (Add as comment, Dismiss), the Submit drawer (Approve is not offered on your own pull request), and
// the phone (the list, then the pull request, and back). It runs inside keel's web like review.test.tsx, on the demo
// data of ./nextDemo.

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { db, server } from "../../../../web/src/test/setup";
import { registerReviewTab } from "../next/tabs";
import { prPill } from "../next/model";
import { nextDemo, PRS } from "./nextDemo";

const MODE = "keel2.review.view";

function start(opts: { found?: number[] } = {}) {
  const demo = nextDemo({ found: opts.found });
  server.use(...demo.handlers);
  db.plugins.review = true;
  return demo;
}

/** Code › Review: the classic view. */
async function openCode() {
  location.hash = "#/repo";
  const app = render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "Review" }));
  return { app, side: await screen.findByRole("region", { name: "Review" }) };
}

/** The Review page (#/review): the new view's list. */
async function openSide() {
  location.hash = "#/review";
  const app = render(<App />);
  return { app, side: await screen.findByRole("region", { name: "Review" }) };
}

/** The new view's list, then one pull request's editor tab. */
async function openPr(n: number) {
  const { side } = await openSide();
  await userEvent.click(
    await within(side).findByRole("button", { name: new RegExp(`^#${n} `) }),
  );
  const pr = await screen.findByRole("region", { name: `Review of #${n}` });
  await within(pr).findByRole("tablist");
  return { side, pr };
}

const tab = (pr: HTMLElement, name: RegExp) =>
  within(pr).getByRole("tab", { name });

describe("the new Review view: its own page", () => {
  it("Code › Review shows the classic view only, with a hint that opens the Review page", async () => {
    start();
    localStorage.setItem(MODE, JSON.stringify("next"));    // a choice 0.18.57–0.18.61 remembered: the classic view still
    const { side } = await openCode();
    expect(
      await within(side).findByRole("region", { name: "This branch" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("group", { name: "Review view" }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /Try the new review view/ }),
    );
    await waitFor(() => expect(location.hash).toBe("#/review"));
  });

  it("the Review page has the list and no Classic | New view switch", async () => {
    start();
    const { side } = await openSide();
    await waitFor(() =>
      expect(within(side).queryAllByRole("button", { name: /^#\d+ / })).toHaveLength(4),
    );
    expect(
      screen.queryByRole("group", { name: "Review view" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Pick a pull request on the left/)).toBeInTheDocument();
  });
});

describe("the new Review view: the list", () => {
  it("says who approved your own pull request, and who asked for changes first", () => {
    const mine = PRS.find((p) => p.mine)!;
    expect(prPill({ ...mine, approved: ["bo", "cy"] }, null)).toEqual([
      "approved by bo, cy",
      "ok",
    ]);
    expect(
      prPill(
        { ...mine, approved: ["bo"], changes_requested: ["cy"] },
        null,
      ),
    ).toEqual(["changes asked by cy", "warn"]);
    expect(prPill({ ...mine, approved: [] }, null)).toEqual(["yours", "idle"]);
    // comments that wait for you come first
    expect(prPill({ ...mine, approved: ["bo"] }, 2)[0]).toBe(
      "2 comments wait for you",
    );
  });

  it("filters by All / To review / Mine / Assigned and a search, and Review #N opens the next one that needs you", async () => {
    start();
    const { side } = await openSide();
    const rows = () =>
      within(side).queryAllByRole("button", { name: /^#\d+ / });
    await waitFor(() => expect(rows()).toHaveLength(4));
    expect(
      within(side).getByText(
        "2 reviews are asked of you · 1 pull request of yours is open",
      ),
    ).toBeInTheDocument();
    const chips = within(side).getByRole("group", { name: "Show" });
    expect(within(chips).getByRole("button", { name: /^All/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await userEvent.click(
      within(chips).getByRole("button", { name: /^To review/ }),
    );
    await waitFor(() =>
      expect(rows().map((r) => r.getAttribute("aria-label"))).toEqual([
        "#45 Export orders as CSV",
        "#44 Order list crashes on an empty page",
      ]),
    );
    expect(
      within(rows()[0]).getByText("your review is asked"),
    ).toBeInTheDocument();
    expect(within(rows()[1]).getByText("draft")).toBeInTheDocument();

    await userEvent.click(within(chips).getByRole("button", { name: /^Mine/ }));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(within(rows()[0]).getByText("yours")).toBeInTheDocument();
    await userEvent.click(
      within(chips).getByRole("button", { name: /^Assigned/ }),
    );
    await waitFor(() =>
      expect(rows().map((r) => r.getAttribute("aria-label"))).toEqual([
        "#41 Upgrade the test runner",
      ]),
    );
    expect(within(rows()[0]).getByText("assigned to you")).toBeInTheDocument();

    await userEvent.click(within(chips).getByRole("button", { name: /^All/ }));
    await waitFor(() => expect(rows()).toHaveLength(4));
    await userEvent.type(
      within(side).getByRole("searchbox", { name: /Search pull requests/ }),
      "csv",
    );
    expect(rows().map((r) => r.getAttribute("aria-label"))).toEqual([
      "#45 Export orders as CSV",
    ]);
    await userEvent.clear(within(side).getByRole("searchbox"));
    await userEvent.type(
      within(side).getByRole("searchbox"),
      "nothing like it",
    );
    expect(
      within(side).getByText('No pull request matches "nothing like it".'),
    ).toBeInTheDocument();

    // the draft #44 asks too, but #45 is the next one ready for review
    await userEvent.click(
      within(side).getByRole("button", { name: "Review #45" }),
    );
    expect(
      await screen.findByRole("region", { name: "Review of #45" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(location.hash).toMatch(/^#\/review\/pr(:|%3A)45$/));
  });

  it("opens a review's link (#/review/pr:45) on the Review page", async () => {
    start();
    location.hash = "#/review/pr%3A45";
    render(<App />);
    expect(
      await screen.findByRole("region", { name: "Review of #45" }),
    ).toBeInTheDocument();
  });
});

describe("the new Review view: one pull request", () => {
  let off = () => {};
  afterEach(() => act(() => off()));

  it("has keel's overview in its head and the tabs Comments · Files · Problems · Commits · Checklist, plus a registered one", async () => {
    start();
    off = registerReviewTab({
      id: "database",
      label: "Database",
      order: 5,
      show: ({ view }) => view.number === 42,
      count: () => 2,
      component: ({ view }) => <p>Tables of {view.title}</p>,
    });
    const { pr } = await openPr(42);
    expect(
      within(pr).getByRole("heading", { name: /#42 Add refunds to orders/ }),
    ).toBeInTheDocument();
    expect(within(pr).getByText("You want to merge")).toBeInTheDocument();
    expect(within(pr).getByText("checks pass")).toBeInTheDocument();
    expect(
      within(pr).getByRole("link", { name: /Open on GitHub/ }),
    ).toHaveAttribute("href", "https://github.com/acme/shop/pull/42");
    await userEvent.click(
      within(pr).getByText("What this change does (keel's overview)"),
    );
    expect(
      within(pr).getByText(/A shop owner can refund a paid order/),
    ).toBeInTheDocument();

    const names = within(pr)
      .getAllByRole("tab")
      .map((t) => t.textContent);
    expect(names).toEqual([
      "Database2",
      "Comments3",
      "Files5",
      "Problems1",
      "Commits4",
      "Checklist",
    ]);
    expect(tab(pr, /^Comments/)).toHaveAttribute("aria-selected", "true");
    // three comments wait for your answer: the count says so
    expect(within(tab(pr, /^Comments/)).getByText("3")).toHaveClass("warn");

    await userEvent.click(tab(pr, /^Commits/));
    expect(
      within(pr).getByText("AC-2: refuse more than was paid"),
    ).toBeInTheDocument();
    await userEvent.click(tab(pr, /^Database/));
    expect(
      within(pr).getByText("Tables of Add refunds to orders"),
    ).toBeInTheDocument();
    // the arrow keys move between the tabs
    tab(pr, /^Database/).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(tab(pr, /^Comments/)).toHaveAttribute("aria-selected", "true");

    await userEvent.click(tab(pr, /^Checklist/));
    const first = within(pr).getByRole("checkbox", { name: /Broad view/ });
    await userEvent.click(first);
    expect(first).toBeChecked();
    const key = Object.keys(localStorage).find((k) => k.startsWith("keel2.review.next.check.") && k.endsWith(".pr:42"))!;
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual([0]);
  });

  it("shows keel's verdict point by point, uses it as the reply, and starts Address it only after you confirm", async () => {
    const demo = start();
    const { pr } = await openPr(42);
    const card = (name: RegExp) => within(pr).getByRole("article", { name });
    // bo's comment: keel says the reviewer is not right
    expect(
      within(card(/refunds\.py · line 13/)).getByText("Reviewer is not right"),
    ).toBeInTheDocument();

    const th2 = card(/refunds\.py · line 20/);
    expect(within(th2).getByText("Reviewer is right")).toBeInTheDocument();
    await userEvent.click(within(th2).getByRole("button", { name: /alex/ }));
    const check = within(th2).getByRole("region", { name: "keel's check" });
    expect(
      within(check).getByText("keel: Reviewer is right"),
    ).toBeInTheDocument();
    expect(within(check).getByText("Right:")).toBeInTheDocument();
    expect(
      within(check).getByText(/add a test that posts "abc"/),
    ).toBeInTheDocument();
    // the code the comment is on
    expect(
      within(th2).getByLabelText(/The code of orders\/refunds\.py at line 20/),
    ).toHaveTextContent("if amount > refundable_amount(order):");

    await userEvent.click(
      within(check).getByRole("button", { name: "Use as my reply" }),
    );
    expect(
      within(th2).getByRole("textbox", { name: "Your reply" }),
    ).toHaveValue(
      "Good catch. views.py converts it now; I am adding a test for a bad value.",
    );

    // Address it asks first: Cancel starts nothing
    await userEvent.click(
      within(check).getByRole("button", { name: "Address it" }),
    );
    let confirm = within(th2).getByRole("group", {
      name: "Confirm Address it",
    });
    expect(confirm).toHaveTextContent(
      "Start the small flow on feature/SHOPS-1234-refunds?",
    );
    await userEvent.click(
      within(confirm).getByRole("button", { name: "Cancel" }),
    );
    expect(demo.calls.some((c) => c.path.includes("/address"))).toBe(false);

    await userEvent.click(
      within(check).getByRole("button", { name: "Address it" }),
    );
    confirm = within(th2).getByRole("group", { name: "Confirm Address it" });
    // you have not replied here: keel asks what the fix should be, the reviewer's words quoted
    const fix = within(confirm).getByRole("textbox", {
      name: "What should the fix be?",
    });
    expect((fix as HTMLTextAreaElement).value).toContain(
      "> alex: The amount comes from a form.",
    );
    await userEvent.type(fix, "Add the test.");
    await userEvent.click(
      within(confirm).getByRole("button", { name: "Start the flow" }),
    );
    await waitFor(() =>
      expect(
        demo.calls.find((c) => c.path.includes("/threads/th2/address")),
      ).toBeTruthy(),
    );
    const call = demo.calls.find((c) =>
      c.path.includes("/threads/th2/address"),
    )!;
    expect(call.body.fix).toContain("Add the test.");
    expect(
      await within(th2).findByRole("link", {
        name: /works on this comment.*Open flow/,
      }),
    ).toHaveAttribute("href", "#/flow/fl_1");
    expect(within(th2).getByText("fix running")).toBeInTheDocument();
  });

  it("asks keel about a comment it has not checked, and Check all open checks the rest", async () => {
    const demo = start();
    const { pr } = await openPr(42);
    const th3 = within(pr).getByRole("article", { name: /views\.py · line 7/ });
    expect(within(th3).getByText("waits for you")).toBeInTheDocument();
    expect(
      within(pr).getByRole("button", { name: "Check all open (1)" }),
    ).toBeInTheDocument();
    await userEvent.click(within(th3).getByRole("button", { name: /bo/ }));
    await userEvent.click(
      within(th3).getByRole("button", {
        name: "Is the reviewer right? Ask keel",
      }),
    );
    expect(
      await within(th3).findByText("keel: Partly right"),
    ).toBeInTheDocument();
    expect(within(th3).getByText("Not sure:")).toBeInTheDocument();
    expect(demo.calls.some((c) => c.path.endsWith("/threads/th3/judge"))).toBe(
      true,
    );
    await waitFor(() =>
      expect(
        within(pr).queryByRole("button", { name: /Check all open/ }),
      ).not.toBeInTheDocument(),
    );

    // Think harder asks again, with more effort
    await userEvent.click(
      within(th3).getByRole("button", { name: "Think harder" }),
    );
    await waitFor(() =>
      expect(
        demo.calls.filter((c) => c.path.endsWith("/threads/th3/judge")).at(-1)
          ?.body,
      ).toMatchObject({ harder: true, again: true }),
    );
  });

  it("Files: ticks a file viewed, adds a pending comment on a line, and a comment's marker opens it in Comments", async () => {
    const demo = start();
    const { pr } = await openPr(42);
    await userEvent.click(tab(pr, /^Files/));
    const files = within(pr).getByRole("list", { name: "Changed files" });
    expect(within(files).getByText("refunds.py")).toBeInTheDocument();
    const viewed = within(files).getByRole("checkbox", {
      name: "Viewed: orders/refunds.py",
    });
    await userEvent.click(viewed);
    await waitFor(() => expect(viewed).toBeChecked());
    expect(
      demo.calls.find((c) => c.path.endsWith("/viewed"))?.body,
    ).toMatchObject({ path: "orders/refunds.py", viewed: true });
    expect(within(pr).getByText("1 of 5 viewed")).toBeInTheDocument();

    const diff = await within(pr).findByRole("region", {
      name: "Changes of orders/refunds.py",
    });
    await within(diff).findByText(/return order\.paid_total - already/);
    await userEvent.click(
      within(diff).getByRole("button", { name: "Comment on line 20" }),
    );
    await userEvent.type(
      within(diff).getByRole("textbox", { name: "Your comment on line 20" }),
      "Name the error in the message.",
    );
    await userEvent.click(
      within(diff).getByRole("button", { name: "Add to my review" }),
    );
    await waitFor(() =>
      expect(
        demo.calls.find((c) => c.path.endsWith("/drafts"))?.body,
      ).toMatchObject({
        path: "orders/refunds.py",
        line: 20,
        side: "RIGHT",
        body: "Name the error in the message.",
      }),
    );
    expect(
      await within(diff).findByText("Name the error in the message."),
    ).toBeInTheDocument();
    expect(
      within(pr).getByRole("button", { name: "Submit review (1)" }),
    ).toBeInTheDocument();

    // bo's comment on line 13 is a marker under its line: it opens in Comments
    await userEvent.click(
      within(diff).getByRole("button", {
        name: "Open the comment of bo on line 13",
      }),
    );
    expect(tab(pr, /^Comments/)).toHaveAttribute("aria-selected", "true");
    const th1 = within(pr).getByRole("article", {
      name: /refunds\.py · line 13/,
    });
    expect(within(th1).getByRole("button", { name: /bo/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("Problems: Find problems, then Add as comment and Dismiss, grouped Blocking / Should fix / Already there before", async () => {
    const demo = start({ found: [] });
    const { pr } = await openPr(45);
    await userEvent.click(tab(pr, /^Problems/));
    expect(
      within(pr).getByText("keel has not looked for problems yet"),
    ).toBeInTheDocument();
    await userEvent.click(
      within(pr).getByRole("button", { name: "Find problems" }),
    );
    expect(
      await within(pr).findByRole("region", { name: "Blocking" }),
    ).toBeInTheDocument();
    expect(demo.calls.some((c) => c.path.endsWith("/ai/findings"))).toBe(true);
    expect(within(pr).getByText(/^1 blocking/)).toBeInTheDocument();
    expect(
      within(pr).getByRole("region", { name: "Should fix" }),
    ).toBeInTheDocument();
    const before = within(pr).getByRole("region", {
      name: /Already there before/,
    });
    // a problem that was there before is never blocking, and has no actions
    expect(
      within(before).queryByRole("button", { name: "Add as comment" }),
    ).not.toBeInTheDocument();

    const blocking = within(pr).getByRole("article", {
      name: 'A cell that starts with "-" is not made safe',
    });
    await userEvent.click(
      within(blocking).getByRole("button", { name: "Add as comment" }),
    );
    expect(
      await within(blocking).findByText("Added as a pending comment."),
    ).toBeInTheDocument();
    expect(
      demo.calls.find((c) => c.path.endsWith("/drafts"))?.body,
    ).toMatchObject({ finding_id: "f1", line: 4 });
    expect(
      demo.calls.find((c) => c.path.endsWith("/ai/findings/f1"))?.body,
    ).toMatchObject({ decision: "commented" });

    const should = within(pr).getByRole("article", {
      name: "Column order changed",
    });
    await userEvent.click(
      within(should).getByRole("button", { name: "Dismiss" }),
    );
    expect(
      await within(should).findByText(/Dismissed: not a problem here/),
    ).toBeInTheDocument();
    await userEvent.click(within(should).getByRole("button", { name: "Undo" }));
    expect(
      await within(should).findByRole("button", { name: "Dismiss" }),
    ).toBeInTheDocument();
  });
});

describe("the new Review view: Settings › Review on the Problems tab (0.18.65)", () => {
  it("says which reviewers read the change, and a finding offers quick, small and change when Settings asks each time", async () => {
    const demo = start({ found: [] });
    const { pr } = await openPr(45);
    await userEvent.click(tab(pr, /^Problems/));
    expect(
      within(pr).getByText(/^Three keel reviewers read the change \(A: correctness and security, B: tests, the description and design, C: money\)/),
    ).toBeInTheDocument();
    await userEvent.click(within(pr).getByRole("button", { name: "Find problems" }));
    const blocking = await within(pr).findByRole("article", {
      name: 'A cell that starts with "-" is not made safe',
    });
    expect(within(blocking).getByRole("button", { name: "Fix: quick" })).toBeInTheDocument();
    expect(within(blocking).getByRole("button", { name: "Fix: change" })).toBeInTheDocument();
    await userEvent.click(within(blocking).getByRole("button", { name: "Fix: small" }));
    expect(await within(blocking).findByText(/The small flow fixes it/)).toBeInTheDocument();
    expect(within(blocking).getByRole("link", { name: "Open flow ▸" })).toHaveAttribute("href", "#/flow/t-fix-1");
    expect(demo.calls.find((c) => c.path.endsWith("/ai/findings/f1/fix"))?.body).toMatchObject({ workflow: "small" });
    expect(within(blocking).queryByRole("button", { name: "Fix: quick" })).not.toBeInTheDocument();
  });
});

describe("the new Review view: Submit", () => {
  it("does not offer Approve or Request changes on your own pull request; it sends a comment", async () => {
    const demo = start();
    const { pr } = await openPr(42);
    await userEvent.click(
      within(pr).getByRole("button", { name: "Submit review" }),
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Submit your review of #42",
    });
    expect(within(drawer).getByText(/None yet/)).toBeInTheDocument();
    expect(
      within(drawer).getByRole("radio", { name: /^Approve/ }),
    ).toBeDisabled();
    expect(
      within(drawer).getByRole("radio", { name: /^Request changes/ }),
    ).toBeDisabled();
    expect(
      within(drawer).getByText("You cannot approve your own change."),
    ).toBeInTheDocument();
    expect(
      within(drawer).getByRole("radio", { name: /^Comment/ }),
    ).toBeChecked();
    // the focus is in the drawer
    expect(drawer.contains(document.activeElement)).toBe(true);
    await userEvent.type(
      within(drawer).getByRole("textbox", { name: /A summary for the author/ }),
      "Thanks, all answered.",
    );
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Submit review" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(
      demo.calls.find((c) => c.path.endsWith("/submit"))?.body,
    ).toMatchObject({ event: "COMMENT", body: "Thanks, all answered." });
    expect(
      await screen.findByText("Review sent to GitHub: you commented on #42."),
    ).toBeInTheDocument();
  });

  it("approves another's pull request with its pending comments; the list says so and Review #N moves on", async () => {
    const demo = start();
    const { side, pr } = await openPr(45);
    await userEvent.click(tab(pr, /^Files/));
    const diff = await within(pr).findByRole("region", {
      name: "Changes of orders/export.py",
    });
    await within(diff).findByText('FORMULA = ("=", "+", "@")');
    await userEvent.click(
      within(diff).getByRole("button", { name: "Comment on line 4" }),
    );
    await userEvent.type(
      within(diff).getByRole("textbox", { name: "Your comment on line 4" }),
      "Add the minus sign.",
    );
    await userEvent.click(
      within(diff).getByRole("button", { name: "Add to my review" }),
    );
    await userEvent.click(
      await within(pr).findByRole("button", { name: "Submit review (1)" }),
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Submit your review of #45",
    });
    expect(within(drawer).getByText("Add the minus sign.")).toBeInTheDocument();
    await userEvent.click(
      within(drawer).getByRole("radio", { name: /^Approve/ }),
    );
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Submit and approve" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(
      demo.calls.find((c) => c.path.endsWith("/submit"))?.body,
    ).toMatchObject({ event: "APPROVE" });
    expect(
      await screen.findByText(
        "Review sent to GitHub: you approved on #45 with 1 line comment.",
      ),
    ).toBeInTheDocument();
    const row = within(side).getByRole("button", { name: /^#45 / });
    expect(within(row).getByText("you approved")).toBeInTheDocument();
    // #44 is a draft: nothing else waits for you
    expect(
      within(side).getByRole("button", { name: "All reviewed" }),
    ).toBeInTheDocument();
    expect(within(pr).getByText("approved")).toBeInTheDocument();
  });

  it("removes a pending comment in the drawer", async () => {
    const demo = start();
    const { pr } = await openPr(45);
    await userEvent.click(tab(pr, /^Files/));
    const diff = await within(pr).findByRole("region", {
      name: "Changes of orders/export.py",
    });
    await within(diff).findByText('FORMULA = ("=", "+", "@")');
    await userEvent.click(
      within(diff).getByRole("button", { name: "Comment on line 7" }),
    );
    await userEvent.type(
      within(diff).getByRole("textbox", { name: "Your comment on line 7" }),
      "A docstring?",
    );
    await userEvent.click(
      within(diff).getByRole("button", { name: "Add to my review" }),
    );
    await userEvent.click(
      await within(pr).findByRole("button", { name: "Submit review (1)" }),
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Submit your review of #45",
    });
    await userEvent.click(
      within(drawer).getByRole("button", {
        name: "Remove the pending comment on line 7",
      }),
    );
    expect(await within(drawer).findByText(/None yet/)).toBeInTheDocument();
    expect(
      demo.calls.some(
        (c) => c.method === "DELETE" && c.path.includes("/drafts/dr_1"),
      ),
    ).toBe(true);
    await userEvent.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });
});

describe("the new Review view on a phone", () => {
  it("shows the list, then the pull request on its own screen, and goes back to the list", async () => {
    const had = window.matchMedia;
    window.matchMedia = ((q: string) => ({
      matches: q.includes("max-width: 720px"),
      media: q,
      addEventListener() {},
      removeEventListener() {},
      onchange: null,
    })) as unknown as typeof window.matchMedia;
    try {
      start();
      location.hash = "#/review";
      render(<App />);
      const side = await screen.findByRole("region", { name: "Review" });
      // one at a time: the list, no pull request beside it
      expect(screen.queryByText(/Pick a pull request on the left/)).not.toBeInTheDocument();
      await userEvent.click(
        await within(side).findByRole("button", { name: /^#45 / }),
      );
      expect(
        await screen.findByRole("region", { name: "Review of #45" }),
      ).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Review" })).not.toBeInTheDocument();
      await userEvent.click(
        screen.getByRole("button", { name: "‹ All pull requests" }),
      );
      const back = await screen.findByRole("region", { name: "Review" });
      expect(
        await within(back).findByRole("button", { name: /^#45 / }),
      ).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Review of #45" })).not.toBeInTheDocument();
    } finally {
      act(() => {
        window.matchMedia = had;
      });
    }
  });
});
