// Agents (Build): keel agents and your custom agents, per project. A change here overrides the defaults
// for this project only (PUT /api/projects/{pid}/agents/{aid}).

import { useState } from "react";
import { api, errorParts, KNOWLEDGE_SECTIONS, type Agent, type AgentKnowledge, type AgentLane, type CustomAgent, type McpServer, type Model } from "../api";
import { MODES_FOR, ModelPicker, modeLabel } from "../components/ModelPicker";
import { Async, Drawer, ErrorBox, GoButton, PageHead, Prov, Tabs } from "../components/ui";
import { kfmt, slug } from "../format";
import { useApp, useLoad } from "../state";

type Err = { message: string; hint?: string } | null;

const LANES: Record<AgentLane, string> = { follow: "follow the AC (api or web)", api: "api only", web: "web only" };

function AgentRow({ a, onOpen }: { a: Agent; onOpen: () => void }) {
  return (
    <tr className="click" tabIndex={0} onClick={onOpen} onKeyDown={(e) => e.key === "Enter" && onOpen()}>
      <td>
        <b>{a.label || a.id}</b>{a.custom && <> <span className="tag star">★ custom</span></>}
        {!a.enabled && <> <span className="tag">off</span></>}
        {a.overridden.length > 0 && <> <span className="tag star" title={`changed here: ${a.overridden.join(", ")}`}>this project</span></>}
        <div className="sub">{a.about}</div>
      </td>
      <td>{a.phases.length ? a.phases.map((n) => <span key={n} className="tag" style={{ marginRight: 4 }}>{n}</span>) : <span className="sub">any</span>}</td>
      <td><Prov p={a.model?.provider} m={a.model ? `${a.model.model}${a.model.effort ? " · " + a.model.effort : ""}` : undefined} /></td>
      <td className="sub">{a.model ? modeLabel(a.model.provider, a.model.mode) : "—"}</td>
      <td>{a.tools.length ? a.tools.map((t) => <span key={t} className={`tag ${t.includes("keel") ? "keel" : ""}`} style={{ marginRight: 4 }}>{t.replace(/^mcp:/, "")}</span>) : <span className="sub">none</span>}</td>
    </tr>
  );
}

const SECTION_ABOUT: Record<string, string> = {
  architecture: "where code lives and its layers",
  domain: "what the system is for, the business words",
  conventions: "how code here is written",
  data: "tables and migrations",
  integrations: "outside services and their test stand-ins",
  journeys: "what users do, end to end",
};

/** "Knowledge this agent uses": which docs/knowledge sections, the code graph, memory, strict. */
function KnowledgeField({ k, files, onChange, overridden, onDefaults }: {
  k: AgentKnowledge; files: Record<string, number>; onChange: (k: AgentKnowledge) => void; overridden: boolean; onDefaults: () => void;
}) {
  const total = k.sections.reduce((n, s) => n + (files[s] ?? 0), 0);
  const tick = (s: AgentKnowledge["sections"][number], on: boolean) =>
    onChange({ ...k, sections: KNOWLEDGE_SECTIONS.filter((x) => (x === s ? on : k.sections.includes(x))) });
  return (
    <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
      <legend className="lab">Knowledge this agent uses</legend>
      <div className="grid" style={{ gap: 4 }}>
        {KNOWLEDGE_SECTIONS.map((s) => (
          <label key={s} className="chk">
            <input type="checkbox" checked={k.sections.includes(s)} onChange={(e) => tick(s, e.target.checked)} />{" "}
            <b>{s}</b> <span className="sub">{SECTION_ABOUT[s]} · {s in files ? `~${kfmt(files[s])} tokens` : "not written yet"}</span>
          </label>
        ))}
      </div>
      <span className="hint">About {kfmt(total)} tokens if it reads every ticked section. Sections come from docs/knowledge/ in the project.</span>
      <label className="chk"><input type="checkbox" checked={k.code_graph} onChange={(e) => onChange({ ...k, code_graph: e.target.checked })} /> Code graph <span className="sub">look up code by symbols and calls before grep</span></label>
      <label className="chk"><input type="checkbox" checked={k.memory} onChange={(e) => onChange({ ...k, memory: e.target.checked })} /> Memory <span className="sub">a repeated step continues its own earlier session</span></label>
      <label className="chk"><input type="checkbox" checked={k.strict} onChange={(e) => onChange({ ...k, strict: e.target.checked })} /> Strict <span className="sub">block reading sections that are not ticked (off: the agent is only told)</span></label>
      {overridden && <button className="btn sm ghost" type="button" onClick={onDefaults}>Use defaults</button>}
    </fieldset>
  );
}

