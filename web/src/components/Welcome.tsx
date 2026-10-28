// keel 0.18.66 the first-run welcome: what keel is (flows, gates, guards) and the 4-minute tour, then the same five
// set-up steps as Help › Start here (components/StartSteps: one state for both), then "keel is ready".
//
//   welcome ──Set up keel──▶ step 1 … step 5 ──▶ keel is ready
//      └────────────── Skip, I know keel (always there) ──────────────┘
//
// It opens by itself once per keel: when keel has never seen it finished or skipped (api /help/welcome, kept in keel's
// database, so another browser does not show it again) and keel has no project yet or no flow yet; an older keel with
// flows is not greeted again. Help › Start here's "See the welcome again" opens it any time.
// A step's link to a page of keel (Connections) hides the welcome behind a small "Back to the setup" button; the
// drawers (Add projects, Start a flow) open over it. The tour plays from the GitHub release in an inline <video>; the
// cover is in the bundle, so nothing here waits for the network. prefers-reduced-motion: no animation (welcome.css).

import { useEffect, useRef, useState } from "react";
import cover from "../../../docs/keel-video.jpg";
import { helpApi } from "../helpApi";
import { go, useApp } from "../state";
import { AddProjectsDrawer } from "./AddProjects";
import { useStartInit } from "./InitAsk";
import { LOGO_PATH } from "./logo";
import { StartFlowDrawer } from "./StartFlow";
import { FIRST_FLOWS, McpSetup, useStartStatus, type StepStatus } from "./StartSteps";
import { closeWelcome, openWelcome, useWelcomeUi, welcomeUi } from "./welcomeState";

/** The 4-minute tour (README.md links the same file). */
export const TOUR_URL = "https://github.com/MiladNalbandi/keel-v2/releases/download/v0.18.49/keel-v2-explainer.mp4";
const DONE = 6;

/** Opens the welcome the first time this keel's web opens (see the top of this file). */
function useFirstOpen() {
  useEffect(() => {
    let gone = false;
    (async () => {
      try {
        const w = await helpApi.welcome();
        if (gone || w.seen) return;
        const s = await helpApi.start();
        if (!gone && !s.welcome.seen && (s.projects === 0 || !s.flow)) openWelcome(0);
      } catch {
        // an older api, or keel is not reachable: no welcome
      }
    })();
    return () => {
      gone = true;
    };
  }, []);
}

function remember(how: "finished" | "skipped") {
  helpApi.saveWelcome(true, how).catch(() => undefined);
}

export function Welcome() {
  useFirstOpen();
  const ui = useWelcomeUi();
  if (!ui.open) return null;
  if (ui.hidden)
    return (
      <button type="button" className="wl-back" onClick={() => welcomeUi.set({ hidden: false })}>
        Back to the setup · step {ui.screen} of 5
      </button>
    );
  return <WelcomeScreens screen={ui.screen} />;
}

function Mark() {
  return (
    <div className="wl-brand">
      <svg width="36" height="21" viewBox="0 0 120 68" aria-hidden="true">
        <path fill="#8b6dff" fillRule="evenodd" d={LOGO_PATH} />
      </svg>
      keel
    </div>
  );
}

