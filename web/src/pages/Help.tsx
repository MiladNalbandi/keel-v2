// keel 0.18.66 Help: #/help is "Start here" — the five set-up steps with what keel really sees (components/StartSteps),
// the short guides and their search; #/help/<id> is one short guide (docs/guides/<id>.md, bundled in: guides.ts).
// "See the welcome again" opens the first-run welcome (components/Welcome) over the page.

import { useState, type ReactNode } from "react";
import { AddProjectsDrawer } from "../components/AddProjects";
import { useStartInit } from "../components/InitAsk";
import { Markdown } from "../components/Markdown";
import { RdPage } from "../components/rdBuild";
import { FIRST_FLOWS, McpSetup, McpWaiting, STEP_TITLES, useStartStatus, type StepStatus } from "../components/StartSteps";
import { SessionConnect } from "../components/Session";
import { StartFlowDrawer } from "../components/StartFlow";
import { openWelcome } from "../components/welcomeState";
import { GUIDES, guideById, longGuideUrl, searchGuides } from "../guides";
import { AREAS, KEYS, keyLabel, readKeymap } from "../keys";
import { hashFor } from "../routes";
import { useApp, useRoute } from "../state";

export function HelpPage() {
  const { arg } = useRoute();
  if (arg) return <GuidePage id={arg} />;
  return <StartHerePage />;
}

function StepCard({ s, current, children }: { s: StepStatus; current: boolean; children: ReactNode }) {
  return (
    <section className={`hs-step${s.done ? " done" : ""}${current ? " now" : ""}`} aria-labelledby={`hs-${s.id}`} data-testid={`step-${s.id}`}>
      <span className="hs-n" aria-hidden="true">{s.done ? "✓" : s.n}</span>
      <div className="hs-step-b">
        <h2 id={`hs-${s.id}`}>
          {s.n} · {STEP_TITLES[s.id]}
          <span className="sr-only">{s.done ? " (done)" : " (not done yet)"}</span>
        </h2>
        {children}
      </div>
    </section>
  );
}

/** Step 5's three choices; each opens Start a flow with that workflow. */
export function FirstFlowCards({ onPick }: { onPick: (workflow: string) => void }) {
  return (
    <div className="hs-flows" role="group" aria-label="Your first flow">
      {FIRST_FLOWS.map((f) => (
        <button key={f.id} type="button" className="hs-flow" onClick={() => onPick(f.id)}>
          <b>{f.id}</b>
          <span>{f.line}</span>
        </button>
      ))}
    </div>
  );
}

