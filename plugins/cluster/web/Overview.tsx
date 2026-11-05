// Cluster › Overview: what needs the person (crashes, pods that cannot start), the nodes with their IPs and load, which commit of
// the project runs on each cluster against its base branch on GitHub, the ways in (ingresses, load balancers, node ports), and the
// changes keel made.

import { Fragment, useState } from "react";
import { EmptyState, go, Panel, Pill, useLoad } from "@keel/web-sdk";
import { Meter } from "./Workloads";
import { age, clusterApi, memSize, type RunCell, type Snapshot } from "./clusterApi";
import { AccessPill, type Shared } from "./ClusterPage";

const KIND_TONE: Record<string, "bad" | "warn" | "idle"> = { crash: "bad", image: "bad", failed: "bad", node: "bad", waiting: "warn", deploy: "warn" };
const KIND_WORD: Record<string, string> = { crash: "Crash", image: "Image", failed: "Failed", node: "Node", waiting: "Waiting", deploy: "Not ready" };

export function Overview({ s, pid }: { s: Shared; pid: string }) {
  const snap = s.snap;
  const ctx = s.ctx!;
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="kc-bar" style={{ marginBottom: 0 }}>
        <label className="sub" style={{ display: "flex", gap: 8, alignItems: "center" }}>Cluster
          <select value={ctx.name} onChange={(e) => s.setCtx(e.target.value)} aria-label="Cluster" style={{ fontWeight: 600, color: "var(--fg)" }}>
            {s.on.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </label>
        <AccessPill c={ctx} />
        {snap && <span className="sub">{snap.nodes.length} nodes · {snap.pods.length} pods · {snap.problems.length} need you</span>}
      </div>
      {!snap ? (
        s.snapError ? <EmptyState title={s.snapError.message}>{s.snapError.hint}</EmptyState> : <div className="empty loading" role="status">Reading {ctx.name}…</div>
      ) : (
        <>
          <Problems snap={snap} onOpen={(ns, name) => { s.setSel({ ns, name }); go("cluster"); }} />
          <Nodes snap={snap} />
          <div className="kc-split">
            <div style={{ flex: "2 1 600px", minWidth: 0 }}><RunsWhere pid={pid} /></div>
            <div style={{ flex: "1 1 320px", minWidth: 0 }}><WaysIn snap={snap} /></div>
          </div>
        </>
      )}
      <History />
    </div>
  );
}

function Problems({ snap, onOpen }: { snap: Snapshot; onOpen: (ns: string, name: string) => void }) {
  return (
    <Panel title={`Needs you · ${snap.problems.length}`} body={false}>
      {snap.problems.length ? snap.problems.map((p, i) => (
        <div className="kc-row" key={i}>
          <Pill tone={KIND_TONE[p.kind] ?? "warn"}>{KIND_WORD[p.kind] ?? p.kind}</Pill>
          <div style={{ flex: "1 1 320px", minWidth: 0 }}><b>{p.title}</b><div className="sub">{p.line}</div></div>
          {p.object.startsWith("pod/") && <button className="btn sm" type="button" onClick={() => onOpen(p.ns, p.name)}>Open the pod</button>}
        </div>
      )) : <p className="sub" style={{ padding: "12px 16px", margin: 0 }}>Nothing: every pod runs and every node is ready.</p>}
    </Panel>
  );
}

