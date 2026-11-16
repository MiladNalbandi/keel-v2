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
.kc-bar select,.kc-in{border:1px solid var(--border);background:var(--panel);color:var(--fg);border-radius:9px;padding:5px 10px;min-height:34px;box-sizing:border-box;font:inherit}
.kc-in:focus,.kc-sel:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.kc-ctx{display:flex;gap:10px 14px;flex-wrap:wrap;align-items:center;padding:12px 16px;margin-bottom:16px;background:var(--panel);border:1px solid var(--border);border-radius:14px;animation:k-rise .36s cubic-bezier(.2,.7,.2,1) .04s both}
.kc-lab{display:inline-flex;gap:10px;align-items:center;font-size:11.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--dim)}
.kc .kc-sel{height:32px;padding:0 10px;border-radius:9px;font-size:13.5px;font-weight:600;letter-spacing:0;text-transform:none;color:var(--fg)}
.kc-nsw{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.kc-ns{display:flex;gap:4px;flex-wrap:wrap}
.kc-ns button{display:inline-flex;align-items:center;height:28px;border:1px solid transparent;background:transparent;border-radius:8px;padding:0 9px;cursor:pointer;color:var(--dim);font:inherit;font-size:13px;font-weight:500;transition:background .16s ease,color .16s ease}
.kc-ns button:hover{background:var(--panel-2);color:var(--fg)}
.kc-ns button[aria-pressed="true"]{background:var(--accent-soft);color:var(--fg)}
.kc-live{margin-left:auto;display:inline-flex;align-items:center;gap:7px;font-size:12.5px;color:var(--dim);white-space:nowrap}
.kc-live i{width:7px;height:7px;border-radius:50%;background:var(--ok);animation:k-pulse 2s ease-in-out infinite}
.kc-cnt{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;box-sizing:border-box;padding:0 5px;margin-left:6px;border-radius:999px;background:var(--panel-2);color:var(--dim);font-size:11px;font-weight:600}
.kc .tabs button[aria-selected="true"] .kc-cnt{background:var(--accent-soft);color:var(--accent)}
.kc-ns button[aria-pressed="true"] .kc-cnt{background:var(--panel);color:var(--fg)}
.kc-kinds{margin-bottom:12px}
.kc .grid>.kc-ctx{margin-bottom:0}
.kc .kc-search{display:block;width:100%;margin-bottom:12px;font-family:var(--f-mono);font-size:12.5px}
.kc-split{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}
.kc-list{flex:1 1 560px;min-width:0}
.kc-side{flex:1 1 440px;min-width:0;animation:k-rise .3s cubic-bezier(.2,.7,.2,1) both}
.kc-table th{font-size:12px;font-weight:600}
.kc-table td{padding-top:10px;padding-bottom:10px}
.kc-table tbody tr{transition:background .16s ease}
.kc-table tbody tr:hover td{background:color-mix(in srgb,var(--panel-2) 55%,transparent)}
.kc-table tr.sel td,.kc-table tr.sel:hover td{background:var(--accent-soft)}
.kc-table td.mono,.kc-table .mono{font-size:12px}
.kc-table td .pill{background:transparent;padding:0;min-height:0;font-size:13px}
.kc-table td .pill::before{display:none}
.kc-name{border:0;background:transparent;padding:0;cursor:pointer;font-weight:600;font-family:var(--f-mono);font-size:12.5px;text-align:left;color:inherit}
.kc-name:hover{color:var(--accent)}
.kc-log{background:#151517;color:#e9e7e2;font-family:var(--f-mono);font-size:12px;line-height:1.6;padding:10px 12px;min-height:220px;max-height:380px;overflow:auto;white-space:pre-wrap;word-break:break-word;border-radius:0 0 14px 14px}
.kc-log .err{color:#ff8a7a}.kc-log .warn{color:#f2c46a}
.kc-pre{margin:0;padding:12px 14px;white-space:pre-wrap;overflow:auto;max-height:420px;font-family:var(--f-mono);font-size:12px;line-height:1.6}
.kc-keys{display:flex;gap:8px 14px;flex-wrap:wrap;align-items:center;font-size:12px;color:var(--dim);margin-top:12px}
.kc-keys kbd{display:inline-flex;align-items:center;justify-content:center;min-width:20px;height:20px;box-sizing:border-box;padding:0 5px;border-radius:5px;border:1px solid var(--rail);border-bottom-width:2px;background:var(--panel-2);color:var(--dim);font:500 11px var(--f-mono)}
.kc-keys-n{margin-left:auto}
.kc-meter{width:90px;height:6px;border-radius:3px;background:var(--panel-2);overflow:hidden;display:inline-block;vertical-align:middle}
.kc-meter i{display:block;height:100%;background:var(--accent);border-radius:3px}
.kc-meter i.hot{background:var(--warn)}
.kc-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}
.kc-cards>.panel{border-radius:14px}
.kc-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:12px 16px;border-bottom:1px solid var(--border)}
.kc-row:last-child{border-bottom:0}
.kc-sub{display:flex;gap:2px;flex-wrap:wrap;border-bottom:1px solid var(--border);padding:0 8px}
.kc-sub button{height:36px;border:0;border-bottom:2px solid transparent;margin-bottom:-1px;background:transparent;padding:0 10px;cursor:pointer;color:var(--dim);font:inherit;font-size:13.5px;font-weight:500;transition:color .16s ease,border-color .2s ease}
.kc-sub button:hover{color:var(--fg)}
.kc-sub button[aria-selected="true"]{color:var(--fg);border-bottom-color:var(--accent)}
@media (max-width:900px){.kc-live{margin-left:0}.kc-keys-n{margin-left:0}}
@media (prefers-reduced-motion:reduce){.kc-ctx,.kc-side,.kc-live i{animation:none}}
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
    <div className="rdw kc">
      <style>{CSS}</style>
      {head}
      {body}
    </div>
  );
}

export function AccessPill({ c }: { c: Context }) {
  return <Pill tone={c.access === "read" ? "idle" : c.access === "ask" ? "run" : "warn"} title={`keel may ${c.access === "read" ? "only look" : c.access === "ask" ? "change after you approve" : "change without asking"}`}>{ACCESS_PILL[c.access]}</Pill>;
}
