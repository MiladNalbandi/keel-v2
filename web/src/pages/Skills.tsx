// Skill hub (Build): skills agents use in this project — keel skills for its stacks, packs, Claude skills,
// and skills only this project has. Open one to see its size, who loads it and when, and edit SKILL.md.

import { Fragment, useState } from "react";
import { api, errorParts, type Agent, type Skill, type Stack } from "../api";
import { Async, Drawer, ErrorBox, Loading, PageHead, Panel, Tabs } from "../components/ui";
import { useApp, useLoad } from "../state";

type Src = "all" | "keel" | "keel pack" | "claude" | "yours";
const SRC_LABEL: Record<Src, string> = { all: "All", keel: "keel", "keel pack": "keel pack", claude: "Claude skills", yours: "yours" };
const k1 = (n: number) => `${(n / 1000).toFixed(1)}k`;

function SkillDrawer({ pid, id, agents, onClose, onSaved }: { pid: string; id: string; agents: Agent[]; onClose: () => void; onSaved: () => void }) {
  const { toast } = useApp();
  const sk = useLoad(`skill:${id}`, () => api.skill(id), { live: false });
  const [who, setWho] = useState<string[] | null>(null);
  const [when, setWhen] = useState<string | null>(null);
  const [body, setBody] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const s = sk.data;
  const save = async () => {
    if (!s) return;
    setBusy(true);
    setErr(null);
    try {
      await api.saveSkill(pid, id, {
        ...(who !== null ? { agents: who } : {}), ...(when !== null ? { when } : {}), ...(body !== null ? { body } : {}),
      });
      toast(`Saved ${id}.`);
      onSaved();
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const agentsNow = who ?? s?.agents ?? [];
  return (
    <Drawer title={id} onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" onClick={save} disabled={busy || !s || (who === null && when === null && body === null)}>{busy ? "Saving…" : "Save"}</button></>}>
      {sk.error ? <ErrorBox error={sk.error} /> : !s ? <Loading /> : (
        <>
          <div className="row"><span className="tag">{s.kind}</span><span className="tag">{s.stack}</span><span className="sub">{s.source} · version {s.version}</span></div>
          <div className="field"><span className="lab">Size</span>
            <div className="kv">
              <span>SKILL.md (always loaded)</span><b className="num mono">{k1(s.tokens)} tokens</b>
              {s.refs.map((r) => <Fragment key={r.path}><span>{r.path} (only when asked)</span><b className="num mono">{k1(r.tokens)}</b></Fragment>)}
            </div>
          </div>
          <div className="field"><span className="lab">Load it for</span>
            <div className="grid" style={{ gap: 6 }}>
              {agents.map((a) => (
                <label key={a.id} className="chk">
                  <input type="checkbox" checked={agentsNow.includes(a.id)}
                    onChange={(e) => setWho(e.target.checked ? [...agentsNow, a.id] : agentsNow.filter((x) => x !== a.id))} />{" "}
                  {a.label || a.id} <span className="sub">{a.phases.join(", ")}</span>
                </label>
              ))}
              {!agents.length && <span className="sub">No agents loaded.</span>}
            </div>
          </div>
          <div className="field"><label htmlFor="sk-when">When</label>
            <input type="text" id="sk-when" value={when ?? s.when} onChange={(e) => setWhen(e.target.value)} />
            <span className="hint">phase · layer · stack. Empty means "on demand": the agent sees the name and asks for it.</span></div>
          <div className="field"><label htmlFor="sk-body">SKILL.md</label>
            <textarea id="sk-body" value={body ?? s.body} onChange={(e) => setBody(e.target.value)} style={{ minHeight: 260 }} readOnly={s.source === "keel"} />
            {s.source === "keel" && <span className="hint">keel's own skills are read-only here. Copy it as a new skill to change it.</span>}
          </div>
          {err && <ErrorBox error={err} />}
        </>
      )}
    </Drawer>
  );
}

function NewSkillDrawer({ pid, stacks, onClose, onCreated }: { pid: string; stacks: Stack[]; onClose: () => void; onCreated: () => void }) {
  const { project, toast } = useApp();
  const [name, setName] = useState("");
  const [kind, setKind] = useState("knowledge");
  const [stack, setStack] = useState("any");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const tpl = `---\nname: ${name || "my-skill"}\ndescription: What this is about. Load when …\n---\n\n# ${name || "My skill"}\n- `;
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.newSkill(pid, { name: name.trim(), kind, stack, body: body || tpl });
      toast(`${name} saved. Assign it to agents in its details.`);
      onCreated();
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer title="New skill" onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" id="saveSkill" onClick={save} disabled={busy || !name.trim()}>{busy ? "Saving…" : "Save skill"}</button></>}>
      <div className="field"><label htmlFor="ns-name">Name</label><input type="text" id="ns-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={`e.g. ${project?.id ?? "project"}-domain-rules`} /></div>
      <div className="field"><label htmlFor="ns-kind">Kind</label>
        <select id="ns-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          {["knowledge", "testing", "implementation", "placement", "debugging", "review"].map((k) => <option key={k}>{k}</option>)}
        </select></div>
      <div className="field"><label htmlFor="ns-stack">Stack</label>
        <select id="ns-stack" value={stack} onChange={(e) => setStack(e.target.value)}>
          <option>any</option>{stacks.map((x) => <option key={x.name}>{x.name}</option>)}
        </select></div>
      <div className="field"><label htmlFor="ns-body">SKILL.md</label>
        <textarea id="ns-body" value={body || tpl} onChange={(e) => setBody(e.target.value)} style={{ minHeight: 200 }} />
        <span className="hint">≈ {k1(Math.round((body || tpl).length / 4))} tokens. Add reference files for long details; they load only when asked.</span></div>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

function ImportSkillDrawer({ pid, onClose, onImported }: { pid: string; onClose: () => void; onImported: (s: Skill) => void }) {
  const { toast } = useApp();
  const [how, setHow] = useState<"url" | "paste">("url");
  const [url, setUrl] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const urlOk = /^https?:\/\/\S+$/i.test(url.trim());
  const ready = how === "url" ? urlOk : body.trim().length > 0;
  const save = async () => {
    if (!ready) {
      setErr(how === "url" ? { message: "Write a link that starts with http:// or https://." } : { message: "Paste the SKILL.md text first." });
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const sk = await api.importSkill(pid, how === "url" ? { url: url.trim() } : { body });
      toast(`${sk?.id ?? "Skill"} imported. Assign it to agents in its details.`);
      onImported(sk);
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer title="Import a skill" onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" onClick={save} disabled={busy}>{busy ? "Importing…" : "Import"}</button></>}>
      <Tabs value={how} onChange={(v) => { setHow(v); setErr(null); }} label="Import from" options={[["url", "From a link"], ["paste", "Paste SKILL.md"]]} />
      {how === "url" ? (
        <div className="field"><label htmlFor="isk-url">Link to a SKILL.md</label>
          <input type="text" id="isk-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://raw.githubusercontent.com/…/SKILL.md" />
          <span className="hint">http or https, up to 256 KB. keel reads it once and keeps a copy in this project.</span></div>
      ) : (
        <div className="field"><label htmlFor="isk-body">SKILL.md</label>
          <textarea id="isk-body" value={body} onChange={(e) => setBody(e.target.value)} style={{ minHeight: 240 }}
            placeholder={"---\nname: my-skill\ndescription: What it is about. Load when …\n---\n\n# My skill"} />
          <span className="hint">The name and description come from the front matter at the top.</span></div>
      )}
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

export function SkillsPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const skills = useLoad(`skills:${pid}`, () => api.skills(pid), { live: false });
  const agents = useLoad(`agents:${pid}`, () => api.agents(pid), { live: false });
  const stacks = useLoad(`stacks:${pid}`, () => api.stacks(pid), { live: false });
  const [filter, setFilter] = useState<Src>("all");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const needle = q.trim().toLowerCase();
  const match = (s: Skill) => (filter === "all" || s.source === filter) && (!needle || (s.id + s.kind + s.stack).toLowerCase().includes(needle));
  return (
    <>
      <PageHead title="Skill hub" sub={`Skills agents use in ${project?.name ?? pid}: keel skills for its stacks, plus skills only this project has.`}
        actions={<>
          <button className="btn" type="button" onClick={() => setImporting(true)}>Import</button>
          <button className="btn primary" type="button" id="newSkill" onClick={() => setCreating(true)}>New skill</button>
        </>} />
      <div className="row" style={{ marginBottom: 12, justifyContent: "space-between" }}>
        <Tabs value={filter} onChange={setFilter} label="Source" options={(Object.keys(SRC_LABEL) as Src[]).map((k) => [k, SRC_LABEL[k]])} />
        <input type="text" id="skq" className="inline-input" placeholder="Search skills" aria-label="Search skills" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: "0 1 220px" }} />
      </div>
      <Async r={skills} what="Loading skills">
        {(all) => {
          const list = all.filter(match);
          return (
            <div className="panel"><div className="table-wrap"><table>
              <thead><tr><th>Skill</th><th>Kind</th><th>Stack</th><th>Loaded by</th><th>When</th><th>Size</th><th></th></tr></thead>
              <tbody>
                {list.map((s) => (
                  <tr key={s.id} className="click" tabIndex={0} onClick={() => setOpen(s.id)} onKeyDown={(e) => e.key === "Enter" && setOpen(s.id)}>
                    <td><b>{s.id}</b> {s.version === "draft" && <span className="tag star">draft</span>}<div className="sub">{SRC_LABEL[s.source as Src] ?? s.source} · {s.version}</div></td>
                    <td><span className="tag">{s.kind}</span></td>
                    <td className="mono sub">{s.stack}</td>
                    <td>{s.agents.length ? s.agents.map((a) => <span key={a} className="tag" style={{ marginRight: 4 }}>{a}</span>) : <span className="sub">nobody</span>}</td>
                    <td className="sub">{s.when || "on demand"}</td>
                    <td className="num mono">{k1(s.tokens)}</td>
                    <td>{s.enabled ? <span className="pill p-ok">on</span> : <button className="btn sm" type="button" onClick={(e) => { e.stopPropagation(); setOpen(s.id); }}>Add</button>}</td>
                  </tr>
                ))}
                {!list.length && <tr><td colSpan={7} className="empty">No skill matches.</td></tr>}
              </tbody>
            </table></div></div>
          );
        }}
      </Async>
      <div className="grid g2" style={{ marginTop: 16 }}>
        <Panel title="Flow skills became the graph" body="grid">
          <div className="grid" style={{ gap: 8 }}>
            <p className="sub" style={{ margin: 0 }}>In keel v1 the model reads the flow rules as text every session. In keel v2 the graph holds them, so agents never load these:</p>
            <div className="row">{["feature", "ship", "hunt", "init", "fix", "change"].map((f) => <span key={f} className="tag">{f}</span>)}</div>
          </div>
        </Panel>
        <Panel title="How a skill is loaded" body="grid">
          <div className="grid" style={{ gap: 6 }}>
            <span>1. The node asks: <span className="mono">skills for (phase, layer, stack)</span></span>
            <span>2. Only the matching skill's <b>SKILL.md</b> goes in the prompt</span>
            <span>3. A reference file is opened only when the agent asks (one at a time)</span>
            <span className="hint">Skill tokens count in the estimate and the budget like any other input.</span>
          </div>
        </Panel>
      </div>
      {open && <SkillDrawer pid={pid} id={open} agents={agents.data ?? []} onClose={() => setOpen(null)} onSaved={() => void skills.reload()} />}
      {importing && <ImportSkillDrawer pid={pid} onClose={() => setImporting(false)} onImported={() => { setFilter("yours"); void skills.reload(); }} />}
      {creating && <NewSkillDrawer pid={pid} stacks={stacks.data ?? []} onClose={() => setCreating(false)} onCreated={() => { setFilter("yours"); void skills.reload(); }} />}
    </>
  );
}
