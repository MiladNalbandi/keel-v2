// keel 0.29 connected sessions: the person's own Claude Code or OpenCode, connected to keel's MCP server, does the
// harness flow's plan and build for keel (engine runtime/sessions.py). keel never starts that session and never
// touches its login. These are the pieces more than one page shows: who is connected, the session's todo list, and how
// to connect one.

import { useEffect, useState } from "react";
import { api, type SessionPresence, type SessionsView, type SessionTodo } from "../api";
import { agoText, clock } from "../format";
import { helpApi } from "../helpApi";
import { CopyBlock, mcpSnippets, type McpTool } from "./StartSteps";

/** What the person tells their session, word for word (Help › Start here, the Start a flow hint). */
export const SESSION_SENTENCE = "Work for keel: call keel_session_open, then keel_session_next, and follow its work orders.";

/** The project's connected sessions, read again every `every` ms while the page shows them (null pid: none). */
export function useSessions(pid: string | null, every = 15_000): { data: SessionsView | null; error: string | null } {
  const [data, setData] = useState<SessionsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setData(null);
    setError(null);
    if (!pid) return;
    let live = true;
    const load = () => api.sessions(pid).then((d) => { if (live) { setData(d); setError(null); } },
      (e: unknown) => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    void load();
    const t = window.setInterval(() => void load(), every);
    return () => { live = false; window.clearInterval(t); };
  }, [pid, every]);
  return { data, error };
}

/** "last seen 12 s ago" / "last seen 3 min ago". */
export function seenWords(s: Pick<SessionPresence, "seen_ago_s" | "last_seen">): string {
  if (s.seen_ago_s < 60) return `last seen ${Math.max(0, s.seen_ago_s)} s ago`;
  return `last seen ${agoText(s.last_seen)}`;
}

/** "since 10:02" (today) or "since Nov 12". */
export const sinceWords = (iso: string) => `since ${clock(iso, false)}`;

const MARK: Record<SessionTodo["status"], string> = { done: "✓", in_progress: "•", pending: "☐" };
const WORD: Record<SessionTodo["status"], string> = { done: "done", in_progress: "in progress", pending: "to do" };

/** The session's todo list, like a coding agent's: ✓ done (dim), • in progress (accent), ☐ pending. */
export function SessionTodos({ todos, label = "The session's todo list" }: { todos: SessionTodo[]; label?: string }) {
  if (!todos.length) return null;
  const done = todos.filter((t) => t.status === "done").length;
  return (
    <div className="sess-todos-w">
      <span className="sub sess-todos-n">{done} of {todos.length} done</span>
      <ul className="sess-todos" aria-label={label}>
        {todos.map((t, i) => (
          <li key={`${i}:${t.text}`} className={`is-${t.status}`}>
            <span className="sess-mark" aria-hidden="true">{MARK[t.status]}</span>
            <span className="sess-todo-t">{t.text}</span>
            <span className="sr-only"> ({WORD[t.status]})</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Help: connect your own Claude Code or OpenCode session so it can do the harness flow for keel. */
export function SessionConnect() {
  const [bin, setBin] = useState("keel2");
  useEffect(() => {
    let live = true;
    helpApi.access().then((a) => live && a.keel2_path && setBin(a.keel2_path)).catch(() => {});
    return () => { live = false; };
  }, []);
  // keel's MCP server at the write level: the same command and config as step 3 (StartSteps.mcpSnippets)
  const ways: { tool: McpTool; label: string }[] = [{ tool: "claude", label: "Claude Code" }, { tool: "opencode", label: "OpenCode" }];
  return (
    <div className="hs-mcp sess-connect" data-testid="session-connect">
      <p className="hs-line">
        Your own Claude Code or OpenCode session can do the harness flow (plan → you approve → build) for keel, with its own
        login. keel stays the control room: it keeps the gates, runs the tests, checks the change after each step and
        makes the commits. keel never starts the session and never touches its login.
      </p>
      <ol className="hs-ol">
        {ways.map(({ tool, label }) => {
          const s = mcpSnippets(tool, "write", bin)[0];
          return (
            <li key={tool}>
              <span><b>{label}</b>: {s.where.replace(/:$/, "")} (keel's MCP server, write level):</span>
              <CopyBlock text={s.text} label={`Connect ${label} to keel`} />
            </li>
          );
        })}
        <li>
          <span>Then tell the session:</span>
          <CopyBlock text={SESSION_SENTENCE} label="What to tell the session" />
          <span className="hs-then">It waits for keel's work orders. Pick <b>Run with: Connected session</b> in Start a flow (the harness flow).</span>
        </li>
      </ol>
      <p className="sub sess-honest">
        The session runs its own tools, so keel cannot check them before they run. keel checks the change after each step
        against the project's rules and puts back what a step may not change.
      </p>
    </div>
  );
}