function AgentDrawer({ pid, a, onClose, onSaved, onDeleted }: { pid: string; a: Agent; onClose: () => void; onSaved: (a: Agent) => void; onDeleted: () => void }) {
  const { toast } = useApp();
  const [model, setModel] = useState<Model>(a.model ?? { provider: "fake", mode: "api", model: "fake" });
  const [prompt, setPrompt] = useState(a.prompt);
  const [enabled, setEnabled] = useState(a.enabled);
  const [lane, setLane] = useState<AgentLane>(a.lane ?? "follow");
  const [know, setKnow] = useState<AgentKnowledge | undefined>(a.knowledge);
  // What the server has now (changes when "Use defaults" clears this project's change).
  const [savedKnow, setSavedKnow] = useState({ k: a.knowledge, overridden: a.overridden.includes("knowledge") });
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<string>("");
  const [err, setErr] = useState<Err>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const body: Parameters<typeof api.saveAgent>[2] = {};
      if (JSON.stringify(model) !== JSON.stringify(a.model)) body.model = model;
      if (prompt !== a.prompt) body.prompt = prompt;
      if (enabled !== a.enabled) body.enabled = enabled;
      if (lane !== (a.lane ?? "follow")) body.lane = lane;
      if (know && JSON.stringify(know) !== JSON.stringify(savedKnow.k)) body.knowledge = know;
      const out = await api.saveAgent(pid, a.id, body);
      onSaved(out ?? { ...a, ...body, knowledge: body.knowledge ?? a.knowledge });
      toast("Saved for this project.");
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const useDefaults = async () => {
    setErr(null);
    try {
      const out = await api.saveAgent(pid, a.id, { knowledge: null });
      setKnow(out.knowledge);
      setSavedKnow({ k: out.knowledge, overridden: false });
      onSaved(out);
      toast("Knowledge is back to this agent's defaults.");
    } catch (e) {
      setErr(errorParts(e));
    }
  };
  const runTest = async () => {
    setTest("sending “Reply with exactly: OK”…");
    try {
      const r = await api.testAgent(a.id, pid);
      setTest(r.ok ? `${r.text ?? "OK"} · ${(r.ms / 1000).toFixed(1)}s` : `failed: ${r.error ?? "no answer"}`);
    } catch (e) {
      setTest(`failed: ${errorParts(e).message}`);
    }
  };
  return (
    <Drawer title={a.label || a.id} onClose={onClose}
      footer={<>
        {a.custom && <button className="btn ghost" type="button" style={{ marginRight: "auto" }} onClick={async () => {
          try {
            await api.deleteAgent(pid, a.id);
            toast(`${a.label || a.id} deleted.`);
            onDeleted();
            onClose();
          } catch (e) {
            setErr(errorParts(e));
          }
        }}>Delete</button>}
        <button className="btn" type="button" onClick={runTest}>Test</button>
        <button className="btn primary" type="button" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
      </>}>
      <p className="sub" style={{ margin: 0 }}>{a.about}</p>
      {test && <span className="hint" role="status">{test}</span>}
      <div className="field"><span className="lab">Model</span><ModelPicker id="am" value={model} onChange={setModel} /></div>
      <div className="field"><span className="lab">Tools per phase</span>
        {(a.phases.length ? a.phases : ["any"]).map((n) => (
          <div key={n}><span className="tag">{n}</span> <span className="sub">{a.tools.length ? a.tools.join(" · ") : "read-only tools"}</span></div>
        ))}
        <span className="hint">The graph gives these tools. A write outside them is refused, and the diff guard reverts it after the node. Change MCP access in Tools (MCP).</span>
      </div>
      <div className="field"><span className="lab">Skills it loads</span>
        <div className="row">
          {a.skills.length ? a.skills.map((k) => <span key={k} className="tag">{k}</span>) : <span className="sub">none yet</span>}
          <GoButton to="skills" className="btn sm ghost">+ Add from Skill hub</GoButton>
        </div>
        <span className="hint">Picked by phase, layer and the project's stack.</span>
      </div>
      {know && <KnowledgeField k={know} files={a.knowledge_files ?? {}} onChange={setKnow}
        overridden={savedKnow.overridden} onDefaults={useDefaults} />}
      <div className="field"><label htmlFor="alane">Lane</label>
        <select id="alane" value={lane} onChange={(e) => setLane(e.target.value as AgentLane)}>
          {(Object.keys(LANES) as AgentLane[]).map((k) => <option key={k} value={k}>{LANES[k]}</option>)}
        </select>
        <span className="hint">Which acceptance criteria this agent works on. "Follow the AC" takes the AC's layer (API or WEB) and its stack.</span></div>
      <div className="field"><label htmlFor="ap">System prompt</label><textarea id="ap" value={prompt} onChange={(e) => setPrompt(e.target.value)} /></div>
      <label className="chk"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled in this project</label>
      {a.overridden.length > 0 && <span className="hint">Changed for this project: {a.overridden.join(", ")}.</span>}
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

function NewAgentDrawer({ pid, servers, onClose, onCreated }: { pid: string; servers: McpServer[]; onClose: () => void; onCreated: (a: Agent) => void }) {
  const { toast } = useApp();
  const [name, setName] = useState("");
  const [about, setAbout] = useState("");
  const [phases, setPhases] = useState("");
  const [allow, setAllow] = useState({ read: true, edit: false, run: false });
  const [mcp, setMcp] = useState<string[]>(["keel"]);
  const [model, setModel] = useState<Model>({ provider: "claude", mode: MODES_FOR.claude[0], model: "sonnet" });
  const [prompt, setPrompt] = useState("The last commit is $diff for $ac in $phase.\nSay what to change, with a file:line for each point.\nReturn { notes: string[] }.");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const create = async () => {
    setBusy(true);
    setErr(null);
    const tools = [
      ...(allow.read ? ["read_file"] : []), ...(allow.edit ? ["write_file"] : []), ...(allow.run ? ["run_command"] : []),
      ...mcp.map((s) => `mcp:${s}`),
    ];
    const body: CustomAgent = {
      id: slug(name), label: name.trim(), about: about.trim(), phases: phases.split(",").map((s) => s.trim()).filter(Boolean),
      model, tools, skills: [], prompt,
    };
    try {
      const a = await api.newAgent(pid, body);
      toast(`${body.label} saved. Add it to a workflow in the builder.`);
      onCreated(a);
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer title="New agent" onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" id="saveAgent" onClick={create} disabled={busy || !name.trim()}>{busy ? "Saving…" : "Save agent"}</button></>}>
      <div className="field"><label htmlFor="na-name">Name</label><input type="text" id="na-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Doc-Sync" />
        {name && <span className="hint mono">id: {slug(name)}</span>}</div>
      <div className="field"><label htmlFor="na-about">What it does</label><input type="text" id="na-about" value={about} onChange={(e) => setAbout(e.target.value)} placeholder="Updates docs/ when an AC changes public behaviour" /></div>
      <div className="field"><label htmlFor="na-ph">Phases it may run in</label><input type="text" id="na-ph" value={phases} onChange={(e) => setPhases(e.target.value)} placeholder="e.g. green, ac-gate (empty = any)" />
        <span className="hint">To make it run, add it as a step in a workflow, or at a gate.</span></div>
      <div className="field"><span className="lab">Allowed to</span>
        <div className="row">
          <label className="chk"><input type="checkbox" checked={allow.read} onChange={(e) => setAllow({ ...allow, read: e.target.checked })} /> read the repo</label>
          <label className="chk"><input type="checkbox" checked={allow.edit} onChange={(e) => setAllow({ ...allow, edit: e.target.checked })} /> edit files</label>
          <label className="chk"><input type="checkbox" checked={allow.run} onChange={(e) => setAllow({ ...allow, run: e.target.checked })} /> run commands</label>
        </div>
        <span className="hint">keel's guard still applies: a phase only allows the files it allows.</span>
      </div>
      <div className="field"><span className="lab">Model</span><ModelPicker id="nam" value={model} onChange={setModel} /></div>
      <div className="field"><span className="lab">MCP tools</span>
        <div className="grid" style={{ gap: 6 }}>
          {servers.map((s) => (
            <label key={s.name} className="chk">
              <input type="checkbox" checked={mcp.includes(s.name)} disabled={s.status === "off"}
                onChange={(e) => setMcp(e.target.checked ? [...mcp, s.name] : mcp.filter((x) => x !== s.name))} />{" "}
              <b>{s.name}</b> <span className="sub">{s.tools.slice(0, 3).join(" · ") || "server is off"}</span>
            </label>
          ))}
          {!servers.length && <span className="sub">No MCP server yet.</span>}
        </div>
      </div>
      <div className="field"><label htmlFor="na-prompt">Prompt</label><textarea id="na-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <span className="hint">Placeholders: $spec $acs $ac $phase $diff</span></div>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

export function AgentsPage({ pid }: { pid: string }) {
  const { project, toast } = useApp();
  const agents = useLoad(`agents:${pid}`, () => api.agents(pid), { live: false });
  const servers = useLoad("mcp", () => api.mcpServers(), { live: false });
  const [tab, setTab] = useState<"keel" | "custom">("keel");
  const [open, setOpen] = useState<Agent | null>(null);
  const [creating, setCreating] = useState(false);
  const [testing, setTesting] = useState(false);
  const all = agents.data ?? [];
  const keel = all.filter((a) => !a.custom), custom = all.filter((a) => a.custom);
  const list = tab === "keel" ? keel : custom;
  const testAll = async () => {
    setTesting(true);
    const results = await Promise.all(all.filter((a) => a.enabled).map((a) => api.testAgent(a.id, pid).then((r) => r.ok, () => false)));
    const ok = results.filter(Boolean).length;
    toast(`Sent 'Reply with exactly: OK' to ${results.length} agents — ${ok === results.length ? "all OK" : `${ok} OK, ${results.length - ok} failed`}.`);
    setTesting(false);
  };
  return (
    <>
      <PageHead title="Agents" sub={`Agent settings for ${project?.name ?? pid}. A change here overrides the defaults for this project only.`}
        actions={<>
          <button className="btn" type="button" onClick={testAll} disabled={testing || !all.length}>{testing ? "Testing…" : "Test all"}</button>
          <button className="btn primary" type="button" id="newAgent" onClick={() => setCreating(true)}>New agent</button>
        </>} />
      <div className="row" style={{ marginBottom: 12 }}>
        <Tabs value={tab} onChange={setTab} label="Agents" options={[["keel", `keel agents (${keel.length})`], ["custom", `Custom (${custom.length})`]]} />
      </div>
      <Async r={agents} what="Loading agents">
        {() => (
          <div className="panel"><div className="table-wrap"><table>
            <thead><tr><th>Agent</th><th>Runs in</th><th>Model</th><th>Runs on</th><th>MCP tools</th></tr></thead>
            <tbody>
              {list.map((a) => <AgentRow key={a.id} a={a} onOpen={() => setOpen(a)} />)}
              {!list.length && <tr><td colSpan={5} className="empty">{tab === "custom" ? "No custom agent yet. Make one with New agent." : "No keel agents found in keel's content (agents/). Is KEEL_CONTENT right?"}</td></tr>}
            </tbody>
          </table></div></div>
        )}
      </Async>
      {open && <AgentDrawer pid={pid} a={open} onClose={() => setOpen(null)}
        onSaved={(n) => agents.setData((l) => (l ? l.map((x) => (x.id === n.id ? n : x)) : l))}
        onDeleted={() => agents.setData((l) => (l ? l.filter((x) => x.id !== open.id) : l))} />}
      {creating && <NewAgentDrawer pid={pid} servers={servers.data ?? []} onClose={() => setCreating(false)}
        onCreated={(a) => { agents.setData((l) => (l ? [...l, a] : [a])); setTab("custom"); }} />}
    </>
  );
}
