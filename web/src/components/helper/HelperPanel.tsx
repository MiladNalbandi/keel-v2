// keel's Helper in the Repo page: a chat with an agent that reads this project and answers with file:line links,
// inside keel's rules (Ask mode changes nothing). Sessions are the engine's; each answer is one agent call, so its
// steps stream live (helper.step events) and its tokens count in the budget bar. ⌘I opens it from the Repo page.

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  api, errorParts, type GraphHit, type HelperCommand, type HelperMention, type HelperMessage, type HelperSelection,
  type HelperSession, type JobStep, type Model,
} from "../../api";
import { modelLabel, provLabel } from "../../format";
import { rankFiles } from "../../pages/repo/model";
import { useApp, useLoad } from "../../state";
import { Markdown } from "../Markdown";
import { ModelPicker } from "../ModelPicker";
import { mergeSteps } from "../StepFeed";
import { StepView } from "../StepView";
import { fileLink, messageTokens, replaceTyping, sessionTokens, starters, typingAt, usageText, type Typing } from "./model";

type Props = {
  pid: string;
  /** the file open in the editor (sent along, so "this file" means something) */
  openFile?: string | null;
  /** lines the person chose to ask about ("Ask about these lines" in the editor) */
  selection?: HelperSelection | null;
  onClearSelection?: () => void;
  onOpenFile: (path: string, line?: number) => void;
  onClose: () => void;
  /** grows each time the Repo page wants the input focused (⌘I) */
  focusKey?: number;
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
    api.jobSteps(callId).then((r) => live && setStored(r.steps), () => live && setStored([]));
    return () => { live = false; };
  }, [open, running, stored, callId]);
  const steps = mergeSteps(stored ?? [], live).filter((s) => s.kind !== "answer");
  if (!running && !open) {
    return <button type="button" className="hp-steps-btn" onClick={() => setOpen(true)}>Show what it did</button>;
  }
  const shown = running ? steps.slice(-6) : steps;
  return (
    <div className="hp-steps" aria-label="What the Helper did" aria-live={running ? "polite" : undefined}>
      {running && steps.length > shown.length && <p className="hp-steps-more">{steps.length - shown.length} earlier steps</p>}
      {shown.map((s) => <StepView key={s.n} s={s} idPrefix={`hp-${callId}`} />)}
      {running && <p className="hp-working"><span className="pg-spin" aria-hidden="true" /> Working…</p>}
      {!running && !steps.length && stored && <p className="hp-steps-more">No steps were recorded.</p>}
      {!running && <button type="button" className="hp-steps-btn" onClick={() => setOpen(false)}>Hide the steps</button>}
    </div>
  );
}

