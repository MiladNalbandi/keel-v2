// Stacks (Build): how each project builds and tests. A stack is one YAML file plus its skills;
// the flow never changes per language.
//
// keel 0.18.9: a stack the project added (its own, an installed pack, one from the marketplace) can be removed, after a
// yes; keel's own cannot (other projects use them), so a project turns one off instead (.keel/config.yml
// stacks.ignore). New stack is a guided form: keel reads the project's files (package.json scripts, Gradle, Maven,
// pyproject, composer.json, go.mod, a Makefile, pre-commit) and suggests the lane, what detects it, the test layers,
// the commands and the tools; keel checks the result (nothing is saved) and then saves .keel/stacks/<name>.yml.

import { Fragment, useEffect, useRef, useState } from "react";
import { api, errorParts, type Stack, type StackCheck, type StackForm, type StackSuggestion } from "../api";
import { EmptyState, Skeleton, useNarrow } from "../components/page";
import { pageKey, RdPage } from "../components/rdBuild";
import { Confirm, Drawer, ErrorBox, Loading, PageHead, Panel, Pill } from "../components/ui";
import { MARKET_STACKS, stackHref } from "../stackMarketApi";
import { useApp, useLoad } from "../state";

const FAIL_TEXT: Record<string, string> = { fix: "fix: keep its changes", block: "block: refuse the commit", warn: "warn: say so, go on" };
const ORIGIN_TEXT: Record<string, string> = {
  keel: "keel", "keel pack": "keel pack", pack: "pack (installed)", marketplace: "marketplace", project: "this project",
};

function state(x: Stack): { tone: "ok" | "warn" | null; text: string } {
  if (x.off) return { tone: "warn", text: "turned off" };
  if (x.detected) return { tone: "ok", text: "detected" };
  return { tone: null, text: "not used" };
}

