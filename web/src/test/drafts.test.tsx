// keel 0.18.64 an answer to a waiting question that is not sent yet is kept (src/drafts.tsx, in @keel/web-sdk): picked
// options, own words and a gate's reason come back after another project or page, the Inbox shows the same draft, a sent
// answer forgets it, a new question does not show the old one's, an old or too large draft is not kept, blocked storage
// never throws.

import { act, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { ThreadState } from "../api";
import {
  DRAFT_MAX_AGE_MS,
  DRAFT_MAX_CHARS,
  DRAFT_PREFIX,
  clearDraft,
  draftKey,
  dropOtherDrafts,
  pruneDrafts,
  questionKey,
  useAnswerDraft,
} from "../drafts";
import { http, HttpResponse } from "msw";
import type { InboxItem, InboxView } from "../inboxApi";
import { db, server } from "./setup";

afterEach(() => vi.restoreAllMocks());

const draftKeys = () => Object.keys(localStorage).filter((k) => k.startsWith(DRAFT_PREFIX));

/** A clarify round with two questions (neutral words), waiting under [id]. */
function clarify(id: string): NonNullable<ThreadState["waiting"]> {
  return {
    step: "clarify_gate", kind: "clarify", title: "Questions before the plan", detail: "", options: ["approve"], id,
    labels: { approve: "Send my answers" },
    questions: [
      { id: "store", question: "Where should the cache live?", options: [{ label: "in memory", recommended: true }, { label: "on disk" }] },
      { id: "ttl", question: "How long should entries live?", options: [{ label: "one hour", recommended: true }, { label: "one day" }] },
    ],
  };
}

const gate = () => screen.findByRole("region", { name: "Gate waits for you" });

async function switchProject(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole("button", { name: /^Project: / }));
  await user.click(within(screen.getByRole("listbox", { name: "Projects" })).getByRole("option", { name }));
}

describe("a waiting question's unsent answer (Flow page)", () => {
  it("comes back after another project, shows 'draft kept', and goes once it is sent", async () => {
    const user = userEvent.setup();
    db.flows["ludus-engine"].thread!.waiting = clarify("q-1");
    render(<App />);
    let g = await gate();
    await user.click(within(g).getByRole("radio", { name: /on disk/ }));
    await user.type(within(g).getByLabelText("Your own answer to: How long should entries live?"), "until the next deploy");
    await user.type(within(g).getByLabelText("Anything else the explorer should know (optional)"), "reads are rare");
    // typing a draft is not a kept draft yet: no hint
    expect(within(g).queryByTestId("draft-kept")).not.toBeInTheDocument();

    await switchProject(user, "YegiResearcher");
    expect(await screen.findByText("Nothing running here yet")).toBeInTheDocument();
    await switchProject(user, "ludus-engine");

    g = await gate();
    expect(within(g).getByRole("radio", { name: /on disk/ })).toHaveAttribute("aria-checked", "true");
    expect(within(g).getByLabelText("Your own answer to: How long should entries live?")).toHaveValue("until the next deploy");
    expect(within(g).getByLabelText("Anything else the explorer should know (optional)")).toHaveValue("reads are rare");
    expect(within(g).getByTestId("draft-kept")).toHaveTextContent("Draft kept");

    await user.click(within(g).getByRole("button", { name: "Send my answers" }));
    await waitFor(() => expect(db.calls.find((c) => c.path.endsWith("/resume"))?.body).toEqual({
      decision: "approve", why: "reads are rare", payload: { answers: { store: "on disk", ttl: "until the next deploy" } } }));
    await waitFor(() => expect(draftKeys()).toEqual([]));
  });

  it("a new question of the same flow does not show the old one's draft, and forgets it", async () => {
    const user = userEvent.setup();
    db.flows["ludus-engine"].thread!.waiting = clarify("q-1");
    const first = render(<App />);
    const g = await gate();
    await user.type(within(g).getByLabelText("Your own answer to: Where should the cache live?"), "a shared volume");
    expect(draftKeys().length).toBe(1);
    first.unmount();

    db.flows["ludus-engine"].thread!.waiting = clarify("q-2");
    render(<App />);
    const again = await gate();
    expect(within(again).getByLabelText("Your own answer to: Where should the cache live?")).toHaveValue("");
    expect(within(again).queryByTestId("draft-kept")).not.toBeInTheDocument();
    await waitFor(() => expect(draftKeys()).toEqual([]));
  });

  it("a gate's picked choice and its reason are kept (ChoiceForm)", async () => {
    const user = userEvent.setup();
    db.flows["ludus-engine"].thread!.waiting = { step: "verify_green", kind: "fix", title: "AC-002 is stuck", detail: "failed 3 times", id: "q-fix",
      options: ["approve", "reject"], choices: ["retry", "skip"], choice_info: [
        { name: "retry", label: "Try again", description: "one more round", why: "optional", default: true },
        { name: "skip", label: "Skip it for now", description: "move it to the end", why: "required", default: false }] };
    const first = render(<App />);
    let g = await gate();
    await user.click(within(g).getByRole("radio", { name: /Skip it for now/ }));
    await user.type(within(g).getByLabelText("Why (needed for “Skip it for now”)"), "waits for another change");
    first.unmount();

    render(<App />);
    g = await gate();
    expect(within(g).getByRole("radio", { name: /Skip it for now/ })).toHaveAttribute("aria-checked", "true");
    expect(within(g).getByLabelText("Why (needed for “Skip it for now”)")).toHaveValue("waits for another change");
    expect(within(g).getByTestId("draft-kept")).toBeInTheDocument();
    await user.click(within(g).getByRole("button", { name: "Send: Skip it for now" }));
    await waitFor(() => expect(db.calls.find((c) => c.path.endsWith("/resume"))?.body).toEqual(
      { decision: "approve", why: "waits for another change", payload: { choice: "skip" } }));
    await waitFor(() => expect(draftKeys()).toEqual([]));
  });

  it("a send back's reason is kept too", async () => {
    const user = userEvent.setup();
    const first = render(<App />);
    await user.type(within(await gate()).getByLabelText("Why (needed to send back)"), "check the empty case");
    first.unmount();
    render(<App />);
    expect(within(await gate()).getByLabelText("Why (needed to send back)")).toHaveValue("check the empty case");
  });

  it("a flow that waits for nothing any more drops its drafts", async () => {
    localStorage.setItem(draftKey({ project: "ludus-engine", thread: "th_7f3a", question: "old" }, "why"),
      JSON.stringify({ v: "left over", at: Date.now() }));
    db.flows["ludus-engine"].thread = { ...db.flows["ludus-engine"].thread!, status: "running", waiting: undefined };
    render(<App />);
    await waitFor(() => expect(draftKeys()).toEqual([]));
  });
});