/** An answer: Markdown whose `file:line` chips open the editor. */
function Answer({ text, onOpen }: { text: string; onOpen: (path: string, line?: number) => void }) {
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
      <Markdown text={text} breaks />
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

export function HelperPanel({ pid, openFile, selection, onClearSelection, onOpenFile, onClose, focusKey = 0 }: Props) {
  const { recent, liveSteps, toast, tick } = useApp();
  const [sid, setSidState] = useState<string | null>(() => read(sidKey(pid)));
  const setSid = useCallback((v: string | null) => { setSidState(v); write(sidKey(pid), v); }, [pid]);
  const list = useLoad(`helper:${pid}:list`, () => api.helperSessions(pid), { live: false });
  const sess = useLoad(sid ? `helper:${pid}:${sid}` : null, () => api.helperSession(pid, sid!), { live: false });
  const cmds = useLoad(`helper:${pid}:commands`, () => api.helperCommands(pid), { live: false });
  const flow = useLoad(`helper:${pid}:flow`, () => api.flow(pid), { live: false });
  const [text, setText] = useState("");
  const [caret, setCaret] = useState(0);
  const [mentions, setMentions] = useState<HelperMention[]>([]);
  const [pending, setPending] = useState<{ call: string; n: number } | null>(null);
  const [sending, setSending] = useState(false);
  const [pick, setPick] = useState(0);
  const [files, setFiles] = useState<string[] | null>(null);
  const [symbols, setSymbols] = useState<GraphHit[]>([]);
  const [showModel, setShowModel] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const scroller = useRef<HTMLDivElement>(null);

  // a session that is gone (deleted, another keel) is forgotten
  useEffect(() => { if (sess.error && sid) setSid(null); }, [sess.error, sid, setSid]);
  useEffect(() => { input.current?.focus(); }, [focusKey, sid]);

  const s: HelperSession | null = sess.data ?? null;
  const messages = s?.messages ?? [];
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
  }, [tick]); // eslint-disable-line react-hooks/exhaustive-deps
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
  }, [finishedAt]); // eslint-disable-line react-hooks/exhaustive-deps

  // keep the newest message in view
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, runningCall, runningCall ? liveSteps[runningCall]?.length : 0]);

  const typing: Typing = useMemo(() => typingAt(text, caret), [text, caret]);
  useEffect(() => {
    if (typing?.kind === "mention" && files === null) {
      api.repoFiles(pid).then((r) => setFiles(r.files), () => setFiles([]));
    }
  }, [typing, files, pid]);
  useEffect(() => {
    if (typing?.kind !== "mention" || typing.query.length < 2) { setSymbols([]); return; }
    let live = true;
    const t = window.setTimeout(() => {
      api.graphSearch(pid, typing.query).then((r) => live && setSymbols(r.results ?? []), () => live && setSymbols([]));
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
    const created = await api.helperCreate(pid, { mode: "ask" });
    setSid(created.id);
    void list.reload();
    return created.id;
  };

  const send = async (raw?: string) => {
    const body = (raw ?? text).trim();
    if (!body || sending || runningCall) return;
    setSending(true);
    try {
      const id = await ensureSession();
      const used = mentions.filter((m) => body.includes(`@${m.value}`));
      const started = await api.helperTurn(pid, id, {
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
      await api.helperStop(pid, sid);
    } catch (e) {
      toast(`Not stopped: ${errorParts(e).message}`);
    }
  };

  const newChat = () => {
    setSid(null);
    setPending(null);
    setText("");
    input.current?.focus();
  };

  const remove = async () => {
    if (!sid) return;
    try {
      await api.helperDelete(pid, sid);
      newChat();
      void list.reload();
    } catch (e) {
      toast(`Not deleted: ${errorParts(e).message}`);
    }
  };

  const setModel = async (m: Model) => {
    try {
      const id = await ensureSession();
      await api.helperPatch(pid, id, { model: m });
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
  const flowWaits = flow.data?.thread?.status === "waiting";
  const model = s?.model;

  return (
    <aside className="hp" aria-label="Helper">
      <header className="hp-head">
        <div className="hp-title">
          <b>Helper</b>
          <span className="hp-mode" title="Ask mode: the Helper reads and answers; it changes no file">Ask · read only</span>
        </div>
        <div className="hp-tools">
          <button type="button" className="hp-tb" onClick={() => setShowModel((v) => !v)} aria-expanded={showModel}
            title="The model that answers" aria-label="Model">
            {model ? `${provLabel(model.provider)} ${modelLabel(model)}` : "Model"}
          </button>
          <button type="button" className="hp-tb" onClick={newChat} title="Start a new chat" aria-label="New chat">New</button>
          <button type="button" className="hp-tb hp-x" onClick={onClose} title="Close the Helper (⌘I)" aria-label="Close the Helper">×</button>
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
        <label className="hp-sr" htmlFor="hp-session">Chat</label>
        <select id="hp-session" value={sid ?? ""} onChange={(e) => setSid(e.target.value || null)}>
          <option value="">New chat</option>
          {(list.data ?? []).map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}
        </select>
        {s && <span className="hp-usage" title="What this chat used">{usageText(sessionTokens(s), s.cost_usd)}</span>}
        {s && <button type="button" className="hp-tb" onClick={() => void remove()} aria-label="Delete this chat" title="Delete this chat">Delete</button>}
      </div>

      <div ref={scroller} className="hp-body" role="log" aria-label="Conversation" aria-live="polite">
        {!messages.length && !runningCall && (
          <div className="hp-empty">
            <p>Ask about this project. The Helper reads the code, the knowledge pages, the map and the code graph, and links
              every answer to the lines. It changes nothing in Ask mode.</p>
            <ul className="hp-starters">
              {starters({ flowWaits, openFile }).map((q) => (
                <li key={q}><button type="button" onClick={() => void send(q)} disabled={busy}>{q}</button></li>
              ))}
            </ul>
            <p className="hp-hint">Type <kbd>@</kbd> for a file, a symbol or a criterion, and <kbd>/</kbd> for a command.</p>
          </div>
        )}
        {messages.map((m) => m.role === "user" ? <UserMessage key={m.n} m={m} /> : (
          <div key={m.n} className={`hp-msg ${m.role === "note" ? "hp-note" : "hp-help"}`}>
            {m.role === "note" ? <p className="hp-text">{m.data.status === "stopped" ? "Stopped." : m.text}</p> : <Answer text={m.text} onOpen={onOpenFile} />}
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
        <textarea ref={input} id="hp-input" rows={2} value={text} aria-label="Ask the Helper"
          placeholder={busy ? "The Helper is answering…" : "Ask about this project…  (@ files, symbols · / commands)"}
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
    </aside>
  );
}
