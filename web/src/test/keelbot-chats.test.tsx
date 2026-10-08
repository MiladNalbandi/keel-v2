// v0.15.2 KeelBot's chats: the open chat, its draft and its place stay when you leave and come back; the chat list with
// search, folders, rename, move and delete (asked in the page); a new answer you did not see is a number on KeelBot with
// its own sound, which a switch turns off; and the guide in a new chat says how to use KeelBot.

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { HelperSession } from "../api";
import { App } from "../App";
import { ago, getDraft, groupChats, setDraft } from "../components/helper/chats";
import { KEELBOT_NOTES } from "../notify";
import { audioLog, db, FakeEventSource } from "./setup";

const PID = "ludus-engine";
const panel = () => screen.findByRole("complementary", { name: "KeelBot" });
const chatList = () => screen.findByRole("navigation", { name: "KeelBot chats" });
const turns = () => db.calls.filter((c) => c.method === "POST" && /\/helper\/sessions\/[^/]+\/turn$/.test(c.path));
const goTo = async (hash: string) => {
  await act(async () => {
    location.hash = hash;
    await new Promise((r) => setTimeout(r, 20));
  });
};

function chat(id: string, title: string, extra: Partial<HelperSession> = {}): HelperSession {
  const at = new Date().toISOString();
  return { id, project: PID, root: "/workspace", mode: "ask", title, model: { provider: "claude", mode: "subscription", model: "sonnet" },
    status: "idle", tokens_in: 0, tokens_out: 0, tokens_cached: 0, cost_usd: 0, turns: 1, created_at: at, updated_at: at, busy: false,
    messages: [{ n: 1, role: "user", text: title, data: {}, at }, { n: 2, role: "helper", text: `About ${title}.`, call_id: `c-${id}`, data: { status: "done" }, at }],
    folder: null, ...extra };
}

/** The engine answers the chat's last question: the answer is stored, then helper.finished arrives as a live event. */
function answer(sid: string, text: string, status = "done") {
  const sess = db.helper.sessions.find((x) => x.id === sid)!;
  const n = (sess.messages?.length ?? 0) + 1;
  const call = `call-${sid}-${n - 1}`;
  sess.messages = [...(sess.messages ?? []), { n, role: status === "done" ? "helper" : "note", text, call_id: call, data: { status }, at: new Date().toISOString() }];
  sess.status = "idle";
  sess.busy = false;
  sess.turns += 1;
  act(() => {
    FakeEventSource.emit("helper.finished", { type: "helper.finished", thread_id: sid, project_id: PID, call_id: call, step: "helper",
      at: new Date().toISOString(), data: { status } });
  });
}

const setHidden = (hidden: boolean) => {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  act(() => void document.dispatchEvent(new Event("visibilitychange")));
};
afterEach(() => {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
});

describe("KeelBot chats: the pure helpers", () => {
  it("groups chats by folder with search, keeps drafts per chat, and says how long ago", () => {
    const folders = [{ id: "f2", project: PID, name: "payments", chats: 1, created_at: "", updated_at: "" },
      { id: "f1", project: PID, name: "Auth", chats: 1, created_at: "", updated_at: "" },
      { id: "f3", project: PID, name: "Empty", chats: 0, created_at: "", updated_at: "" }];
    const chats = [chat("a", "Login bug", { folder: "f1" }), chat("b", "Refund rules", { folder: "f2" }), chat("c", "Scores API"),
      chat("d", "Old one", { folder: "f-gone" })];
    const g = groupChats(chats, folders);
    expect(g.map((x) => [x.folder?.name ?? "none", x.chats.map((c) => c.id)])).toEqual([
      ["Auth", ["a"]], ["Empty", []], ["payments", ["b"]], ["none", ["c", "d"]]]);
    expect(groupChats(chats, folders, "LOGIN").map((x) => x.chats.map((c) => c.id))).toEqual([["a"]]);
    expect(groupChats(chats, folders, "payments").map((x) => x.chats.map((c) => c.id))).toEqual([["b"]]);   // by folder name
    expect(groupChats(chats, folders, "nothing like it")).toEqual([]);

    setDraft(PID, "a", { text: "half a thought", mentions: [{ kind: "file", value: "a.kt" }] });
    setDraft(PID, null, { text: "a new idea", mentions: [] });
    expect(getDraft(PID, "a")).toEqual({ text: "half a thought", mentions: [{ kind: "file", value: "a.kt" }] });
    expect(getDraft(PID, null).text).toBe("a new idea");
    setDraft(PID, "a", { text: "  ", mentions: [] });
    expect(getDraft(PID, "a").text).toBe("");
    expect(JSON.parse(localStorage.getItem(`keel2.helper.${PID}.drafts`)!)).toEqual({ new: { text: "a new idea", mentions: [] } });

    const now = Date.parse("2026-10-08T12:00:00Z");
    expect(ago("2026-10-08T11:59:40Z", now)).toBe("now");
    expect(ago("2026-10-08T11:15:00Z", now)).toBe("45 min");
    expect(ago("2026-10-08T07:00:00Z", now)).toBe("5 h");
    expect(ago("2026-10-06T12:00:00Z", now)).toBe("2 d");
    expect(ago(null, now)).toBe("");
  });
});

