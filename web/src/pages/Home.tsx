// keel 0.28 Home: one box to ask KeelBot or start a flow, then what waits for you, what runs now and the recent flows.
// Asking comes first (⇥ in the empty box goes to Build a flow): Ask starts a KeelBot chat with the question and opens
// KeelBot's page on it (no ⌘K pop-up), where the answer streams in. Starting opens the Start a flow drawer with the text in it (its estimate, size and cap stay there).
//
//            Ask keel about greeter
//   ┌───────────────────────────────────────────────┐
//   │ [Ask KeelBot | Build a flow]       ⇥ switches │
//   │ Where is the scoreboard sorted?               │
//   │                                         Ask ↩ │
//   └───────────────────────────────────────────────┘
//   Waiting for you (2) · Running now · Recent
//
// #233 above the box, keel's message at first sight: keel is what your agent connects to (MCP) so it gets smarter and
// more organized and works better with other agents. It does not control or manage them. Six things keel gives the
// agent, the commit-style example in three steps, and "Connect your agent" (Help › Start here, the MCP step). "Hide"
// folds it to one line (saved in the browser); "What keel gives your agent" opens it again.

import { useRef, useState } from "react";
import { api, errorParts, type RunRow } from "../api";
import { inboxApi, type InboxItem } from "../inboxApi";
import { openStartFlow, runTone } from "../components/ShellNav";
import { hashFor, settingsHash } from "../routes";
import type { PageProps } from "../sdk/registry";
import { go, useApp, useLoad } from "../state";

const KIND: Record<string, string> = {
  gate: "Gate",
  clarify: "Questions",
  fix: "Needs a fix",
  crash: "Step failed",
  budget: "Over the cap",
  usage: "Over the cap",
  dependency: "New dependency",
  permission: "May it run?",
  task: "Task",
};

const INTRO_KEY = "keel2.home.intro";

const GIVES: { t: string; d: string }[] = [
  { t: "Project knowledge", d: "architecture, domain, data and how the parts talk" },
  { t: "Code and commit style", d: "how code is written here and how commits look" },
  { t: "Rules", d: "what agents may change and what is off limits" },
  { t: "Wiki", d: "the specs and docs, with their build state" },
  { t: "Graph", d: "what uses what: classes, functions, routes" },
  { t: "Shared context", d: "several agents on one keel see the same knowledge and flows, so less is done twice" },
];

function readIntro(): boolean {
  try {
    return localStorage.getItem(INTRO_KEY) !== "hidden";
  } catch {
    return true;
  }
}

/** #233 what keel is, at first sight: what it gives your agent and how the agent connects. */
export function HomeIntro() {
  const [open, setOpen] = useState(readIntro);
  const set = (v: boolean) => {
    setOpen(v);
    try {
      if (v) localStorage.removeItem(INTRO_KEY);
      else localStorage.setItem(INTRO_KEY, "hidden");
    } catch {
      /* private window */
    }
  };
  if (!open)
    return (
      <p className="h2-intro-min">
        <button type="button" className="h2-linkish" onClick={() => set(true)}>
          What keel gives your agent ›
        </button>
      </p>
    );
  return (
    <section className="h2-intro rise" aria-labelledby="h2-intro-h">
      <div className="h2-intro-top">
        <h1 id="h2-intro-h">Your agent connects to keel and gets smarter, more organized, and better with other agents.</h1>
        <button type="button" className="btn ghost h2-hide" onClick={() => set(false)} aria-label="Hide what keel gives your agent">
          Hide
        </button>
      </div>
      <p className="h2-intro-sub">
        keel does not take control of your agents or manage them. Your agent stays yours: it asks keel over MCP and gets what it needs to know about the
        project. Several agents on one keel share it, so together they are even more efficient.
      </p>
      <ul className="h2-gives" aria-label="What keel gives your agent">
        {GIVES.map((g) => (
          <li key={g.t}>
            <b>{g.t}</b>
            <span>{g.d}</span>
          </li>
        ))}
      </ul>
      <div className="h2-example" role="group" aria-label="Example: a commit">
        <span className="h2-ex-h">Example</span>
        <ol>
          <li>Your agent wants to commit.</li>
          <li>
            It asks keel over MCP how commits look in this project (<code className="mono">keel_commit_style</code>).
          </li>
          <li>It commits that way, like everyone on the project.</li>
        </ol>
      </div>
      <div className="h2-intro-acts">
        <span className="h2-tip">Claude Code, Codex, OpenCode, Cursor: add keel's MCP server once.</span>
        <span className="grow" />
        <a className="btn primary" href={hashFor("help")}>
          Connect your agent ›
        </a>
      </div>
    </section>
  );
}

