// keel 0.28 Home: one box to ask KeelBot or start a flow, then what waits for you, what runs now and the recent flows.
// Asking comes first (⇥ in the empty box goes to Build a flow): it opens ⌘K in KeelBot mode with the question, where
// the answer shows. Starting opens the Start a flow drawer with the text in it (its estimate, size and cap stay there).
//
//            Ask keel about greeter
//   ┌───────────────────────────────────────────────┐
//   │ [Ask KeelBot | Build a flow]       ⇥ switches │
//   │ Where is the scoreboard sorted?               │
//   │                                         Ask ↩ │
//   └───────────────────────────────────────────────┘
//   Waiting for you (2) · Running now · Recent

import { useRef, useState } from "react";
import { api, type RunRow } from "../api";
import { inboxApi, type InboxItem } from "../inboxApi";
import { openLauncher } from "../components/launcher/Launcher";
import { openStartFlow, runTone } from "../components/ShellNav";
import { hashFor } from "../routes";
import type { PageProps } from "../sdk/registry";
import { useApp, useLoad } from "../state";

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

export function HomePage({ pid }: PageProps) {
  const { project } = useApp();
  // the person's wish: Ask first, Build a flow second (⇥ switches)
  const [mode, setMode] = useState<"build" | "ask">("ask");
  const [text, setText] = useState("");
  const [err, setErr] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const inbox = useLoad("home-inbox", () => inboxApi.list({}), { live: true });
  const runs = useLoad(pid ? `home-runs:${pid}` : null, () => api.runs(pid, undefined, 8), { live: true });
  const waiting = (inbox.data?.items ?? []).slice(0, 4);
  const all = runs.data ?? [];
  const running = all.filter((r) => runTone(r) === "run");
  const recent = all.filter((r) => runTone(r) !== "run").slice(0, 4);

  const go = () => {
    const t = text.trim();
    if (mode === "build") {
      if (!t) {
        setErr(true);
        box.current?.focus();
        return;
      }
      openStartFlow(t);
    } else {
      openLauncher({ text: t ? `?${t}` : "?" });
    }
  };

  return (
    <div className="home2">
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
                go();
              } else if (e.key === "Tab" && !e.shiftKey && !text) {
                e.preventDefault();
                setMode(mode === "build" ? "ask" : "build");
              }
            }}
          />
          {err && <p className="h2-err">Say what to build first.</p>}
          <div className="h2-acts">
            <span className="h2-tip">
              {mode === "build" ? "Workflow, size, cap and run mode come next." : "KeelBot reads the project; it changes nothing."}
            </span>
            <span className="grow" />
            {mode === "build" && (
              <button type="button" className="btn ghost" onClick={() => openStartFlow(text.trim())}>
                More options
              </button>
            )}
            <button type="button" className="btn primary" onClick={go}>
              {mode === "build" ? "Start flow" : "Ask"} <kbd>↩</kbd>
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