describe("the Inbox keeps the same draft", () => {
  it("a draft begun on the Flow page shows in the Inbox card, and another page and back keeps it", async () => {
    const user = userEvent.setup();
    db.flows["ludus-engine"].thread!.waiting = clarify("q-1");
    const item: InboxItem = {
      project_id: "ludus-engine", project_name: "ludus-engine", thread_id: "th_7f3a", flow: "Scores for players", workflow_id: "feature",
      step: "clarify_gate", kind: "clarify", title: "Questions before the plan", detail: "", more: false, options: ["approve"], id: "q-1",
      questions: clarify("q-1").questions,
    };
    server.use(http.get("/api/inbox", () => HttpResponse.json(
      { items: [item], count: 1, kinds: ["clarify"], projects: [{ id: "ludus-engine", name: "ludus-engine", count: 1 }] } satisfies InboxView)));
    render(<App />);
    await user.type(within(await gate()).getByLabelText("Your own answer to: Where should the cache live?"), "a shared volume");

    location.hash = "#/inbox";
    const card = await screen.findByRole("article", { name: "Questions before the plan" });
    expect(within(card).getByLabelText("Your own answer to: Where should the cache live?")).toHaveValue("a shared volume");
    expect(within(card).getByTestId("draft-kept")).toBeInTheDocument();
    await user.click(within(card).getByRole("radio", { name: /one day/ }));

    location.hash = "#/flow";
    const g = await gate();
    expect(within(g).getByRole("radio", { name: /one day/ })).toHaveAttribute("aria-checked", "true");
  });
});