function StartHerePage() {
  const st = useStartStatus();
  const { projects, setProjectId } = useApp();
  const [drawer, setDrawer] = useState<{ kind: "add" } | { kind: "flow"; workflow: string } | null>(null);
  const [query, setQuery] = useState("");
  const proj = st.data?.project ?? null;
  const init = useStartInit(proj ? { id: proj.id, name: proj.name } : null);
  const current = st.steps.find((s) => !s.done)?.id;
  const by = Object.fromEntries(st.steps.map((s) => [s.id, s])) as Record<StepStatus["id"], StepStatus>;
  const hits = searchGuides(query);
  const { health } = useApp();
  return (
    <RdPage name="help">
    <div className="hs-page">
      <header className="hs-head top">
        <div>
          <span className="hs-crumb">Help</span>
          <h1>Start here</h1>
          <p>Five steps, about ten minutes. Each step shows a green tick when keel sees it is done.</p>
        </div>
        <div className="actions">
          <button type="button" className="btn ghost" onClick={() => openWelcome()}>See the welcome again</button>
          <button type="button" className="btn rdb-keyhint" data-keys={`${keyLabel(KEYS.sheet)}`} onClick={openKeySheet}>All keys</button>
        </div>
      </header>
      <div className="hs-progress rdb-progress" role="progressbar" aria-label="Set-up steps done" aria-valuemin={0} aria-valuemax={5} aria-valuenow={st.done}>
        <div className="rdb-progress-t">
          <b>{st.data ? `${st.done} of 5 done` : "Looking…"}</b>
          {st.data && current && <span className="sub">Next: {STEP_TITLES[current]}</span>}
        </div>
        <span className="hs-bar"><span style={{ width: `${st.done * 20}%` }} /></span>
      </div>
      {st.error && <p className="hs-warn" role="alert">keel could not read the steps: {st.error.message}</p>}
      {st.data && (
        <>
          <StepCard s={by.project} current={current === "project"}>
            <p className="hs-line">
              {by.project.line}{" "}
              {by.project.done ? <a href="#/projects/add">Add another</a> : null}
            </p>
            {!by.project.done && (
              <div><button type="button" className="btn primary" onClick={() => setDrawer({ kind: "add" })}>Add projects</button></div>
            )}
          </StepCard>
          <StepCard s={by.model} current={current === "model"}>
            <p className="hs-line">
              {by.model.line} <a href="#/settings/connections">Connections ›</a>
              {!by.model.done && <> · <a href="#/settings/models">Models &amp; keys ›</a></>}
            </p>
            {!by.model.done && <div><a className="btn primary" href="#/settings/connections">Open Connections</a></div>}
          </StepCard>
          <StepCard s={by.mcp} current={current === "mcp"}>
            <p className="hs-line">
              Then you can ask it "what does keel's flow wait for?", answer gates, or start a flow, from your editor.
            </p>
            {by.mcp.done ? (
              <>
                <McpWaiting tools={st.mcp} toolLabel="your tool" />
                <details className="hs-fold">
                  <summary>Connect another tool</summary>
                  <McpSetup tools={st.mcp} live={false} />
                </details>
              </>
            ) : (
              <McpSetup tools={st.mcp} />
            )}
            {/* 0.29 the person's own session does the harness flow for keel (Start a flow › Run with) */}
            <details className="hs-fold" id="connect-session">
              <summary>Connect your session: your Claude Code or OpenCode does the harness flow for keel</summary>
              <SessionConnect />
            </details>
          </StepCard>
          <StepCard s={by.init} current={current === "init"}>
            <p className="hs-line">
              {by.init.done || !proj ? by.init.line : "keel finds the stack and the test commands, runs them once, and writes its notes (.keel/config.yml and the project knowledge)."}
            </p>
            {proj && !by.init.done && (
              proj.init_running ? (
                <div><a className="btn" href={`#/projects/${encodeURIComponent(proj.id)}/flow`} onClick={() => setProjectId(proj.id)}>Open the init flow</a></div>
              ) : (
                <div className="hs-row">
                  <button type="button" className="btn primary" disabled={init.busy} onClick={() => void init.start()}>
                    {init.busy ? "Starting…" : `Start init on ${proj.name}`}
                  </button>
                  {init.err && <span className="hs-warn" role="alert">{init.err.message}</span>}
                </div>
              )
            )}
          </StepCard>
          <StepCard s={by.flow} current={current === "flow"}>
            {by.flow.done && st.data.flow ? (
              <p className="hs-line">
                {by.flow.line}{" "}
                <a href={`#/projects/${encodeURIComponent(st.data.flow.project_id)}/flow/${encodeURIComponent(st.data.flow.thread_id)}`}>Open it ›</a>
              </p>
            ) : (
              <>
                <p className="hs-line">{by.flow.line} keel picks the steps, and stops at its first gate for you.</p>
                {projects.length ? (
                  <FirstFlowCards onPick={(workflow) => setDrawer({ kind: "flow", workflow })} />
                ) : (
                  <p className="hs-line">Add a project first.</p>
                )}
              </>
            )}
          </StepCard>
        </>
      )}
      <div className="rdb-help-foot">
        <section className="hs-guides" aria-labelledby="hs-guides-h">
          <div className="hs-guides-top">
            <h2 id="hs-guides-h">Short guides</h2>
            <label className="hs-search">
              <span className="sr-only">Search the help</span>
              <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="gate, phone, mcp…" aria-label="Search the help" />
            </label>
          </div>
          {query.trim() ? (
            <GuideHits hits={hits} />
          ) : (
            <>
              <div className="hs-glist rdb-gcards">
                {GUIDES.map((g) => (
                  <div key={g.id} className="rdb-gcard">
                    <a href={hashFor("help", g.id)}>{g.title}</a>
                    {g.summary && <span>{g.summary}</span>}
                  </div>
                ))}
              </div>
              <a className="rdb-glong" href={longGuideUrl(health?.version)} target="_blank" rel="noreferrer">All of the guide (long) ›</a>
            </>
          )}
        </section>
        <KeyCard />
      </div>
      {drawer?.kind === "add" && <AddProjectsDrawer onClose={() => { setDrawer(null); void st.reload(); }} />}
      {drawer?.kind === "flow" && (
        <StartFlowDrawer workflowId={drawer.workflow} projectId={proj?.id} onClose={() => { setDrawer(null); void st.reload(); }} />
      )}
    </div>
    </RdPage>
  );
}