export function StacksPage({ pid }: { pid: string }) {
  const { project, toast } = useApp();
  const stacks = useLoad(`stacks:${pid}`, () => api.stacks(pid), { live: false });
  const [sel, setSel] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [installing, setInstalling] = useState<string | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [yaml, setYaml] = useState<{ name: string; text: string } | null>(null);
  const narrow = useNarrow(900);
  const detail = useRef<HTMLDivElement>(null);
  const name = project?.name ?? pid;
  const put = (st: Stack) => stacks.setData((l) => (l ? (l.some((x) => x.name === st.name) ? l.map((x) => (x.name === st.name ? st : x)) : [...l, st]) : [st]));
  const fail = (what: string, e: unknown) => {
    const p = errorParts(e);
    toast(`${what}: ${p.message}${p.hint ? ` ${p.hint}` : ""}`);
  };
  const install = async (n: string) => {
    setInstalling(n);
    try {
      const st = await api.installStack(pid, n);
      if (st) put(st);
      else void stacks.reload();
      toast(`Pack ${n} installed in ${name}.`);
    } catch (e) {
      fail("Not installed", e);
    } finally {
      setInstalling(null);
    }
  };
  const remove = async (n: string) => {
    setBusy(true);
    try {
      const r = await api.removeStack(pid, n);
      toast(`${n} is removed from ${name} (${r.removed}).` + (r.back === "keel" ? ` keel's own ${n} is used again.` : ""));
      setRemoving(null);
      setYaml(null);
      await stacks.reload();
    } catch (e) {
      fail("Not removed", e);
    } finally {
      setBusy(false);
    }
  };
  const switchOff = async (x: Stack) => {
    setBusy(true);
    try {
      await api.stackOff(pid, x.name, !x.off);
      toast(x.off ? `${x.name} is turned on again for ${name}.` : `${x.name} is turned off for ${name}: keel does not use it there, and its tools do not run.`);
      await stacks.reload();
    } catch (e) {
      fail(x.off ? "Not turned on" : "Not turned off", e);
    } finally {
      setBusy(false);
    }
  };
  const showYaml = async (n: string) => {
    if (yaml?.name === n) return setYaml(null);
    try {
      setYaml({ name: n, text: (await api.stackYaml(pid, n)).yaml });
    } catch (e) {
      fail("No YAML", e);
    }
  };
  /** Ask again and say what is found in this project now (the fresh list, not the one on screen). */
  const detect = async () => {
    setDetecting(true);
    try {
      const list = await api.stacks(pid);
      stacks.setData(list);
      const found = list.filter((x) => x.detected && !x.off).map((x) => x.name);
      toast(`Detected in ${name}: ${found.join(" · ") || "nothing"}`);
    } catch (e) {
      toast(`Not detected: ${errorParts(e).message}`);
    } finally {
      setDetecting(false);
    }
  };
  const pick = (n: string) => {
    setSel(n);
    setRemoving(null);
    if (narrow) window.setTimeout(() => detail.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  };
  const installBtn = (x: Stack) => x.installable ? (
    <button className="btn sm" type="button" disabled={installing !== null} aria-label={`Install pack ${x.name}`}
      onClick={(e) => { e.stopPropagation(); void install(x.name); }}>{installing === x.name ? "Installing…" : "Install pack"}</button>
  ) : null;
  return (
    <RdPage name="stacks">
      <PageHead title="Stacks" sub="How each project builds and tests. A stack is one YAML file plus its skills; the flow is the same for every language."
        actions={<>
          <button className="btn" type="button" {...pageKey("D")} onClick={() => void detect()} disabled={detecting}>{detecting ? "Detecting…" : "Detect again"}</button>
          <a className="btn" href={MARKET_STACKS} style={{ textDecoration: "none" }}>Find stacks</a>
          <button className="btn primary" type="button" {...pageKey("N")} onClick={() => setCreating(true)}>New stack</button>
        </>} />
      {stacks.error ? <ErrorBox error={stacks.error} onRetry={() => void stacks.reload()} /> : !stacks.data ? <div className="panel"><Skeleton lines={5} label="Reading stacks" /></div> : (() => {
        const list = stacks.data;
        const cur = list.find((x) => x.name === sel) ?? list.find((x) => x.detected && !x.off) ?? list[0];
        if (!list.length) {
          return <div className="panel"><EmptyState title="No stack files found" action={<button className="btn primary" type="button" onClick={() => setCreating(true)}>New stack</button>}>keel's content has no stacks/ or packs/ folder. Check KEEL_CONTENT, or write a stack for this project.</EmptyState></div>;
        }
        const used = list.filter((x) => x.detected && !x.off).length;
        return (
          <div className="grid g2 stk-grid">
            <div className="panel">
              <div className="panel-head"><h3>{used ? `${used} of ${list.length} used in ${name}` : `None of ${list.length} found in ${name}`}</h3></div>
              <div className="table-wrap rt-wrap"><table className="rt" aria-label="Stacks">
                <thead><tr><th>Stack</th><th>Lane</th><th>From</th><th>Found by</th><th>In {name}</th></tr></thead>
                <tbody>
                  {list.map((x) => {
                    const s = state(x);
                    return (
                      <tr key={x.name} className={`click ${x.name === cur?.name ? "rowsel" : ""}`} onClick={() => pick(x.name)}>
                        <td className="rt-main"><button type="button" className="linkbtn" aria-pressed={x.name === cur?.name} onClick={(e) => { e.stopPropagation(); pick(x.name); }}>{x.name}</button></td>
                        <td><span className="chip">{x.lane}</span></td>
                        <td className="sub" data-label="from">{ORIGIN_TEXT[x.origin ?? x.source] ?? x.origin ?? x.source}{x.market?.version ? ` ${x.market.version}` : ""}</td>
                        <td className="mono sub rt-full stk-detect" data-label="found by">{x.detect}</td>
                        <td className="rt-end"><span className="row">{s.tone ? <Pill tone={s.tone}>{s.text}</Pill> : <span className="sub">{s.text}</span>}{installBtn(x)}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table></div>
            </div>
            {cur && (
              <div ref={detail} className="stk-detail">
                <Panel title={<h2>{cur.name}</h2>} extra={<span className="row"><span className="tag">{ORIGIN_TEXT[cur.origin ?? cur.source] ?? cur.source}</span>{installBtn(cur)}</span>} body="grid">
                  <div className="grid" style={{ gap: 14 }}>
                    {cur.installable && <p className="hint" style={{ margin: 0 }}>This is a keel pack. Install it to add its stack file and skills to this project (<span className="mono">keel packs add</span>).</p>}
                    {cur.off && <p className="hint" style={{ margin: 0 }}>Turned off for {name}: keel does not use it here, whatever its files say, and its tools do not run.</p>}
                    {!cur.detected && !cur.off && <p className="sub" style={{ margin: 0 }}>Not found in this project. It costs nothing until a project uses it.</p>}
                    {cur.market && (
                      <p className="sub" style={{ margin: 0 }}>From the marketplace{cur.market.catalog ? ` (${cur.market.catalog})` : ""}, version {cur.market.version}
                        {cur.market.edited ? ", changed here since" : ""}. <a href={stackHref(cur.name)}>Its page</a></p>
                    )}
                    <div className="row" role="group" aria-label={`What to do with ${cur.name}`}>
                      {cur.origin !== "keel pack" && (
                        <button className="btn sm" type="button" disabled={busy} onClick={() => void switchOff(cur)}>
                          {cur.off ? "Turn on for this project" : "Turn off for this project"}
                        </button>
                      )}
                      <button className="btn sm" type="button" onClick={() => void showYaml(cur.name)} aria-pressed={yaml?.name === cur.name}>
                        {yaml?.name === cur.name ? "Hide YAML" : "Show YAML"}
                      </button>
                      {cur.removable ? (
                        <button className="btn sm ghost" type="button" disabled={busy} onClick={() => setRemoving(cur.name)}>Remove</button>
                      ) : cur.origin === "keel" ? (
                        <span className="sub" title={cur.why ?? ""}>Ships with keel: it cannot be removed.</span>
                      ) : null}
                    </div>
                    {removing === cur.name && (
                      <Confirm text={<>Remove {cur.name} from {name}? keel deletes {cur.file ?? "its file"}{cur.origin === "pack" ? " with the pack's skills" : ""}
                        {cur.market?.edited ? ", and the changes made here" : ""}. Git keeps the old file if it was committed.</>}
                        yes="Remove" busy={busy} onYes={() => void remove(cur.name)} onNo={() => setRemoving(null)} />
                    )}
                    {yaml?.name === cur.name && (
                      <pre className="mono wfm-yaml" aria-label={`YAML of ${cur.name}`} style={{ whiteSpace: "pre-wrap", maxHeight: 420, overflow: "auto", margin: 0 }}>{yaml.text}</pre>
                    )}
                    <div className="field"><span className="lab">Test layers (lowest first)</span>
                      <div className="chips">{cur.layers.length ? cur.layers.map((l, i) => <span key={l} className="chip">{i + 1}. {l}</span>) : <span className="sub">none listed</span>}</div>
                      <span className="hint">The AC loop picks the lowest layer that can show the criterion.</span></div>
                    <div className="field"><span className="lab">Commands</span>
                      {cur.commands.length ? <div className="kv kv-col">{cur.commands.map((c) => <Fragment key={c.name}><span>{c.name}</span><b className="mono">{c.cmd}</b></Fragment>)}</div> : <span className="sub">none</span>}</div>
                    <div className="field"><span className="lab">Tools</span>
                      {cur.tools.length ? (
                        <div className="table-wrap rt-wrap"><table aria-label={`Tools of ${cur.name}`} className="rt"><thead><tr><th>Tool</th><th>Runs on</th><th>If it fails</th><th>What it does</th></tr></thead>
                          <tbody>{cur.tools.map((t) => (
                            <tr key={t.name}>
                              <td className="mono rt-main">{t.name}</td>
                              <td>{t.off ? <span className="sub">turned off</span> : <span className="chip">{t.on}</span>}</td>
                              <td className="sub">{t.off ? "—" : FAIL_TEXT[t.fail] ?? t.fail}</td>
                              <td className="sub rt-full">{t.description ?? ""}{t.kind && t.kind !== "check" ? ` (${t.kind})` : ""}</td>
                            </tr>
                          ))}</tbody></table></div>
                      ) : <span className="sub">none</span>}
                      <span className="hint">edit: after an agent changes a file · pre-commit: before each keel commit · manual: the lint flow and ship. A program that is not installed is skipped.</span></div>
                    <div className="field"><span className="lab">Skills this stack gives agents</span>
                      <div className="chips">{cur.skills.length ? cur.skills.map((k) => <a key={k} className="chip skilltag" href="#/skills" style={{ textDecoration: "none" }}>{k}</a>) : <span className="sub">ships its own testing skill</span>}</div></div>
                  </div>
                </Panel>
              </div>
            )}
          </div>
        );
      })()}
      {creating && <NewStackDrawer pid={pid} onClose={() => setCreating(false)}
        onCreated={(st) => { put(st); setSel(st.name); }} />}
    </RdPage>
  );
}

// ---------- New stack: a guided form, keel's suggestions, keel's check, then save ----------

const STEPS = ["Lane", "Detect", "Test layers", "Commands", "Tools", "Check"] as const;
type Step = (typeof STEPS)[number];
const words = (v: string) => v.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
const emptyForm = (lane = "api"): StackForm => ({ name: "", lane, detect: { files: [], package_deps: [], extensions: [] }, layers: [], commands: {}, tools: {} });

/** The form from keel's suggestion: its name, lane, what detects it, its layers and the first command keel found for each. */
function fromSuggestion(s: StackSuggestion, name?: string): StackForm {
  const commands: Record<string, string> = {};
  for (const k of s.keys) if (s.commands[k.key]?.[0]) commands[k.key] = s.commands[k.key][0].cmd;
  return {
    name: name || s.name, lane: s.lane, detect: { files: s.detect.files.slice(0, 2), package_deps: s.detect.package_deps, extensions: s.detect.extensions.slice(0, 2) },
    layers: s.layers.map((l) => ({ id: l.id, what: l.what ?? "", where: l.where ?? "" })), commands, tools: {},
    ...(Object.keys(s.skills ?? {}).length ? { skills: s.skills } : {}),
  };
}

export function NewStackDrawer({ pid, onClose, onCreated }: { pid: string; onClose: () => void; onCreated: (s: Stack) => void }) {
  const { toast } = useApp();
  const [step, setStep] = useState<Step>("Lane");
  const [lane, setLane] = useState<string | undefined>(undefined);
  const sugg = useLoad(`stack-suggest:${pid}:${lane ?? ""}`, () => api.stackSuggest(pid, lane), { live: false });
  const [form, setForm] = useState<StackForm | null>(null);
  const [check, setCheck] = useState<StackCheck | null>(null);
  const [yamlText, setYamlText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string; details?: string[] } | null>(null);
  // the first suggestion fills the form; a new lane fills it again (keeping the name the person typed)
  useEffect(() => {
    if (sugg.data) setForm((f) => fromSuggestion(sugg.data!, f?.name && f.name !== sugg.data!.name ? f.name : undefined));
  }, [sugg.data]);
  const s = sugg.data;
  const f = form ?? emptyForm(lane);
  const set = (patch: Partial<StackForm>) => setForm({ ...f, ...patch });
  const at = STEPS.indexOf(step);
  const runCheck = async (yaml?: string) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.checkStack(pid, yaml !== undefined ? { yaml } : { form: f });
      setCheck(r);
      setYamlText(r.yaml);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const go = (to: Step) => {
    setStep(to);
    if (to === "Check") void runCheck();
  };
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.saveStack(pid, yamlText);
      onCreated(r.stack);
      toast(`${r.stack.name} saved as ${r.path}. keel uses it from the next step on.`);
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const edited = !!check && yamlText !== check.yaml;
  const footer = (
    <>
      <button className="btn" type="button" onClick={at ? () => go(STEPS[at - 1]) : onClose} disabled={busy}>{at ? "Back" : "Cancel"}</button>
      {step !== "Check" ? (
        <button className="btn primary" type="button" onClick={() => go(STEPS[at + 1])} disabled={!form || (step === "Lane" && !f.name.trim())}>Next</button>
      ) : edited ? (
        <button className="btn primary" type="button" onClick={() => void runCheck(yamlText)} disabled={busy}>{busy ? "Checking…" : "Check again"}</button>
      ) : (
        <button className="btn primary" type="button" onClick={() => void save()} disabled={busy || !check?.ok}>{busy ? "Saving…" : "Save the stack"}</button>
      )}
    </>
  );
  return (
    <Drawer title="New stack" onClose={onClose} footer={footer} id="st-new">
      <ol className="chips" aria-label="Steps" style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {STEPS.map((x, i) => (
          <li key={x}><button type="button" className="chip" aria-current={x === step ? "step" : undefined} disabled={!form || busy}
            onClick={() => go(x)} style={{ fontWeight: x === step ? 700 : 400 }}>{i + 1}. {x}</button></li>
        ))}
      </ol>
      {sugg.error && <ErrorBox error={sugg.error} onRetry={() => void sugg.reload()} />}
      {!form && !sugg.error && <Loading what="Reading the project's files" />}
      {form && step === "Lane" && (
        <>
          <p className="hint" style={{ margin: 0 }}>
            keel read {s?.read.length ? s.read.join(", ") : "no build file it knows"}{s?.closest ? `; it is most like keel's ${s.closest} stack` : ""}.
            The next steps are filled from what it found; change anything.
          </p>
          <div className="field" role="radiogroup" aria-label="Lane">
            <span className="lab">Lane: which criteria it builds and tests</span>
            {(["api", "web"] as const).map((l) => (
              <label key={l} className="radio">
                <input type="radio" name="st-lane" checked={f.lane === l} onChange={() => { setLane(l); set({ lane: l }); }} />
                <span><b>{l}</b> — {l === "api" ? "a backend: the API criteria" : "a frontend: the WEB criteria"}
                  {s?.lanes?.[l]?.length ? <span className="sub"> ({s.lanes[l].join("; ")})</span> : null}</span>
              </label>
            ))}
          </div>
          <div className="field"><label htmlFor="st-name">Name</label>
            <input type="text" id="st-name" value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. my-api" />
            <span className="hint">Lower-case words with dashes. Saved as <span className="mono">.keel/stacks/{f.name || "<name>"}.yml</span>.</span></div>
        </>
      )}
      {form && step === "Detect" && (
        <>
          <p className="hint" style={{ margin: 0 }}>What shows a project uses this stack. One match is enough. A project's own stack is used anyway; this matters when you share it.</p>
          {(["files", "extensions", "package_deps"] as const).map((k) => (
            <div key={k} className="field">
              <label htmlFor={`st-${k}`}>{k === "files" ? "Files (in the root or one folder down)" : k === "extensions" ? "File endings" : "package.json dependencies"}</label>
              <input type="text" id={`st-${k}`} value={(f.detect[k] ?? []).join(", ")} onChange={(e) => set({ detect: { ...f.detect, [k]: words(e.target.value) } })} />
              {!!s?.detect[k]?.length && (
                <span className="chips">{s.detect[k].filter((x) => !(f.detect[k] ?? []).includes(x)).map((x) => (
                  <button key={x} type="button" className="chip" onClick={() => set({ detect: { ...f.detect, [k]: [...(f.detect[k] ?? []), x] } })}>+ {x}</button>
                ))}</span>
              )}
            </div>
          ))}
        </>
      )}
      {form && step === "Test layers" && (
        <>
          <p className="hint" style={{ margin: 0 }}>Lowest first. The AC loop picks the lowest layer that can show a criterion; agents read what each tests and where.</p>
          {f.layers.map((l, i) => (
            <div key={i} className="row" style={{ alignItems: "flex-end", flexWrap: "wrap" }}>
              {(["id", "what", "where"] as const).map((k) => (
                <div key={k} className="field" style={{ flex: k === "what" ? 2 : 1, minWidth: 90 }}>
                  <label htmlFor={`st-l${i}-${k}`}>{k}</label>
                  <input type="text" id={`st-l${i}-${k}`} value={l[k] ?? ""} onChange={(e) => set({ layers: f.layers.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)) })} />
                </div>
              ))}
              <button type="button" className="btn sm ghost" aria-label={`Remove layer ${l.id || i + 1}`} onClick={() => set({ layers: f.layers.filter((_, j) => j !== i) })}>×</button>
            </div>
          ))}
          <button type="button" className="btn sm" onClick={() => set({ layers: [...f.layers, { id: "", what: "", where: "" }] })}>Add a layer</button>
        </>
      )}
      {form && step === "Commands" && (
        <>
          <p className="hint" style={{ margin: 0 }}>The project's own commands. keel fills <span className="mono">{"{AC}"}</span> (AC-6), <span className="mono">{"{AC_ID}"}</span> (AC6), <span className="mono">{"{AC_KEY}"}</span>, <span className="mono">{"{PKG}"}</span> and <span className="mono">{"{PATHS}"}</span>. Leave a line empty when the project has none.</p>
          {(s?.keys ?? []).map((k) => (
            <div key={k.key} className="field">
              <label htmlFor={`st-c-${k.key}`}>{k.what} <span className="sub mono">{k.key}</span></label>
              <input type="text" id={`st-c-${k.key}`} className="mono" value={f.commands[k.key] ?? ""} placeholder={k.placeholder ? `uses ${k.placeholder}` : ""}
                onChange={(e) => set({ commands: { ...f.commands, [k.key]: e.target.value } })} />
              {!!s?.commands[k.key]?.length && (
                <span className="chips">{s.commands[k.key].filter((x) => x.cmd !== f.commands[k.key]).slice(0, 3).map((x) => (
                  <button key={x.cmd} type="button" className="chip" title={`from ${x.from}`} onClick={() => set({ commands: { ...f.commands, [k.key]: x.cmd } })}>
                    <span className="mono">{x.cmd}</span> <span className="sub">· {x.from}</span>
                  </button>
                ))}</span>
              )}
            </div>
          ))}
        </>
      )}
      {form && step === "Tools" && (
        <>
          <p className="hint" style={{ margin: 0 }}>Programs keel runs itself: after an agent edits a file, or before each commit. A program that is not installed is skipped.</p>
          {(s?.tools ?? []).length ? (s?.tools ?? []).map((t) => (
            <label key={t.name} className="chk">
              <input type="checkbox" checked={!!f.tools[t.name]} onChange={(e) => {
                const next = { ...f.tools };
                if (e.target.checked) next[t.name] = { run: t.run, on: t.on, fail: t.fail, ...(t.match ? { match: t.match } : {}), ...(t.description ? { description: t.description } : {}) };
                else delete next[t.name];
                set({ tools: next });
              }} />{" "}
              <b>{t.name}</b> <span className="mono sub">{t.run}</span> <span className="sub">· on {t.on} · {FAIL_TEXT[t.fail] ?? t.fail}{t.from ? ` · from ${t.from}` : ""}</span>
            </label>
          )) : <span className="sub">keel found no formatter or pre-commit setup in this project. You can add tools in the YAML on the next step.</span>}
        </>
      )}
      {form && step === "Check" && (
        <>
          {!check ? <Loading what="keel checks the stack" /> : (
            <div className="grid" style={{ gap: 6 }} aria-label="keel's check">
              {check.ok ? <Pill tone="ok">keel can save it</Pill> : <Pill tone="bad">keel cannot save it yet</Pill>}
              {check.errors.map((x) => <span key={x} role="alert" className="sub">error: {x}</span>)}
              {check.warnings.map((x) => <span key={x} className="sub">warning: {x}</span>)}
              {!!check.skills_missing?.length && <span className="sub">Skills it names that this project does not have: {check.skills_missing.join(", ")}.</span>}
            </div>
          )}
          <div className="field"><label htmlFor="st-yaml">The stack's YAML (nothing is saved yet)</label>
            <textarea id="st-yaml" className="mono" rows={16} value={yamlText} onChange={(e) => setYamlText(e.target.value)} />
            <span className="hint">Change it here if you like, then check again. Or ask KeelBot: "write a stack for this project".</span></div>
        </>
      )}
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}
