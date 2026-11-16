// keel 0.29 the Flow page with a connected session (thread.runner "session"): the person's own Claude Code or OpenCode
// does the harness flow's plan and build. The feed shows who it is (client · connected since · last seen), each work
// order as a turn ("keel → session: plan this"), the session's todo list live, and what it reported; when the session
// is gone (not seen for 10 minutes), the three ways on: keep waiting, run this step on keel's harness, stop the flow.

import { useState } from "react";
import { api, errorParts, type SessionTurn, type ThreadState, type Workflow } from "../../api";
import { Markdown } from "../../components/Markdown";
import { SessionTodos, seenWords, sinceWords } from "../../components/Session";
import { ErrorBox, Pill } from "../../components/ui";
import { clock } from "../../format";
import { useApp } from "../../state";
import type { GateSaid } from "./Gate";

export const SAID: Record<string, string> = {
  plan: "keel → session: plan this", build: "keel → session: build the plan", fix: "keel → session: fix the failing tests",
};

/** A flow whose waiting pause is a work order for the connected session (not a question for the person). */
export const waitsForSession = (t: ThreadState) => t.status === "waiting" && t.waiting?.kind === "session";

/** The flow's status pill words when it waits for the session, not for you. */
export function sessionPill(t: ThreadState): ["run" | "warn", string] | null {
  if (!waitsForSession(t)) return null;
  return t.session?.gone || t.waiting?.gone ? ["warn", "session gone"] : ["run", "waits for the session"];
}

/** "Connected session · claude-code · connected since 10:02 · last seen 12 s ago". */
export function SessionLine({ thread, compact }: { thread: ThreadState; compact?: boolean }) {
  const s = thread.session;
  if (!s) return null;
  const p = s.session;
  const gone = !!(s.gone || thread.waiting?.gone);
  const tone = gone ? "is-gone" : s.connected ? "is-on" : "is-off";
  return (
    <div className={`sess-line ${tone}${compact ? " compact" : ""}`} role="status" aria-label="Connected session" data-testid="session-line">
      <span className="sess-dot" aria-hidden="true" />
      <span className="sess-line-t">
        <b>Connected session</b>
        <span className="mono">{s.client ?? p?.client ?? "a session"}</span>
        {p && <span>connected {sinceWords(p.connected_since)}</span>}
        {p && <span>{gone ? `not seen for ${Math.floor(p.seen_ago_s / 60)} min` : seenWords(p)}</span>}
        {!p && <span>not connected now</span>}
      </span>
    </div>
  );
}

/** One finished turn of the session in the feed: the work order it got, what it reported, its list. */
export function SessionTurnItem({ turn, name }: { turn: SessionTurn; name: string }) {
  const [open, setOpen] = useState(false);
  const todos = turn.todos ?? [];
  return (
    <li className="rdfl-turn sess-turn" data-step={turn.step}>
      <span className="rdfl-av k-session" aria-hidden="true">↗</span>
      <div className="rdfl-turn-b">
        <div className="rdfl-turn-h">
          <b>{turn.by}</b>
          <span>connected session</span>
          <span>{name}</span>
          <span className="num">{clock(turn.at, false)}</span>
          {turn.status === "blocked" && <Pill tone="warn">blocked</Pill>}
          {turn.findings ? <Pill tone="warn">{turn.findings} check finding{turn.findings === 1 ? "" : "s"}</Pill> : null}
        </div>
        <span className="sess-order-said">{SAID[turn.kind] ?? turn.kind}</span>
        {turn.summary ? <div className="rdfl-text md"><Markdown text={turn.summary} breaks /></div> : <p className="rdfl-say sub">No summary.</p>}
        {todos.length > 0 && (
          <>
            <button type="button" className="rdfl-more" aria-expanded={open} onClick={() => setOpen(!open)}>
              {open ? "Hide its list" : `Show its list (${todos.filter((t) => t.status === "done").length} of ${todos.length} done)`}
            </button>
            {open && <SessionTodos todos={todos} />}
          </>
        )}
      </div>
    </li>
  );
}

/** The work order that waits, as a turn at the end of the feed: what keel sent, the session's todo list live, and the
 *  ways on (keep waiting, keel's harness for this step, stop). */
