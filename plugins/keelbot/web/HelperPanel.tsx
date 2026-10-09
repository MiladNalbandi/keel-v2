// KeelBot in the Code page: a chat with an agent that reads this project and answers with file:line links,
// inside keel's rules. Ask mode changes nothing; Fix mode (while a flow waits at a gate) changes files inside the
// phase's rules, asks before a command that changes something, and commits through keel's Done; a side session works
// in its own worktree and branch, and ends as a task, a flow on that branch, or thrown away. Sessions are the
// engine's; each answer is one agent call, so its steps stream live (helper.step events) and its tokens count in the
// budget bar. ⌘I opens it from the Code page.
// v0.15.2 the chat stays as it was when you come back (the open chat, the text not sent yet, where it was scrolled to:
// chats.ts), the chat list has search, folders, rename and delete (ChatList.tsx), and a new answer you did not see is
// counted on KeelBot's menu entry and button, with its own sound (unread.tsx).

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  ApiError, errorParts, go, keyLabel, Markdown, mergeSteps, ModelPicker, modelLabel, ProviderIcon, provLabel, rankFiles, StepView, useApp,
  useLoad, useWide,
  type GraphHit, type HelperCommand, type HelperDone, type HelperMention, type HelperMessage, type HelperMode,
  type HelperSelection, type HelperSession, type JobStep, type Model,
} from "@keel/web-sdk";
import { CiCard, SlotCard, splitActions, StartCard, WorkflowCard } from "./Actions";
import { ChangesBox, DoneFailed, fixRequest, PermissionCard, SideBar } from "./FixParts";
import { kb } from "./keelbotApi";
import { fileLink, messageTokens, PREFILL_KEY, replaceTyping, sessionTokens, starters, typingAt, usageText, type Typing } from "./model";
import { ChatList, ConfirmRow } from "./ChatList";
import { dropDraft, getDraft, getListOpen, getNewMode, getScroll, saveListOpen, saveNewMode, saveScroll, setDraft } from "./chats";
import { markSeen, useKeelBotUnread, useSeen } from "./unread";

type Props = {
  pid: string;
  /** the file open in the editor (sent along, so "this file" means something) */
  openFile?: string | null;
  /** lines the person chose to ask about ("Ask about these lines" in the editor) */
  selection?: HelperSelection | null;
  onClearSelection?: () => void;
  onOpenFile: (path: string, line?: number) => void;
  /** Fix mode: open a changed file in the editor's Changes view */
  onOpenDiff?: (path: string) => void;
  onClose: () => void;
  /** grows each time the Code page wants the input focused (⌘I) */
  focusKey?: number;
  /** text another part of the page hands over (Code › Git: "Ask KeelBot to address the comments"); n changes each time */
  prefill?: { text: string; n: number } | null;
  /** panel: a column next to the code (Code page); page: KeelBot alone, one wide chat column (#/helper) */
  layout?: "panel" | "page";
};

const sidKey = (pid: string) => `keel2.helper.${pid}.session`;
const read = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const write = (k: string, v: string | null) => {
  try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); } catch { /* private window */ }
};

type Suggestion = { key: string; label: string; sub?: string; insert: string; mention?: HelperMention };

/** One answer's steps: the live ones while it runs, the stored ones on demand. */
function TurnSteps({ callId, live, running }: { callId: string; live: JobStep[]; running: boolean }) {
  const [open, setOpen] = useState(running);
  const [stored, setStored] = useState<JobStep[] | null>(null);
  useEffect(() => { if (running) setOpen(true); }, [running]);
  useEffect(() => {
    if (!open || running || stored) return;
    let live = true;
    kb.jobSteps(callId).then((r) => live && setStored(r.steps), () => live && setStored([]));
    return () => { live = false; };
  }, [open, running, stored, callId]);
  const steps = mergeSteps(stored ?? [], live).filter((s) => s.kind !== "answer");
  if (!running && !open) {
    return <button type="button" className="hp-steps-btn" onClick={() => setOpen(true)}>Show what it did</button>;
  }
  const shown = running ? steps.slice(-6) : steps;
  return (
    <div className="hp-steps" aria-label="What KeelBot did" aria-live={running ? "polite" : undefined}>
      {running && steps.length > shown.length && <p className="hp-steps-more">{steps.length - shown.length} earlier steps</p>}
      {shown.map((s) => <StepView key={s.n} s={s} idPrefix={`hp-${callId}`} />)}
      {running && <p className="hp-working"><span className="pg-spin" aria-hidden="true" /> Working…</p>}
      {!running && !steps.length && stored && <p className="hp-steps-more">No steps were recorded.</p>}
      {!running && <button type="button" className="hp-steps-btn" onClick={() => setOpen(false)}>Hide the steps</button>}
    </div>
  );
}

