// "Start a flow" drawer: pick the project and workflow, say what to build, and see the estimate before it starts.

import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorParts, type CapsLeft, type Estimate, type FolderActivity, type FlowWhere, type Limit, type OnCap, type RunMode, type StartCheck, type TaskOwner, type Workflow } from "../api";
import { kfmt, parseTokens, usd } from "../format";
import { go, useApp } from "../state";
import { settingsHash } from "../routes";
import { BaseQuestion } from "./BaseBranch";
import { RunModePicker } from "./RunMode";
import { Drawer, ErrorBox } from "./ui";
import { WorkspaceDoctor } from "./WorkspaceDoctor";
import { SIZE_FLOWS, suggestFlow } from "../flowSize";
import { InitAsk, isInitFlow, needsInit, useStartInit } from "./InitAsk";

const ON_CAP: [OnCap, string][] = [["pause", "pause and ask me"], ["cheaper", "switch to cheaper models"], ["stop", "stop"]];

/** 0.17.3 a flow started from a spec (the Wiki's "Start a flow from this spec"): the engine takes it as the flow's spec
 *  and its criteria (options.spec); acs is its number of criteria, for the estimate. */
export type StartFromSpec = { path: string; acs?: number };

export function StartFlowDrawer({ onClose, workflowId, projectId, title: title0, spec }: {
  onClose: () => void; workflowId?: string; projectId?: string; title?: string; spec?: StartFromSpec;
}) {
  const { projects, pid, setProjectId, reloadProjects, toast } = useApp();
  const [p, setP] = useState(projectId ?? pid ?? projects[0]?.id ?? "");
  const [wfs, setWfs] = useState<Workflow[] | null>(null);
  const [wid, setWid] = useState(workflowId ?? "");
  const [title, setTitle] = useState(title0 ?? "");
  const [titleErr, setTitleErr] = useState(false);
  const [request, setRequest] = useState("");
  // a flow from a spec has its words in the spec
  const vague = !spec && (title + " " + request).trim().split(/\s+/).filter(Boolean).length < 6;
  const [acs, setAcs] = useState(spec?.acs ? Math.min(10, Math.max(1, spec.acs)) : 3);
  const [est, setEst] = useState<Estimate | null>(null);
  const [estErr, setEstErr] = useState<{ message: string; hint?: string } | null>(null);
  const [limits, setLimits] = useState<Limit[]>([]);
  const [capsLeft, setCapsLeft] = useState<CapsLeft | null>(null);
  const [cap, setCap] = useState("");
  // v0.17.5 the cap from Settings, while the person has not typed another one: it follows the estimate (with headroom)
  const [capDefault, setCapDefault] = useState("");
  // B15 a cap someone saved in Settings (General or this project): it is the cap, the estimate never raises it
  const [capSaved, setCapSaved] = useState(false);
  const [onCap, setOnCap] = useState<OnCap>("pause");
  const [runMode, setRunMode] = useState<RunMode>("manual");
  const [modeDefault, setModeDefault] = useState<RunMode>("manual");
  // 0.18.28 the person picked a mode: the project's settings, arriving later, never put the default back over it
  const modePicked = useRef(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [model, setModel] = useState<{ provider: string; model: string; effort?: string } | null>(null);
  const [allowFake, setAllowFake] = useState(false);
  const [allowDirty, setAllowDirty] = useState(false);
  const [doctor, setDoctor] = useState(false);
  const [lintScope, setLintScope] = useState<"diff" | "all">("diff");
  const isDemo = projects.find((x) => x.id === p)?.root === "/data/demo";
  // v0.7.x: a flow already runs or waits in the project folder: this one runs next to it, in a worktree of its own
  const folderBusy = !!projects.find((x) => x.id === p)?.flow;
  const [where, setWhere] = useState<FlowWhere>("auto");
  // 0.18.57 the tests run in a container that sees only the project folder: flows run there, one at a time
  const [check, setCheck] = useState<StartCheck | null>(null);
  const unfit = check?.worktree?.ok === false;
  // 0.18.57 what a refused start says beyond its words: paths that can be ignored, who already works on the task
  const [ignorable, setIgnorable] = useState<string[]>([]);
  const [owners, setOwners] = useState<TaskOwner[]>([]);
  const [force, setForce] = useState(false);
  // B8 the project folder is on a branch that is not the base (what init or another flow left checked out): the flow
  // gets its own branch from the base unless the person keeps working on that one (a flow from a spec: its branch)
  const onBranch = projects.find((x) => x.id === p)?.branch ?? "";
  const offBase = !!onBranch && !BASES.has(onBranch) && !isDemo;
  // B7 ship, cover, review, lint and address finish the work on the folder's branch: they continue it unless the person
  // picks a new one (a new branch from the base would ship an empty diff); the person's pick lasts until the flow changes
  const finishes = finishesBranch(wfs?.find((w) => w.id === wid) ?? { id: wid });
  const [branchPick, setBranchPick] = useState<"new" | "continue" | null>(spec ? "continue" : null);
  useEffect(() => { if (!spec) setBranchPick(null); }, [wid, spec]);
  const branchMode = branchPick ?? (finishes ? "continue" : "new");
  // 0.18.57 a KeelBot Ask or Edit chat reads the project folder: a flow on its own branch would check that branch out
  // under it. Its own copy (a worktree) is recommended, unless the tests cannot run there (the engine's start check)
  const [activity, setActivity] = useState<FolderActivity | null>(null);
  const chats = activity?.chats ?? [];
  const switchesBranch = !(offBase && branchMode === "continue");
  const worktreeOk = activity?.worktree_ok !== false;
  const chatBusy = chats.length > 0 && switchesBranch;
  const inWorktree = where === "worktree" || (where === "auto" && !unfit && (folderBusy || (chatBusy && worktreeOk)));
  const fakeRefused = !!err && /fake model/i.test(err.message);
  // keel 0.18.55 a project keel does not know yet: before another flow starts, ask once (Run init first / Start anyway)
  const proj = projects.find((x) => x.id === p);
  const unknown = needsInit(proj) && !isInitFlow(wfs?.find((w) => w.id === wid) ?? { id: wid });
  const [asking, setAsking] = useState(false);
  const [askedOk, setAskedOk] = useState(false);
  useEffect(() => {
    setAsking(false);
    setAskedOk(false);
  }, [p, wid]);
  const init = useStartInit(proj, onClose);
  const dirtyRefused = !!err && /uncommitted changes/i.test(err.message);

  useEffect(() => {
    setCheck(null);
    setIgnorable([]);
    setOwners([]);
    setForce(false);
    if (!p) return;
    let gone = false;
    api.startCheck(p).then((c) => { if (!gone) setCheck(c); }).catch(() => undefined);
    return () => { gone = true; };
  }, [p]);

  useEffect(() => {
    if (!p) return;
    // a slow answer for the project that was left must not land over the one picked after it
    let gone = false;
    setWfs(null);
    api.workflows(p).then((list) => {
      if (gone) return;
      setWfs(list);
      setWid((w) => (w && list.some((x) => x.id === w) ? w : list.find((x) => x.id === "feature")?.id ?? list[0]?.id ?? ""));
    }, (e) => !gone && setErr(errorParts(e)));
    api.projectSettings(p).then((s) => {
      if (gone) return;
      setModel({ provider: s.effective.default_model.provider, model: s.effective.default_model.model, effort: s.effective.default_model.effort });
      const c = kfmt(s.effective.cap_tokens);
      setCap(c);
      setCapDefault(c);
      setCapSaved("cap_tokens" in (s.overrides ?? {}) || !!s.general_saved?.includes("cap_tokens"));
      setOnCap(s.effective.on_cap);
      // v0.4.1: the project's default run mode (Settings › Run mode); this flow may pick another
      const m = s.effective.run_mode ?? "manual";
      if (!modePicked.current) setRunMode(m);
      setModeDefault(m);
    }, () => undefined);
    // v0.4.2: the project's caps can make this flow's cap smaller (the smallest one left binds) or refuse the start
    setCapsLeft(null);
    api.capsLeft(p).then((c) => !gone && setCapsLeft(c), () => undefined);
    return () => {
      gone = true;
    };
  }, [p]);

  useEffect(() => {
    api.limits().then(setLimits, () => undefined);
  }, []);

  useEffect(() => {
    setActivity(null);
    if (!p) return;
    let gone = false;
    api.folderActivity(p).then((a) => !gone && setActivity(a), () => undefined);
    return () => { gone = true; };
  }, [p]);

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

  // v0.17.5 a cap below the estimate stops the flow half way (a real run: 1.49M tokens against a 500K default): the cap
  // follows the estimate's high end plus 30% while it is still the Settings default; a cap the person typed stays.
  const follow = est && !capSaved ? capForEstimate(capDefault, est.high) : null;
  useEffect(() => {
    if (follow && cap === capDefault) setCap(follow.cap);
  }, [follow?.cap]); // eslint-disable-line react-hooks/exhaustive-deps
  const low = est && parseTokens(cap) > 0 && parseTokens(cap) < est.high;

  // keel 0.18.12 the sizes: the flows from quick to feature that this project has, each with its estimate, and the one
  // that fits the words (the person still picks; a flow that is not a size, a review say, hides the row)
  const sizes = SIZE_FLOWS.filter((s) => wfs?.some((w) => w.id === s.id));
  const suggested = suggestFlow(`${title}\n${request}`, [...sizes.map((s) => s.id), ...(wfs?.some((w) => w.id === "fix") ? ["fix"] : [])]);
  const [sizeEst, setSizeEst] = useState<Record<string, number>>({});
  const sizeIds = sizes.map((s) => s.id).join(",");
  useEffect(() => {
    if (!p || !sizeIds) return;
    let gone = false;
    setSizeEst({});
    const t = window.setTimeout(() => {
      sizeIds.split(",").forEach((id) => {
        api.estimate(p, id, acs).then((e) => !gone && setSizeEst((m) => ({ ...m, [id]: e.tokens })), () => undefined);
      });
    }, 250);
    return () => {
      gone = true;
      window.clearTimeout(t);
    };
  }, [p, acs, sizeIds]);
  const showSizes = sizes.length > 1 && (sizes.some((s) => s.id === wid) || wid === "fix");

  const premium = limits.find((l) => /premium/i.test(l.name));
  const claude = limits.find((l) => /claude/i.test(l.name));

  // Flows that build nothing (a review, a hunt, a lint …) need no "what to build": they get a title of their own.
  const needsTitle = !NO_BUILD.has(wid);
  const start = async (anyway = askedOk) => {
    if (needsTitle && !title.trim()) {
      setTitleErr(true);
      document.getElementById("sf-what")?.focus();
      return;
    }
    if (unknown && !anyway) {
      setAsking(true);
      return;
    }
    setAsking(false);
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
        // 0.18.28 always the mode shown: a flow ran in auto while the form showed important (the settings, arriving
        // after the pick, put the project's mode back; and an omitted mode followed whatever the project said by then)
        run_mode: runMode,
        // the lint flow reads its scope from the flow's data (docs/CONTRACT.md, v0.4.1); a flow from a spec its path
        ...(wid === "lint" ? { options: { scope: lintScope } } : spec ? { options: { spec: spec.path } } : {}),
        // 0.18.57 auto with a chat in the folder: the worktree this form recommends (the api's auto knows only flows)
        ...(where !== "auto" ? { where } : inWorktree && !folderBusy ? { where: "worktree" as const } : {}),
        ...(!inWorktree && offBase ? { branch: branchMode } : {}),
        ...(force ? { force: true } : {}),
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
      const body = e instanceof ApiError ? e.body ?? {} : {};
      setIgnorable(Array.isArray(body.ignorable) ? (body.ignorable as string[]) : []);
      setOwners(Array.isArray(body.owners) ? (body.owners as TaskOwner[]) : []);
    } finally {
      setBusy(false);
    }
  };

  const ignore = async (path: string) => {
    try {
      await api.excludePath(p, path);
      setIgnorable((xs) => xs.filter((x) => x !== path));
      toast(`${path} is ignored on this computer (.git/info/exclude). Press Start flow.`);
    } catch (e) {
      setErr(errorParts(e));
    }
  };

  return (
    <Drawer title="Start a flow" onClose={onClose}
      footer={<>
        <button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" onClick={() => void start()} disabled={busy || init.busy || !wid || !p}>{busy ? "Starting…" : "Start flow"}</button>
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
      {spec && (
        <div className="wbar" role="note" data-testid="sf-spec">
          <span>From the spec <b className="mono">{spec.path}</b>: the flow starts from it and keeps its criteria{spec.acs ? ` (${spec.acs})` : ""}.</span>
        </div>
      )}
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
          <option value="folder" disabled={folderBusy && !unfit}>The project folder{folderBusy ? " (a flow runs there)" : ""}</option>
          <option value="worktree">A worktree of its own, next to other flows{unfit ? " (its tests cannot run there)" : ""}</option>
        </select>
        {unfit && (
          <span className="hint amber" data-testid="sf-container">
            Runs in the project folder: {check?.worktree?.reason}.{folderBusy ? " A flow runs there now: this one can start when it ends." : ""}
          </span>
        )}
        {check?.base && <span className="hint amber" data-testid="sf-base">{check.base}.</span>}
        {check?.base_missing && p && (
          <BaseQuestion pid={p} missing={check.base_missing}
            onDone={() => { api.startCheck(p).then(setCheck).catch(() => undefined); }} />
        )}
        <span className="hint">{inWorktree
          ? "keel makes a copy of the project on a new branch from the base branch (.keel/worktrees). The project folder and its flow are not touched; the Flow page shows both, and the files they share."
          : offBase && branchMode === "continue" ? `The flow works in the project folder, on ${onBranch}.`
            : "The flow works in the project folder, on its own branch."}</span>
        {chatBusy && (
          <span className={`hint${inWorktree ? "" : " amber"}`} role={inWorktree ? undefined : "alert"} data-testid="sf-chat-busy">
            {chats.length === 1 ? <>KeelBot's chat “{chats[0].title}” ({chats[0].mode})</> : <>{chats.length} KeelBot chats</>} read the project folder now.{" "}
            {inWorktree
              ? "This flow runs in its own copy (a worktree), so the chat keeps reading the code it reads now (recommended)."
              : <>This flow checks out its own branch there, so the chat would read other code (KeelBot says so in its next answer).{" "}
                {worktreeOk
                  ? <>Use its own copy (worktree) instead? <button type="button" className="btn sm" onClick={() => setWhere("worktree")}>Use a worktree</button></>
                  : "Its own copy cannot run this project's tests here, so it runs in the folder."}</>}
          </span>
        )}
      </div>
      {!inWorktree && offBase && (
        <div className="field">
          <label htmlFor="sf-branch">Branch</label>
          <select id="sf-branch" value={branchMode} onChange={(e) => setBranchPick(e.target.value as "new" | "continue")}>
            <option value="new">A new branch from the base branch{finishes ? "" : " (recommended)"}</option>
            <option value="continue">Continue {onBranch}{finishes ? " (recommended)" : ""}</option>
          </select>
          <span className="hint">The project folder is on <b className="mono">{onBranch}</b>. {finishes
            ? `${wid} works on that branch's commits: a new branch from the base branch would leave them out.`
            : "A new branch starts from the base branch, so this flow's commits do not stack on that one."}</span>
        </div>
      )}
      <div className="field">
        <label htmlFor="sf-what">{needsTitle ? "What to build" : "Title (optional)"}</label>
        <input type="text" id="sf-what" value={title} aria-invalid={titleErr && needsTitle ? true : undefined} aria-describedby={titleErr && needsTitle ? "sf-what-err" : undefined}
          onChange={(e) => { setTitle(e.target.value); setTitleErr(false); }}
          placeholder={needsTitle ? "e.g. Players can see their rank next to the top 10" : `e.g. ${wid} of this branch`} />
        {titleErr && needsTitle && <span id="sf-what-err" className="hint amber" role="alert">Say in a few words what to build: it names the flow and its branch.</span>}
      </div>
      {needsTitle && <div className="field">
        <label htmlFor="sf-request">Describe it for the agents</label>
        <textarea id="sf-request" value={request} onChange={(e) => setRequest(e.target.value)} rows={4}
          placeholder="Who needs it, what should happen, what must not happen, where in the code if you know. The spec agent turns this into acceptance criteria." />
        {vague && title.trim() && <span className="hint amber">This is very short. The agents may not know what to build — a few sentences save a lot of tokens.</span>}
      </div>}
      {showSizes && (
        <fieldset className="field sf-sizes">
          <legend>How big is it?</legend>
          <div className="sf-size-row" role="radiogroup" aria-label="Size of the flow">
            {sizes.map((s) => {
              const w = wfs!.find((x) => x.id === s.id)!;
              const mine = suggested?.id === s.id;
              return (
                <button key={s.id} type="button" role="radio" aria-checked={wid === s.id} className={`sf-size ${wid === s.id ? "on" : ""} ${mine ? "is-suggested" : ""}`}
                  onClick={() => setWid(s.id)} title={s.about}>
                  <b>{s.id}</b>
                  <span className="sub">{w.steps.length} steps{sizeEst[s.id] ? ` · ≈ ${kfmt(sizeEst[s.id])}` : ""}</span>
                  {mine && <span className="sf-size-tag">keel suggests</span>}
                </button>
              );
            })}
          </div>
          {suggested && (
            <span className="hint" data-testid="sf-suggest">
              keel suggests <b>{suggested.id}</b>: {suggested.why}.{suggested.id !== wid && (
                <> <button type="button" className="linkbtn" onClick={() => setWid(suggested.id)}>Use {suggested.id}</button></>
              )}
            </span>
          )}
          {!suggested && <span className="hint">Say what to build, and keel suggests a size. {SIZE_FLOWS.find((s) => s.id === wid)?.about ?? ""}</span>}
        </fieldset>
      )}
      {needsTitle && <div className="field">
        <label htmlFor="sf-acs">About how many acceptance criteria</label>
        <div className="row"><input type="range" id="sf-acs" min={1} max={10} value={acs} onChange={(e) => setAcs(Number(e.target.value))} style={{ flex: "1 1 140px" }} /><b className="num">{acs}</b></div>
      </div>}
      {model && (
        <div className={model.provider === "fake" && !isDemo ? "wbar warn" : "wbar"} role="note">
          <span>Agents use <b>{model.provider === "fake" ? "the fake model" : `${model.provider} · ${model.model}${model.effort ? ` · ${model.effort}` : ""}`}</b>
            {model.provider === "fake" ? (isDemo ? " — fine for the demo." : ": it writes example files, not real code. Pick a real model in Settings › Models & logins › Use for all agents.") : "."}
            {!isDemo && " The flow works on its own branch (Settings › Branch name)."}</span>
          {model.provider === "fake" && !isDemo && <button className="btn sm" type="button" onClick={() => { onClose(); location.hash = settingsHash("models"); }}>Open Models &amp; logins</button>}
        </div>
      )}
      <div className="est-box" aria-live="polite">
        <span className="lab">Before you start</span>
        {estErr ? <span className="sub">No estimate: {estErr.message}{estErr.hint ? ` ${estErr.hint}` : ""}</span> : !est ? <span className="sub loading">Estimating…</span> : (
          <>
            <div className="est-big">
              <span className="num">{kfmt(est.tokens)}</span>
              <span className="sub">tokens expected{needsTitle ? ` for about ${acs} ACs` : ""} · range {kfmt(est.low)} – {kfmt(est.high)}</span>
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
        {follow && cap === follow.cap && <span className="hint" data-testid="sf-cap-est">{follow.note}</span>}
        {low && <span className="hint amber" data-testid="sf-cap-low">This cap is below the estimate ({kfmt(est!.high)} at the high end): the flow will {onCap === "stop" ? "stop" : "pause"} before it is done.</span>}
        <span className="hint">The estimate gets exact after the spec is approved and the real number of ACs is known. This cap is for this flow only; the project default stays in Settings.</span>
        {capsLeft && capsLeft.caps.length > 0 && <ProjectCaps left={capsLeft} />}
      </div>
      <RunModePicker value={runMode} onChange={(m) => { modePicked.current = true; setRunMode(m); }}
        hint={`The project default is ${modeDefault} (Settings › Run mode). You can change it on the Flow page while the flow runs.`} />
      {err && <ErrorBox error={err} />}
      {asking && proj && (
        <InitAsk name={proj.name || proj.id} busy={init.busy} onInit={() => void init.start()}
          onAnyway={() => { setAskedOk(true); void start(true); }} onCancel={() => setAsking(false)} />
      )}
      {/* init refused (uncommitted files, the fake model): this drawer moves to init, with its boxes for that */}
      {init.err && wid !== "init" && <ErrorBox error={init.err} />}
      {init.err && wid !== "init" && wfs?.some((w) => w.id === "init") && (
        <button className="btn" type="button" onClick={() => { setErr(init.err); setWid("init"); }}>Start init from this form</button>
      )}
      {(fakeRefused || allowFake) && (
        <label className="chk"><input type="checkbox" checked={allowFake} onChange={(e) => setAllowFake(e.target.checked)} /> Run with the fake model anyway (it writes example files and commits them on the flow's branch)</label>
      )}
      {dirtyRefused && !doctor && (
        <button className="btn" type="button" onClick={() => setDoctor(true)}>Ask the Doctor what to do with these files</button>
      )}
      {doctor && <WorkspaceDoctor pid={p} onClean={() => { setErr(null); toast("The working tree is clean. Press Start flow."); }} />}
      {ignorable.length > 0 && (
        <div className="field" data-testid="sf-ignorable">
          <span className="hint">Not your work (a tool's folder, notes)? Ignore it on this computer only:</span>
          {ignorable.map((path) => (
            <div key={path} className="row">
              <span className="mono">{path}</span>{" "}
              <button className="btn" type="button" onClick={() => void ignore(path)}
                title="adds it to .git/info/exclude (this computer only; the project's .gitignore stays)">Ignore this path</button>
            </div>
          ))}
        </div>
      )}
      {owners.length > 0 && (
        <div className="field" data-testid="sf-owners">
          <span className="hint amber">Someone already works on this:</span>
          <ul>{owners.map((o) => <li key={o.id}><b>{o.who}</b> — {o.why}</li>)}</ul>
          <label className="chk"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> Start anyway — two agents on one task write over each other</label>
        </div>
      )}
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
const BASES = new Set(["main", "master"]);
const NO_BUILD = new Set(["review", "cover", "ship", "lint", "hunt", "hunt-next", "init", "knowledge-refresh"]);
/** B7 the flows that finish the folder's branch (the api's CONTINUE_FLOWS): they continue it by default. */
const CONTINUE_FLOWS = new Set(["ship", "cover", "review", "lint", "address"]);
const finishesBranch = (w: { id: string; based_on?: string | null }) =>
  CONTINUE_FLOWS.has(w.id) || CONTINUE_FLOWS.has((w.based_on ?? "").replace(/^keel\//, ""));

/** The plan's use in words: the provider's own percentage when known, else against the cap you set, else just tokens.
 * (A missing cap once divided 3.7M tokens by 1 and showed "376640900% used".) */
export function claudeUse(l: Limit): string {
  const pct = l.used_pct != null ? l.used_pct * 100 : l.cap > 0 ? (l.used / l.cap) * 100 : null;
  if (pct == null) return `${kfmt(l.used)} tokens · no cap set`;
  return `${Math.round(pct)}% used${pct > 60 ? " — may pause" : ""}`;
}

/** v0.17.5 the cap a flow should get from the estimate: its high end plus 30%, when that is above the Settings cap
 *  (null when the Settings cap is enough, or there is none). */
export function capForEstimate(settingsCap: string, high: number): { cap: string; note: string } | null {
  const base = parseTokens(settingsCap);
  if (!(base > 0) || !(high > 0)) return null;
  const want = Math.ceil((high * 1.3) / 10_000) * 10_000;
  if (want <= base) return null;
  return { cap: kfmt(want), note: `keel raised this flow's cap from ${kfmt(base)} (Settings) to ${kfmt(want)}: the estimate's high end plus 30%. Type another cap to change it.` };
}
