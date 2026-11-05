// Cluster › Workloads: one list per kind (pods, deployments, services, nodes, events) with a search by name, IP, node or status,
// k9s's `:svc` / `:ns shop` commands and keys, and the selected pod's panel: its log, describe, one command, the port-forward and
// exec commands to copy, and its events. A change asks in the page first; a read-only context has none.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { askAssistant, Confirm, copyText, errorParts, go, Note, Panel, Pill, useApp, useLoad } from "@keel/web-sdk";
import { AccessPill, type Shared, usePoll } from "./ClusterPage";
import {
  age, clusterApi, command, deployOf, memSize, execCommand, forwardCommand, KINDS, matches, podTone,
  type ChangeBody, type Deploy, type Kind, type KEvent, type Pod, type Snapshot,
} from "./clusterApi";

type Tab = "logs" | "describe" | "shell" | "forward" | "events";
const TABS: [Tab, string, string][] = [["logs", "Logs", "l"], ["describe", "Describe", "d"], ["shell", "Shell", "s"], ["forward", "Port-forward", "f"], ["events", "Events", "e"]];
const KEY_TAB: Record<string, Tab> = { l: "logs", d: "describe", s: "shell", f: "forward", e: "events" };
const typing = (t: EventTarget | null) => { const n = (t as HTMLElement | null)?.tagName; return n === "INPUT" || n === "SELECT" || n === "TEXTAREA"; };

function lists(snap: Snapshot, ns: string, query: string) {
  const inNs = <T extends { ns: string }>(x: T) => !ns || x.ns === ns;
  return {
    pods: snap.pods.filter(inNs).filter((p) => matches(query, p.name, p.ip, p.node, p.status, p.ns, p.app, p.commit)),
    deploy: snap.deployments.filter(inNs).filter((d) => matches(query, d.name, d.ns, d.commit, ...d.images)),
    svc: snap.services.filter(inNs).filter((s) => matches(query, s.name, s.ns, s.type, s.cluster_ip, ...s.external, ...s.ports)),
    nodes: snap.nodes.filter((n) => matches(query, n.name, n.internal_ip, n.external_ip, ...n.roles)),
    events: snap.events.filter(inNs).filter((e) => matches(query, e.type, e.reason, e.object, e.message)),
  };
}