export function SessionWaitCard({ thread, workflow, onDone, onDecided }: {
  thread: ThreadState; workflow: Workflow; onDone: () => Promise<void>; onDecided?: (said: GateSaid) => void;
}) {
  const { toast, reloadProjects } = useApp();
  const w = thread.waiting!;
  const s = thread.session;
  const o = w.order ?? s?.order;
  const gone = !!(w.gone || s?.gone);
  const step = workflow.steps.find((x) => x.id === w.step);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [told, setTold] = useState(false);
  const todos = s?.todos ?? [];
  const send = async (choice: "wait" | "harness" | "stop", label: string) => {
    setBusy(choice);
    setErr(null);
    try {
      await api.resume(thread.thread_id, choice === "stop" ? "reject" : "approve", undefined,
        { choice, ...(w.id ? { question_id: w.id } : {}) });
      toast(choice === "wait" ? "keel keeps waiting for the connected session." : choice === "harness"
        ? "This step runs on keel's harness now (your API key)." : "The flow is stopped.");
      onDecided?.({ decision: choice === "stop" ? "reject" : "approve", label, title: w.title, at: Date.now() });
      await Promise.all([onDone(), reloadProjects()]);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(null);
    }
  };
  return (
    <section className={`sess-card${gone ? " is-gone" : ""}`} aria-label="Waits for the connected session" data-testid="session-wait">
      <div className="sess-card-h">
        <span className="rdfl-av k-session" aria-hidden="true">↗</span>
        <div className="sess-card-t">
          <span className="sess-order-said">{o?.said ?? SAID[o?.kind ?? ""] ?? w.title}</span>
          <span className="sub">
            {step?.name ?? w.step}{o?.published_at ? ` · sent ${clock(o.published_at, false)}` : ""} · {gone ? "the session is gone" : "waits for the connected session"} · no tokens used
          </span>
        </div>
        {gone ? <Pill tone="warn">session gone</Pill> : <span className="rdfl-working">waiting</span>}
      </div>
      {gone && (
        <div className="sess-gone" role="alert">
          <b>The connected session is gone.</b> No session was seen for 10 minutes or more. Keep waiting (the work order stays
          for it), run this one step on keel's harness (your API key, tokens are used), or stop the flow. The Inbox asks the same.
        </div>
      )}
      {todos.length > 0 ? <SessionTodos todos={todos} label="The session's todo list, live" />
        : <p className="sub sess-none">{gone ? "No todo list came from the session." : "The session's todo list shows here as it works."}</p>}
      {o && (
        <>
          <button type="button" className="rdfl-more" aria-expanded={told} onClick={() => setTold(!told)}>
            {told ? "Hide the work order" : "Show the work order (what the session was told)"}
          </button>
          {told && (
            <div className="sess-order" data-testid="session-order">
              {o.root && <p className="sub">Folder: <span className="mono">{o.root}</span>{o.branch ? <> · branch <span className="mono">{o.branch}</span></> : null}</p>}
              {o.failure && <><b>keel's test run failed</b><pre className="hs-pre">{o.failure}</pre></>}
              {o.notes && <><b>Your notes</b><pre className="hs-pre">{o.notes}</pre></>}
              {o.plan && <><b>The approved plan</b><div className="md"><Markdown text={o.plan} /></div></>}
              {o.instructions && <><b>Instructions</b><pre className="hs-pre">{o.instructions}</pre></>}
            </div>
          )}
        </>
      )}
      <p className="sub sess-honest">The session runs its own tools, so keel cannot check them before they run. keel checks the change after
        this step, runs the tests and makes the commits.</p>
      {err && <ErrorBox error={err} />}
      <div className="row sess-actions">
        {gone && <button type="button" className="btn primary" disabled={busy !== null} onClick={() => void send("wait", "Keep waiting")}>
          {busy === "wait" ? "…" : "Keep waiting"}</button>}
        <button type="button" className={gone ? "btn" : "btn sm ghost"} disabled={busy !== null} onClick={() => void send("harness", "Run this step on keel's harness")}>
          {busy === "harness" ? "Starting…" : "Run this step on keel's harness"}</button>
        <button type="button" className={gone ? "btn" : "btn sm ghost"} disabled={busy !== null} onClick={() => void send("stop", "Stop the flow")}>
          {busy === "stop" ? "Stopping…" : "Stop the flow"}</button>
      </div>
    </section>
  );
}

/** Details › Now while the flow waits for the session: the line, its live list, and the way to the card. */
export function SessionNow({ thread, onJump }: { thread: ThreadState; onJump?: () => void }) {
  const waits = waitsForSession(thread);
  const gone = !!(thread.session?.gone || thread.waiting?.gone);
  return (
    <div className="interrupt fl-waits sess-now" data-testid="session-now">
      {waits && <b><Pill tone={gone ? "warn" : "run"}>{gone ? "session gone" : "waits for the session"}</Pill> {SAID[thread.waiting?.order?.kind ?? ""] ?? thread.waiting?.title}</b>}
      <SessionLine thread={thread} compact />
      {waits && <SessionTodos todos={thread.session?.todos ?? []} label="The session's todo list, live" />}
      {(thread.session?.findings?.length ?? 0) > 0 && (
        <span className="sub">keel's checks after the session's steps: {thread.session!.findings.length} finding(s), shown at the done gate.</span>
      )}
      {waits && onJump && <div className="row"><button className="btn sm" type="button" onClick={onJump}>Go to it</button></div>}
    </div>
  );
}
