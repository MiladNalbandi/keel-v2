// Stacks (Build): how each project builds and tests. A stack is one YAML file plus its skills;
// the flow never changes per language.

import { Fragment, useRef, useState } from "react";
import { api, errorParts, type Stack } from "../api";
import { EmptyState, Skeleton, useNarrow } from "../components/page";
import { Drawer, ErrorBox, PageHead, Panel, Pill } from "../components/ui";
import { useApp, useLoad } from "../state";

const FAIL_TEXT: Record<string, string> = { fix: "fix: keep its changes", block: "block: refuse the commit", warn: "warn: say so, go on" };

export function StacksPage({ pid }: { pid: string }) {
  const { project, toast } = useApp();
  const stacks = useLoad(`stacks:${pid}`, () => api.stacks(pid), { live: false });
  const [sel, setSel] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [installing, setInstalling] = useState<string | null>(null);
  const [detecting, setDetecting] = useState(false);
  const narrow = useNarrow(900);
  const detail = useRef<HTMLDivElement>(null);
  const name = project?.name ?? pid;
  const put = (st: Stack) => stacks.setData((l) => (l ? (l.some((x) => x.name === st.name) ? l.map((x) => (x.name === st.name ? st : x)) : [...l, st]) : [st]));
  const install = async (n: string) => {
    setInstalling(n);
    try {
      const st = await api.installStack(pid, n);
      if (st) put(st);
      else void stacks.reload();
      toast(`Pack ${n} installed in ${name}.`);
    } catch (e) {
      const p = errorParts(e);
      toast(`Not installed: ${p.message}${p.hint ? ` ${p.hint}` : ""}`);
    } finally {
      setInstalling(null);
    }
  };
  /** Ask again and say what is found in this project now (the fresh list, not the one on screen). */
  const detect = async () => {
    setDetecting(true);
    try {
      const list = await api.stacks(pid);
      stacks.setData(list);
      const found = list.filter((x) => x.detected).map((x) => x.name);
      toast(`Detected in ${name}: ${found.join(" · ") || "nothing"}`);
    } catch (e) {
      toast(`Not detected: ${errorParts(e).message}`);
    } finally {
      setDetecting(false);
    }
  };
  const pick = (n: string) => {
    setSel(n);
    if (narrow) window.setTimeout(() => detail.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  };
  const installBtn = (x: Stack) => x.installable ? (
    <button className="btn sm" type="button" disabled={installing !== null} aria-label={`Install pack ${x.name}`}
      onClick={(e) => { e.stopPropagation(); void install(x.name); }}>{installing === x.name ? "Installing…" : "Install pack"}</button>
  ) : null;
  return (
    <>
      <PageHead title="Stacks" sub="How each project builds and tests. A stack is one YAML file plus its skills; the flow is the same for every language."
        actions={<>
          <button className="btn" type="button" onClick={() => void detect()} disabled={detecting}>{detecting ? "Detecting…" : "Detect again"}</button>
          <button className="btn primary" type="button" onClick={() => setCreating(true)}>New stack</button>
        </>} />
      {stacks.error ? <ErrorBox error={stacks.error} onRetry={() => void stacks.reload()} /> : !stacks.data ? <div className="panel"><Skeleton lines={5} label="Reading stacks" /></div> : (() => {
        const list = stacks.data;
        const cur = list.find((x) => x.name === sel) ?? list.find((x) => x.detected) ?? list[0];
        if (!list.length) {
          return <div className="panel"><EmptyState title="No stack files found" action={<button className="btn primary" type="button" onClick={() => setCreating(true)}>New stack</button>}>keel's content has no stacks/ or packs/ folder. Check KEEL_CONTENT, or write a stack for this project.</EmptyState></div>;
        }
        const used = list.filter((x) => x.detected).length;
        return (
          <div className="grid g2 stk-grid">
            <div className="panel">
              <div className="panel-head"><h3>{used ? `${used} of ${list.length} used in ${name}` : `None of ${list.length} found in ${name}`}</h3></div>
              <div className="table-wrap"><table className="rt" aria-label="Stacks">
                <thead><tr><th>Stack</th><th>Lane</th><th>From</th><th>Found by</th><th>In {name}</th></tr></thead>
                <tbody>
                  {list.map((x) => (
                    <tr key={x.name} className={`click ${x.name === cur?.name ? "rowsel" : ""}`} onClick={() => pick(x.name)}>
                      <td className="rt-main"><button type="button" className="linkbtn" aria-pressed={x.name === cur?.name} onClick={(e) => { e.stopPropagation(); pick(x.name); }}>{x.name}</button></td>
                      <td><span className="chip">{x.lane}</span></td>
                      <td className="sub" data-label="from">{x.source}</td>
                      <td className="mono sub rt-full stk-detect" data-label="found by">{x.detect}</td>
                      <td className="rt-end"><span className="row">{x.detected ? <Pill tone="ok">detected</Pill> : <span className="sub">not used</span>}{installBtn(x)}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            </div>
            {cur && (
              <div ref={detail} className="stk-detail">
                <Panel title={<h2>{cur.name}</h2>} extra={<span className="row"><span className="tag">{cur.source}</span>{installBtn(cur)}</span>} body="grid">
                  <div className="grid" style={{ gap: 14 }}>
                    {cur.installable && <p className="hint" style={{ margin: 0 }}>This is a keel pack. Install it to add its stack file and skills to this project (<span className="mono">keel packs add</span>).</p>}
                    {!cur.detected && <p className="sub" style={{ margin: 0 }}>Not found in this project. It costs nothing until a project uses it.</p>}
                    <div className="field"><span className="lab">Test layers (lowest first)</span>
                      <div className="chips">{cur.layers.length ? cur.layers.map((l, i) => <span key={l} className="chip">{i + 1}. {l}</span>) : <span className="sub">none listed</span>}</div>
                      <span className="hint">The AC loop picks the lowest layer that can show the criterion.</span></div>
                    <div className="field"><span className="lab">Commands</span>
                      {cur.commands.length ? <div className="kv kv-col">{cur.commands.map((c) => <Fragment key={c.name}><span>{c.name}</span><b className="mono">{c.cmd}</b></Fragment>)}</div> : <span className="sub">none</span>}</div>
                    <div className="field"><span className="lab">Tools</span>
                      {cur.tools.length ? (
                        <div className="table-wrap"><table aria-label={`Tools of ${cur.name}`} className="rt"><thead><tr><th>Tool</th><th>Runs on</th><th>If it fails</th><th>What it does</th></tr></thead>
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
      {creating && <NewStackDrawer pid={pid} stacks={stacks.data ?? []} onClose={() => setCreating(false)}
        onCreated={(st) => { put(st); setSel(st.name); }} />}
    </>
  );
}

function NewStackDrawer({ pid, stacks, onClose, onCreated }: { pid: string; stacks: Stack[]; onClose: () => void; onCreated: (s: Stack) => void }) {
  const { toast } = useApp();
  const [name, setName] = useState("");
  const [from, setFrom] = useState(stacks.find((s) => s.detected)?.name ?? stacks[0]?.name ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const st = await api.newStack(pid, name.trim(), from);
      onCreated(st);
      toast(`${st?.name ?? name} saved as .keel/stacks/${st?.name ?? name}.yml. Edit its commands there.`);
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer title="New stack" onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" onClick={save} disabled={busy || !name.trim() || !from}>{busy ? "Saving…" : "Create stack"}</button></>}>
      <div className="field"><label htmlFor="st-name">Name</label>
        <input type="text" id="st-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. kotlin-ktor" /></div>
      <div className="field"><label htmlFor="st-from">Start from</label>
        <select id="st-from" value={from} onChange={(e) => setFrom(e.target.value)}>
          {stacks.map((s) => <option key={s.name} value={s.name}>{s.name}{s.detected ? " (detected)" : ""}</option>)}
        </select>
        <span className="hint">keel copies this stack's YAML into <span className="mono">.keel/stacks/</span> in the repo. Change the commands and test layers there.</span></div>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}