function WelcomeScreens({ screen }: { screen: number }) {
  const st = useStartStatus({ poll: screen === 3 });
  const box = useRef<HTMLDivElement>(null);
  const setScreen = (n: number) => {
    if (n >= DONE) remember("finished");
    welcomeUi.set({ screen: Math.max(0, Math.min(DONE, n)) });
  };
  const skip = () => {
    remember("skipped");
    closeWelcome();
  };
  // a new screen starts at its top, with the focus inside the welcome
  useEffect(() => {
    box.current?.scrollTo?.({ top: 0 });
    box.current?.querySelector<HTMLElement>("h1")?.focus();
  }, [screen]);
  // Esc closes it for now (it is not remembered: it comes back on the next open until finished or skipped)
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.querySelector(".drawer")) closeWelcome();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
  const inSetup = screen >= 1 && screen <= 5;
  return (
    <div className="wl" role="dialog" aria-modal="true" aria-label="Welcome to keel" ref={box} data-testid="welcome">
      <div className="wl-glow a" aria-hidden="true" />
      <div className="wl-glow b" aria-hidden="true" />
      <div className="wl-in">
        <div className="wl-top">
          <Mark />
          <span className="wl-grow" />
          {inSetup && (
            <div className="wl-dots" aria-label={`Setup progress: step ${screen} of 5`}>
              {[1, 2, 3, 4, 5].map((n) => (
                <span key={n} className={n < screen ? "done" : n === screen ? "now" : ""} />
              ))}
              <span className="wl-dots-t">step {screen} of 5</span>
            </div>
          )}
          {screen < DONE && (
            <button type="button" className="wl-skip" onClick={skip}>Skip, I know keel</button>
          )}
        </div>
        {screen === 0 && <Hello onNext={() => setScreen(1)} />}
        {inSetup && st.steps.length === 5 && (
          <Step key={screen} s={st.steps[screen - 1]} st={st} onBack={() => setScreen(screen - 1)} onNext={() => setScreen(screen + 1)} />
        )}
        {inSetup && st.steps.length < 5 && <p className="wl-p" role="status">Looking at what keel has…</p>}
        {screen === DONE && <Ready flow={!!st.data?.flow} />}
      </div>
    </div>
  );
}

function Hello({ onNext }: { onNext: () => void }) {
  return (
    <div className="wl-hello">
      <div className="wl-col">
        <div className="wl-kicker up">Welcome</div>
        <h1 className="up d1" tabIndex={-1}>Your AI agents,<br />with a process you trust.</h1>
        <p className="wl-lead up d2">
          keel runs coding agents through spec, tests, code and review. It stops at the gates where you decide, and keeps
          every step in the record.
        </p>
        <div className="wl-cards">
          <div className="wl-card up d3"><span className="wl-num p">1</span><div><b>Flows</b> <span>· spec → a failing test → code → review, one criterion at a time</span></div></div>
          <div className="wl-card up d4"><span className="wl-num g">2</span><div><b>Gates</b> <span>· you approve what matters, from the web, the terminal or your phone</span></div></div>
          <div className="wl-card up d5"><span className="wl-num y">3</span><div><b>Guards</b> <span>· rules agents cannot break: no secrets, no skipped tests, no force push</span></div></div>
        </div>
        <div className="wl-row up d6">
          <button type="button" className="wl-btn p big" onClick={onNext}>Set up keel · 5 steps, ~5 min</button>
          <span className="wl-hint">you can stop any time and come back</span>
        </div>
      </div>
      <Tour />
    </div>
  );
}

