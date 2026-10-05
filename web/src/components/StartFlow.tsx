// "Start a flow" drawer: pick the project and workflow, say what to build, and see the estimate before it starts.

import { useEffect, useState } from "react";
import { api, errorParts, type Estimate, type Limit, type OnCap, type Workflow } from "../api";
import { kfmt, parseTokens, usd } from "../format";
import { go, useApp } from "../state";
import { Drawer, ErrorBox } from "./ui";
import { WorkspaceDoctor } from "./WorkspaceDoctor";

const ON_CAP: [OnCap, string][] = [["pause", "pause and ask me"], ["cheaper", "switch to cheaper models"], ["stop", "stop"]];

export function StartFlowDrawer({ onClose, workflowId }: { onClose: () => void; workflowId?: string }) {
  const { projects, pid, setProjectId, reloadProjects, toast } = useApp();
  const [p, setP] = useState(pid ?? projects[0]?.id ?? "");
  const [wfs, setWfs] = useState<Workflow[] | null>(null);
  const [wid, setWid] = useState(workflowId ?? "");
  const [title, setTitle] = useState("");
  const [request, setRequest] = useState("");
  const vague = (title + " " + request).trim().split(/\s+/).filter(Boolean).length < 6;
  const [acs, setAcs] = useState(3);
  const [est, setEst] = useState<Estimate | null>(null);
  const [estErr, setEstErr] = useState<{ message: string; hint?: string } | null>(null);
  const [limits, setLimits] = useState<Limit[]>([]);
  const [cap, setCap] = useState("");
  const [onCap, setOnCap] = useState<OnCap>("pause");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [model, setModel] = useState<{ provider: string; model: string } | null>(null);
  const [allowFake, setAllowFake] = useState(false);
  const [allowDirty, setAllowDirty] = useState(false);
  const [doctor, setDoctor] = useState(false);
  const [lintScope, setLintScope] = useState<"diff" | "all">("diff");
  const isDemo = projects.find((x) => x.id === p)?.root === "/data/demo";
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
    }, () => undefined);
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

  const start = async () => {
    if (!title.trim()) {
      document.getElementById("sf-what")?.focus();
      toast("Say what to build first.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const capTokens = parseTokens(cap);
      await api.startFlow(p, {
        workflow_id: wid, title: title.trim(),
        // the cap belongs to this flow only; project settings stay as they are
        ...(capTokens > 0 ? { cap_tokens: capTokens } : {}), on_cap: onCap,
        ...(allowFake ? { allow_fake: true } : {}), ...(allowDirty ? { allow_dirty: true } : {}),
        ...(request.trim() ? { request: request.trim() } : {}),
        // the lint flow reads its scope from the flow's data (docs/CONTRACT.md, v0.4.1)
        ...(wid === "lint" ? { options: { scope: lintScope } } : {}),
      });
      if (p !== pid) setProjectId(p);
      await reloadProjects();
      toast("Flow started as a new LangGraph thread.");
      onClose();
      go("flow");
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
        <label htmlFor="sf-what">What to build</label>
        <input type="text" id="sf-what" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Players can see their rank next to the top 10" />
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
              {claude && <><span>{claude.name} now</span><b className="num">{Math.round((claude.used / (claude.cap || 1)) * 100)}% used{claude.used / (claude.cap || 1) > 0.6 ? " — may pause" : ""}</b></>}
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
      </div>
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