export function Workloads({ s }: { s: Shared }) {
  const { toast } = useApp();
  const ctx = s.ctx!;
  const snap = s.snap;
  const [ns, setNs] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<Kind>("pods");
  const [tab, setTab] = useState<Tab>("logs");
  const search = useRef<HTMLInputElement>(null);
  const L = useMemo(() => (snap ? lists(snap, ns, query) : null), [snap, ns, query]);
  const pod = snap && s.sel ? snap.pods.find((p) => p.ns === s.sel!.ns && p.name === s.sel!.name) ?? null : null;

  const onSearchKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      const c = command(query);
      if (c) {
        e.preventDefault();
        if (c.kind) setKind(c.kind);
        if (c.ns !== undefined) setNs(c.ns);
        setQuery("");
      }
    } else if (e.key === "Escape") {
      setQuery("");
    } else if ((e.key === "ArrowDown" || e.key === "ArrowUp") && kind === "pods") {
      e.preventDefault();
      move(e.key === "ArrowDown" ? 1 : -1);
    }
  };

  const move = (step: number) => {
    const pods = L?.pods ?? [];
    if (!pods.length) return;
    const at = pods.findIndex((p) => s.sel && p.ns === s.sel.ns && p.name === s.sel.name);
    const next = pods[Math.max(0, Math.min(pods.length - 1, at < 0 ? 0 : at + step))];
    s.setSel({ ns: next.ns, name: next.name });
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "/" || e.key === ":") {
      if (typing(e.target)) return;
      e.preventDefault();
      if (e.key === ":") setQuery(":");
      search.current?.focus();
      return;
    }
    if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (kind === "pods" && (e.key === "j" || e.key === "ArrowDown")) { e.preventDefault(); move(1); }
    else if (kind === "pods" && (e.key === "k" || e.key === "ArrowUp")) { e.preventDefault(); move(-1); }
    else if (KEY_TAB[e.key] && pod) setTab(KEY_TAB[e.key]);
    else if (e.key === "Escape") s.setSel(null);
  };

  const change = async (b: Omit<ChangeBody, "context">) => {
    try {
      const r = await clusterApi.change({ context: ctx.name, ...b });
      toast(r.message || "Done.");
      s.reloadSnap();
    } catch (err) {
      const p = errorParts(err);
      toast(p.hint ? `${p.message} ${p.hint}` : p.message);
    }
  };

  const namespaces = snap?.namespaces ?? [];
  const counts = L ? { pods: L.pods.length, deploy: L.deploy.length, svc: L.svc.length, nodes: L.nodes.length, events: L.events.length } : null;
  return (
    <div onKeyDown={onKey}>
      <div className="panel" style={{ padding: "10px 12px", marginBottom: 12 }}>
        <div className="kc-bar" style={{ marginBottom: 0 }}>
          <label className="sub" style={{ display: "flex", gap: 8, alignItems: "center" }}>Cluster
            <select value={ctx.name} onChange={(e) => s.setCtx(e.target.value)} aria-label="Cluster" style={{ fontWeight: 600, color: "var(--fg)" }}>
              {s.on.map((c) => <option key={c.name} value={c.name}>{c.name}{c.local ? " · this computer" : ""}</option>)}
            </select>
          </label>
          <AccessPill c={ctx} />
          {namespaces.length > 0 && (
            <div className="kc-ns" role="group" aria-label="Namespace">
              <button type="button" aria-pressed={ns === ""} onClick={() => setNs("")}>all<span className="kc-cnt">{snap?.pods.length ?? 0}</span></button>
              {namespaces.map((n) => (
                <button key={n} type="button" aria-pressed={ns === n} onClick={() => setNs(n)}>{n}<span className="kc-cnt">{snap?.pods.filter((p) => p.ns === n).length ?? 0}</span></button>
              ))}
            </div>
          )}
          <input ref={search} className="kc-in kc-search" type="search" aria-label="Search" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={onSearchKey}
            placeholder="/  search name, IP, node, status  ·  :svc  :nodes  :ns shop" />
          {snap && <Pill tone="ok" title={`read at ${new Date(snap.at).toLocaleTimeString()}`}>live</Pill>}
        </div>
      </div>

      {snap?.warnings.map((w) => <Note key={w} tone="warn">{w}</Note>)}
      {s.snapError && <Note tone="err" role="alert">{s.snapError.message} {s.snapError.hint ?? ""}</Note>}

      <div className="tabs" role="tablist" aria-label="Kind" style={{ marginBottom: 12 }}>
        {KINDS.map(([id, label]) => (
          <button key={id} role="tab" type="button" aria-selected={kind === id} onClick={() => setKind(id)}>{label}{counts && <span className="kc-cnt">{counts[id]}</span>}</button>
        ))}
      </div>

      {!snap || !L ? (
        !s.snapError && <div className="empty loading" role="status">Reading {ctx.name}…</div>
      ) : (
        <div className="kc-split">
          <div className="kc-list">
            <Panel body={false}>
              <div className="ss-tablewrap">
                {kind === "pods" && <PodTable pods={L.pods} sel={s.sel} onPick={(p) => s.setSel({ ns: p.ns, name: p.name })} query={query} />}
                {kind === "deploy" && <DeployTable list={L.deploy} ctx={s} onPods={(d) => { setKind("pods"); setNs(d.ns); setQuery(`${d.name}-`); }} onChange={change} />}
                {kind === "svc" && <SvcTable snap={snap} list={L.svc} ctx={ctx.name} />}
                {kind === "nodes" && <NodeTable list={L.nodes} metrics={snap.metrics} />}
                {kind === "events" && <EventTable list={L.events} />}
              </div>
            </Panel>
          </div>
          {kind === "pods" && pod && (
            <div className="kc-side">
              <PodPanel key={`${pod.ns}/${pod.name}`} s={s} pod={pod} snap={snap} tab={tab} setTab={setTab} onChange={change} />
            </div>
          )}
        </div>
      )}

      <div className="kc-keys" aria-label="Keys">
        <span><kbd>/</kbd> search</span><span><kbd>:</kbd> kind, e.g. <span className="mono">:svc</span></span><span><kbd>j</kbd> <kbd>k</kbd> move</span>
        <span><kbd>l</kbd> logs</span><span><kbd>d</kbd> describe</span><span><kbd>s</kbd> shell</span><span><kbd>f</kbd> port-forward</span><span><kbd>e</kbd> events</span>
        <span><kbd>Esc</kbd> close</span><span style={{ marginLeft: "auto" }}>Keys work once you click in the list.</span>
      </div>
    </div>
  );
}

