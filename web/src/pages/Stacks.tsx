// Stacks (Build): how each project builds and tests. A stack is one YAML file plus its skills;
// the flow never changes per language.

import { Fragment, useState } from "react";
import { api, errorParts, type Stack } from "../api";
import { Async, Drawer, ErrorBox, PageHead, Panel, Pill } from "../components/ui";
import { useApp, useLoad } from "../state";

const FAIL_TEXT: Record<string, string> = { fix: "fix: keep its changes", block: "block: refuse the commit", warn: "warn: say so, go on" };

export function StacksPage({ pid }: { pid: string }) {
  const { project, toast } = useApp();
  const stacks = useLoad(`stacks:${pid}`, () => api.stacks(pid), { live: false });
  const [sel, setSel] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [installing, setInstalling] = useState<string | null>(null);
  const put = (st: Stack) => stacks.setData((l) => (l ? (l.some((x) => x.name === st.name) ? l.map((x) => (x.name === st.name ? st : x)) : [...l, st]) : [st]));
  const install = async (name: string) => {
    setInstalling(name);
    try {
      const st = await api.installStack(pid, name);
      if (st) put(st);
      else void stacks.reload();
      toast(`Pack ${name} installed in ${project?.name ?? pid}.`);
    } catch (e) {
      const p = errorParts(e);
      toast(`Not installed: ${p.message}${p.hint ? ` ${p.hint}` : ""}`);
    } finally {
      setInstalling(null);
    }
  };
  const installBtn = (x: Stack) => x.installable ? (
    <button className="btn sm" type="button" disabled={installing !== null} aria-label={`Install pack ${x.name}`}
      onClick={(e) => { e.stopPropagation(); void install(x.name); }}>{installing === x.name ? "Installing…" : "Install pack"}</button>
  ) : null;
  return (
    <>
      <PageHead title="Stacks" sub="How each project builds and tests. A stack is one YAML file plus its skills; the flow never changes per language."
        actions={<><button className="btn" type="button" onClick={async () => {
          await stacks.reload();
          const found = (stacks.data ?? []).filter((s) => s.detected).map((s) => s.name);
          toast(`Detected in ${project?.name ?? pid}: ${found.join(" · ") || "nothing"}`);
        }}>Detect again</button><button className="btn primary" type="button" onClick={() => setCreating(true)}>New stack</button></>} />
      <Async r={stacks} what="Reading stacks">
        {(list) => {
          const cur = list.find((s) => s.name === sel) ?? list.find((s) => s.detected) ?? list[0];
          return !list.length ? <div className="empty">No stack files found in keel. Is KEEL_HOME set?</div> : (
            <div className="grid g2">
              <div className="panel"><div className="table-wrap"><table>
                <thead><tr><th>Stack</th><th>Lane</th><th>From</th><th>Found by</th><th>In {project?.name ?? pid}</th></tr></thead>
                <tbody>
                  {list.map((x) => (
                    <tr key={x.name} className={`click ${x.name === cur?.name ? "rowsel" : ""}`} tabIndex={0} onClick={() => setSel(x.name)} onKeyDown={(e) => e.key === "Enter" && setSel(x.name)}>
                      <td><b>{x.name}</b></td><td><span className="tag">{x.lane}</span></td><td className="sub">{x.source}</td><td className="mono sub">{x.detect}</td>
                      <td><span className="row">{x.detected ? <Pill tone="ok">detected</Pill> : <span className="sub">not used</span>}{installBtn(x)}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table></div></div>
              {cur && (
                <Panel title={<h2>{cur.name}</h2>} extra={<span className="row"><span className="tag">{cur.source}</span>{installBtn(cur)}</span>} body="grid">
                  <div className="grid" style={{ gap: 14 }}>
                    {cur.installable && <p className="hint" style={{ margin: 0 }}>This is a keel pack. Install it to add its stack file and skills to this project (<span className="mono">keel packs add</span>).</p>}
                    {!cur.detected && <p className="sub" style={{ margin: 0 }}>Not found in this project. It costs nothing until a project uses it.</p>}
                    <div className="field"><span className="lab">Test layers (lowest first)</span>
                      <div className="row">{cur.layers.length ? cur.layers.map((l, i) => <span key={l} className="tag">{i + 1}. {l}</span>) : <span className="sub">none listed</span>}</div>
                      <span className="hint">The AC loop picks the lowest layer that can show the criterion.</span></div>
                    <div className="field"><span className="lab">Commands</span>
                      {cur.commands.length ? <div className="kv kv-col">{cur.commands.map((c) => <Fragment key={c.name}><span>{c.name}</span><b className="mono">{c.cmd}</b></Fragment>)}</div> : <span className="sub">none</span>}</div>
                    <div className="field"><span className="lab">Tools</span>
                      {cur.tools.length ? (
                        <div className="table-wrap"><table aria-label={`Tools of ${cur.name}`}><thead><tr><th>Tool</th><th>Runs on</th><th>If it fails</th><th>What it does</th></tr></thead>
                          <tbody>{cur.tools.map((t) => (
                            <tr key={t.name}>
                              <td className="mono">{t.name}</td>
                              <td>{t.off ? <span className="sub">turned off</span> : <span className="tag">{t.on}</span>}</td>
                              <td className="sub">{t.off ? "—" : FAIL_TEXT[t.fail] ?? t.fail}</td>
                              <td className="sub">{t.description ?? ""}{t.kind && t.kind !== "check" ? ` (${t.kind})` : ""}</td>
                            </tr>
                          ))}</tbody></table></div>
                      ) : <span className="sub">none</span>}
                      <span className="hint">edit: after an agent changes a file · pre-commit: before each keel commit · manual: the lint flow and ship. A program that is not installed is skipped.</span></div>
                    <div className="field"><span className="lab">Skills this stack gives agents</span>
                      <div className="row">{cur.skills.length ? cur.skills.map((k) => <a key={k} className="tag skilltag" href="#/skills" style={{ textDecoration: "none" }}>{k}</a>) : <span className="sub">ships its own testing skill</span>}</div></div>
                  </div>
                </Panel>
              )}
            </div>
          );
        }}
      </Async>
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
