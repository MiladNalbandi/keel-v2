// The Cluster page (#/cluster, #/cluster/overview, #/cluster/connect): the clusters in the person's kubeconfig, k9s style. It holds
// what the three views share: the chosen context (kept in this browser), the snapshot of that context (every 5 s while the page
// is visible), and the selected pod. It imports only react, @keel/web-sdk and its own files.

import { useCallback, useEffect, useMemo, useState } from "react";
import { EmptyState, go, Note, PageHead, Pill, useLoad, useRoute } from "@keel/web-sdk";
import { ACCESS_PILL, clusterApi, type Context, type Contexts, type Snapshot } from "./clusterApi";
import { Connect } from "./Connect";
import { Overview } from "./Overview";
import { Workloads } from "./Workloads";

const KEEP = "keel.cluster.context";
const read = () => { try { return localStorage.getItem(KEEP) ?? ""; } catch { return ""; } };
const keep = (v: string) => { try { localStorage.setItem(KEEP, v); } catch { /* private window */ } };

/** Calls fn every ms while the page is visible (and once more when it becomes visible again). */
export function usePoll(fn: () => void, ms: number, on = true) {
  useEffect(() => {
    if (!on) return;
    const t = window.setInterval(() => { if (document.visibilityState !== "hidden") fn(); }, ms);
    const back = () => { if (document.visibilityState === "visible") fn(); };
    document.addEventListener("visibilitychange", back);
    return () => { window.clearInterval(t); document.removeEventListener("visibilitychange", back); };
  }, [fn, ms, on]);
}

// the page's own small classes (kc-), on top of keel's: the k9s list, the pod's panel, the log and the key bar
export const CSS = `
.kc-bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.kc-bar select,.kc-in{border:1px solid var(--border);background:var(--panel);border-radius:7px;padding:5px 9px;min-height:32px}
.kc-search{flex:1 1 260px;min-width:0;font-family:var(--f-mono);font-size:12.5px}
.kc-ns{display:flex;gap:4px;flex-wrap:wrap}
.kc-ns button{border:1px solid transparent;background:transparent;border-radius:7px;padding:3px 9px;cursor:pointer;color:var(--dim);font-weight:500}
.kc-ns button[aria-pressed="true"]{background:var(--accent-soft);color:var(--fg)}
.kc-cnt{font-size:11px;border-radius:999px;padding:0 6px;background:var(--panel-2);color:var(--dim);font-weight:600;margin-left:4px}
.kc-split{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}
.kc-list{flex:1 1 560px;min-width:0}
.kc-side{flex:1 1 440px;min-width:0}
.kc-table tr.sel td{background:var(--accent-soft)}
.kc-table td.mono,.kc-table .mono{font-size:12px}
.kc-name{border:0;background:transparent;padding:0;cursor:pointer;font-weight:600;font-family:var(--f-mono);font-size:12.5px;text-align:left;color:inherit}
.kc-name:hover{text-decoration:underline}
.kc-log{background:#17171a;color:#e9e7e2;font-family:var(--f-mono);font-size:12px;line-height:1.55;padding:10px 12px;min-height:200px;max-height:380px;overflow:auto;white-space:pre-wrap;word-break:break-word;border-radius:0 0 10px 10px}
.kc-log .err{color:#ff8a7a}.kc-log .warn{color:#f2c46a}
.kc-pre{margin:0;padding:12px 14px;white-space:pre-wrap;overflow:auto;max-height:420px;font-family:var(--f-mono);font-size:12px}
.kc-keys{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--dim);margin-top:12px}
.kc-keys kbd{font-family:var(--f-mono);font-size:11px;border:1px solid var(--border);border-bottom-width:2px;border-radius:5px;padding:0 5px;background:var(--panel);color:var(--fg)}
.kc-meter{width:90px;height:7px;border-radius:4px;background:var(--panel-2);overflow:hidden;display:inline-block;vertical-align:middle}
.kc-meter i{display:block;height:100%;background:var(--accent)}
.kc-meter i.hot{background:var(--warn)}
.kc-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}
.kc-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--border)}
.kc-row:last-child{border-bottom:0}
.kc-sub{display:flex;gap:2px;flex-wrap:wrap;border-bottom:1px solid var(--border);padding:0 8px}
.kc-sub button{border:0;border-bottom:2px solid transparent;background:transparent;padding:7px 9px;cursor:pointer;color:var(--dim);font-weight:500}
.kc-sub button[aria-selected="true"]{color:var(--fg);border-bottom-color:var(--accent)}
`;