describe("KeelBot keeps the chat when you leave", () => {
  it("keeps the open chat, the text not sent yet and the scroll place across pages, and each chat has its own draft", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    let p = await panel();
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "First question{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(1));
    const sid = db.helper.sessions[0].id;
    answer(sid, "First answer.");
    await within(p).findByText("First answer.");
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "half a thought");

    // the conversation is long and the person scrolled up to read
    const heights = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(2000);
    const client = vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(300);
    const body = within(p).getByRole("log", { name: "Conversation" });
    body.scrollTop = 480;
    fireEvent.scroll(body);

    await goTo("#/flow");
    await waitFor(() => expect(screen.queryByRole("complementary", { name: "KeelBot" })).toBeNull());
    await goTo("#/repo");
    p = await panel();
    expect(await within(p).findByText("First answer.")).toBeInTheDocument();
    expect(within(p).getByRole("textbox", { name: "Ask KeelBot" })).toHaveValue("half a thought");
    await waitFor(() => expect(within(p).getByRole("log", { name: "Conversation" }).scrollTop).toBe(480));
    heights.mockRestore();
    client.mockRestore();

    // the KeelBot page shows the same chat and the same draft
    await goTo("#/keelbot");
    p = await panel();
    expect(await within(p).findByText("First answer.")).toBeInTheDocument();
    expect(within(p).getByRole("textbox", { name: "Ask KeelBot" })).toHaveValue("half a thought");

    // a new chat has a draft of its own; the old chat gets its draft back
    await user.click(within(await chatList()).getByRole("button", { name: "New chat" }));
    expect(within(p).getByRole("textbox", { name: "Ask KeelBot" })).toHaveValue("");
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "a new idea");
    await user.click(within(await chatList()).getByRole("button", { name: /^First question/ }));
    expect(within(p).getByRole("textbox", { name: "Ask KeelBot" })).toHaveValue("half a thought");
    await user.click(within(await chatList()).getByRole("button", { name: "New chat" }));
    expect(within(p).getByRole("textbox", { name: "Ask KeelBot" })).toHaveValue("a new idea");
  });

  it("forgets a chat only when the server says it is gone, not when the server does not answer", async () => {
    db.helper.sessions = [chat("h_9", "Scores API")];
    localStorage.setItem(`keel2.helper.${PID}.session`, "h_9");
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    const { server } = await import("./setup");
    const { http, HttpResponse } = await import("msw");
    server.use(http.get("/api/projects/:pid/helper/sessions/h_9", () => HttpResponse.json({ error: "The engine is not running" }, { status: 503 })));
    render(<App />);
    await panel();
    const p = await panel();
    expect(await within(p).findByText(/This chat did not load: The engine is not running/)).toBeInTheDocument();
    expect(localStorage.getItem(`keel2.helper.${PID}.session`)).toBe("h_9");
    expect(within(p).queryByRole("note", { name: "How to use KeelBot" })).toBeNull();
  });
});