/** An answer: Markdown whose `file:line` chips open the editor, and KeelBot's buttons (start a flow, save a workflow). */
function Answer({ text, onOpen, pid, onAsk }: { text: string; onOpen: (path: string, line?: number) => void; pid: string; onAsk: (t: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    box.current?.querySelectorAll<HTMLElement>(".cite").forEach((el) => {
      if (!fileLink(el.textContent ?? "")) return;
      el.setAttribute("role", "link");
      el.setAttribute("tabindex", "0");
      el.title = `Open ${el.textContent} in the editor`;
    });
  }, [text]);
  const go = (target: EventTarget | null) => {
    const el = (target as HTMLElement | null)?.closest?.(".cite");
    const link = el ? fileLink(el.textContent ?? "") : null;
    if (link) onOpen(link.path, link.line);
    return !!link;
  };
  return (
    <div ref={box} className="hp-answer" onClick={(e) => go(e.target)}
      onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && go(e.target)) e.preventDefault(); }}>
      {splitActions(text).map((seg, i) => seg.kind === "text" ? <Markdown key={i} text={seg.text} breaks />
        : seg.kind === "start" ? <StartCard key={i} pid={pid} body={seg.body} />
          : seg.kind === "ci" ? <CiCard key={i} pid={pid} body={seg.body} />
            : seg.kind === "workflow" ? <WorkflowCard key={i} pid={pid} body={seg.body} onAsk={onAsk} />
              // a plugin's block (keel-query, keel-git): its card from the slot keelbot.card
              : <SlotCard key={i} pid={pid} block={{ kind: `keel-${seg.kind}`, body: seg.body }} />)}
    </div>
  );
}

/** v0.15.2 how to use KeelBot, in short and simple words: a new Ask chat shows it. */
function Guide() {
  return (
    <div className="hp-guide" role="note" aria-label="How to use KeelBot">
      <p className="hp-guide-h"><b>How to use KeelBot</b></p>
      <ul>
        <li><b>Ask</b> is read only. KeelBot reads the code and answers, with links to the lines. It changes nothing in Ask mode.</li>
        <li><b>Fix</b> works while a flow waits at a gate. KeelBot changes the files for that gate, inside keel's rules. You check
          each change, then press Done.</li>
        <li><b>Side</b> tries an idea in KeelBot's own copy of the project, on its own branch. Keep it, make it a task, or throw it away.</li>
        <li>Type <kbd>@</kbd> to point at a file, a symbol or a criterion.</li>
        <li>Type <kbd>/</kbd> for a command, for example <code>/explain</code> or <code>/plan</code>.</li>
        <li><kbd>{keyLabel("meta+i")}</kbd> opens and closes KeelBot in the Code page. Select lines in the code first to ask about them.</li>
        <li>Every chat is kept. <b>Chats</b> finds an old one, and puts it in a folder, renames or deletes it.</li>
      </ul>
    </div>
  );
}

function UserMessage({ m }: { m: HelperMessage }) {
  const sel = m.data.selection;
  return (
    <div className="hp-msg hp-user">
      <p className="hp-text">{m.text}</p>
      {(sel || m.data.mentions?.length) && (
        <div className="hp-chips">
          {sel && <span className="hp-chip">{sel.path}{sel.from ? `:${sel.from}${sel.to && sel.to !== sel.from ? `-${sel.to}` : ""}` : ""}</span>}
          {m.data.mentions?.map((x, i) => <span key={i} className="hp-chip">@{x.value}</span>)}
        </div>
      )}
    </div>
  );
}

