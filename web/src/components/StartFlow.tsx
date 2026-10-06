// "Start a flow" drawer: pick the project and workflow, say what to build, and see the estimate before it starts.

import { useEffect, useState } from "react";
import { api, errorParts, type CapsLeft, type Estimate, type FlowWhere, type Limit, type OnCap, type RunMode, type Workflow } from "../api";
import { kfmt, parseTokens, usd } from "../format";
import { go, useApp } from "../state";
import { RunModePicker } from "./RunMode";
import { Drawer, ErrorBox } from "./ui";
import { WorkspaceDoctor } from "./WorkspaceDoctor";

const ON_CAP: [OnCap, string][] = [["pause", "pause and ask me"], ["cheaper", "switch to cheaper models"], ["stop", "stop"]];

export function StartFlowDrawer({ onClose, workflowId, projectId }: { onClose: () => void; workflowId?: string; projectId?: string }) {
  const { projects, pid, setProjectId, reloadProjects, toast } = useApp();
  const [p, setP] = useState(projectId ?? pid ?? projects[0]?.id ?? "");
  const [wfs, setWfs] = useState<Workflow[] | null>(null);
  const [wid, setWid] = useState(workflowId ?? "");
  const [title, setTitle] = useState("");
  const [titleErr, setTitleErr] = useState(false);
  const [request, setRequest] = useState("");
  const vague = (title + " " + request).trim().split(/\s+/).filter(Boolean).length < 6;
  const [acs, setAcs] = useState(3);
  const [est, setEst] = useState<Estimate | null>(null);
  const [estErr, setEstErr] = useState<{ message: string; hint?: string } | null>(null);
  const [limits, setLimits] = useState<Limit[]>([]);
  const [capsLeft, setCapsLeft] = useState<CapsLeft | null>(null);
  const [cap, setCap] = useState("");
  const [onCap, setOnCap] = useState<OnCap>("pause");
  const [runMode, setRunMode] = useState<RunMode>("manual");
  const [modeDefault, setModeDefault] = useState<RunMode>("manual");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [model, setModel] = useState<{ provider: string; model: string } | null>(null);
  const [allowFake, setAllowFake] = useState(false);
  const [allowDirty, setAllowDirty] = useState(false);
  const [doctor, setDoctor] = useState(false);
  const [lintScope, setLintScope] = useState<"diff" | "all">("diff");
  const isDemo = projects.find((x) => x.id === p)?.root === "/data/demo";
  // v0.7.x: a flow already runs or waits in the project folder: this one runs next to it, in a worktree of its own
  const folderBusy = !!projects.find((x) => x.id === p)?.flow;
  const [where, setWhere] = useState<FlowWhere>("auto");
  const inWorktree = where === "worktree" || (where === "auto" && folderBusy);
  const fakeRefused = !!err && /fake model/i.test(err.message);
  const dirtyRefused = !!err && /uncommitted changes/i.test(err.message);

  useEffect(() => {
    if (!p) return;
    setWfs(null);
    api.workflows(p).then((list) => {
      setWfs(list);
      setWid((w) => (w && list.some((x) => x.id === w) ? w : list.find((x) => x.id === "feature")?.id ?? list[0]?.id ?? ""));
    }, (e) => setErr(errorParts(e)));
    api.projectSettings(p).then((s) => {
      setModel({ provider: s.effective.default_model.provider, model: s.effective.default_model.model });
      const c = kfmt(s.effective.cap_tokens);
      setCap(c);
      setOnCap(s.effective.on_cap);
      // v0.4.1: the project's default run mode (Settings › Run mode); this flow may pick another
      const m = s.effective.run_mode ?? "manual";
      setRunMode(m);
      setModeDefault(m);
    }, () => undefined);
    // v0.4.2: the project's caps can make this flow's cap smaller (the smallest one left binds) or refuse the start
    setCapsLeft(null);
    api.capsLeft(p).then(setCapsLeft, () => undefined);
  }, [p]);

  useEffect(() => {
    api.limits().then(setLimits, () => undefined);
  }, []);

  useEffect(() => {
    if (!p || !wid) return;
    let gone = false;
    setEst(null);
    setEstErr(null);
    const t = window.setTimeout(() => {
      api.estimate(p, wid, acs).then((e) => !gone && setEst(e), (e) => !gone && setEstErr(errorParts(e)));
    }, 200);
    return () => {
      gone = true;
      window.clearTimeout(t);
    };
  }, [p, wid, acs]);

  const premium = limits.find((l) => /premium/i.test(l.name));
  const claude = limits.find((l) => /claude/i.test(l.name));

  // Flows that build nothing (a review, a hunt, a lint …) need no "what to build": they get a title of their own.
  const needsTitle = !NO_BUILD.has(wid);
  const start = async () => {
    if (needsTitle && !title.trim()) {
      setTitleErr(true);
      document.getElementById("sf-what")?.focus();
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const capTokens = parseTokens(cap);
      const state = await api.startFlow(p, {
        workflow_id: wid, title: title.trim() || `${wid} · ${new Date().toLocaleDateString()}`,
        // the cap belongs to this flow only; project settings stay as they are
        ...(capTokens > 0 ? { cap_tokens: capTokens } : {}), on_cap: onCap,
        ...(allowFake ? { allow_fake: true } : {}), ...(allowDirty ? { allow_dirty: true } : {}),
        ...(request.trim() ? { request: request.trim() } : {}),
        // the api falls back to the project's run mode; send it only when this flow picks another one
        ...(runMode !== modeDefault ? { run_mode: runMode } : {}),
        // the lint flow reads its scope from the flow's data (docs/CONTRACT.md, v0.4.1)
        ...(wid === "lint" ? { options: { scope: lintScope } } : {}),
        ...(where !== "auto" ? { where } : {}),
      });
      if (p !== pid) setProjectId(p);
      await reloadProjects();
      const there = inWorktree ? " It runs in a worktree of its own, next to the project folder's flow." : "";
      toast(state?.cap_note ? `Flow started.${there} ${state.cap_note}` : `Flow started as a new LangGraph thread.${there}`);
      onClose();
      if (inWorktree && state?.thread_id) go("flow", state.thread_id);
      else go("flow");
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer title="Start a flow" onClose={onClose}
      footer={<>
        <button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" onClick={start} disabled={busy || !wid || !p}>{busy ? "Starting…" : "Start flow"}</button>
      </>}>
      <div className="field">
        <label htmlFor="sf-p">Project</label>
        <select id="sf-p" value={p} onChange={(e) => setP(e.target.value)}>
          {projects.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </select>
      </div>
      <div className="field">
        <label htmlFor="sf-w">Workflow</label>
        <select id="sf-w" value={wid} onChange={(e) => setWid(e.target.value)} disabled={!wfs}>
          {!wfs && <option>Loading…</option>}
          {wfs?.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      </div>
      {wid === "lint" && (
        <div className="field">
          <label htmlFor="sf-scope">What to check</label>
          <select id="sf-scope" value={lintScope} onChange={(e) => setLintScope(e.target.value as "diff" | "all")}>
            <option value="diff">The files this branch changed</option>
            <option value="all">The whole project</option>
          </select>
          <span className="hint">keel runs the formatters and linters of this project's stacks, fixes what they find without changing behaviour (2 rounds at most) and commits it as chore(lint).</span>
        </div>
      )}
      <div className="field">
        <label htmlFor="sf-where">Where it runs</label>
        <select id="sf-where" value={inWorktree ? "worktree" : "folder"} onChange={(e) => setWhere(e.target.value as FlowWhere)}>
          <option value="folder" disabled={folderBusy}>The project folder{folderBusy ? " (a flow runs there)" : ""}</option>
          <option value="worktree">A worktree of its own, next to other flows</option>
        </select>
        <span className="hint">{inWorktree
          ? "keel makes a copy of the project on a new branch from the base branch (.keel/worktrees). The project folder and its flow are not touched; the Flow page shows both, and the files they share."
          : "The flow works in the project folder, on its own branch."}</span>
      </div>
      <div className="field">
        <label htmlFor="sf-what">{needsTitle ? "What to build" : "Title (optional)"}</label>
        <input type="text" id="sf-what" value={title} aria-invalid={titleErr && needsTitle ? true : undefined} aria-describedby={titleErr && needsTitle ? "sf-what-err" : undefined}
          onChange={(e) => { setTitle(e.target.value); setTitleErr(false); }}
          placeholder={needsTitle ? "e.g. Players can see their rank next to the top 10" : `e.g. ${wid} of this branch`} />
        {titleErr && needsTitle && <span id="sf-what-err" className="hint amber" role="alert">Say in a few words what to build: it names the flow and its branch.</span>}
      </div>
      <div className="field">
        <label htmlFor="sf-request">Describe it for the agents</label>
        <textarea id="sf-request" value={request} onChange={(e) => setRequest(e.target.value)} rows={4}
          placeholder="Who needs it, what should happen, what must not happen, where in the code if you know. The spec agent turns this into acceptance criteria." />
        {vague && title.trim() && <span className="hint amber">This is very short. The agents may not know what to build — a few sentences save a lot of tokens.</span>}
      </div>
      <div className="field">
        <label htmlFor="sf-acs">About how many acceptance criteria</label>
        <div className="row"><input type="range" id="sf-acs" min={1} max={10} value={acs} onChange={(e) => setAcs(Number(e.target.value))} style={{ flex: "1 1 140px" }} /><b className="num">{acs}</b></div>
      </div>
      {model && (
        <div className={model.provider === "fake" && !isDemo ? "wbar warn" : "wbar"} role="note">
          <span>Agents use <b>{model.provider === "fake" ? "the fake model" : `${model.provider} · ${model.model}`}</b>
            {model.provider === "fake" ? (isDemo ? " — fine for the demo." : ": it writes example files, not real code. Pick a real model in Connections › Use for all agents.") : "."}
            {!isDemo && " The flow works on its own branch (Settings › Branch name)."}</span>
          {model.provider === "fake" && !isDemo && <button className="btn sm" type="button" onClick={() => { onClose(); go("connections"); }}>Open Connections</button>}
        </div>
      )}
      <div className="est-box" aria-live="polite">
        <span className="lab">Before you start</span>
        {estErr ? <span className="sub">No estimate: {estErr.message}{estErr.hint ? ` ${estErr.hint}` : ""}</span> : !est ? <span className="sub loading">Estimating…</span> : (
          <>
            <div className="est-big">
              <span className="num">{kfmt(est.tokens)}</span>
              <span className="sub">tokens expected for about {acs} ACs · range {kfmt(est.low)} – {kfmt(est.high)}</span>
            </div>
            <div className="kv">
              <span>API cost if all on keys</span><b className="num">{usd(est.cost_usd)}</b>
              <span>Copilot premium requests</span>
              <b className="num">≈ {est.premium_requests}{premium ? ` (${Math.max(0, premium.cap - premium.used)} left)` : ""}</b>
              {claude && <><span>{claude.name} now</span><b className="num">{claudeUse(claude)}</b></>}
            </div>
          </>
        )}
      </div>
      <div className="field">
        <label htmlFor="sf-cap">Cap for this flow</label>
        <div className="row">
          <input type="text" id="sf-cap" value={cap} onChange={(e) => setCap(e.target.value)} style={{ width: 110 }} />
          <select aria-label="When the cap is hit" value={onCap} onChange={(e) => setOnCap(e.target.value as OnCap)}>
            {ON_CAP.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </div>
        <span className="hint">The estimate gets exact after the spec is approved and the real number of ACs is known. This cap is for this flow only; the project default stays in Settings.</span>
        {capsLeft && capsLeft.caps.length > 0 && <ProjectCaps left={capsLeft} />}
      </div>
      <RunModePicker value={runMode} onChange={setRunMode}
        hint={`The project default is ${modeDefault} (Settings › Run mode). You can change it on the Flow page while the flow runs.`} />
      {err && <ErrorBox error={err} />}
      {(fakeRefused || allowFake) && (
        <label className="chk"><input type="checkbox" checked={allowFake} onChange={(e) => setAllowFake(e.target.checked)} /> Run with the fake model anyway (it writes example files and commits them on the flow's branch)</label>
      )}
      {dirtyRefused && !doctor && (
        <button className="btn" type="button" onClick={() => setDoctor(true)}>Ask the Doctor what to do with these files</button>
      )}
      {doctor && <WorkspaceDoctor pid={p} onClean={() => { setErr(null); toast("The working tree is clean. Press Start flow."); }} />}
      {(dirtyRefused || allowDirty) && (
        <label className="chk"><input type="checkbox" checked={allowDirty} onChange={(e) => setAllowDirty(e.target.checked)} /> Start anyway — my uncommitted files stay out of keel's commits</label>
      )}
    </Drawer>
  );
}

/** What the project's caps (Budget) do to a flow started now: the smallest cap left binds; a used-up cap refuses or goes cheaper. */
function ProjectCaps({ left }: { left: CapsLeft }) {
  const n = left.next_flow;
  if (n.refused) return <span className="hint amber" data-testid="sf-caps">A cap is used up: {n.refused.error}</span>;
  const fromCap = n.tokens_from && n.tokens_from !== "settings" && n.tokens_from !== "flow";
  const parts = [
    fromCap ? `at most ${kfmt(n.cap_tokens)} tokens` : null,
    n.cap_usd ? `at most ${usd(n.cap_usd)} of reported cost` : null,
    n.step_cap_tokens ? `${kfmt(n.step_cap_tokens)} tokens per agent step` : null,
  ].filter(Boolean);
  return (
    <span className="hint" data-testid="sf-caps">
      This project's caps apply too (Budget): the smallest one left wins{parts.length ? `, now ${parts.join(", ")}` : ""}.
      {n.cheaper && <span className="amber"> Every agent starts on the cheaper model: a cap that says so is used up.</span>}
    </span>
  );
}

/** Templates that build nothing, so "what to build" is optional for them. */
const NO_BUILD = new Set(["review", "cover", "ship", "lint", "hunt", "hunt-next", "init", "knowledge-refresh"]);

/** The plan's use in words: the provider's own percentage when known, else against the cap you set, else just tokens.
 * (A missing cap once divided 3.7M tokens by 1 and showed "376640900% used".) */
export function claudeUse(l: Limit): string {
  const pct = l.used_pct != null ? l.used_pct * 100 : l.cap > 0 ? (l.used / l.cap) * 100 : null;
  if (pct == null) return `${kfmt(l.used)} tokens · no cap set`;
  return `${Math.round(pct)}% used${pct > 60 ? " — may pause" : ""}`;
}
