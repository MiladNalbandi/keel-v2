// Skill hub (Build): skills agents use in this project — keel skills for its stacks, packs, Claude skills,
// and skills only this project has. Open one to see its size, who loads it and when, and edit SKILL.md.

import { Fragment, useState } from "react";
import { api, errorParts, type Agent, type RepoSkills, type Skill, type Stack } from "../api";
import { EmptyState, SearchBox, Skeleton, Toolbar } from "../components/page";
import { pageKey, RdPage } from "../components/rdBuild";
import { Drawer, ErrorBox, Loading, PageHead, Panel, Tabs } from "../components/ui";
import { useApp, useLoad } from "../state";

type Src = "all" | "keel" | "keel pack" | "claude" | "repo" | "yours" | "marketplace";
const SRC_LABEL: Record<Src, string> = { all: "All", keel: "keel", "keel pack": "keel pack", claude: "Claude skills", repo: "from the repo", yours: "yours", marketplace: "marketplace" };
const k1 = (n: number) => `${(n / 1000).toFixed(1)}k`;

function SkillDrawer({ pid, id, agents, onClose, onSaved }: { pid: string; id: string; agents: Agent[]; onClose: () => void; onSaved: () => void }) {
  const { toast } = useApp();
  const sk = useLoad(`skill:${pid}:${id}`, () => api.skill(id, pid), { live: false });
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
              {s.refs.map((r) => <Fragment key={r.path}><span>{r.path} (only when asked)</span><b className="num mono">{k1(r.tokens)} tokens</b></Fragment>)}
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
            <textarea id="sk-body" value={body ?? s.body} onChange={(e) => setBody(e.target.value)} style={{ minHeight: 260 }} readOnly={s.source === "keel" || s.source === "repo"} />
            {s.source === "keel" && <span className="hint">keel's own skills are read-only here. Copy it as a new skill to change it.</span>}
            {s.source === "repo" && <span className="hint">This skill lives in the repo: change it there. keel reads it from the repo each time.</span>}
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

const USE_LABEL = { tests: "for tests", implementation: "for code changes" } as const;

/** 0.18.57 Found in the repo: the repo's own docs and skills for coding agents, who loads them, and the off switch. */
function RepoFound({ pid, onChanged }: { pid: string; onChanged: () => void }) {
  const { toast } = useApp();
  const repo = useLoad(`skills-repo:${pid}`, () => api.repoSkills(pid), { live: false });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [now, setNow] = useState<RepoSkills | null>(null);
  const r = now ?? repo.data;
  if (repo.error) return null;
  if (!r || (!r.skills.length && !r.docs.length)) return null;
  const flip = async (on: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      setNow(await api.repoSkillsOn(pid, on));
      toast(on ? "The repo's skills are loaded for their agents again." : "The repo's skills are off: listed, loaded by no agent.");
      onChanged();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const n = (k: number, one: string) => `${k} ${one}${k === 1 ? "" : "s"}`;
  return (
    <section className="panel sh-repo" aria-label="Found in the repo">
      <div className="panel-head">
        <h3>Found in the repo: {n(r.skills.length, "skill")}, {n(r.docs.length, "doc")}{r.on && r.attached.length ? <> · attached to: {r.attached.join(", ")}</> : null}</h3>
        <label className="chk">
          <input type="checkbox" checked={r.on} disabled={busy} aria-label="Load the repo's skills for their agents" onChange={(e) => void flip(e.target.checked)} />{" "}
          load them
        </label>
      </div>
      <div className="panel-body grid" style={{ gap: 8 }}>
        {!r.on && <p className="sub" style={{ margin: 0 }}>Off: listed here, loaded by no agent.</p>}
        {r.skills.length > 0 && (
          <ul className="sh-repo-list">
            {r.skills.map((s) => (
              <li key={s.id}>
                <b className="mono">{s.id}</b> <span className="sub mono">{s.path}</span>
                {s.about && <span className="sub"> — {s.about}</span>}
                <div className="sub">
                  {s.use ? USE_LABEL[s.use] : "nothing says when to load it"}
                  {s.why && s.why !== "its description" ? <> ({s.why})</> : s.why ? " (its description)" : null}
                  {" · "}{s.agents.length ? `loaded by ${s.agents.join(", ")}` : "on no agent"}
                </div>
              </li>
            ))}
          </ul>
        )}
        {r.docs.length > 0 && (
          <p className="sub" style={{ margin: 0 }}>
            Instructions for coding agents: {r.docs.map((d, i) => <Fragment key={d.path}>{i ? ", " : ""}<span className="mono" title={d.title}>{d.path}</span></Fragment>)}.
            Every agent's prompt names them.
          </p>
        )}
        <span className="hint">Found at init and at a knowledge refresh{r.found_at ? ` (last: ${r.found_at.slice(0, 10)})` : ""}. Open a skill to change who loads it.</span>
        {err && <ErrorBox error={err} />}
      </div>
    </section>
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
  const match = (s: Skill) => (filter === "all" || s.source === filter) && (!needle || (s.id + s.kind + s.stack + s.agents.join(" ")).toLowerCase().includes(needle));
  const count = (k: Src) => (skills.data ? ` ${k === "all" ? skills.data.length : skills.data.filter((s) => s.source === k).length}` : "");
  return (
    <RdPage name="skills">
      <PageHead title="Skill hub" sub={`Skills the agents of ${project?.name ?? pid} load: keel's skills for its stacks, packs, Claude skills, and skills only this project has.`}
        actions={<>
          <a className="btn" href="#/settings/plugins?tab=market&kind=skills" style={{ textDecoration: "none" }}>Find in the marketplace</a>
          <button className="btn" type="button" {...pageKey("I")} onClick={() => setImporting(true)}>Import</button>
          <button className="btn primary" type="button" id="newSkill" {...pageKey("N")} onClick={() => setCreating(true)}>New skill</button>
        </>} />
      <Toolbar>
        <Tabs value={filter} onChange={setFilter} label="Source" options={(Object.keys(SRC_LABEL) as Src[]).map((k) => [k, <>{SRC_LABEL[k]}<span className="sh-n">{count(k)}</span></>])} />
        <SearchBox id="skq" value={q} onChange={setQ} label="Search skills" />
      </Toolbar>
      <RepoFound pid={pid} onChanged={() => void skills.reload()} />
      <div className="panel">
        {skills.error ? <div className="panel-body"><ErrorBox error={skills.error} onRetry={() => void skills.reload()} /></div>
          : !skills.data ? <Skeleton lines={6} label="Loading skills" />
            : (() => {
              const list = skills.data.filter(match);
              if (!skills.data.length) {
                return <EmptyState title="No skills yet" action={<button className="btn primary" type="button" onClick={() => setCreating(true)}>New skill</button>}>Write one for this project, or import a SKILL.md from a link.</EmptyState>;
              }
              if (!list.length) {
                return <EmptyState title="No skill matches" action={<button className="btn" type="button" onClick={() => { setFilter("all"); setQ(""); }}>Show all skills</button>}>
                  Nothing in {SRC_LABEL[filter]}{needle ? <> has “{q.trim()}” in its name, kind, stack or agents</> : " yet"}.
                </EmptyState>;
              }
              return (
                <div className="table-wrap rt-wrap"><table className="rt sh-table" aria-label="Skills">
                  <thead><tr><th>Skill</th><th>Kind</th><th>Stack</th><th>Loaded by</th><th>When</th><th>Tokens</th><th><span className="sr-only">State</span></th></tr></thead>
                  <tbody>
                    {list.map((s) => (
                      <tr key={s.id} className="click" onClick={() => setOpen(s.id)}>
                        <td className="rt-main">
                          <button type="button" className="linkbtn" onClick={(e) => { e.stopPropagation(); setOpen(s.id); }}>{s.id}</button> {s.version === "draft" && <span className="tag star">draft</span>}
                          <div className="sub">{SRC_LABEL[s.source as Src] ?? s.source} · {s.version}</div>
                        </td>
                        <td><span className="chip">{s.kind}</span></td>
                        <td className="mono sub" data-label="stack">{s.stack}</td>
                        <td className="rt-full"><span className="chips">{s.agents.length ? s.agents.map((a) => <span key={a} className="chip">{a}</span>) : <span className="sub">nobody</span>}</span></td>
                        <td className="sub" data-label="when">{s.when || "on demand"}</td>
                        <td className="num mono" data-label="tokens" title="SKILL.md, always loaded">{k1(s.tokens)}</td>
                        <td className="rt-end">{s.enabled ? <span className="pill p-ok">on</span> : <button className="btn sm" type="button" onClick={(e) => { e.stopPropagation(); setOpen(s.id); }}>Add</button>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table></div>
              );
            })()}
      </div>
      <div className="grid g2" style={{ marginTop: 16 }}>
        <Panel title={<h3>How a skill is loaded</h3>} body="grid">
          <ol className="sh-steps">
            <li>The step asks for <span className="mono">skills for (phase, layer, stack)</span>.</li>
            <li>Only the matching skill's <b>SKILL.md</b> goes in the prompt.</li>
            <li>A reference file is opened only when the agent asks, one at a time.</li>
          </ol>
          <span className="hint">Skill tokens count in the estimate and the budget like any other input.</span>
        </Panel>
        <Panel title={<h3>Flow skills became the graph</h3>} body="grid">
          <p className="sub" style={{ margin: 0 }}>keel v1 read the flow rules as text every session. In keel v2 the graph holds them, so agents never load these:</p>
          <div className="chips">{["feature", "ship", "hunt", "init", "fix", "change"].map((f) => <span key={f} className="chip">{f}</span>)}</div>
        </Panel>
      </div>
      {open && <SkillDrawer pid={pid} id={open} agents={agents.data ?? []} onClose={() => setOpen(null)} onSaved={() => void skills.reload()} />}
      {importing && <ImportSkillDrawer pid={pid} onClose={() => setImporting(false)} onImported={() => { setFilter("yours"); void skills.reload(); }} />}
      {creating && <NewSkillDrawer pid={pid} stacks={stacks.data ?? []} onClose={() => setCreating(false)} onCreated={() => { setFilter("yours"); void skills.reload(); }} />}
    </RdPage>
  );
}