export function HelperPanel({ pid, openFile, selection, onClearSelection, onOpenFile, onOpenDiff, onClose, focusKey = 0, layout = "panel", prefill }: Props) {
  const { recent, liveSteps, toast, tick } = useApp();
  const [sid, setSidState] = useState<string | null>(() => read(sidKey(pid)));
  const setSid = useCallback((v: string | null) => { setSidState(v); write(sidKey(pid), v); }, [pid]);
  const list = useLoad(`helper:${pid}:list`, () => kb.sessions(pid), { live: false });
  // v0.15.2 a chat is forgotten only when the server says it is gone (404), never because the server did not answer
  const gone = useRef<string | null>(null);
  const sess = useLoad(sid ? `helper:${pid}:${sid}` : null, () => kb.session(pid, sid!).catch((e: unknown) => {
    if (e instanceof ApiError && e.status === 404) gone.current = sid;
    throw e;
  }), { live: false });
  const folders = useLoad(`helper:${pid}:folders`, () => kb.folders(pid), { live: false });
  // v0.15.2 the chat list: a column on the KeelBot page of a wide screen, else it opens over the conversation (Chats)
  const wide = useWide();
  const listColumn = layout === "page" && wide;
  const [listOpenState, setListOpenState] = useState(() => getListOpen(pid));
  const listOpen = !listColumn && listOpenState;
  const setListOpen = (open: boolean) => { setListOpenState(open); saveListOpen(pid, open); };
  const [confirmDel, setConfirmDel] = useState(false);
  const { chats: newIn } = useKeelBotUnread(pid);
  // the conversation is on screen (not covered by the list): its new answers are seen
  useSeen(pid, sid, !listOpen);
  const cmds = useLoad(`helper:${pid}:commands`, () => kb.commands(pid), { live: false });
  const flow = useLoad(`helper:${pid}:flow`, () => kb.flow(pid), { live: false });
  const flowWaits = flow.data?.thread?.status === "waiting";
  // Fix needs a flow that waits at a gate and does not run read-only (the api refuses it otherwise)
  const readonlyRun = flow.data?.thread?.run_mode === "readonly";
  const fixable = flowWaits && !readonlyRun;
  const [newMode, setNewModeState] = useState<HelperMode>(() => getNewMode(pid));
  const setNewMode = (m: HelperMode) => { setNewModeState(m); saveNewMode(pid, m); };
  const [doneBusy, setDoneBusy] = useState(false);
  const [failed, setFailed] = useState<Extract<HelperDone, { ok: false }> | null>(null);
  // v0.15.2 the text not sent yet (and its @ mentions) is each chat's draft: kept when you leave and found when you come back
  const [text, setText] = useState(() => getDraft(pid, read(sidKey(pid))).text);
  const [caret, setCaret] = useState(0);
  const [mentions, setMentions] = useState<HelperMention[]>(() => getDraft(pid, read(sidKey(pid))).mentions);
  useEffect(() => setDraft(pid, sid, { text, mentions }), [pid, sid, text, mentions]);
  const [pending, setPending] = useState<{ call: string; n: number } | null>(null);
  const [sending, setSending] = useState(false);
  const [pick, setPick] = useState(0);
  const [files, setFiles] = useState<string[] | null>(null);
  const [symbols, setSymbols] = useState<GraphHit[]>([]);
  const [showModel, setShowModel] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  // v0.15.2 where the chat was scrolled to: put back when it shows again; at the bottom it follows new messages
  const stick = useRef(true);
  const restored = useRef<string | null | undefined>(undefined);
  const bodyRef = useCallback((el: HTMLDivElement | null) => {
    scroller.current = el;
    restored.current = undefined;
  }, []);

  // a session that is gone (deleted, another keel) is forgotten
  useEffect(() => {
    if (sess.error && sid && gone.current === sid) {
      markSeen(pid, sid);
      setSid(null);
    }
  }, [sess.error, sid, setSid, pid]);
  useEffect(() => { input.current?.focus(); }, [focusKey, sid]);
  useEffect(() => {
    if (prefill?.text) {
      setText(prefill.text);
      input.current?.focus();
    }
  }, [prefill?.n]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    // text another page handed over (Run › Jobs › Pipelines: "Ask KeelBot why"): once, into the input
    try {
      const handed = sessionStorage.getItem(PREFILL_KEY);
      if (handed) {
        sessionStorage.removeItem(PREFILL_KEY);
        setText(handed);
        input.current?.focus();
      }
    } catch { /* storage blocked: nothing was handed over */ }
  }, []);

  const s: HelperSession | null = sess.data ?? null;
  // v0.15.2 the open chat is still loading, or did not load (the server did not answer): it is not a new chat
  const loadingChat = !!sid && !s && !sess.error;
  const notReady = !!sid && !s;
  const messages = s?.messages ?? [];
  // a new chat kept as Fix while no flow waits any more is an Ask chat
  const mode: HelperMode = s?.mode ?? (newMode === "fix" && !fixable ? "ask" : newMode);
  const fix = mode === "fix";
  const side = mode === "side";
  const edits = fix || side;                 // a chat that changes files: its changes, Undo, Done / Keep
  const handed = side && !!s && !s.worktree;  // a side session handed over: its worktree is gone
  const changes = useLoad(sid && edits && !handed ? `helper:${pid}:${sid}:changes` : null, () => kb.changes(pid, sid!), { live: false });
  const handover = useLoad(sid && side && !handed ? `helper:${pid}:${sid}:handover` : null, () => kb.handover(pid, sid!), { live: false });
  // commands that wait for the person's OK (this project's; the cards show this chat's)
  const perms = useLoad(`helper:${pid}:perms`, () => kb.permissions(pid), { live: false });
  const asks = (perms.data ?? []).filter((q) => q.session === sid);
  // the running answer: the one this page started, else the one an earlier page started (helper.started event)
  const runningCall = useMemo(() => {
    if (pending) return pending.call;
    if (!s?.busy && s?.status !== "running") return null;
    const ev = [...recent].reverse().find((e) => e.thread_id === sid && e.type === "helper.started");
    return ev?.call_id ?? null;
  }, [pending, s, recent, sid]);

  // While an answer runs, the session is read again on every live tick (also when the event stream comes back after
  // the tab was hidden) and every 5 s, so a missed helper.finished never leaves the panel "working".
  useEffect(() => {
    if (runningCall) void sess.reload();
    if (runningCall && edits) void changes.reload();
  }, [tick]); // eslint-disable-line react-hooks/exhaustive-deps
  // a question came or went (here, in the Inbox, or another tab): read the cards again
  const askedAt = useMemo(() => [...recent].reverse().find((e) => e.type === "helper.permission" || e.type === "helper.permission.answered")?.at,
    [recent]);
  useEffect(() => { void perms.reload(); }, [askedAt]); // eslint-disable-line react-hooks/exhaustive-deps
  // the flow moves (a gate waits, is answered, the flow ends): Fix follows it. Read on every live tick, also the one
  // when the event stream comes back after the tab was hidden, but not while an answer streams its steps.
  useEffect(() => { if (tick && !runningCall) void flow.reload(); }, [tick]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!runningCall) return;
    const t = window.setInterval(() => void sess.reload(), 5000);
    return () => window.clearInterval(t);
  }, [runningCall]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (pending && s && !s.busy && s.status !== "running" && (s.messages ?? []).some((m) => m.n > pending.n)) {
      setPending(null);
      void list.reload();
    }
  }, [s, pending]); // eslint-disable-line react-hooks/exhaustive-deps

  // the answer is in: reload the session and the list (the budget bar reloads on the same live event)
  const finishedAt = useMemo(() => [...recent].reverse().find((e) => e.thread_id === sid && e.type === "helper.finished")?.at, [recent, sid]);
  useEffect(() => {
    if (!finishedAt) return;
    setPending(null);
    void sess.reload();
    void list.reload();
    void perms.reload();
    if (edits) void changes.reload();
  }, [finishedAt]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fix and side: a new message (an answer, a stop, Done's note), however the panel learnt of it, may come with changed
  // files, and a side session's kept commits
  useEffect(() => {
    if (!edits || !sid || handed) return;
    void changes.reload();
    if (side) void handover.reload();
  }, [messages.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // put the chat back where it was scrolled to, once its messages are on screen
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el || restored.current === sid || (sid && s?.id !== sid)) return;
    restored.current = sid;
    const saved = getScroll(pid, sid);
    stick.current = !saved || saved.bottom;
    el.scrollTop = stick.current ? el.scrollHeight : saved!.top;
  });
  // the place is written a moment after the scrolling stops, and at once when the panel goes away
  const scrollSave = useRef<{ sid: string | null; top: number; bottom: boolean; timer: number } | null>(null);
  const flushScroll = useCallback(() => {
    const p = scrollSave.current;
    if (!p) return;
    window.clearTimeout(p.timer);
    scrollSave.current = null;
    saveScroll(pid, p.sid, { top: p.top, bottom: p.bottom });
  }, [pid]);
  useEffect(() => flushScroll, [flushScroll]);
  const onScroll = () => {
    const el = scroller.current;
    if (!el || restored.current !== sid) return;
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    stick.current = bottom;
    if (scrollSave.current && scrollSave.current.sid !== sid) flushScroll();
    if (scrollSave.current) window.clearTimeout(scrollSave.current.timer);
    scrollSave.current = { sid, top: el.scrollTop, bottom, timer: window.setTimeout(flushScroll, 150) };
  };
  // keep the newest message in view, unless the person scrolled up to read
  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages.length, runningCall, runningCall ? liveSteps[runningCall]?.length : 0]);

  const typing: Typing = useMemo(() => typingAt(text, caret), [text, caret]);
  useEffect(() => {
    if (typing?.kind === "mention" && files === null) {
      kb.repoFiles(pid).then((r) => setFiles(r.files), () => setFiles([]));
    }
  }, [typing, files, pid]);
  useEffect(() => {
    if (typing?.kind !== "mention" || typing.query.length < 2) { setSymbols([]); return; }
    let live = true;
    const t = window.setTimeout(() => {
      kb.graphSearch(pid, typing.query).then((r) => live && setSymbols(r.results ?? []), () => live && setSymbols([]));
    }, 180);
    return () => { live = false; window.clearTimeout(t); };
  }, [typing, pid]);

  const acs = flow.data?.thread && ["running", "waiting"].includes(flow.data.thread.status) ? flow.data.thread.acs : [];
  const suggestions: Suggestion[] = useMemo(() => {
    if (!typing) return [];
    const q = typing.query.toLowerCase();
    if (typing.kind === "command") {
      return (cmds.data ?? []).filter((c: HelperCommand) => c.name.startsWith(q))
        .map((c) => ({ key: `c:${c.name}`, label: `/${c.name}`, sub: c.description + (c.source === "project" ? " (this project)" : ""), insert: c.name }));
    }
    const out: Suggestion[] = [];
    for (const a of acs.filter((x) => x.id.toLowerCase().includes(q)).slice(0, 4)) {
      out.push({ key: `a:${a.id}`, label: `@${a.id}`, sub: a.title, insert: a.id, mention: { kind: "ac", value: a.id } });
    }
    for (const h of symbols.slice(0, 6)) {
      out.push({ key: `s:${h.id}`, label: `@${h.name}`, sub: `${h.kind} · ${h.file}:${h.line}`, insert: h.name,
        mention: { kind: "symbol", value: h.name, file: h.file, line: h.line } });
    }
    for (const f of rankFiles(files ?? [], q, 8)) {
      out.push({ key: `f:${f.path}`, label: `@${f.path.split("/").pop()}`, sub: f.path, insert: f.path, mention: { kind: "file", value: f.path } });
    }
    return out.slice(0, 12);
  }, [typing, cmds.data, acs, symbols, files]);
  useEffect(() => setPick(0), [typing?.kind, typing?.query]);

  const choose = (sg: Suggestion) => {
    if (!typing) return;
    const next = replaceTyping(text, typing, caret, sg.insert);
    setText(next.text);
    setCaret(next.caret);
    if (sg.mention) setMentions((m) => (m.some((x) => x.value === sg.mention!.value) ? m : [...m, sg.mention!]));
    window.setTimeout(() => {
      input.current?.focus();
      input.current?.setSelectionRange(next.caret, next.caret);
    }, 0);
  };

  const ensureSession = async (): Promise<string> => {
    if (sid && s) return sid;
    const created = await kb.create(pid, { mode });
    setSid(created.id);          // the draft moves with the chat (the draft effect); the new-chat draft is done
    dropDraft(pid, null);
    void list.reload();
    return created.id;
  };

  const send = async (raw?: string) => {
    const body = (raw ?? text).trim();
    if (!body || sending || runningCall || handed) return;
    setSending(true);
    stick.current = true;
    try {
      const id = await ensureSession();
      const used = mentions.filter((m) => body.includes(`@${m.value}`));
      const started = await kb.turn(pid, id, {
        text: body, mentions: used.length ? used : undefined, selection: selection ?? undefined, open_file: openFile ?? undefined,
      });
      setPending({ call: started.call_id, n: started.n });
      setText("");
      setCaret(0);
      setMentions([]);
      onClearSelection?.();
      if (sid !== id) setSid(id);
      await sess.reload();
    } catch (e) {
      const p = errorParts(e);
      toast(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setSending(false);
    }
  };

  const stop = async () => {
    if (!sid) return;
    try {
      await kb.stop(pid, sid);
    } catch (e) {
      toast(`Not stopped: ${errorParts(e).message}`);
    }
  };

  /** Open a chat of the list (null: a new chat), with its draft. */
  const openChat = (id: string | null) => {
    if (id !== sid) {
      flushScroll();
      const d = getDraft(pid, id);
      setSid(id);
      setText(d.text);
      setMentions(d.mentions);
      setCaret(0);
      setPending(null);
      setFailed(null);
    }
    setConfirmDel(false);
    setListOpen(false);
  };

  const newChat = (m: HelperMode = mode) => {
    setNewMode(m === "fix" && !fixable ? "ask" : m);
    openChat(null);
    input.current?.focus();
  };

  // Ask, Fix and side sessions are different chats: switching starts a new one (the old one stays in the list)
  const switchMode = (m: HelperMode) => {
    if (m === mode) return;
    if (s || sid) newChat(m);
    else setNewMode(m);
  };

  const undo = async (path?: string) => {
    if (!sid) return;
    try {
      changes.setData(await kb.undo(pid, sid, path));
    } catch (e) {
      toast(`Not undone: ${errorParts(e).message}`);
    }
  };

  const done = async (message: string) => {
    if (!sid) return;
    setDoneBusy(true);
    setFailed(null);
    try {
      const res = await kb.done(pid, sid, message);
      const n = res.ok ? `${res.files.length} file${res.files.length === 1 ? "" : "s"}` : "";
      if (res.ok) toast(side ? `Kept on ${s?.branch}: keel committed ${n} (${res.sha.slice(0, 7)}).` : `keel committed ${n} (${res.sha.slice(0, 7)}).`);
      else setFailed(res);
      await Promise.all([changes.reload(), sess.reload(), side ? handover.reload() : null]);
    } catch (e) {
      const p = errorParts(e);
      toast(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setDoneBusy(false);
    }
  };

  // a side session ends as a task (its branch stays), as a flow on its branch, or thrown away
  const toTask = async () => {
    if (!sid) return;
    try {
      const t = await kb.toTask(pid, sid);
      toast(`Task created: ${t.title}. It names the branch ${s?.branch}.`);
    } catch (e) {
      const p = errorParts(e);
      toast(p.hint ? `${p.message} ${p.hint}` : p.message);
    }
  };

  const toFlow = async () => {
    if (!sid || !s?.branch) return;
    if (!window.confirm(`keel removes this side session's worktree, checks out ${s.branch} in the project folder and starts a change flow on it. Go on?`)) return;
    try {
      await kb.toFlow(pid, sid);
      toast(`A change flow started on ${s.branch}.`);
      await sess.reload();
      go("flow");
    } catch (e) {
      const p = errorParts(e);
      toast(p.hint ? `${p.message} ${p.hint}` : p.message);
    }
  };

  const throwAway = async () => {
    if (!window.confirm(`Throw this side session away? Its worktree and the branch ${s?.branch ?? ""} are deleted, with anything kept on it.`)) return;
    await remove();
  };

  const remove = async () => {
    if (!sid) return;
    try {
      await kb.remove(pid, sid);
      markSeen(pid, sid);
      dropDraft(pid, sid);
      newChat();
      void list.reload();
      void folders.reload();
    } catch (e) {
      toast(`Not deleted: ${errorParts(e).message}`);
    }
  };

  const toggleList = () => {
    if (!listOpen) {
      void list.reload();
      void folders.reload();
    }
    setConfirmDel(false);
    setListOpen(!listOpen);
  };
  // the list changed (a rename, a move, a folder): read both again
  const listChanged = () => {
    void list.reload();
    void folders.reload();
    if (sid) void sess.reload();
  };
  // the open chat was deleted from the list: a new chat, and the list stays open
  const chatDeleted = (id: string) => {
    if (id !== sid) return;
    const open = listOpen;
    openChat(null);
    if (open) setListOpen(true);
  };

  const setModel = async (m: Model) => {
    try {
      const id = await ensureSession();
      await kb.patch(pid, id, { model: m });
      await sess.reload();
    } catch (e) {
      toast(`Not changed: ${errorParts(e).message}`);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (suggestions.length && typing) {
      if (e.key === "ArrowDown") { e.preventDefault(); setPick((p) => (p + 1) % suggestions.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setPick((p) => (p - 1 + suggestions.length) % suggestions.length); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); choose(suggestions[pick]); return; }
      if (e.key === "Escape") { e.preventDefault(); setCaret(-1); return; }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void send();
  };

  const busy = !!runningCall || sending;
  const model = s?.model;
  const thread = flow.data?.thread ?? null;
  // Fix mode works on the flow that waited when the chat began; once that flow moved on, Done cannot commit here
  const moved = fix && !!s?.thread_id && !(thread?.thread_id === s.thread_id && thread.status === "waiting");
  // the engine picks it when the chat starts: at a gate, the phase of the work under review (the AC gate: green)
  const phase = s?.phase && s.phase !== "none" ? s.phase : null;
  const noHook = fix && !!model && ["codex", "copilot"].includes(model.provider);
  const chatFolder = s?.folder ? (folders.data ?? []).find((f) => f.id === s.folder)?.name : undefined;
  // new answers in the other chats (the open one is seen)
  const newElsewhere = Object.entries(newIn).reduce((a, [id, n]) => a + (id === sid ? 0 : n), 0);
  const chatList = (
    <ChatList pid={pid} chats={list.data ?? []} folders={folders.data ?? []} current={sid} unread={newIn}
      onOpen={(id) => openChat(id)} onNew={listColumn ? () => newChat() : undefined} onChanged={listChanged} onDeleted={chatDeleted}
      onClose={listColumn ? undefined : () => setListOpen(false)} />
  );

  return (
    <>
    {listColumn && <div className="hp-list-col">{chatList}</div>}
    <aside className={`hp${layout === "page" ? " page" : ""}`} aria-label="KeelBot"
      onKeyDown={(e) => { if (e.key === "Escape" && listOpen) { e.preventDefault(); setListOpen(false); } }}>
      <header className="hp-head">
        <div className="hp-title">
          <b>KeelBot</b>
          <div className="hp-modes" role="group" aria-label="Mode">
            <button type="button" aria-pressed={mode === "ask"} onClick={() => switchMode("ask")}
              title="Ask: KeelBot reads and answers; it changes no file">Ask</button>
            <button type="button" aria-pressed={fix} disabled={!fix && !fixable} onClick={() => switchMode("fix")}
              title={fix || fixable ? "Fix: KeelBot changes files at this gate, inside the phase's rules"
                : readonlyRun ? "This flow runs read-only: change its run mode on the Flow page to let KeelBot edit"
                  : "Fix works while a flow waits at a gate"}>Fix</button>
            <button type="button" aria-pressed={side} onClick={() => switchMode("side")}
              title="Side session: KeelBot changes files in its own copy of the project (a git worktree on its own branch)">Side</button>
          </div>
          {fix
            ? <span className="hp-mode fix" title="Fix mode: keel's rules of this phase apply">Fix{phase ? ` · ${phase}` : ""}</span>
            : side
              ? <span className="hp-mode side" title={s?.branch ? `Branch ${s.branch}` : "Its own worktree and branch"}>Side{s?.branch ? ` · ${s.branch.split("/").pop()}` : ""}</span>
              : <span className="hp-mode" title="Ask mode: KeelBot reads and answers; it changes no file">Ask · read only</span>}
        </div>
        <div className="hp-tools">
          <button type="button" className="hp-tb" onClick={() => setShowModel((v) => !v)} aria-expanded={showModel}
            title="The model that answers" aria-label="Model">
            {model && <ProviderIcon provider={model.provider} size={14} />}
            {model ? `${provLabel(model.provider)} ${modelLabel(model)}` : "Model"}
          </button>
          {!listColumn && <button type="button" className="hp-tb" onClick={() => newChat()} title="Start a new chat" aria-label="New chat">New</button>}
          {layout === "panel"
            ? <>
              <button type="button" className="hp-tb" onClick={() => go("helper")} title="Only KeelBot, on a page of its own"
                aria-label="Open KeelBot full screen">⤢</button>
              <button type="button" className="hp-tb hp-x" onClick={onClose} title="Close KeelBot (⌘I)" aria-label="Close KeelBot">×</button>
            </>
            : <button type="button" className="hp-tb" onClick={onClose} title="The Code page, with the code and KeelBot side by side"
              aria-label="Back to the code">Back to the code</button>}
        </div>
      </header>
      {showModel && (
        <div className="hp-model">
          <ModelPicker id="hp-model" value={model ?? { provider: "claude", mode: "subscription", model: "sonnet" }}
            onChange={(m) => void setModel(m)} />
          <p className="hp-hint">Claude and Codex continue their own session; other models get the conversation so far.</p>
        </div>
      )}
      <div className="hp-sessions">
        {!listColumn && (
          <button type="button" className={`hp-tb hp-chats-btn${listOpen ? " on" : ""}`} aria-expanded={listOpen} aria-controls="hp-list"
            onClick={toggleList} title="All your chats: search, folders, rename, delete">
            <span aria-hidden="true">☰</span> Chats{newElsewhere > 0 && <span className="hp-new-n" aria-label={`, ${newElsewhere} new`}>{newElsewhere}</span>}
          </button>
        )}
        <span className="hp-cur" title={s ? `${chatFolder ? `${chatFolder} / ` : ""}${s.title}` : "A new chat: it is kept when you send the first message"}>
          {chatFolder && <span className="hp-cur-f">{chatFolder} /</span>}
          <span className="hp-cur-t">{s?.title ?? (loadingChat ? "…" : "New chat")}</span>
        </span>
        {s && <span className="hp-usage" title="What this chat used">{usageText(sessionTokens(s), s.cost_usd)}</span>}
        {s && !listOpen && (
          <button type="button" className="hp-tb" onClick={() => setConfirmDel((v) => !v)} aria-label="Delete this chat" title="Delete this chat"
            aria-expanded={confirmDel}>Delete</button>
        )}
      </div>
      {confirmDel && s && !listOpen && (
        <ConfirmRow yes="Delete chat" onYes={() => { setConfirmDel(false); void remove(); }} onNo={() => setConfirmDel(false)}
          text={`Delete “${s.title}”? Its messages are gone for good.${side && s.worktree ? ` Its worktree and the branch ${s.branch ?? ""} are deleted too.` : ""}`} />
      )}
      {listOpen && chatList}
      {!listOpen && <>

      {fix && (
        <div className={`hp-fixbar${moved ? " moved" : ""}`}>
          {moved
            ? <p><b>The flow moved on.</b> This chat fixed at a gate that is no longer waiting, so Done cannot commit here. Undo what you do not want, or start a new chat.</p>
            : <p>Fixing at the gate <b>{thread?.title ?? "…"}</b>{phase ? <> · rules of the <b>{phase}</b> phase</> : null}. keel's
              rules for the work under review apply; a command that changes something waits for your OK.</p>}
          {noHook && <p className="hp-hint">Codex and Copilot run commands in their own sandbox, so keel cannot ask you first. keel's diff guard still checks every file.</p>}
        </div>
      )}

      {side && (
        <SideBar s={s} handover={handover.data} busy={busy || doneBusy} onTask={() => void toTask()} onFlow={() => void toFlow()}
          onThrow={() => void throwAway()} />
      )}

      <div ref={bodyRef} className="hp-body" role="log" aria-label="Conversation" aria-live="polite" onScroll={onScroll}>
        {loadingChat && <p className="hp-hint">Loading the chat…</p>}
        {notReady && sess.error && (
          <p className="hp-err">This chat did not load: {sess.error.message}{" "}
            <button type="button" className="hp-tb" onClick={() => void sess.reload()}>Try again</button></p>
        )}
        {!messages.length && !runningCall && !notReady && side && (
          <div className="hp-empty">
            <p>Try an idea without touching the project folder. KeelBot edits its own copy, you see every changed file
              below, and Keep runs the checks and commits on the side branch. Then make it a task, start a flow on the
              branch, or throw it away.</p>
            <ul className="hp-starters">
              {["Sketch the change in as few files as you can, then run the tests", "/plan"].map((q) => (
                <li key={q}><button type="button" onClick={() => void send(q)} disabled={busy}>{q}</button></li>
              ))}
            </ul>
          </div>
        )}
        {!messages.length && !runningCall && !notReady && fix && (
          <div className="hp-empty">
            <p>Tell KeelBot what to change for this gate. It edits the files here, inside keel's rules for the work under review.
              You see every changed file below, can undo it, and Done runs the checks and lets keel commit.</p>
            <ul className="hp-starters">
              {["/gate", "Make the change the reviewer asked for, then run the tests"].map((q) => (
                <li key={q}><button type="button" onClick={() => void send(q)} disabled={busy}>{q}</button></li>
              ))}
            </ul>
          </div>
        )}
        {!messages.length && !runningCall && !notReady && mode === "ask" && (
          <div className="hp-empty">
            <Guide />
            <p className="hp-hint">Try one:</p>
            <ul className="hp-starters">
              {starters({ flowWaits, openFile }).map((q) => (
                <li key={q}><button type="button" onClick={() => void send(q)} disabled={busy}>{q}</button></li>
              ))}
            </ul>
          </div>
        )}
        {messages.map((m) => m.role === "user" ? <UserMessage key={m.n} m={m} /> : (
          <div key={m.n} className={`hp-msg ${m.role === "note" ? "hp-note" : "hp-help"}`}>
            {m.role === "note" ? <p className="hp-text">{m.data.status === "stopped" ? "Stopped." : m.text}</p> : <Answer text={m.text} onOpen={onOpenFile} pid={pid} onAsk={(t) => void send(t)} />}
            {m.call_id && <TurnSteps callId={m.call_id} live={liveSteps[m.call_id] ?? []} running={false} />}
            {m.role === "helper" && (
              <p className="hp-meta">{m.data.model ? `${provLabel(m.data.provider)} ${m.data.model} · ` : ""}{usageText(messageTokens(m), m.data.cost_usd ?? 0)}
                {m.data.ms ? ` · ${(m.data.ms / 1000).toFixed(1)} s` : ""}</p>
            )}
          </div>
        ))}
        {runningCall && !messages.some((m) => m.call_id === runningCall) && (
          <div className="hp-msg hp-help hp-live">
            <TurnSteps callId={runningCall} live={liveSteps[runningCall] ?? []} running />
          </div>
        )}
        {asks.map((q) => <PermissionCard key={q.id} pid={pid} q={q} onAnswered={() => void perms.reload()} />)}
        {edits && !!changes.data?.length && (
          <ChangesBox changes={changes.data} busy={doneBusy || busy} title={s?.title ?? "the chat's title"}
            doneLabel={side ? "Keep: run the checks and commit on the branch" : undefined}
            onOpen={side ? undefined : (p) => (onOpenDiff ? onOpenDiff(p) : onOpenFile(p))} onUndo={(p) => void undo(p)} onDone={(m) => void done(m)} />
        )}
        {failed && (
          <DoneFailed res={failed} onClose={() => setFailed(null)}
            onAskFix={() => { const t = fixRequest(failed); setFailed(null); void send(t); }} />
        )}
      </div>

      <form className="hp-compose" onSubmit={submit}>
        {(selection || openFile) && (
          <div className="hp-chips">
            {selection && (
              <span className="hp-chip on">
                {selection.path}{selection.from ? `:${selection.from}${selection.to && selection.to !== selection.from ? `-${selection.to}` : ""}` : ""}
                <button type="button" aria-label="Do not send the selected lines" onClick={() => onClearSelection?.()}>×</button>
              </span>
            )}
            {!selection && openFile && <span className="hp-chip" title="The file open in the editor goes along">{openFile.split("/").pop()}</span>}
          </div>
        )}
        {typing && suggestions.length > 0 && caret >= 0 && (
          <ul className="hp-suggest" role="listbox" aria-label={typing.kind === "command" ? "Commands" : "Mentions"}>
            {suggestions.map((sg, i) => (
              <li key={sg.key} role="option" aria-selected={i === pick} className={i === pick ? "on" : ""}
                onMouseDown={(e) => { e.preventDefault(); choose(sg); }}>
                <span className="mono">{sg.label}</span>{sg.sub && <span className="hp-sub">{sg.sub}</span>}
              </li>
            ))}
          </ul>
        )}
        <textarea ref={input} id="hp-input" rows={2} value={text} aria-label="Ask KeelBot" disabled={handed}
          placeholder={handed ? "This side session was handed over: start a new chat" : busy ? "KeelBot is answering…"
            : fix ? "Tell KeelBot what to change…  (@ files, symbols · / commands)"
              : side ? "Tell KeelBot what to try…  (@ files, symbols · / commands)" : "Ask about this project…  (@ files, symbols · / commands)"}
          onChange={(e) => { setText(e.target.value); setCaret(e.target.selectionStart ?? e.target.value.length); }}
          onKeyUp={(e) => setCaret((e.target as HTMLTextAreaElement).selectionStart ?? 0)}
          onClick={(e) => setCaret((e.target as HTMLTextAreaElement).selectionStart ?? 0)}
          onKeyDown={onKey} />
        <div className="hp-send">
          <span className="hp-hint">Enter sends · Shift+Enter for a new line</span>
          {runningCall
            ? <button type="button" className="btn sm" onClick={() => void stop()}>Stop</button>
            : <button type="submit" className="btn sm primary" disabled={!text.trim() || sending}>{sending ? "Sending…" : "Send"}</button>}
        </div>
      </form>
      </>}
    </aside>
    </>
  );
}