describe("KeelBot's chat list", () => {
  it("searches, makes folders, moves, renames and deletes chats and folders, asking in the page", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm");
    db.helper.sessions = [chat("h_1", "Scores API"), chat("h_2", "Login bug"), chat("h_3", "Price helper", { mode: "side", worktree: "/w/h3", branch: "keel/helper/3" })];
    location.hash = "#/keelbot";
    render(<App />);
    const list = await chatList();
    await within(list).findByRole("button", { name: /^Login bug/ });

    // search
    await user.type(within(list).getByRole("searchbox", { name: "Search chats" }), "login");
    expect(within(list).queryByRole("button", { name: /^Scores API/ })).toBeNull();
    expect(within(list).getByRole("button", { name: /^Login bug/ })).toBeInTheDocument();
    await user.clear(within(list).getByRole("searchbox", { name: "Search chats" }));
    await user.type(within(list).getByRole("searchbox", { name: "Search chats" }), "nothing like it");
    expect(within(list).getByText("No chat matches “nothing like it”.")).toBeInTheDocument();
    await user.clear(within(list).getByRole("searchbox", { name: "Search chats" }));

    // a folder, and a chat moved into it (kept on the server)
    await user.click(within(list).getByRole("button", { name: "+ New folder" }));
    await user.type(within(list).getByRole("textbox", { name: "Folder name" }), "Auth{Enter}");
    const auth = await within(list).findByRole("region", { name: "Folder Auth" });
    expect(db.helper.folders).toMatchObject([{ id: "hf_1", name: "Auth", project: PID }]);
    await user.click(within(list).getByRole("button", { name: "More for Login bug" }));
    await user.selectOptions(within(list).getByRole("combobox", { name: "Folder of Login bug" }), "Auth");
    await waitFor(() => expect(within(auth).getByRole("button", { name: /^Login bug/ })).toBeInTheDocument());
    expect(db.helper.sessions.find((s) => s.id === "h_2")!.folder).toBe("hf_1");
    expect(within(list).getByRole("region", { name: "No folder" })).toBeInTheDocument();

    // rename the folder and a chat
    await user.click(within(list).getByRole("button", { name: "More for the folder Auth" }));
    await user.click(within(list).getByRole("button", { name: "Rename folder" }));
    const fname = within(list).getByRole("textbox", { name: "Folder name" });
    await user.clear(fname);
    await user.type(fname, "Security{Enter}");
    expect(await within(list).findByRole("region", { name: "Folder Security" })).toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: "More for Scores API" }));
    await user.click(within(list).getByRole("button", { name: "Rename" }));
    const cname = within(list).getByRole("textbox", { name: "Chat name" });
    await user.clear(cname);
    await user.type(cname, "Scores endpoint{Enter}");
    expect(await within(list).findByRole("button", { name: /^Scores endpoint/ })).toBeInTheDocument();
    expect(db.calls.some((c) => c.method === "PATCH" && c.path.endsWith("/helper/sessions/h_1") && (c.body as { title?: string }).title === "Scores endpoint")).toBe(true);

    // delete a side chat: the page asks first (no browser box), Cancel keeps it
    await user.click(within(list).getByRole("button", { name: "More for Price helper" }));
    await user.click(within(list).getByRole("button", { name: "Delete" }));
    const ask = within(list).getByRole("alertdialog", { name: "Delete chat" });
    expect(ask).toHaveTextContent("Delete “Price helper”? Its messages are gone for good. Its worktree and the branch keel/helper/3 are deleted too.");
    await user.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(db.helper.sessions).toHaveLength(3);
    await user.click(within(list).getByRole("button", { name: "Delete" }));                 // its actions are still open
    await user.click(within(within(list).getByRole("alertdialog", { name: "Delete chat" })).getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(db.helper.sessions.map((s) => s.id)).toEqual(["h_1", "h_2"]));
    await waitFor(() => expect(within(list).queryByRole("button", { name: /^Price helper/ })).toBeNull());

    // delete the folder: its chat stays, with no folder
    await user.click(within(list).getByRole("button", { name: "More for the folder Security" }));
    await user.click(within(list).getByRole("button", { name: "Delete folder" }));
    expect(within(list).getByRole("alertdialog", { name: "Delete folder" })).toHaveTextContent("Its chats are kept, with no folder.");
    await user.click(within(within(list).getByRole("alertdialog", { name: "Delete folder" })).getByRole("button", { name: "Delete folder" }));
    await waitFor(() => expect(within(list).queryByRole("region", { name: "Folder Security" })).toBeNull());
    expect(within(list).getByRole("button", { name: /^Login bug/ })).toBeInTheDocument();
    expect(db.helper.sessions.find((s) => s.id === "h_2")!.folder).toBeNull();
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("opens over the conversation in the Code page's panel, and the open chat's folder shows next to its name", async () => {
    const user = userEvent.setup();
    db.helper.folders = [{ id: "hf_1", project: PID, name: "Auth", chats: 1, created_at: "", updated_at: "" }];
    db.helper.sessions = [chat("h_1", "Scores API"), chat("h_2", "Login bug", { folder: "hf_1" })];
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    const p = await panel();
    const btn = within(p).getByRole("button", { name: /^Chats/ });
    expect(btn).toHaveAttribute("aria-expanded", "false");
    await user.click(btn);
    const list = await chatList();
    expect(within(p).queryByRole("textbox", { name: "Ask KeelBot" })).toBeNull();          // the list covers the chat
    await user.click(within(list).getByRole("button", { name: /^Login bug/ }));
    expect(screen.queryByRole("navigation", { name: "KeelBot chats" })).toBeNull();
    expect(await within(p).findByText("About Login bug.")).toBeInTheDocument();
    expect(p.querySelector(".hp-cur")).toHaveTextContent("Auth /Login bug");
    // the bar's Delete asks in the page too
    await user.click(within(p).getByRole("button", { name: "Delete this chat" }));
    expect(within(p).getByRole("alertdialog", { name: "Delete chat" })).toHaveTextContent("Delete “Login bug”?");
    await user.keyboard("{Escape}");
    expect(within(p).queryByRole("alertdialog")).toBeNull();
    expect(db.helper.sessions).toHaveLength(2);
  });

  it("a new chat teaches how to use KeelBot", async () => {
    location.hash = "#/keelbot";
    render(<App />);
    const p = await panel();
    const guide = within(p).getByRole("note", { name: "How to use KeelBot" });
    expect(guide).toHaveTextContent("Ask is read only.");
    expect(guide).toHaveTextContent("Fix works while a flow waits at a gate.");
    expect(guide).toHaveTextContent("Side tries an idea in KeelBot's own copy of the project");
    expect(guide).toHaveTextContent("Type @ to point at a file, a symbol or a criterion.");
    expect(guide).toHaveTextContent("Type / for a command");
    expect(guide).toHaveTextContent(/I opens and closes KeelBot in the Code page/);
  });
});