const clock = (s: number) => (Number.isFinite(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "–:––");

/** The tour: the cover with a pulsing play button; play loads the video from GitHub into the same frame. It never
 *  blocks: offline or a failed load shows the cover and a link to watch it on GitHub. */
function Tour() {
  const video = useRef<HTMLVideoElement>(null);
  const [state, setState] = useState<"cover" | "playing" | "paused" | "failed">("cover");
  const [time, setTime] = useState({ at: 0, all: 243 });
  const play = () => {
    const v = video.current;
    if (!v || (typeof navigator !== "undefined" && navigator.onLine === false)) return setState("failed");
    try {
      const p = v.play();
      setState("playing");
      if (p && typeof p.catch === "function") p.catch(() => setState("failed"));
    } catch {
      setState("failed");
    }
  };
  const toggle = () => {
    const v = video.current;
    if (!v) return;
    if (v.paused) play();
    else {
      v.pause();
      setState("paused");
    }
  };
  const started = state === "playing" || state === "paused";
  return (
    <div className="wl-tour up d2">
      <div className="wl-frame">
        <video
          ref={video}
          className={started ? "on" : ""}
          src={TOUR_URL}
          poster={cover}
          preload="none"
          playsInline
          aria-label="The 4-minute tour of keel"
          data-testid="tour-video"
          onClick={started ? toggle : undefined}
          onError={() => started && setState("failed")}
          onTimeUpdate={(e) => setTime({ at: e.currentTarget.currentTime, all: e.currentTarget.duration || 243 })}
          onEnded={() => setState("paused")}
        />
        {!started && (
          <img src={cover} alt="The 4-minute tour of keel: a flow from spec to tested commits, the Inbox, Code Review, KeelBot, the Supervisor, the terminal and the phone app" />
        )}
        {state === "cover" && (
          <button type="button" className="wl-play" onClick={play} aria-label="Play the 4-minute tour">
            <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true"><path d="M9 6 L20 13 L9 20 Z" fill="#15111f" /></svg>
          </button>
        )}
        {started && (
          <button type="button" className="wl-bar fade" onClick={toggle} aria-label={state === "playing" ? "Pause the tour" : "Play the tour"}>
            <span className={`wl-rec${state === "playing" ? " on" : ""}`} aria-hidden="true" />
            <span>{state === "playing" ? "Playing" : "Paused"} · {clock(time.at)} / {clock(time.all)}</span>
            <span className="wl-track" aria-hidden="true"><span style={{ width: `${Math.min(100, (time.at / (time.all || 1)) * 100)}%` }} /></span>
          </button>
        )}
        {state === "failed" && (
          <div className="wl-fail" role="status">
            The tour cannot play here. <a href={TOUR_URL} target="_blank" rel="noreferrer">Watch it on GitHub ›</a>
          </div>
        )}
      </div>
      <div className="wl-under"><span>The 4-minute tour of keel</span><a href={TOUR_URL} target="_blank" rel="noreferrer">watch on GitHub</a></div>
    </div>
  );
}

type St = ReturnType<typeof useStartStatus>;

const TEXT: Record<StepStatus["id"], string> = {
  project: "Pick a git repository on this computer. keel works only in the folders you add.",
  model: "Use a subscription you already have, or an API key. keel never shows a key again after you save it.",
  mcp: "Claude Code, opencode, Codex or Cursor can talk to keel over MCP: read the flow, answer gates, start flows. This step is optional.",
  init: "init finds the stack and the test commands, runs them once, and writes its notes for the next flows.",
  flow: "Say what you want in your words. keel picks the steps, or asks you first when it is not clear.",
};

function Dot({ tone }: { tone: "ok" | "wait" | "off" }) {
  return <span className={`wl-dot ${tone}`} aria-hidden="true" />;
}

function Step({ s, st, onBack, onNext }: { s: StepStatus; st: St; onBack: () => void; onNext: () => void }) {
  const { projects } = useApp();
  const [drawer, setDrawer] = useState<{ kind: "add" } | { kind: "flow"; workflow: string } | null>(null);
  const [pick, setPick] = useState("clarify");
  const proj = st.data?.project ?? null;
  const init = useStartInit(proj ? { id: proj.id, name: proj.name } : null, () => void st.reload());
  // a page of keel: the welcome waits behind its "Back to the setup" button
  const visit = (hash: string) => {
    welcomeUi.set({ hidden: true });
    location.hash = hash;
  };
  let rows: { tone: "ok" | "wait" | "off"; label: string; note?: string }[] = [];
  let cta: { label: string; run: () => void; busy?: boolean } = { label: "Next", run: onNext };
  let hint = "";
  let body = null;
  switch (s.id) {
    case "project":
      rows = projects.length
        ? projects.slice(0, 3).map((p) => ({ tone: "ok" as const, label: p.name, note: p.root }))
        : [{ tone: "off", label: "No project yet", note: "a git repository on this computer" }];
      if (projects.length > 3) rows.push({ tone: "ok", label: `and ${projects.length - 3} more` });
      if (!s.done) cta = { label: "Add projects", run: () => setDrawer({ kind: "add" }) };
      hint = s.done ? "add more any time: All projects › Add projects" : "keel looks only in the folders it may read";
      break;
    case "model":
      rows = st.data?.models.length
        ? st.data.models.slice(0, 3).map((m) => ({ tone: "ok" as const, label: m.label, note: "ready" }))
        : [
            { tone: "off", label: "Claude, Codex or Copilot (your subscription)", note: "sign in" },
            { tone: "off", label: "An API key", note: "Anthropic, OpenAI, GitHub" },
            { tone: "off", label: "A local model server", note: "Ollama, LM Studio…" },
          ];
      if (!s.done) cta = { label: "Open Connections", run: () => visit("#/settings/connections") };
      hint = "more in Settings › Connections";
      break;
    case "mcp":
      body = <McpSetup tools={st.mcp} compact />;
      hint = s.done ? "" : "optional: you can do it later in Help › Start here";
      break;
    case "init":
      rows = !proj
        ? [{ tone: "off", label: "Add a project first" }]
        : [{ tone: s.done ? "ok" : proj.init_running ? "wait" : "off", label: proj.name,
            note: s.done ? "keel knows it" : proj.init_running ? "init runs now" : "not set up yet" }];
      if (proj && !s.done && !proj.init_running) cta = { label: init.busy ? "Starting…" : "Start init", run: () => void init.start(), busy: init.busy };
      hint = s.done ? "" : "about 2 minutes; it stops at its first question for you";
      break;
    case "flow":
      if (st.data?.flow) rows = [{ tone: "ok", label: st.data.flow.title || "your first flow", note: st.data.flow.workflow ?? "" }];
      else
        body = (
          <div className="wl-pick" role="radiogroup" aria-label="Your first flow">
            {FIRST_FLOWS.map((f) => (
              <label key={f.id} className={pick === f.id ? "on" : ""}>
                <input type="radio" name="wl-first-flow" checked={pick === f.id} onChange={() => setPick(f.id)} />
                <Dot tone={pick === f.id ? "ok" : "off"} />
                <span><b>{f.id}</b> · {f.line}</span>
                {f.id === "clarify" && <span className="wl-note">recommended</span>}
              </label>
            ))}
          </div>
        );
      if (!s.done) cta = projects.length ? { label: "Start the flow", run: () => setDrawer({ kind: "flow", workflow: pick }) } : { label: "Next", run: onNext };
      else cta = { label: "Finish", run: onNext };
      hint = s.done ? "" : projects.length ? "it stops at its first gate for you" : "add a project first (step 1)";
      break;
  }
  return (
    <div className="wl-step">
      <div className="wl-kicker up">Step {s.n} of 5{s.done ? " · done" : ""}</div>
      <h1 className="up d1" tabIndex={-1}>{s.id === "mcp" ? "Connect your AI tool (optional)" : s.title}</h1>
      <p className="wl-lead up d2">{TEXT[s.id]}</p>
      <div className="wl-box up d3">
        {rows.map((r, i) => (
          <div key={i} className="wl-r">
            <Dot tone={r.tone} />
            <span className="wl-grow">{r.label}</span>
            {r.note && <span className="wl-note">{r.note}</span>}
          </div>
        ))}
        {body}
      </div>
      {s.id === "init" && init.err && <p className="wl-err" role="alert">{init.err.message}</p>}
      <div className="wl-row up d4">
        <button type="button" className="wl-btn" onClick={onBack}>Back</button>
        <button type="button" className="wl-btn p" onClick={cta.run} disabled={cta.busy}>{cta.label}</button>
        {cta.label !== "Next" && cta.label !== "Finish" && (
          <button type="button" className="wl-btn q" onClick={onNext}>{s.id === "mcp" ? "Later" : "Next"}</button>
        )}
        {hint && <span className="wl-hint">{hint}</span>}
      </div>
      {drawer?.kind === "add" && <AddProjectsDrawer onClose={() => { setDrawer(null); void st.reload(); }} />}
      {drawer?.kind === "flow" && (
        <StartFlowDrawer workflowId={drawer.workflow} projectId={proj?.id} onClose={() => { setDrawer(null); void st.reload(); }} />
      )}
    </div>
  );
}

function Ready({ flow }: { flow: boolean }) {
  return (
    <div className="wl-done">
      <div className="wl-tick up ring" aria-hidden="true">
        <svg width="38" height="38" viewBox="0 0 38 38"><path d="M9 20 L16 27 L29 12" fill="none" stroke="#e8fff0" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </div>
      <h1 className="up d1" tabIndex={-1}>keel is ready</h1>
      <p className="wl-lead up d2">
        {flow ? "Your first flow waits for you in the Inbox when it needs you. " : ""}Help › Start here keeps this list, and the tour is there too.
      </p>
      <div className="wl-row center up d3">
        <button type="button" className="wl-btn p big" onClick={() => { closeWelcome(); go(flow ? "inbox" : "help"); }}>
          {flow ? "Open the Inbox" : "Open Start here"}
        </button>
        <button type="button" className="wl-btn big" onClick={() => openWelcome(0)}>See the welcome again</button>
      </div>
    </div>
  );
}