describe("useAnswerDraft", () => {
  const place = { project: "p1", thread: "t1", question: "q1" };

  it("keeps a field per project, flow and question; an empty one keeps no key; clearDraft forgets every field", () => {
    const a = renderHook(() => useAnswerDraft<string>(place, "why", ""));
    const b = renderHook(() => useAnswerDraft<Record<string, string>>(place, "typed", {}));
    act(() => a.result.current[1]("half a reason"));
    act(() => b.result.current[1]((p) => ({ ...p, q: "own words" })));
    expect(draftKeys().length).toBe(2);
    act(() => a.result.current[1](""));
    expect(draftKeys().length).toBe(1);
    act(() => clearDraft(place));
    expect(draftKeys()).toEqual([]);
    // the mounted form shows the emptied value at once
    expect(b.result.current[0]).toEqual({});
  });

  it("the third value says a draft came back (not one being typed)", () => {
    const a = renderHook(() => useAnswerDraft<string>(place, "why", ""));
    act(() => a.result.current[1]("typed now"));
    expect(a.result.current[2]).toBe(false);
    a.unmount();
    const again = renderHook(() => useAnswerDraft<string>(place, "why", ""));
    expect(again.result.current).toEqual(["typed now", expect.any(Function), true]);
  });

  it("a draft older than 14 days is not shown and goes at the clean-up", () => {
    localStorage.setItem(draftKey(place, "why"), JSON.stringify({ v: "old words", at: Date.now() - DRAFT_MAX_AGE_MS - 1000 }));
    const a = renderHook(() => useAnswerDraft<string>(place, "why", ""));
    expect(a.result.current[0]).toBe("");
    pruneDrafts();
    expect(draftKeys()).toEqual([]);
  });

  it("a field too large is not kept, but stays in the form", () => {
    const a = renderHook(() => useAnswerDraft<string>(place, "why", ""));
    act(() => a.result.current[1]("x".repeat(DRAFT_MAX_CHARS + 1)));
    expect(a.result.current[0].length).toBe(DRAFT_MAX_CHARS + 1);
    expect(draftKeys()).toEqual([]);
  });

  it("without a place nothing is kept, and two such forms do not share their state", () => {
    const a = renderHook(() => useAnswerDraft<string>(null, "why", ""));
    const b = renderHook(() => useAnswerDraft<string>(null, "why", ""));
    act(() => a.result.current[1]("only here"));
    expect(a.result.current[0]).toBe("only here");
    expect(b.result.current[0]).toBe("");
    expect(draftKeys()).toEqual([]);
  });

  it("dropOtherDrafts keeps only the flow's current question (or none)", () => {
    for (const q of ["q1", "q2"]) localStorage.setItem(draftKey({ ...place, question: q }, "why"), JSON.stringify({ v: "x", at: Date.now() }));
    localStorage.setItem(draftKey({ ...place, thread: "t2" }, "why"), JSON.stringify({ v: "x", at: Date.now() }));
    dropOtherDrafts("p1", "t1", "q2");
    expect(draftKeys().sort()).toEqual([draftKey({ ...place, question: "q2" }, "why"), draftKey({ ...place, thread: "t2" }, "why")].sort());
    dropOtherDrafts("p1", "t1", null);
    expect(draftKeys()).toEqual([draftKey({ ...place, thread: "t2" }, "why")]);
  });

  it("never throws when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("full"); });
    vi.spyOn(Storage.prototype, "key").mockImplementation(() => { throw new Error("blocked"); });
    const a = renderHook(() => useAnswerDraft<string>(place, "why", ""));
    act(() => a.result.current[1]("still typed"));
    expect(a.result.current[0]).toBe("still typed");
    expect(() => { clearDraft(place); dropOtherDrafts("p1", "t1", null); pruneDrafts(); }).not.toThrow();
  });

  it("keel 0.18.64 sign-out forgets every unsent answer and KeelBot's chat drafts, nothing else", async () => {
    const place = { project: "p1", thread: "t1", question: "q1" };
    localStorage.setItem(draftKey(place, "why"), JSON.stringify({ v: "half a reason", at: Date.now() }));
    localStorage.setItem("keel2.helper.p1.drafts", JSON.stringify({ new: { text: "half a question", mentions: [] } }));
    localStorage.setItem("keel2.helper.p1.folded", JSON.stringify(["f1"]));
    let called = "";
    server.use(http.post("*/api/web/sign-out", () => {
      called = "this";
      return HttpResponse.json({ ok: true });
    }));
    const { signOut } = await import("../pages/settings/Access");
    await signOut(false);
    expect(called).toBe("this");
    expect(draftKeys()).toEqual([]);
    expect(localStorage.getItem("keel2.helper.p1.drafts")).toBeNull();
    expect(localStorage.getItem("keel2.helper.p1.folded")).not.toBeNull();
    const { signInDone } = await import("../signIn");
    signInDone();
  });

  it("questionKey: the question's id, else its step and criterion", () => {
    expect(questionKey({ id: "q-9", step: "s8", ac: "AC-1" })).toBe("q-9");
    expect(questionKey({ id: null, step: "s8", ac: "AC-1" })).toBe("s8|AC-1");
  });
});