describe("KeelBot's new answers: a number and its own sound", () => {
  it("counts an answer you did not see, plays KeelBot's sound, and clears it when you open the chat", async () => {
    const user = userEvent.setup();
    location.hash = "#/repo";
    localStorage.setItem("keel2.repo.helper", "true");
    render(<App />);
    const p = await panel();
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "Why is it slow?{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(1));
    const sid = db.helper.sessions[0].id;

    // you look at it: nothing to count, no sound
    answer(sid, "Because of the loop.");
    await within(p).findByText("Because of the loop.");
    expect(screen.queryByTestId("keelbot-unread")).toBeNull();
    expect(audioLog).toEqual([]);

    // the panel is closed: the next answer is new, on the menu and on the KeelBot button, with KeelBot's own notes
    await user.type(within(p).getByRole("textbox", { name: "Ask KeelBot" }), "And the fix?{Enter}");
    await waitFor(() => expect(turns()).toHaveLength(2));
    await user.click(within(p).getByRole("button", { name: "Close KeelBot" }));
    answer(sid, "Cache it.");
    expect(await screen.findByTestId("keelbot-unread")).toHaveTextContent("1");
    expect(screen.getByTestId("keelbot-unread")).toHaveAccessibleName("1 new KeelBot answer");
    expect(within(screen.getByRole("button", { name: "KeelBot" })).getByTestId("keelbot-unread-act")).toHaveTextContent("1");
    expect(audioLog).toEqual(KEELBOT_NOTES);
    expect(KEELBOT_NOTES).not.toEqual([660, 880]);                                  // not the notification chime
    expect(JSON.parse(localStorage.getItem("keel2.keelbot.unread")!)).toEqual({ [PID]: { [sid]: [`call-${sid}-3`] } });

    // opening the chat clears it
    await user.click(screen.getByRole("button", { name: "KeelBot" }));
    expect(await within(await panel()).findByText("Cache it.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("keelbot-unread")).toBeNull());
    expect(JSON.parse(localStorage.getItem("keel2.keelbot.unread")!)).toEqual({ [PID]: {} });
  });

  it("counts answers that finish on another page or in a hidden tab; a stopped one is not news; the switch turns the sound off", async () => {
    const user = userEvent.setup();
    db.helper.sessions = [chat("h_1", "Scores API"), chat("h_2", "Login bug")];
    localStorage.setItem(`keel2.helper.${PID}.session`, "h_1");
    location.hash = "#/keelbot";
    render(<App />);
    await panel();

    // another chat answers: counted on its row in the list, not on the open one
    answer("h_2", "Fixed the token check.");
    const list = await chatList();
    expect(await within(list).findByLabelText("1 new answer")).toBeInTheDocument();
    expect(screen.getByTestId("keelbot-unread")).toHaveTextContent("1");
    expect(audioLog).toEqual(KEELBOT_NOTES);

    // the tab is hidden: the open chat's answer counts too, and showing the tab again clears that one
    setHidden(true);
    answer("h_1", "It is in ScoreController.");
    await waitFor(() => expect(screen.getByTestId("keelbot-unread")).toHaveTextContent("2"));
    setHidden(false);
    await waitFor(() => expect(screen.getByTestId("keelbot-unread")).toHaveTextContent("1"));

    // on another page, with the sound off (Settings › This browser): counted, silent; a stopped answer is not counted
    await goTo("#/settings");
    const sw = await screen.findByRole("switch", { name: "KeelBot's sound when an answer is ready" });
    expect(sw).toBeChecked();
    await user.click(sw);
    expect(localStorage.getItem("keel2.keelbot.sound")).toBe("0");
    audioLog.length = 0;
    answer("h_1", "Stopped.", "stopped");
    answer("h_1", "Here is more.");
    await waitFor(() => expect(screen.getByTestId("keelbot-unread")).toHaveTextContent("2"));
    expect(audioLog).toEqual([]);

    // the notification settings have the same switch
    await user.click(screen.getAllByRole("button", { name: /^Notifications/ })[0]);
    expect(await screen.findByRole("switch", { name: "KeelBot's sound when an answer is ready" })).not.toBeChecked();
  });
});