export function HomePage({ pid }: PageProps) {
  const { project } = useApp();
  // the person's wish: Ask first, Build a flow second (⇥ switches)
  const [mode, setMode] = useState<"build" | "ask">("ask");
  const [text, setText] = useState("");
  const [err, setErr] = useState(false);
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState<{ message: string; hint?: string } | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const inbox = useLoad("home-inbox", () => inboxApi.list({}), { live: true });
  const runs = useLoad(pid ? `home-runs:${pid}` : null, () => api.runs(pid, undefined, 8), { live: true });
  const waiting = (inbox.data?.items ?? []).slice(0, 4);
  const all = runs.data ?? [];
  const running = all.filter((r) => runTone(r) === "run");
  const recent = all.filter((r) => runTone(r) !== "run").slice(0, 4);

  const send = () => {
    const t = text.trim();
    if (mode === "build") {
      if (!t) {
        setErr(true);
        box.current?.focus();
        return;
      }
      openStartFlow(t);
    } else {
      if (!t) {
        go("helper");
        return;
      }
      if (!pid || busy) return;
      setBusy(true);
      setFail(null);
      void (async () => {
        let made = "";
        try {
          const s = await api.helperCreate(pid, { mode: "ask", title: t.slice(0, 80) });
          made = s.id;
          await api.helperTurn(pid, s.id, { text: t });
          made = "";
          try {
            localStorage.setItem(`keel2.helper.${pid}.session`, s.id);
          } catch {
            /* private window */
          }
          setText("");
          go("helper");
        } catch (e) {
          const er = errorParts(e);
          setFail({ message: er.message, hint: er.hint });
          // the question was refused (no model yet): no empty chat stays behind
          if (made) void api.helperDelete(pid, made).catch(() => undefined);
        } finally {
          setBusy(false);
        }
      })();
    }
  };

  return (
    <div className="home2">
      <HomeIntro />
      <section className="h2-hero rise">
        <h1>{mode === "build" ? `What should keel build${project ? ` in ${project.name}` : ""}?` : `Ask keel about ${project?.name ?? "this project"}`}</h1>
        <div className={`h2-box${err ? " bad" : ""}`}>
          <div className="h2-top">
            <div className="seg2" role="group" aria-label="What the box does">
              <button type="button" aria-pressed={mode === "ask"} onClick={() => setMode("ask")}>
                Ask KeelBot
              </button>
              <button type="button" aria-pressed={mode === "build"} onClick={() => setMode("build")}>
                Build a flow
              </button>
            </div>
            <span className="h2-tip">
              <kbd>⇥</kbd> switches
            </span>
          </div>
          <label htmlFor="h2-text" className="sr-only">
            {mode === "build" ? "What to build" : "Your question"}
          </label>
          <textarea
            id="h2-text"
            ref={box}
            className="h2-text"
            rows={3}
            value={text}
            placeholder={
              mode === "build"
                ? "Describe the change. The spec agent turns it into acceptance criteria."
                : "Where is the scoreboard sorted? Why did the last flow stop?"
            }
            onChange={(e) => {
              setText(e.target.value);
              setErr(false);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              } else if (e.key === "Tab" && !e.shiftKey && !text) {
                e.preventDefault();
                setMode(mode === "build" ? "ask" : "build");
              }
            }}
          />
          {err && <p className="h2-err">Say what to build first.</p>}
          {fail && (
            <p className="h2-err" role="alert">
              <b>{fail.message}.</b> {fail.hint}{" "}
              {/models & keys/i.test(fail.hint ?? "") && <a href={settingsHash("models")}>Open Models &amp; keys</a>}
            </p>
          )}
          <div className="h2-acts">
            <span className="h2-tip">
              {mode === "build" ? "Workflow, size, cap and run mode come next." : "Opens in KeelBot. It reads the project; it changes nothing."}
            </span>
            <span className="grow" />
            {mode === "build" && (
              <button type="button" className="btn ghost" onClick={() => openStartFlow(text.trim())}>
                More options
              </button>
            )}
            <button type="button" className="btn primary" onClick={send} disabled={busy}>
              {mode === "build" ? "Start flow" : busy ? "Asking…" : "Ask"} <kbd>↩</kbd>
            </button>
          </div>
        </div>
        <p className="h2-keys">
          <kbd>N</kbd> new flow · <kbd>⌘K</kbd> find anything · <kbd>?</kbd> all keys
        </p>
      </section>

      <section className="h2-sec rise r2" aria-labelledby="h2-wait">
        <div className="h2-sh">
          <h2 id="h2-wait">Waiting for you</h2>
          {(inbox.data?.count ?? 0) > 0 && <span className="count">{inbox.data?.count}</span>}
          <span className="grow" />
          <a href={hashFor("inbox")}>Inbox ›</a>
        </div>
        {waiting.length === 0 && inbox.data && <p className="h2-none">Nothing waits for you. keel tells you here, in the bell and on your phone.</p>}
        <div className="h2-cards">
          {waiting.map((w) => (
            <WaitCard key={`${w.thread_id}:${w.id ?? w.kind}`} w={w} />
          ))}
        </div>
      </section>

      {running.length > 0 && (
        <section className="h2-sec rise r3" aria-labelledby="h2-run">
          <div className="h2-sh">
            <h2 id="h2-run">Running now</h2>
          </div>
          {running.map((r) => (
            <RunLine key={r.thread_id} r={r} />
          ))}
        </section>
      )}

      <section className="h2-sec rise r4" aria-labelledby="h2-rec">
        <div className="h2-sh">
          <h2 id="h2-rec">Recent</h2>
          <span className="grow" />
          <a href={hashFor("flow")}>All flows ›</a>
        </div>
        {recent.length === 0 && runs.data && <p className="h2-none">No flow yet. Describe one above and press ↩.</p>}
        {recent.map((r) => (
          <RunLine key={r.thread_id} r={r} />
        ))}
      </section>
    </div>
  );
}

