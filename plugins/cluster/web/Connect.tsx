// Cluster › Connect: the contexts of the kubeconfig on this computer, each one on or off, and what keel may do there (only look,
// change after you approve, change without asking: only for a cluster on this computer). A test reaches the cluster with kubectl.
// keel copies no token: kubectl reads the kubeconfig itself. A GKE context can sign in with a Google service account instead of
// gcloud (the Google Cloud panel: the Vertex AI connection's account, a key for clusters only, or off).

import { useEffect, useState } from "react";
import { errorParts, Note, Panel, Pill, Switch, useApp } from "@keel/web-sdk";
import { ACCESS_WORDS, clusterApi, isGke, loopback, type Access, type Contexts, type GoogleSource, type GoogleView, type TestResult } from "./clusterApi";

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
  const [google, setGoogle] = useState<GoogleView | undefined>(c.google);
  useEffect(() => setDraft(draftOf(c)), [c]);
  useEffect(() => setGoogle(c.google), [c.google]);
  const anyGke = c.contexts.some((x) => isGke(x.auth));
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
                {isGke(x.auth) && <Pill tone={x.auth === "gke" && google?.email ? "ok" : "warn"} title={x.auth === "gke" && google?.email ? `keel signs in as ${google.email}` : undefined}>GKE</Pill>}
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
                ? <Pill tone="ok">{[t.version, t.nodes != null ? `${t.nodes} node${t.nodes === 1 ? "" : "s"}` : null, `${t.ms} ms`, t.as ? `as ${t.as}` : null].filter(Boolean).join(" · ")}</Pill>
                : <Pill tone="bad" title={t.hint ?? undefined}>not reached{t.error ? `: ${t.error}` : ""}</Pill>)}
              {c.in_docker && loopback(x.server) && (
                <div style={{ flexBasis: "100%" }}>
                  <Note tone="warn">This cluster listens on <span className="mono">127.0.0.1</span> of your computer, and keel runs in Docker, so keel cannot reach it.
                    For kind: <span className="mono">docker network connect kind &lt;keel's container&gt;</span>, then use the kubeconfig from <span className="mono">kind get kubeconfig --internal</span>.</Note>
                </div>
              )}
              {t && t !== "busy" && !t.ok && t.hint && <div className="sub" style={{ flexBasis: "100%" }}>{t.hint}</div>}
              {t && t !== "busy" && t.ok && t.note && <div className="sub" style={{ flexBasis: "100%" }}>{t.note}</div>}
            </div>
          );
        })}
        <p className="sub" style={{ margin: 0, padding: "10px 16px" }}>
          A change is a command in a pod, deleting a pod, and restarting or scaling a deployment. Logs, describe and events are always allowed on a cluster that is on. “Change without asking” is only offered for a cluster on this computer.
        </p>
      </Panel>

      <GoogleSignIn view={google} anyGke={anyGke} onSaved={setGoogle} />

      <Panel title="3 · What agents may do (MCP)" body={false}>
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

const SOURCES: [GoogleSource, string][] = [
  ["vertex", "The service account of Connections › Vertex AI"],
  ["own", "A key for clusters only"],
  ["off", "Off: the kubeconfig's own sign-in"],
];

/** How GKE contexts sign in. keel makes a short-lived token from the key for each kubectl run; the key never reaches the page. */
function GoogleSignIn({ view, anyGke, onSaved }: { view?: GoogleView; anyGke: boolean; onSaved: (v: GoogleView) => void }) {
  const { toast } = useApp();
  const [source, setSource] = useState<GoogleSource>(view?.source ?? "vertex");
  const [json, setJson] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => setSource(view?.source ?? "vertex"), [view?.source]);
  if (!view) return null;
  const dirty = source !== view.source || json.trim() !== "";
  const save = async (clear = false) => {
    setBusy(true);
    setProblem(null);
    try {
      const v = await clusterApi.google({ source, ...(json.trim() ? { credentials_json: json.trim() } : {}), ...(clear ? { clear_credentials: true } : {}) });
      setJson("");
      onSaved(v);
      toast(clear ? "Key removed." : "Saved.");
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };
  const account = (k: GoogleSource) =>
    k === "vertex" ? view.vertex_email : k === "own" ? view.own_email : null;

  return (
    <Panel title="2 · Google Cloud (GKE)" body={false}>
      <div style={{ padding: "12px 16px", display: "grid", gap: 10 }} role="radiogroup" aria-label="How GKE clusters sign in">
        <span className="sub">
          A GKE cluster signs in with Google. keel can do this itself with a Google service account, so gcloud is not needed where keel runs.
          {!anyGke && " None of your contexts is a GKE cluster now."}
        </span>
        {SOURCES.map(([k, words]) => (
          <label key={k} style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
            <input type="radio" name="kc-google" checked={source === k} onChange={() => setSource(k)} />
            <span>{words}</span>
            {k !== "off" && <span className="sub mono" style={{ overflowWrap: "anywhere" }}>{account(k) ?? "no key saved yet"}</span>}
            {k === "off" && <span className="sub">needs gcloud and <span className="mono">gke-gcloud-auth-plugin</span> where keel runs</span>}
          </label>
        ))}
        {source === "own" && (
          <>
            <label htmlFor="kc-google-key" className="sub">Service-account key (JSON)</label>
            <textarea id="kc-google-key" className="kc-in mono" rows={3} spellCheck={false} value={json} onChange={(e) => setJson(e.target.value)}
              placeholder={view.own_key_set ? `saved: ${view.own_email ?? "a service account"} (paste a new key to replace it)` : "paste the key file"} />
          </>
        )}
        {source === "vertex" && !view.vertex_email && (
          <Note tone="warn">Connections › Vertex AI has no service-account key saved, so GKE clusters use the kubeconfig's own sign-in.</Note>
        )}
        <span className="sub">
          The account needs a GKE role in the cluster's Google Cloud project: Kubernetes Engine Viewer to read, Kubernetes Engine Developer to run commands and change things.
          The key is stored encrypted and never shown again.
        </span>
        {problem && <Note tone="err" role="alert">{problem}</Note>}
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button className="btn sm primary" type="button" onClick={() => void save()} disabled={busy || !dirty}>{busy ? "Saving…" : "Save sign-in"}</button>
          {view.own_key_set && source !== "own" && (
            <button className="btn sm" type="button" onClick={() => void save(true)} disabled={busy}>Remove the clusters-only key</button>
          )}
          {view.email && <span className="sub">GKE clusters sign in as <span className="mono">{view.email}</span>.</span>}
        </div>
      </div>
    </Panel>
  );
}
