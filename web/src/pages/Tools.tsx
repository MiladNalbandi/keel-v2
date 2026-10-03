// Tools (MCP) (Build): servers your agents can call, a test for each, who may use what, and recent calls.

import { Fragment, useState } from "react";
import { api, errorParts, type JobStep, type McpAllow, type McpServer } from "../api";
import { Async, Drawer, ErrorBox, PageHead, Panel, Pill } from "../components/ui";
import { clock } from "../format";
import { useApp, useLoad } from "../state";

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

export function ToolsPage({ pid }: { pid: string }) {
  const { toast } = useApp();
  const servers = useLoad("mcp", () => api.mcpServers(), { live: false });
  const allow = useLoad(`allow:${pid}`, () => api.mcpAllow(pid), { live: false });
  const agents = useLoad(`agents:${pid}`, () => api.agents(pid), { live: false });
  const calls = useLoad(`calls:${pid}`, () => recentCalls(pid));
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const test = async (s: McpServer) => {
    try {
      const r = await api.testMcpServer(s.name);
      toast(r.ok ? `${s.name}: tools/list returned ${r.tools.length} tools` : `${s.name}: ${r.error ?? "failed"}`);
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

  return (
    <>
      <PageHead title="Tools (MCP servers)" sub="Servers your agents can call. Pick per agent which servers it may use."
        actions={<button className="btn primary" type="button" onClick={() => setAdding(true)}>Add server</button>} />
      <div className="grid g2">
        <Async r={servers} what="Loading servers">
          {(list) => (
            <div className="panel"><div className="table-wrap"><table>
              <thead><tr><th>Server</th><th>How it starts</th><th>Status</th><th>Tools</th><th></th></tr></thead>
              <tbody>
                {list.map((s) => (
                  <Fragment key={s.name}>
                    <tr className="click" tabIndex={0} onClick={() => setOpen(open === s.name ? null : s.name)} onKeyDown={(e) => e.key === "Enter" && setOpen(open === s.name ? null : s.name)} aria-expanded={open === s.name}>
                      <td><b>{s.name}</b>{s.builtin && <> <span className="tag keel">built in</span></>}</td>
                      <td className="mono sub">{[s.command, ...s.args].join(" ")}</td>
                      <td>{s.status === "ok" ? <Pill tone="ok">ok</Pill> : s.status === "error" ? <Pill tone="bad">error</Pill> : <Pill tone="idle">off</Pill>}</td>
                      <td className="num">{s.tools.length || "—"}</td>
                      <td><div className="row" style={{ flexWrap: "nowrap", gap: 4 }}>
                        <button className="btn sm" type="button" onClick={(e) => { e.stopPropagation(); void test(s); }}>Test</button>
                        {!s.builtin && <button className="btn sm ghost" type="button" aria-label={`Remove ${s.name}`} onClick={async (e) => {
                          e.stopPropagation();
                          try { await api.deleteMcpServer(s.name); toast(`${s.name} removed.`); void servers.reload(); } catch (er) { toast(errorParts(er).message); }
                        }}>×</button>}
                      </div></td>
                    </tr>
                    {open === s.name && (
                      <tr><td colSpan={5}><div className="row">
                        {s.tools.length ? s.tools.map((t) => <span key={t} className={`tag ${s.name === "keel" ? "keel" : ""}`}>{t}</span>) : <span className="sub">No tools known yet. Press Test.</span>}
                      </div></td></tr>
                    )}
                  </Fragment>
                ))}
                {!list.length && <tr><td colSpan={5} className="empty">No server yet.</td></tr>}
              </tbody>
            </table></div></div>
          )}
        </Async>
        <Panel title="Recent tool calls">
          <div className="events">
            {calls.error ? <ErrorBox error={calls.error} /> : !calls.data ? <span className="sub loading">Loading…</span> : !calls.data.length ? <span className="sub">No MCP call yet.</span> :
              calls.data.map((c, i) => (
                <div key={i}><span className="t mono sub">{clock(c.at, false)}</span><span className="k">{c.server}</span>
                  <span>{c.agent} → <span className="mono">{c.tool ?? c.text}</span> <span className="sub">{c.ok === false ? "failed" : "ok"}{c.ms !== undefined ? ` · ${c.ms}ms` : ""}</span></span></div>
              ))}
          </div>
        </Panel>
      </div>
      <Panel title="Who may use what" extra={<span className="hint">Applies to API-key agents and to CLI agents (a config file is written per call).</span>} body={false} style={{ marginTop: 16 }}>
        {allow.error ? <div className="panel-body"><ErrorBox error={allow.error} /></div> : !servers.data || !agents.data || !allow.data ? <div className="panel-body empty loading">Loading…</div> : (
          <div className="table-wrap"><table className="matrix">
            <thead><tr><th>Agent</th>{servers.data.map((s) => <th key={s.name}>{s.name}</th>)}</tr></thead>
            <tbody>{agents.data.map((a) => (
              <tr key={a.id}><td>{a.label || a.id}</td>
                {servers.data!.map((s) => (
                  <td key={s.name}><input type="checkbox" aria-label={`${a.id} may use ${s.name}`} checked={(allow.data![a.id] ?? []).includes(s.name)}
                    disabled={s.status === "off"} onChange={(e) => void toggle(a.id, s.name, e.target.checked)} /></td>
                ))}
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Panel>
      {adding && <AddServerDrawer onClose={() => setAdding(false)} onAdded={() => void servers.reload()} />}
    </>
  );
}