function WaitCard({ w }: { w: InboxItem }) {
  const gate = w.kind === "gate";
  return (
    <a className={`h2-card${gate ? " gate" : ""}`} href={hashFor("flow", w.thread_id)}>
      <span className="h2-meta">
        {gate && <span className="dia" aria-hidden="true" />}
        <b className={gate ? "amber" : "acc"}>{KIND[w.kind] ?? w.kind}</b>
        <span>· {w.project_name}</span>
        {w.step && <span>· {w.step}</span>}
      </span>
      <span className="h2-title">{w.title}</span>
      <span className="h2-sub">{w.flow}</span>
      <span className="h2-open">Answer ›</span>
    </a>
  );
}

function RunLine({ r }: { r: RunRow }) {
  const tone = runTone(r);
  const what =
    tone === "wait" ? `waits: ${r.waiting}` : tone === "run" ? r.current ?? "running" : r.status;
  return (
    <a className="h2-run" href={hashFor("flow", r.thread_id)}>
      <span className={`nav-dot t-${tone}`} aria-hidden="true" />
      <span className="h2-rt">
        <b>{r.title || "Untitled flow"}</b>
        <span className="sub">
          {r.workflow_id ?? "flow"} · {what}
          {r.acs_total > 0 ? ` · ${r.acs_done}/${r.acs_total} criteria` : ""}
        </span>
      </span>
      {r.acs_total > 0 && (
        <span className="h2-steps" aria-hidden="true">
          {Array.from({ length: Math.min(r.acs_total, 8) }, (_, i) => (
            <i key={i} className={i < r.acs_done ? "ok" : i === r.acs_done && tone === "run" ? "run" : ""} />
          ))}
        </span>
      )}
      <span className="mono sub">{r.cost_usd != null ? `$${r.cost_usd.toFixed(2)}` : ""}</span>
    </a>
  );
}