function PodTable({ pods, sel, onPick, query }: { pods: Pod[]; sel: { ns: string; name: string } | null; onPick: (p: Pod) => void; query: string }) {
  if (!pods.length) return <p className="sub" style={{ padding: 16, margin: 0 }}>{query && !query.startsWith(":") ? `Nothing matches “${query}”.` : "No pods here."}</p>;
  return (
    <table className="ss-table kc-table" aria-label="Pods">
      <thead><tr><th>Name</th><th>Ns</th><th>Ready</th><th>Status</th><th>Restarts</th><th>Pod IP</th><th>Node</th><th>Age</th><th>Commit</th></tr></thead>
      <tbody>
        {pods.map((p) => {
          const on = !!sel && sel.ns === p.ns && sel.name === p.name;
          return (
            <tr key={`${p.ns}/${p.name}`} className={on ? "sel" : ""} aria-selected={on}>
              <td><button type="button" className="kc-name" onClick={() => onPick(p)}>{p.name}</button></td>
              <td className="sub">{p.ns}</td>
              <td className="mono">{p.ready}</td>
              <td><Pill tone={podTone(p.status)}>{p.status}</Pill></td>
              <td className="mono" style={p.restarts > 3 ? { color: "var(--bad)", fontWeight: 600 } : undefined}>{p.restarts}</td>
              <td className="mono">{p.ip ?? "—"}</td>
              <td className="mono sub">{p.node ?? "—"}</td>
              <td className="sub">{age(p.age)}</td>
              <td className="mono sub">{p.commit ?? "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function DeployTable({ list, ctx, onPods, onChange }: { list: Deploy[]; ctx: Shared; onPods: (d: Deploy) => void; onChange: (b: Omit<ChangeBody, "context">) => Promise<void> }) {
  const [ask, setAsk] = useState<{ d: Deploy; action: "restart" | "scale" } | null>(null);
  const [replicas, setReplicas] = useState(1);
  const [busy, setBusy] = useState(false);
  const ro = ctx.ctx!.access === "read";
  if (!list.length) return <p className="sub" style={{ padding: 16, margin: 0 }}>No deployments here.</p>;
  const run = async () => {
    if (!ask) return;
    setBusy(true);
    await onChange({ action: ask.action, kind: "deployment", ns: ask.d.ns, name: ask.d.name, replicas: ask.action === "scale" ? replicas : undefined });
    setBusy(false);
    setAsk(null);
  };
  return (
    <>
      {ask && (
        <div style={{ padding: 12 }}>
          <Confirm busy={busy} yes={ask.action === "restart" ? "Restart" : "Scale"} onNo={() => setAsk(null)} onYes={() => void run()}
            text={ask.action === "restart" ? <>Restart <b className="mono">{ask.d.name}</b>? Its pods are replaced one by one.</>
              : <>Scale <b className="mono">{ask.d.name}</b> to <input className="kc-in" type="number" min={0} max={50} aria-label="Replicas" value={replicas} onChange={(e) => setReplicas(Math.max(0, Math.min(50, Number(e.target.value) || 0)))} style={{ width: 70 }} /> pods?</>} />
        </div>
      )}
      <table className="ss-table kc-table" aria-label="Deployments">
        <thead><tr><th>Deployment</th><th>Ns</th><th>Ready</th><th>Commit</th><th>Image</th><th>Age</th><th></th></tr></thead>
        <tbody>
          {list.map((d) => (
            <tr key={`${d.ns}/${d.name}`}>
              <td className="mono" style={{ fontWeight: 600 }}>{d.name}</td>
              <td className="sub">{d.ns}</td>
              <td><Pill tone={d.available < d.desired ? "bad" : "ok"}>{d.ready}</Pill></td>
              <td className="mono">{d.commit ?? "—"}</td>
              <td className="mono sub">{d.images.join(", ")}</td>
              <td className="sub">{age(d.age)}</td>
              <td style={{ display: "flex", gap: 6 }}>
                <button className="btn sm" type="button" onClick={() => onPods(d)}>Pods</button>
                <button className="btn sm" type="button" disabled={ro} title={ro ? `${ctx.ctx!.name} is read only in keel` : undefined} onClick={() => setAsk({ d, action: "restart" })}>Restart</button>
                <button className="btn sm" type="button" disabled={ro} title={ro ? `${ctx.ctx!.name} is read only in keel` : undefined} onClick={() => { setReplicas(d.desired); setAsk({ d, action: "scale" }); }}>Scale</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function SvcTable({ snap, list, ctx }: { snap: Snapshot; list: Snapshot["services"]; ctx: string }) {
  const { toast } = useApp();
  if (!list.length) return <p className="sub" style={{ padding: 16, margin: 0 }}>No services here.</p>;
  const forward = (name: string, ns: string, ports: string[]) => {
    const port = /^(\d+)/.exec(ports[0] ?? "")?.[1] ?? "80";
    const cmd = `kubectl --context ${ctx} -n ${ns} port-forward svc/${name} 8080:${port}`;
    void copyText(cmd).then((ok) => toast(ok ? `Copied: ${cmd}` : cmd));
  };
  return (
    <table className="ss-table kc-table" aria-label="Services">
      <thead><tr><th>Service</th><th>Ns</th><th>Type</th><th>Cluster IP</th><th>External</th><th>Ports</th><th></th></tr></thead>
      <tbody>
        {list.map((x) => (
          <tr key={`${x.ns}/${x.name}`}>
            <td className="mono" style={{ fontWeight: 600 }}>{x.name}</td>
            <td className="sub">{x.ns}</td>
            <td><Pill tone="idle">{x.type}</Pill></td>
            <td className="mono">{x.cluster_ip ?? "—"}</td>
            <td className="mono">{x.external.length ? x.external.join(", ") : "—"}</td>
            <td className="mono sub">{x.ports.join(", ")}</td>
            <td><button className="btn sm" type="button" onClick={() => forward(x.name, x.ns, x.ports)} title="Copies the kubectl command for your own terminal">Copy port-forward</button></td>
          </tr>
        ))}
      </tbody>
      {snap.services.length > list.length && <caption className="sub" style={{ captionSide: "bottom", padding: 8 }}>{snap.services.length - list.length} more in other namespaces or not matching.</caption>}
    </table>
  );
}

export function Meter({ pct }: { pct: number }) {
  return <span className="kc-meter" role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><i className={pct > 80 ? "hot" : ""} style={{ width: `${Math.min(100, pct)}%` }} /></span>;
}

function NodeTable({ list, metrics }: { list: Snapshot["nodes"]; metrics: boolean }) {
  return (
    <table className="ss-table kc-table" aria-label="Nodes">
      <thead><tr><th>Node</th><th>Status</th><th>Role</th><th>Internal IP</th><th>External IP</th><th>CPU</th><th>Memory</th><th>Pods</th><th>Version</th></tr></thead>
      <tbody>
        {list.map((n) => (
          <tr key={n.name}>
            <td className="mono" style={{ fontWeight: 600 }}>{n.name}</td>
            <td><Pill tone={n.ready ? "ok" : "bad"}>{n.ready ? "Ready" : "NotReady"}</Pill></td>
            <td className="sub">{n.roles.join(", ") || "—"}</td>
            <td className="mono">{n.internal_ip ?? "—"}</td>
            <td className="mono">{n.external_ip ?? "—"}</td>
            <td>{metrics && n.cpu_pct != null ? <><Meter pct={n.cpu_pct} /> <span className="mono">{n.cpu_pct}%</span></> : <span className="mono sub">{n.cpu ?? "—"}</span>}</td>
            <td>{metrics && n.mem_pct != null ? <><Meter pct={n.mem_pct} /> <span className="mono">{n.mem_pct}%</span></> : <span className="mono sub">{memSize(n.memory)}</span>}</td>
            <td className="mono">{n.pods}</td>
            <td className="mono sub">{n.version ?? "—"}</td>
          </tr>
        ))}
      </tbody>
      {!metrics && <caption className="sub" style={{ captionSide: "bottom", padding: 8 }}>CPU and memory show the node's size: the cluster has no metrics-server, so keel cannot see what is used.</caption>}
    </table>
  );
}

function EventTable({ list }: { list: KEvent[] }) {
  if (!list.length) return <p className="sub" style={{ padding: 16, margin: 0 }}>No events.</p>;
  return (
    <table className="ss-table kc-table" aria-label="Events">
      <thead><tr><th>Type</th><th>Reason</th><th>Object</th><th>Message</th><th>Age</th></tr></thead>
      <tbody>
        {list.map((e, i) => (
          <tr key={i}>
            <td><Pill tone={e.type === "Warning" ? "warn" : "idle"}>{e.type}</Pill></td>
            <td style={{ fontWeight: 600 }}>{e.reason}</td>
            <td className="mono">{e.object}</td>
            <td style={{ whiteSpace: "normal", minWidth: 260 }}>{e.message}</td>
            <td className="sub">{age(e.age)}{e.count && e.count > 1 ? ` ×${e.count}` : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PodPanel({ s, pod, snap, tab, setTab, onChange }: {
  s: Shared; pod: Pod; snap: Snapshot; tab: Tab; setTab: (t: Tab) => void; onChange: (b: Omit<ChangeBody, "context">) => Promise<void>;
}) {
  const { toast } = useApp();
  const ctx = s.ctx!;
  const ro = ctx.access === "read";
  const [container, setContainer] = useState(pod.containers[0] ?? "");
  const [ask, setAsk] = useState<"delete" | "restart" | null>(null);
  const [busy, setBusy] = useState(false);
  const deploy = deployOf(pod, snap.deployments);
  const events = snap.events.filter((e) => e.ns === pod.ns && e.object === `pod/${pod.name}`);
  const logsRef = useRef<string[]>([]);
  const copy = (text: string) => void copyText(text).then((ok) => toast(ok ? "Copied." : text));

  const run = async () => {
    if (!ask) return;
    setBusy(true);
    await onChange(ask === "delete" ? { action: "delete", kind: "pod", ns: pod.ns, name: pod.name } : { action: "restart", kind: "deployment", ns: pod.ns, name: deploy! });
    setBusy(false);
    setAsk(null);
  };

  const why = () => {
    const last = logsRef.current.slice(-40).join("\n");
    const ev = events.map((e) => `${e.type} ${e.reason}: ${e.message}`).join("\n");
    askAssistant(`Why is the pod ${pod.name} (namespace ${pod.ns}, cluster ${ctx.name}) ${pod.status}, with ${pod.restarts} restarts? ` +
      `It runs ${pod.images.join(", ") || "an unknown image"}${pod.commit ? ` from commit ${pod.commit}` : ""}. Find the cause in this project's code and say how to fix it, with file:line.\n\n` +
      `Events:\n${ev || "(none)"}\n\nLast log lines:\n${last || "(none)"}`);
    go("helper");
  };

  return (
    <Panel body={false} title={
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", minWidth: 0 }}>
        <h2 className="mono" style={{ textTransform: "none", letterSpacing: 0, color: "var(--fg)", overflowWrap: "anywhere" }}>{pod.name}</h2>
        <Pill tone={podTone(pod.status)}>{pod.status}</Pill>
      </div>
    } extra={<button className="btn sm" type="button" aria-label="Close the pod" onClick={() => s.setSel(null)}>✕</button>}>
      <div style={{ padding: "10px 16px", display: "grid", gap: 8, borderBottom: "1px solid var(--border)" }}>
        <div className="mono sub" style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
          <span>IP {pod.ip ?? "—"}</span><span>node {pod.node ?? "—"}</span><span>ns {pod.ns}</span>{pod.commit && <span>commit {pod.commit}</span>}
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {deploy && <button className="btn sm" type="button" disabled={ro} title={ro ? `${ctx.name} is read only in keel` : undefined} onClick={() => setAsk("restart")}>Restart {deploy}</button>}
          <button className="btn sm" type="button" disabled={ro} title={ro ? `${ctx.name} is read only in keel` : undefined} onClick={() => setAsk("delete")}>Delete pod</button>
          {pod.ip && <button className="btn sm" type="button" onClick={() => copy(pod.ip!)}>Copy IP</button>}
          <button className="btn sm primary" type="button" onClick={why}>Ask KeelBot why</button>
        </div>
        {ro && <span className="sub">{ctx.name} is read only in keel: change it in <a href="#/cluster/connect">Connect</a>.</span>}
        {ask && (
          <Confirm busy={busy} yes={ask === "delete" ? "Delete" : "Restart"} onNo={() => setAsk(null)} onYes={() => void run()}
            text={ask === "delete" ? <>Delete <b className="mono">{pod.name}</b>? {pod.owner ? "Its owner starts a new one." : "Nothing starts it again."}</>
              : <>Restart <b className="mono">{deploy}</b>? Its pods are replaced one by one.</>} />
        )}
      </div>
      <div className="kc-sub" role="tablist" aria-label="Pod view">
        {TABS.map(([id, label, key]) => (
          <button key={id} role="tab" type="button" aria-selected={tab === id} onClick={() => setTab(id)}>{label} <kbd>{key}</kbd></button>
        ))}
      </div>
      {tab === "logs" && <LogsTab s={s} pod={pod} container={container} setContainer={setContainer} onLines={(l) => { logsRef.current = l; }} />}
      {tab === "describe" && <DescribeTab ctx={ctx.name} pod={pod} />}
      {tab === "shell" && <ShellTab ctx={ctx.name} ro={ro} pod={pod} container={container} setContainer={setContainer} copy={copy} />}
      {tab === "forward" && <ForwardTab ctx={ctx.name} pod={pod} copy={copy} />}
      {tab === "events" && (
        <div>
          {events.length ? events.map((e, i) => (
            <div className="kc-row" key={i}><Pill tone={e.type === "Warning" ? "warn" : "idle"}>{e.type}</Pill><b>{e.reason}</b><span style={{ flex: "1 1 200px" }}>{e.message}</span><span className="sub">{age(e.age)}</span></div>
          )) : <p className="sub" style={{ padding: "12px 16px", margin: 0 }}>No events for this pod.</p>}
        </div>
      )}
    </Panel>
  );
}

function ContainerPick({ pod, container, setContainer }: { pod: Pod; container: string; setContainer: (c: string) => void }) {
  if (pod.containers.length < 2) return null;
  return (
    <label className="sub" style={{ display: "flex", gap: 6, alignItems: "center" }}>Container
      <select className="kc-in" value={container} onChange={(e) => setContainer(e.target.value)}>{pod.containers.map((c) => <option key={c}>{c}</option>)}</select>
    </label>
  );
}

function LogsTab({ s, pod, container, setContainer, onLines }: { s: Shared; pod: Pod; container: string; setContainer: (c: string) => void; onLines: (l: string[]) => void }) {
  const ctx = s.ctx!.name;
  const [follow, setFollow] = useState(true);
  const [previous, setPrevious] = useState(false);
  const [filter, setFilter] = useState("");
  const logs = useLoad(`cluster:logs:${ctx}:${pod.ns}:${pod.name}:${container}:${previous}`,
    () => clusterApi.logs({ context: ctx, ns: pod.ns, name: pod.name, container, previous, tail: 500 }), { live: false });
  const reload = useCallback(() => void logs.reload(), [logs.reload]);
  usePoll(reload, 3000, follow && !previous);
  const lines = logs.data?.lines ?? [];
  useEffect(() => onLines(lines), [lines, onLines]);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { if (follow && box.current) box.current.scrollTop = box.current.scrollHeight; }, [lines.length, follow]);
  const f = filter.trim().toLowerCase();
  const shown = f ? lines.filter((l) => l.toLowerCase().includes(f)) : lines;
  return (
    <>
      <div className="kc-bar" style={{ padding: "10px 12px 0" }}>
        <input className="kc-in mono" style={{ flex: "1 1 160px", minWidth: 0 }} aria-label="Filter log lines" placeholder="filter lines, e.g. ERROR" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <ContainerPick pod={pod} container={container} setContainer={setContainer} />
        <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={follow} onChange={() => setFollow(!follow)} /> Follow</label>
        <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={previous} onChange={() => setPrevious(!previous)} /> Before the last restart</label>
      </div>
      {logs.error && <div style={{ padding: "0 12px" }}><Note tone="err" role="alert">{logs.error.message} {logs.error.hint ?? ""}</Note></div>}
      <div className="kc-log" ref={box} role="log" aria-label="Log">
        {shown.map((l, i) => <div key={i} className={/\b(error|fatal|panic|exception)\b/i.test(l) ? "err" : /\bwarn(ing)?\b/i.test(l) ? "warn" : ""}>{l}</div>)}
        {!logs.data && !logs.error && <div style={{ color: "#8b877f" }}>Reading the log…</div>}
        {logs.data && !lines.length && <div style={{ color: "#8b877f" }}>{previous ? "No log from before the last restart." : "The log is empty."}</div>}
        {follow && !previous && logs.data && <div style={{ color: "#8b877f" }}>▍ following…</div>}
      </div>
      <p className="sub" style={{ margin: 0, padding: "6px 12px" }}>{shown.length} of {lines.length} lines{logs.data?.container ? ` · ${logs.data.container}` : ""}</p>
    </>
  );
}

function DescribeTab({ ctx, pod }: { ctx: string; pod: Pod }) {
  const d = useLoad(`cluster:describe:${ctx}:${pod.ns}:${pod.name}`, () => clusterApi.describe({ context: ctx, kind: "pod", ns: pod.ns, name: pod.name }), { live: false });
  if (d.error) return <div style={{ padding: 12 }}><Note tone="err" role="alert">{d.error.message} {d.error.hint ?? ""}</Note></div>;
  return d.data ? <pre className="kc-pre">{d.data.text}</pre> : <div className="empty loading" role="status">Describing…</div>;
}

function ShellTab({ ctx, ro, pod, container, setContainer, copy }: { ctx: string; ro: boolean; pod: Pod; container: string; setContainer: (c: string) => void; copy: (t: string) => void }) {
  const [cmd, setCmd] = useState("");
  const [out, setOut] = useState<{ cmd: string; output: string; code: number }[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const exec = execCommand(ctx, pod.ns, pod.name, container);
  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!cmd.trim()) return;
    setBusy(true);
    setProblem(null);
    try {
      const r = await clusterApi.exec({ context: ctx, ns: pod.ns, name: pod.name, container, command: cmd });
      setOut((o) => [...o, { cmd, output: r.output, code: r.code }]);
      setCmd("");
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div style={{ padding: 12, display: "grid", gap: 10 }}>
      <p style={{ margin: 0 }}>Run one command inside <span className="mono">{pod.name}</span>. keel writes it down in the cluster's history. {ro && <b>{ctx} is read only in keel, so it runs nothing.</b>}</p>
      <form onSubmit={(e) => void run(e)} aria-label="Run a command" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <ContainerPick pod={pod} container={container} setContainer={setContainer} />
        <input className="kc-in mono" style={{ flex: "1 1 220px", minWidth: 0 }} aria-label="Command" placeholder="ls -la /app" value={cmd} onChange={(e) => setCmd(e.target.value)} disabled={ro} />
        <button className="btn primary" type="submit" disabled={ro || busy || !cmd.trim()}>{busy ? "Running…" : "Run"}</button>
      </form>
      {problem && <Note tone="err" role="alert">{problem}</Note>}
      {out.length > 0 && (
        <div className="kc-log" style={{ borderRadius: 8 }} aria-label="Output">
          {out.map((o, i) => (
            <div key={i}><div style={{ color: "#7ee2a8" }}>$ {o.cmd}</div><div>{o.output}</div>{o.code !== 0 && <div className="err">exit {o.code}</div>}</div>
          ))}
        </div>
      )}
      <div style={{ display: "grid", gap: 6 }}>
        <span className="sub">A shell you can type in runs in your own terminal (keel has no terminal of its own yet):</span>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <code className="mono" style={{ overflowWrap: "anywhere" }}>{exec}</code>
          <button className="btn sm" type="button" onClick={() => copy(exec)}>Copy</button>
        </div>
      </div>
    </div>
  );
}

function ForwardTab({ ctx, pod, copy }: { ctx: string; pod: Pod; copy: (t: string) => void }) {
  const [local, setLocal] = useState("8080");
  const [port, setPort] = useState("80");
  const cmd = forwardCommand(ctx, pod.ns, pod.name, local, port);
  return (
    <div style={{ padding: 12, display: "grid", gap: 10 }}>
      <p className="sub" style={{ margin: 0 }}>Open the pod's port on your computer, for a browser or a database tool. Run this in your own terminal: keel in Docker cannot open a port on your computer.</p>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label className="sub" style={{ display: "grid", gap: 4 }}>Port on your computer<input className="kc-in mono" aria-label="Port on your computer" value={local} onChange={(e) => setLocal(e.target.value.replace(/\D/g, "").slice(0, 5))} style={{ width: 110 }} /></label>
        <span style={{ paddingBottom: 8 }}>→</span>
        <label className="sub" style={{ display: "grid", gap: 4 }}>Pod port<input className="kc-in mono" aria-label="Pod port" value={port} onChange={(e) => setPort(e.target.value.replace(/\D/g, "").slice(0, 5))} style={{ width: 110 }} /></label>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <code className="mono" style={{ overflowWrap: "anywhere" }}>{cmd}</code>
        <button className="btn sm primary" type="button" onClick={() => copy(cmd)}>Copy</button>
      </div>
    </div>
  );
}