/** Opens the key cheat sheet (components/KeySheet), as ? does when you are not typing. */
const openKeySheet = () => window.dispatchEvent(new KeyboardEvent("keydown", { key: KEYS.sheetPlain }));

/** keel is keyboard first: the keys that work on every page, and the sheet with all of them. */
function KeyCard() {
  const rows = AREAS.find((a) => a.id === "everywhere")?.rows(readKeymap()).filter((r) => r.keys?.length).slice(0, 5) ?? [];
  return (
    <aside className="panel rdb-keycard" aria-labelledby="rdb-keys-h">
      <div className="panel-head"><h2 id="rdb-keys-h">keel is keyboard first</h2></div>
      <ul>
        <li><span>Start a flow</span><kbd>N</kbd></li>
        {rows.map((r) => <li key={r.label}><span>{r.label}</span><kbd>{keyLabel(r.keys![0])}</kbd></li>)}
        <li><span>A page's own button</span><span className="sub">the key on it</span></li>
      </ul>
      <div className="panel-body"><button type="button" className="btn sm" onClick={openKeySheet}>Open the keys sheet</button></div>
    </aside>
  );
}

function GuideHits({ hits }: { hits: ReturnType<typeof searchGuides> }) {
  if (!hits.length) return <p className="hs-line" role="status">No short guide has that. The long guide has every detail.</p>;
  return (
    <ul className="hs-hits" aria-label="Search results">
      {hits.map((h) => (
        <li key={h.guide.id}>
          <a href={hashFor("help", h.guide.id)}>{h.guide.title}</a>
          <span>{h.snippet || h.guide.summary}</span>
        </li>
      ))}
    </ul>
  );
}

function GuidePage({ id }: { id: string }) {
  const g = guideById(id);
  const { health } = useApp();
  const [query, setQuery] = useState("");
  const body = g ? g.text.replace(/^# .*\n+/, "") : "";
  return (
    <RdPage name="help">
    <div className="hs-page hs-guide">
      <header className="hs-head top">
        <div>
          <span className="hs-crumb"><a href={hashFor("help")}>Help</a> › Short guides</span>
          <h1>{g?.title ?? "No such guide"}</h1>
        </div>
      </header>
      <div className="hs-guide-grid">
        <article className="hs-md" data-testid="guide">
          {g ? <Markdown text={body} fold={200} /> : <p>There is no guide called "{id}". Pick one from the list.</p>}
        </article>
        <nav className="hs-side" aria-label="Short guides">
          <a href={hashFor("help")}>‹ Start here</a>
          <label className="hs-search">
            <span className="sr-only">Search the help</span>
            <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search the help" aria-label="Search the help" />
          </label>
          {query.trim() ? (
            <GuideHits hits={searchGuides(query)} />
          ) : (
            GUIDES.map((x) => (
              <a key={x.id} href={hashFor("help", x.id)} aria-current={x.id === id ? "page" : undefined}>{x.title}</a>
            ))
          )}
          <a href={longGuideUrl(health?.version)} target="_blank" rel="noreferrer">All of the guide (long) ›</a>
        </nav>
      </div>
    </div>
    </RdPage>
  );
}