function Nodes({ snap }: { snap: Snapshot }) {
  return (
    <section aria-label="Nodes" className="kc-cards">
      {snap.nodes.map((n) => {
        const pods = snap.pods.filter((p) => p.node === n.name);
        return (
          <div className="panel" key={n.name} style={{ padding: "12px 14px", display: "grid", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
              <b className="mono">{n.name}</b><Pill tone={n.ready ? "ok" : "bad"}>{n.ready ? "Ready" : "NotReady"}</Pill>
            </div>
            <div className="mono sub">{n.internal_ip ?? "—"}{n.external_ip ? ` · ${n.external_ip}` : ""} · {n.roles.join(", ") || "worker"}</div>
            {snap.metrics && n.cpu_pct != null ? (
              <div className="sub" style={{ display: "grid", gap: 4 }}>
                <span>CPU <Meter pct={n.cpu_pct} /> <span className="mono">{n.cpu_pct}%</span></span>
                <span>Memory <Meter pct={n.mem_pct ?? 0} /> <span className="mono">{n.mem_pct ?? 0}%</span></span>
              </div>
            ) : <div className="sub">{n.cpu || "?"} CPU · {memSize(n.memory)} memory</div>}
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
              {pods.slice(0, 12).map((p) => (
                <span key={`${p.ns}/${p.name}`} className="mono" title={`${p.ns}/${p.name}: ${p.status}`}
                  style={{ fontSize: 11, border: "1px solid var(--border)", borderRadius: 6, padding: "0 6px", background: p.status === "Running" || p.status === "Completed" ? "var(--panel-2)" : "var(--bad-soft)", color: p.status === "Running" || p.status === "Completed" ? undefined : "var(--bad)" }}>
                  {p.app || p.name}
                </span>
              ))}
              {pods.length > 12 && <span className="sub">+{pods.length - 12}</span>}
            </div>
          </div>
        );
      })}
    </section>
  );
}

const cellTone = (c: RunCell | undefined): "ok" | "warn" | "idle" | "bad" =>
  !c || c.state === "unknown" ? "idle" : c.state === "same" ? "ok" : c.state === "diverged" ? "bad" : "warn";
const cellText = (c: RunCell | undefined, base: string) =>
  !c ? "not here" : !c.commit ? "no commit" : c.state === "same" ? `${c.commit.slice(0, 7)} = ${base}`
    : c.state === "behind" ? `${c.commit.slice(0, 7)} · ${c.behind ?? "?"} behind` : c.state === "ahead" ? `${c.commit.slice(0, 7)} · ahead`
      : c.state === "diverged" ? `${c.commit.slice(0, 7)} · diverged` : `${c.commit.slice(0, 7)} · not in the repo`;

function RunsWhere({ pid }: { pid: string }) {
  const runs = useLoad(pid ? `cluster:runs:${pid}` : null, () => clusterApi.runs(pid), { live: false });
  const [only, setOnly] = useState(false);
  if (!pid) return <Panel title="What runs where"><p className="sub" style={{ margin: 0 }}>Choose a project: keel compares the commit each cluster runs with the project's base branch.</p></Panel>;
  const r = runs.data;
  const base = r?.repo?.base ?? "main";
  const apps = (r?.apps ?? []).filter((a) => !only || Object.values(a.by).some((c) => c.state !== "same"));
  return (
    <Panel title="What runs where" extra={<label className="sub" style={{ display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={only} onChange={() => setOnly(!only)} /> Only what differs from {base}</label>} body={false}>
      {runs.error && <p className="sub" style={{ padding: "12px 16px", margin: 0 }}>{runs.error.message} {runs.error.hint ?? ""}</p>}
      {!r && !runs.error && <div className="empty loading" role="status">Comparing…</div>}
      {r && (
        <>
          {r.repo && <p className="sub" style={{ margin: 0, padding: "8px 16px" }}>{r.repo.remote && <>{r.repo.web ? <a href={r.repo.web} target="_blank" rel="noreferrer">{r.repo.remote}</a> : r.repo.remote} · </>}{r.repo.head ? <>{base} is at <span className="mono">{r.repo.head.slice(0, 7)}</span></> : "The project has no main branch, so keel cannot compare."}</p>}
          {!r.repo && <p className="sub" style={{ margin: 0, padding: "8px 16px" }}>This project's folder has no git remote, so keel cannot compare.</p>}
          {apps.length ? (
            <div className="ss-tablewrap">
              <table className="ss-table kc-table" aria-label="What runs where">
                <thead><tr><th>App</th>{r.contexts.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>
                  {apps.map((a) => (
                    <tr key={`${a.ns}/${a.name}`}>
                      <td className="mono" style={{ fontWeight: 600 }}>{a.name} <span className="sub">{a.ns}</span></td>
                      {r.contexts.map((c) => {
                        const cell = a.by[c];
                        return (
                          <td key={c} title={cell?.subject ?? undefined}>
                            <Pill tone={cellTone(cell)}>{cellText(cell, base)}</Pill>
                            {cell?.pr && <>{" "}{r.repo?.web ? <a className="mono" href={`${r.repo.web}/pull/${cell.pr.slice(1)}`} target="_blank" rel="noreferrer">{cell.pr}</a> : <span className="mono">{cell.pr}</span>}</>}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="sub" style={{ padding: "8px 16px", margin: 0 }}>{only ? `Every app runs ${base}.` : "No deployment says which commit it runs."}</p>}
          {Object.entries(r.errors).map(([c, m]) => <p key={c} className="sub" style={{ margin: 0, padding: "4px 16px" }}>{c}: {m}</p>)}
          <p className="sub" style={{ margin: 0, padding: "8px 16px" }}>keel reads the commit from the annotation <span className="mono">keel.dev/commit</span>, the label <span className="mono">org.opencontainers.image.revision</span>, or an image tag that is a commit, then looks it up in the project's git.</p>
        </>
      )}
    </Panel>
  );
}

function WaysIn({ snap }: { snap: Snapshot }) {
  const ways = [
    ...snap.ingresses.flatMap((i) => i.hosts.map((h) => ({ key: `i:${i.ns}/${i.name}/${h.host}${h.path}`, host: `${h.host || "*"}${h.path && h.path !== "/" ? h.path : ""}`, kind: "Ingress", to: h.service, ip: i.address.join(", ") || "—" }))),
    ...snap.services.filter((x) => x.type === "LoadBalancer" || x.type === "NodePort").map((x) => ({
      key: `s:${x.ns}/${x.name}`, host: x.type === "LoadBalancer" ? x.external.join(", ") || "(pending)" : x.ports.join(", "), kind: x.type, to: `svc/${x.name}`, ip: x.ns,
    })),
  ];
  return (
    <Panel title="Ways in" body={false}>
      {ways.length ? ways.map((w) => (
        <div className="kc-row" key={w.key} style={{ display: "grid", gap: 2 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}><b className="mono">{w.host}</b><Pill tone="idle">{w.kind}</Pill></div>
          <div className="mono sub">→ {w.to} · {w.ip}</div>
        </div>
      )) : <p className="sub" style={{ padding: "12px 16px", margin: 0 }}>No ingress, load balancer or node port.</p>}
    </Panel>
  );
}

function History() {
  const h = useLoad("cluster:history", () => clusterApi.history(), { live: false });
  const items = h.data?.items ?? [];
  return (
    <Panel title="Changes keel made" body={false}>
      {items.length ? (
        <div className="ss-tablewrap">
          <table className="ss-table kc-table" aria-label="Changes keel made">
            <thead><tr><th>When</th><th>Who</th><th>Cluster</th><th>What</th><th>Result</th></tr></thead>
            <tbody>
              {items.slice(0, 50).map((x, i) => (
                <Fragment key={i}>
                  <tr>
                    <td className="sub">{age((Date.now() - Date.parse(x.at)) / 1000)} ago</td>
                    <td>{x.who}</td>
                    <td className="mono">{x.context}</td>
                    <td><span className="mono">{x.action}</span> {x.target}</td>
                    <td className="sub" style={{ whiteSpace: "normal" }}>{x.result}</td>
                  </tr>
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="sub" style={{ padding: "12px 16px", margin: 0 }}>{h.error ? h.error.message : "No change yet. Every command and change, by you or an agent, is written here."}</p>}
    </Panel>
  );
}