export type Shared = {
  contexts: Contexts;
  on: Context[];
  ctx: Context | null;
  setCtx: (name: string) => void;
  snap: Snapshot | null;
  snapError: { message: string; hint?: string } | null;
  reloadSnap: () => void;
  reloadContexts: () => void;
  sel: { ns: string; name: string } | null;
  setSel: (s: { ns: string; name: string } | null) => void;
};

export function ClusterPage({ pid }: { pid: string }) {
  const { arg } = useRoute();
  const view = arg === "overview" || arg === "connect" ? arg : "workloads";
  const contexts = useLoad("cluster:contexts", () => clusterApi.contexts(), { live: false });
  const [chosen, setChosen] = useState(read);
  const [sel, setSel] = useState<{ ns: string; name: string } | null>(null);
  const on = useMemo(() => (contexts.data?.contexts ?? []).filter((c) => c.on), [contexts.data]);
  const ctx = on.find((c) => c.name === chosen) ?? on.find((c) => c.current) ?? on[0] ?? null;
  const setCtx = useCallback((name: string) => { setChosen(name); keep(name); setSel(null); }, []);
  const snap = useLoad(ctx && view !== "connect" ? `cluster:snap:${ctx.name}` : null, () => clusterApi.snapshot(ctx!.name), { live: false });
  const reloadSnap = useCallback(() => void snap.reload(), [snap.reload]);
  usePoll(reloadSnap, 5000, !!ctx && view !== "connect");

  const tabs: [string, string, string | undefined][] = [["workloads", "Workloads", undefined], ["overview", "Overview", "overview"], ["connect", "Connect", "connect"]];
  const head = (
    <PageHead title="Cluster" sub="See what runs, find it by name or IP, read its logs and run a command. keel asks before it changes anything."
      actions={
        <div className="tabs" role="tablist" aria-label="Cluster views">
          {tabs.map(([id, label, a]) => (
            <button key={id} role="tab" type="button" aria-selected={view === id} onClick={() => go("cluster", a)}>{label}</button>
          ))}
        </div>
      } />
  );

  const c = contexts.data;
  let body;
  if (!c) {
    body = contexts.error ? <Note tone="err" role="alert">{contexts.error.message} {contexts.error.hint ?? ""}</Note> : <div className="empty loading" role="status">Loading the clusters…</div>;
  } else if (view === "connect") {
    body = <Connect contexts={c} onSaved={(v) => contexts.setData(v)} />;
  } else if (!c.kubectl) {
    body = <EmptyState title="keel cannot find kubectl">keel reads clusters with kubectl. Install it on the computer keel runs on, then open <a href="#/cluster/connect">Connect</a>.</EmptyState>;
  } else if (!on.length) {
    body = <EmptyState title="No cluster yet" action={<a className="btn primary" href="#/cluster/connect">Connect a cluster</a>}>
      {c.contexts.length ? "Every context in your kubeconfig is turned off." : `Your kubeconfig${c.kubeconfig ? ` (${c.kubeconfig})` : ""} has no context.`} keel uses the kubeconfig on this computer, like kubectl does.
    </EmptyState>;
  } else {
    const shared: Shared = {
      contexts: c, on, ctx, setCtx, snap: snap.data, snapError: snap.error, reloadSnap, reloadContexts: () => void contexts.reload(), sel, setSel,
    };
    body = view === "overview" ? <Overview s={shared} pid={pid} /> : <Workloads s={shared} />;
  }
  return (
    <>
      <style>{CSS}</style>
      {head}
      {body}
    </>
  );
}

export function AccessPill({ c }: { c: Context }) {
  return <Pill tone={c.access === "read" ? "idle" : c.access === "ask" ? "run" : "warn"} title={`keel may ${c.access === "read" ? "only look" : c.access === "ask" ? "change after you approve" : "change without asking"}`}>{ACCESS_PILL[c.access]}</Pill>;
}
