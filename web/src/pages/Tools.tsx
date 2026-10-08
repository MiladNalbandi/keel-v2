// Tools (MCP) (Build): servers your agents can call, a test for each (the result stays next to it), who may use
// what as a compact matrix (one column per server, its state in the header), and recent calls. The parts add cards
// under the servers (slot tools.card: Jira's MCP catalog).

import { Fragment, useState } from "react";
import { api, errorParts, type JobStep, type McpAllow, type McpServer, type Plugin } from "../api";
import { EmptyState, SearchBox, Section, Skeleton, Spinner } from "../components/page";
import { Drawer, ErrorBox, PageHead, Panel, Pill } from "../components/ui";
import { clock } from "../format";
import { PluginCards } from "../components/PluginCards";
import { useSlot } from "../sdk/registry";
import { SLOTS, type ToolsCardItem } from "../sdk/slots";
import { useApp, useLoad, type Loaded } from "../state";

function AddServerDrawer({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const { toast } = useApp();
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("");
  const [env, setEnv] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const add = async () => {
    setBusy(true);
    setErr(null);
    try {
      const envObj: Record<string, string> = {};
      env.split("\n").map((l) => l.trim()).filter(Boolean).forEach((l) => {
        const i = l.indexOf("=");
        if (i > 0) envObj[l.slice(0, i).trim()] = l.slice(i + 1).trim();
      });
      await api.addMcpServer({ name: name.trim(), command: command.trim(), args: args.trim() ? args.trim().split(/\s+/) : [], env: Object.keys(envObj).length ? envObj : undefined });
      toast(`${name} added. Test it to see its tools.`);
      onAdded();
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer title="Add server" onClose={onClose}
      footer={<><button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="button" onClick={add} disabled={busy || !name.trim() || !command.trim()}>{busy ? "Adding…" : "Add server"}</button></>}>
      <div className="field"><label htmlFor="ms-name">Name</label><input type="text" id="ms-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. github" /></div>
      <div className="field"><label htmlFor="ms-cmd">Command</label><input type="text" id="ms-cmd" value={command} onChange={(e) => setCommand(e.target.value)} placeholder="e.g. docker" /></div>
      <div className="field"><label htmlFor="ms-args">Arguments</label><input type="text" id="ms-args" value={args} onChange={(e) => setArgs(e.target.value)} placeholder="run -i ghcr.io/github/github-mcp-server" /></div>
      <div className="field"><label htmlFor="ms-env">Environment (NAME=value, one per line)</label><textarea id="ms-env" value={env} onChange={(e) => setEnv(e.target.value)} style={{ minHeight: 70 }} />
        <span className="hint">For secrets, save a key in Connections and refer to its name.</span></div>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

type Call = JobStep & { agent: string };

async function recentCalls(pid: string): Promise<Call[]> {
  const jobs = (await api.jobs({ project: pid, limit: 30 })).filter((j) => j.mcp_calls > 0).slice(0, 5);
  const details = await Promise.all(jobs.map((j) => api.job(j.id).catch(() => null)));
  return details.flatMap((d) => (d ? d.steps.filter((s) => s.kind === "tool" && s.server).map((s) => ({ ...s, agent: d.agent })) : []))
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).slice(0, 12);
}

type TestState = { busy?: boolean; ok?: boolean; text: string };

function serverPill(s: McpServer) {
  return !s.enabled ? <Pill tone="idle">turned off</Pill> : s.status === "ok" ? <Pill tone="ok">ok</Pill> : s.status === "error" ? <Pill tone="bad">error</Pill> : <Pill tone="idle">off</Pill>;
}
const usable = (s: McpServer) => s.enabled && s.status !== "off";

/** The MCP servers plus one row per plugin that is on (its read tools; on and ok, never removable). */
function withPlugins(servers: Loaded<McpServer[]>, plugins: Plugin[] | null): Loaded<McpServer[]> {
  const extra = (plugins ?? []).filter((p) => p.enabled && p.tools?.server).map((p): McpServer => ({
    name: p.tools.server!, command: "keel", args: [], enabled: true, builtin: true, status: "ok", tools: p.tools.read ?? [],
    label: `${p.title} plugin`,
  }));
  return servers.data ? { ...servers, data: [...servers.data, ...extra] } : servers;
}

export function ToolsPage({ pid }: { pid: string }) {
  const { toast } = useApp();
  const mcpServers = useLoad("mcp", () => api.mcpServers(), { live: false });
  const plugins = useLoad(`plugins:${pid}`, () => api.plugins(pid), { live: false });
  // a plugin that is on brings its read tools as a server agents can be given (keel-db, keel-git)
  const servers = withPlugins(mcpServers, plugins.data);
  const allow = useLoad(`allow:${pid}`, () => api.mcpAllow(pid), { live: false });
  const agents = useLoad(`agents:${pid}`, () => api.agents(pid), { live: false });
  const calls = useLoad(`calls:${pid}`, () => recentCalls(pid));
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [tests, setTests] = useState<Record<string, TestState>>({});
  const [q, setQ] = useState("");
  const cards = useSlot<ToolsCardItem>(SLOTS.toolsCard);

  const test = async (s: McpServer) => {
    setTests((t) => ({ ...t, [s.name]: { busy: true, text: "asking for its tools…" } }));
    try {
      const r = await api.testMcpServer(s.name);
      setTests((t) => ({ ...t, [s.name]: r.ok ? { ok: true, text: `${r.tools.length} tools` } : { ok: false, text: r.error ?? "failed" } }));
      void servers.reload();
    } catch (e) {
      setTests((t) => ({ ...t, [s.name]: { ok: false, text: errorParts(e).message } }));
    }
  };
  const setEnabled = async (s: McpServer, enabled: boolean) => {
    try {
      await api.saveMcpServer(s.name, { enabled });
      toast(enabled ? `${s.name} is on. Test it, then pick which agents may use it.` : `${s.name} is off: no agent gets it.`);
      void servers.reload();
    } catch (e) {
      toast(`${s.name}: ${errorParts(e).message}`);
    }
  };
  const toggle = async (agent: string, server: string, on: boolean) => {
    const cur: McpAllow = { ...(allow.data ?? {}) };
    const list = new Set(cur[agent] ?? []);
    if (on) list.add(server); else list.delete(server);
    cur[agent] = [...list];
    allow.setData(cur);
    try {
      await api.saveMcpAllow(pid, cur);
      toast(`${agent} ${on ? "may now use" : "can no longer use"} ${server}`);
    } catch (e) {
      toast(errorParts(e).message);
      void allow.reload();
    }
  };
  const needle = q.trim().toLowerCase();
  const rows = (agents.data ?? []).filter((a) => !needle || `${a.id} ${a.label}`.toLowerCase().includes(needle));

  return (
    <>
      <PageHead title="Tools (MCP servers)" sub="Plugins keel adds, programs your agents can call for extra tools, and which agent may use which."
        actions={<button className="btn primary" type="button" onClick={() => setAdding(true)}>Add server</button>} />
      <Section title="Plugins" sub="keel's own add-ons. Install one and keel changes where you already work: KeelBot, Workflows, Connections and the Map or Code page.">
        {plugins.error ? <ErrorBox error={plugins.error} onRetry={() => void plugins.reload()} />
          : !plugins.data ? <div className="panel"><Skeleton lines={2} label="Loading plugins" /></div>
            : <PluginCards pid={pid} plugins={plugins} />}
      </Section>
      <div className="tl-top">
        <Section title="Servers" sub="Test a server to see its tools. A turned-off server gives no agent anything.">
          {mcpServers.error ? <ErrorBox error={mcpServers.error} onRetry={() => void mcpServers.reload()} /> : !mcpServers.data ? <div className="panel"><Skeleton lines={3} label="Loading servers" /></div> : !mcpServers.data.length ? (
            <div className="panel"><EmptyState title="No server yet" action={<button className="btn primary" type="button" onClick={() => setAdding(true)}>Add server</button>}>Add one (GitHub, a database, a browser…) and agents can call its tools.</EmptyState></div>
          ) : (
            <div className="panel"><div className="table-wrap rt-wrap"><table className="rt tl-servers" aria-label="MCP servers">
              <thead><tr><th>Server and how it starts</th><th>Status</th><th>Tools</th><th><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {mcpServers.data.map((s) => {
                  const t = tests[s.name];
                  const cmd = [s.command, ...s.args].join(" ");
                  return (
                    <Fragment key={s.name}>
                      <tr>
                        <td className="rt-main tl-server"><b>{s.name}</b>{s.builtin && <> <span className="tag keel">built in</span></>}
                          {s.label && <span className="sub"> {s.label}</span>}
                          <span className="tl-cmd mono sub" title={cmd}>{cmd}</span></td>
                        <td><span className="row" style={{ gap: 6 }}>{serverPill(s)}
                          {t && <span className={`hint ${t.ok === true ? "okc" : t.ok === false ? "badc" : ""}`} role="status">{t.ok === true ? "Test OK: " : t.ok === false ? "Test failed: " : ""}{t.text}</span>}</span></td>
                        <td data-label="tools">
                          <button className="linkbtn tl-count" type="button" aria-expanded={open === s.name} onClick={() => setOpen(open === s.name ? null : s.name)}
                            aria-label={`${s.tools.length || "No"} tools of ${s.name}`}>{s.tools.length || "—"}</button>
                        </td>
                        <td className="rt-end"><div className="row" style={{ flexWrap: "nowrap", gap: 4, justifyContent: "flex-end" }}>
                          <button className="btn sm" type="button" onClick={() => void test(s)} disabled={t?.busy}>{t?.busy ? "Testing…" : "Test"}</button>
                          <button className="btn sm ghost" type="button" onClick={() => void setEnabled(s, !s.enabled)}>{s.enabled ? "Turn off" : "Turn on"}</button>
                          {!s.builtin && <button className="btn sm ghost" type="button" aria-label={`Remove ${s.name}`} onClick={async () => {
                            try { await api.deleteMcpServer(s.name); toast(`${s.name} removed.`); void mcpServers.reload(); } catch (er) { toast(errorParts(er).message); }
                          }}>×</button>}
                        </div></td>
                      </tr>
                      {open === s.name && (
                        <tr className="tl-tools"><td colSpan={4}><div className="chips">
                          {s.tools.length ? s.tools.map((x) => <span key={x} className={`chip ${s.name === "keel" ? "c-mcp" : ""}`}>{x}</span>) : <span className="sub">No tools known yet. Press Test.</span>}
                        </div></td></tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table></div></div>
          )}
        </Section>
        <Section title="Recent tool calls" sub="The last MCP calls agents of this project made.">
          <Panel>
            <div className="events tl-events">
              {calls.error ? <ErrorBox error={calls.error} /> : !calls.data ? <Spinner>Reading the last jobs</Spinner> : !calls.data.length ? <span className="sub">No MCP call yet. They show here once an agent uses a server.</span> :
                calls.data.map((c, i) => (
                  <div key={i}><span className="t mono sub">{clock(c.at, false)}</span><span className="k">{c.server}</span>
                    <span>{c.agent} → <span className="mono">{c.tool ?? c.text}</span> <span className="sub">{c.ok === false ? "failed" : "ok"}{c.ms !== undefined ? ` · ${c.ms}ms` : ""}</span></span></div>
                ))}
            </div>
          </Panel>
        </Section>
      </div>
      {cards.map((c) => <div key={c.id} className="tl-catwrap"><c.component pid={pid} onAdded={() => void mcpServers.reload()} /></div>)}
      <Section title="Who may use what" sub="Tick a box to give an agent that server's tools. Applies to API-key agents and to CLI agents (a config file is written per call)."
>
        {(agents.data?.length ?? 0) > 8 && <div><SearchBox value={q} onChange={setQ} label="Find an agent" /></div>}
        <div className="panel tl-mpanel">
          {allow.error ? <div className="panel-body"><ErrorBox error={allow.error} onRetry={() => void allow.reload()} /></div>
            : agents.error ? <div className="panel-body"><ErrorBox error={agents.error} onRetry={() => void agents.reload()} /></div>
              : !servers.data || !agents.data || !allow.data ? <Skeleton lines={5} label="Loading who may use what" />
                : !servers.data.length ? <EmptyState compact title="No server to give">Add a server above first.</EmptyState>
                  : (
                    <div className="table-wrap tl-mwrap"><table className="tl-matrix" aria-label="Who may use what">
                      <thead><tr>
                        <th scope="col" className="tl-agent">Agent</th>
                        {servers.data.map((s) => {
                          const n = Object.values(allow.data!).filter((l) => l.includes(s.name)).length;
                          return (
                            <th key={s.name} scope="col" className={usable(s) ? "" : "is-off"}>
                              <span className="tl-sname">{s.name}</span>
                              <span className="tl-sstate">{!s.enabled ? "turned off" : s.status === "ok" ? <>ok · {n} of {agents.data!.length}</> : s.status === "error" ? "error" : "off"}</span>
                            </th>
                          );
                        })}
                      </tr></thead>
                      <tbody>
                        {rows.map((a) => (
                          <tr key={a.id}>
                            <th scope="row" className="tl-agent">{a.label || a.id}{a.custom && <span className="sub"> · custom</span>}</th>
                            {servers.data!.map((s) => (
                              <td key={s.name} className={usable(s) ? "" : "is-off"}>
                                <input type="checkbox" aria-label={`${a.id} may use ${s.name}`} checked={(allow.data![a.id] ?? []).includes(s.name)}
                                  disabled={!usable(s)} title={usable(s) ? undefined : `Turn ${s.name} on first`} onChange={(e) => void toggle(a.id, s.name, e.target.checked)} />
                              </td>
                            ))}
                          </tr>
                        ))}
                        {!rows.length && <tr><td colSpan={servers.data.length + 1} className="empty">No agent has “{q.trim()}” in its name.</td></tr>}
                      </tbody>
                    </table></div>
                  )}
        </div>
      </Section>
      {adding && <AddServerDrawer onClose={() => setAdding(false)} onAdded={() => void servers.reload()} />}
    </>
  );
}
