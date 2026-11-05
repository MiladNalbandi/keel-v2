// Cluster › Connect: the contexts of the kubeconfig on this computer, each one on or off, and what keel may do there (only look,
// change after you approve, change without asking: only for a cluster on this computer). A test reaches the cluster with kubectl.
// keel copies no token: kubectl reads the kubeconfig itself.

import { useEffect, useState } from "react";
import { errorParts, Note, Panel, Pill, Switch, useApp } from "@keel/web-sdk";
import { ACCESS_WORDS, clusterApi, loopback, type Access, type Contexts, type TestResult } from "./clusterApi";

const TOOLS: [string, string, boolean][] = [
  ["cluster_find", "List pods, deployments, services and nodes; search by name or IP", false],
  ["cluster_logs", "Read a pod's log, also from before its last restart", false],
  ["cluster_describe", "Describe a pod, a deployment, a service or a node", false],
  ["cluster_exec", "Run one command in a pod", true],
  ["cluster_change", "Delete a pod, restart or scale a deployment", true],
];

type Draft = Record<string, { on: boolean; access: Access }>;
const draftOf = (c: Contexts): Draft => Object.fromEntries(c.contexts.map((x) => [x.name, { on: x.on, access: x.access }]));

export function Connect({ contexts: c, onSaved }: { contexts: Contexts; onSaved: (c: Contexts) => void }) {
  const { toast } = useApp();
  const [draft, setDraft] = useState<Draft>(() => draftOf(c));
  const [tests, setTests] = useState<Record<string, TestResult | "busy">>({});
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(draftOf(c)), [c]);
  const dirty = c.contexts.some((x) => draft[x.name] && (draft[x.name].on !== x.on || draft[x.name].access !== x.access));

  const test = async (name: string) => {
    setTests((t) => ({ ...t, [name]: "busy" }));
    try {
      const r = await clusterApi.test(name);
      setTests((t) => ({ ...t, [name]: r }));
    } catch (err) {
      const p = errorParts(err);
      setTests((t) => ({ ...t, [name]: { ok: false, ms: 0, error: p.message, hint: p.hint } }));
    }
  };
  const save = async () => {
    setBusy(true);
    setProblem(null);
    try {
      onSaved(await clusterApi.save(draft));
      toast("Saved.");
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };
  const set = (name: string, v: Partial<{ on: boolean; access: Access }>) => setDraft((d) => ({ ...d, [name]: { ...d[name], ...v } }));

  return (
    <div className="grid" style={{ gap: 16, maxWidth: 1100 }}>
      <p style={{ margin: 0 }} className="sub">
        keel uses the kubeconfig on this computer{c.kubeconfig ? <> (<span className="mono">{c.kubeconfig}</span>)</> : null}, like kubectl and k9s do. It does not copy your token.
        {c.in_docker ? <> keel runs in Docker and sees your <span className="mono">~/.kube</span> read only.</> : null}
      </p>
      {!c.kubectl && <Note tone="err" role="alert"><b>keel cannot find kubectl.</b> Install it on the computer keel runs on (keel's Docker image has it).</Note>}
      {c.kubectl && c.kubectl_version && <span className="sub">kubectl {c.kubectl_version}</span>}

      <Panel title="1 · Clusters in your kubeconfig" extra={c.contexts.length > 0 && <button className="btn sm" type="button" onClick={() => c.contexts.forEach((x) => void test(x.name))}>Test all</button>} body={false}>
        {!c.contexts.length && <p className="sub" style={{ padding: "12px 16px", margin: 0 }}>Your kubeconfig has no context yet. Add one with your cloud's tool (for example <span className="mono">kind create cluster</span>), then open this page again.</p>}
        {c.contexts.map((x) => {
          const d = draft[x.name] ?? { on: x.on, access: x.access };
          const t = tests[x.name];
          return (
            <div className="kc-row" key={x.name} aria-label={x.name} role="group">
              <div style={{ flex: "1 1 260px", minWidth: 0, display: "flex", gap: 10, alignItems: "center" }}>
                <Switch on={d.on} onChange={(on) => set(x.name, { on })}><span className="sr-only">Use {x.name} in keel</span></Switch>
                <span style={{ display: "grid", minWidth: 0 }}>
                  <b className="mono" style={{ overflowWrap: "anywhere" }}>{x.name}{x.current ? <span className="sub"> · current</span> : null}</b>
                  <span className="sub" style={{ overflowWrap: "anywhere" }}>{x.server}{x.local ? " · on this computer" : ""}</span>
                </span>
              </div>
              <label className="sub" style={{ display: "flex", gap: 8, alignItems: "center" }}>keel may
                <select className="kc-in" aria-label={`What keel may do on ${x.name}`} value={d.access} disabled={!d.on} onChange={(e) => set(x.name, { access: e.target.value as Access })} style={{ color: "var(--fg)" }}>
                  <option value="read">{ACCESS_WORDS.read}</option>
                  <option value="ask">{ACCESS_WORDS.ask}</option>
                  {(x.local || d.access === "free") && <option value="free">{ACCESS_WORDS.free}</option>}
                </select>
              </label>
              <button className="btn sm" type="button" onClick={() => void test(x.name)} disabled={t === "busy"}>{t === "busy" ? "Testing…" : "Test"}</button>
              {t && t !== "busy" && (t.ok
                ? <Pill tone="ok">{[t.version, t.nodes != null ? `${t.nodes} node${t.nodes === 1 ? "" : "s"}` : null, `${t.ms} ms`].filter(Boolean).join(" · ")}</Pill>
                : <Pill tone="bad" title={t.hint ?? undefined}>not reached{t.error ? `: ${t.error}` : ""}</Pill>)}
              {c.in_docker && loopback(x.server) && (
                <div style={{ flexBasis: "100%" }}>
                  <Note tone="warn">This cluster listens on <span className="mono">127.0.0.1</span> of your computer, and keel runs in Docker, so keel cannot reach it.
                    For kind: <span className="mono">docker network connect kind &lt;keel's container&gt;</span>, then use the kubeconfig from <span className="mono">kind get kubeconfig --internal</span>.</Note>
                </div>
              )}
              {t && t !== "busy" && !t.ok && t.hint && <div className="sub" style={{ flexBasis: "100%" }}>{t.hint}</div>}
            </div>
          );
        })}
        <p className="sub" style={{ margin: 0, padding: "10px 16px" }}>
          A change is a command in a pod, deleting a pod, and restarting or scaling a deployment. Logs, describe and events are always allowed on a cluster that is on. “Change without asking” is only offered for a cluster on this computer.
        </p>
      </Panel>

      <Panel title="2 · What agents may do (MCP)" body={false}>
        {TOOLS.map(([name, what, change]) => (
          <div className="kc-row" key={name}>
            <span className="mono" style={{ fontWeight: 600, flex: "0 0 180px" }}>{name}</span>
            <span className="sub" style={{ flex: "1 1 260px" }}>{what}</span>
            <Pill tone={change ? "warn" : "ok"}>{change ? "follows the cluster's rule" : "always"}</Pill>
          </div>
        ))}
        <p className="sub" style={{ margin: 0, padding: "10px 16px" }}>
          Agents get these tools in projects that have the Cluster plugin on. On “change after you approve” an agent's change waits for your OK in the Inbox, with the exact command.
          What an agent reads (pod names, logs, events) goes to your model company, so do not turn on a cluster whose logs may not leave.
        </p>
      </Panel>

      {problem && <Note tone="err" role="alert">{problem}</Note>}
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button className="btn primary" type="button" onClick={() => void save()} disabled={busy || !dirty}>{busy ? "Saving…" : "Save"}</button>
        {!dirty && <span className="sub">Nothing to save.</span>}
      </div>
    </div>
  );
}
