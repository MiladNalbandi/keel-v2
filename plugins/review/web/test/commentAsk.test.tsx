// keel 0.36 (#188) a review comment on selected lines, and Ask KeelBot inside the comment box: KeelBot answers in the
// box (one ask-mode chat for the review), "Use in my comment" puts the answer in the comment, "Open the chat" goes on
// in KeelBot with that chat. It runs inside keel's web on the demo data of ./nextDemo, like selection.test.tsx.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../../../../web/src/App";
import { SLOTS } from "../../../../web/src/sdk/slots";
import { restorePlugins, withoutSlot } from "../../../../web/src/test/coreOnly";
import { db, server } from "../../../../web/src/test/setup";
import { nextDemo } from "./nextDemo";
import { answerAfter, askText } from "../next/askKeelBot";

afterEach(() => {
  window.getSelection()?.removeAllRanges();
  restorePlugins();
  try {
    localStorage.clear();
  } catch {
    /* no storage */
  }
});

async function openDiff() {
  const demo = nextDemo({});
  server.use(...demo.handlers);
  db.plugins.review = true;
  location.hash = "#/review";
  render(<App />);
  const side = await screen.findByRole("region", { name: "Review" });
  await userEvent.click(await within(side).findByRole("button", { name: /^#42 / }));
  const pr = await screen.findByRole("region", { name: "Review of #42" });
  await userEvent.click(await within(pr).findByRole("tab", { name: /^Files/ }));
  const diff = await within(pr).findByRole("region", { name: "Changes of orders/refunds.py" });
  await within(diff).findByText(/return order\.paid_total - already/);
  return { diff, demo };
}

const row = (diff: HTMLElement, text: RegExp) => within(diff).getByText(text).closest<HTMLElement>(".rvn-dl-w")!;

function select(a: HTMLElement, b: HTMLElement) {
  const r = document.createRange();
  r.setStart(a, 0);
  r.setEnd(b, b.childNodes.length);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(r);
  fireEvent.mouseUp(document);
}

/** KeelBot's answer to the last question of chat `sid`, as the engine would write it. */
function answer(sid: string, text: string) {
  const s = db.helper.sessions.find((x) => x.id === sid)!;
  const n = (s.messages?.length ?? 0) + 1;
  s.messages = [...(s.messages ?? []), { n, role: "helper", text, data: {}, at: new Date().toISOString() }];
  s.status = "idle";
  s.busy = false;
}

describe("Review › Files: comment on selected lines, ask KeelBot from the comment box", () => {
  it("Comment on a selection keeps a pending comment on those lines", async () => {
    const { diff, demo } = await openDiff();
    select(row(diff, /already = sum/), row(diff, /return order\.paid_total/));
    fireEvent.click(within(await screen.findByRole("toolbar", { name: "Selected code" })).getByRole("button", { name: "Comment" }));
    const box = await within(diff).findByRole("form", { name: "Comment on lines 12–13" });
    expect(within(box).getByLabelText("The code the comment is on").textContent).toContain("return order.paid_total - already");
    await userEvent.type(within(box).getByRole("textbox", { name: "Your comment on lines 12–13" }), "Coupons too?");
    await userEvent.click(within(box).getByRole("button", { name: "Add to my review" }));
    await waitFor(() => {
      const c = demo.calls.find((x) => x.method === "POST" && x.path.endsWith("/drafts"));
      expect(c?.body).toMatchObject({ path: "orders/refunds.py", line: 13, side: "RIGHT", start_line: 12, start_side: "RIGHT", body: "Coupons too?" });
    });
  });

  it("Ask KeelBot answers in the box; Use in my comment and Open the chat", async () => {
    const { diff } = await openDiff();
    await userEvent.click(within(diff).getByRole("button", { name: "Comment on line 20" }));
    const box = await within(diff).findByRole("form", { name: "Comment on line 20" });
    const comment = within(box).getByRole("textbox", { name: "Your comment on line 20" }) as HTMLTextAreaElement;
    await userEvent.type(comment, "Hmm.");
    await userEvent.click(within(box).getByRole("button", { name: "Ask KeelBot" }));
    const q = within(box).getByRole("textbox", { name: "Your question to KeelBot" });
    await userEvent.type(q, "Can this be negative?{Enter}");
    await within(box).findByRole("status");
    // one ask-mode chat, titled for the review; the question has the line, the code and the commit
    await waitFor(() => expect(db.helper.sessions[0]?.messages?.length).toBe(1));
    const chat = db.helper.sessions[0];
    expect(chat.mode).toBe("ask");
    expect(chat.title).toBe("Review · pull request #42");
    const asked = chat.messages![0].text;
    expect(asked).toMatch(/^I am writing a review comment on line orders\/refunds\.py:20 in pull request #42 \(commit [0-9a-f]+; read it with git show/);
    expect(asked).toContain("if amount > refundable_amount(order):");
    expect(asked).toContain("My question: Can this be negative?");
    answer(chat.id, "Yes: a **refund** after a coupon.");
    const a = await within(box).findByLabelText("KeelBot's answer", {}, { timeout: 4000 });
    expect(a.textContent).toContain("Yes: a refund after a coupon.");
    await userEvent.click(within(box).getByRole("button", { name: "Use in my comment" }));
    expect(comment.value).toBe("Hmm.\n\nYes: a **refund** after a coupon.");

    // a second question goes to the same chat
    await userEvent.type(within(box).getByRole("textbox", { name: "Your question to KeelBot" }), "{Enter}");
    await waitFor(() => expect(db.helper.sessions.find((s) => s.id === chat.id)?.messages?.length).toBe(3));
    expect(db.helper.sessions.filter((s) => s.title.startsWith("Review ·"))).toHaveLength(1);
    expect(chat.messages![2].text).toContain("Explain what this code does");
    answer(chat.id, "It checks the amount.");
    await within(box).findByText("It checks the amount.", {}, { timeout: 4000 });

    await userEvent.click(within(box).getByRole("button", { name: "Open the chat" }));
    await waitFor(() => expect(location.hash).toBe("#/helper"));
    expect(localStorage.getItem("keel2.helper.p1.session") ?? localStorage.getItem(`keel2.helper.${chat.project}.session`)).toBe(chat.id);
  });

  it("without KeelBot the box has no Ask", async () => {
    withoutSlot(SLOTS.assistant);
    const { diff } = await openDiff();
    await userEvent.click(within(diff).getByRole("button", { name: "Comment on line 20" }));
    const box = await within(diff).findByRole("form", { name: "Comment on line 20" });
    expect(within(box).queryByRole("button", { name: /^Ask/ })).toBeNull();
  });
});

describe("askKeelBot helpers", () => {
  it("answerAfter takes the first answer or note after the question", () => {
    const m = (n: number, role: "user" | "helper" | "note", text = "") => ({ n, role, text, data: {}, at: "" });
    expect(answerAfter([m(1, "user"), m(2, "helper", "a"), m(3, "user")], 3)).toBeNull();
    expect(answerAfter([m(1, "user"), m(2, "helper", "a"), m(3, "user"), m(4, "note", "stopped")], 3)?.text).toBe("stopped");
  });
  it("askText asks the default question when none is typed", () => {
    expect(askText({ where: "line a.py:3", code: "x = 1", question: "" })).toContain("Explain what this code does");
    expect(askText({ where: "line a.py:3", code: "x = 1", question: " why? " })).toContain("My question: why?");
  });
});
