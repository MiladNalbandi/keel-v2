// Cluster › Connect: the contexts of the kubeconfig on this computer, each one on or off, and what keel may do there (only look,
// change after you approve, change without asking: only for a cluster on this computer). A test reaches the cluster with kubectl.
// keel copies no token: kubectl reads the kubeconfig itself. A GKE context signs in with Google (the Google Cloud panel): the person's
// own Google account (gcloud's login, signed in here), the Vertex AI connection's service account, a key for clusters only, or off.
// Signed in with gcloud, the person can add GKE clusters to keel's own kubeconfig.

import { useEffect, useState } from "react";
import { errorParts, Note, Panel, Pill, Switch, useApp } from "@keel/web-sdk";
import {
  ACCESS_WORDS, clusterApi, isGke, loopback, type Access, type Contexts, type Gcloud, type GkeCluster, type GkeProject, type GoogleSource,
  type GoogleView, type TestResult,
} from "./clusterApi";

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
  const [gcloud, setGcloud] = useState<Gcloud | null>(null);
  useEffect(() => setDraft(draftOf(c)), [c]);
  useEffect(() => setGoogle(c.google), [c.google]);
  const loadGcloud = async () => {
    try {
      setGcloud(await clusterApi.gcloud());
    } catch {
      setGcloud(null);
    }
  };
  useEffect(() => {
    if (google?.source === "gcloud") void loadGcloud();
  }, [google?.source]);
  const signedAs = google?.source === "gcloud" ? gcloud?.account ?? null : google?.email ?? null;
  const reload = async () => onSaved(await clusterApi.contexts());
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
                {isGke(x.auth) && <Pill tone={x.auth !== "gcp" && signedAs ? "ok" : "warn"} title={x.auth !== "gcp" && signedAs ? `signs in as ${signedAs}` : undefined}>GKE</Pill>}
                {x.added && <Pill tone="idle" title="Added in keel (Google Cloud › Add a GKE cluster): it is in keel's own kubeconfig">added in keel</Pill>}
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

      <GoogleSignIn view={google} anyGke={anyGke} onSaved={setGoogle} gcloud={gcloud} loadGcloud={loadGcloud} setGcloud={setGcloud} onAdded={reload} />

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
  ["gcloud", "Your Google account (gcloud login)"],
  ["vertex", "The service account of Connections › Vertex AI"],
  ["own", "A key for clusters only"],
  ["off", "Off: the kubeconfig's own sign-in"],
];

/** How GKE contexts sign in. keel makes a short-lived token from the key for each kubectl run; the key never reaches the page. */
function GoogleSignIn({ view, anyGke, onSaved, gcloud, loadGcloud, setGcloud, onAdded }: {
  view?: GoogleView; anyGke: boolean; onSaved: (v: GoogleView) => void;
  gcloud: Gcloud | null; loadGcloud: () => Promise<void>; setGcloud: (g: Gcloud) => void; onAdded: () => Promise<void>;
}) {
  const { toast } = useApp();
  const [source, setSource] = useState<GoogleSource>(view?.source ?? "vertex");
  const [json, setJson] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => setSource(view?.source ?? "vertex"), [view?.source]);
  useEffect(() => {
    if (source === "gcloud" && !gcloud) void loadGcloud();
  }, [source]); // eslint-disable-line react-hooks/exhaustive-deps
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
    k === "vertex" ? view.vertex_email : k === "own" ? view.own_email : k === "gcloud" ? gcloud?.account ?? null : null;

  return (
    <Panel title="2 · Google Cloud (GKE)" body={false}>
      <div style={{ padding: "12px 16px", display: "grid", gap: 10 }} role="radiogroup" aria-label="How GKE clusters sign in">
        <span className="sub">
          A GKE cluster signs in with Google: with your own Google account (gcloud), or with a service account keel signs in with itself.
          {!anyGke && " None of your contexts is a GKE cluster yet."}
        </span>
        {SOURCES.map(([k, words]) => (
          <label key={k} style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
            <input type="radio" name="kc-google" checked={source === k} onChange={() => setSource(k)} />
            <span>{words}</span>
            {k !== "off" && <span className="sub mono" style={{ overflowWrap: "anywhere" }}>{account(k) ?? (k === "gcloud" ? "not signed in" : "no key saved yet")}</span>}
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
        {source === "gcloud" && <GcloudLogin gcloud={gcloud} setGcloud={setGcloud} reload={loadGcloud} onAdded={onAdded} />}
        {source === "vertex" && !view.vertex_email && (
          <Note tone="warn">Connections › Vertex AI has no service-account key saved, so GKE clusters use the kubeconfig's own sign-in.</Note>
        )}
        <span className="sub">
          The account needs a GKE role in the cluster's Google Cloud project: Kubernetes Engine Viewer to read, Kubernetes Engine Developer to run commands and change things.
          {source === "own" && " The key is stored encrypted and never shown again."}
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

/** Sign in with Google (gcloud auth login without a browser where keel runs): keel shows Google's link, the person signs in there and
 *  pastes the code Google shows. Then: who is signed in, sign out (keel's own login only), and adding a GKE cluster. */
function GcloudLogin({ gcloud, setGcloud, reload, onAdded }: {
  gcloud: Gcloud | null; setGcloud: (g: Gcloud) => void; reload: () => Promise<void>; onAdded: () => Promise<void>;
}) {
  const { toast } = useApp();
  const [login, setLogin] = useState<{ id: string; url: string } | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const act = async (what: string, f: () => Promise<void>) => {
    setBusy(what);
    setProblem(null);
    try {
      await f();
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(null);
    }
  };
  if (!gcloud) return <span className="sub" role="status">Looking for gcloud where keel runs…</span>;
  if (!gcloud.installed) {
    return (
      <Note tone="warn">
        <b>gcloud is not installed where keel runs.</b> Install the Google Cloud CLI and its <span className="mono">gke-gcloud-auth-plugin</span> on this computer
        (<span className="mono">gcloud components install gke-gcloud-auth-plugin</span>), or use keel's Docker image, which has both.
      </Note>
    );
  }
  const start = () => act("start", async () => {
    setCode("");
    setLogin(await clusterApi.gcloudLogin());
  });
  const finish = () => act("finish", async () => {
    if (!login) return;
    const g = await clusterApi.gcloudCode(login.id, code.trim());
    setLogin(null);
    setCode("");
    setGcloud(g);
    toast(g.account ? `Signed in as ${g.account}.` : "Signed in.");
  });
  const cancel = () => act("cancel", async () => {
    if (login) await clusterApi.gcloudCancel(login.id);
    setLogin(null);
    setCode("");
  });
  const signOut = () => act("out", async () => {
    setGcloud(await clusterApi.gcloudLogout());
    toast("Signed out of Google in keel.");
  });

  return (
    <div className="kc-gcloud" style={{ display: "grid", gap: 10, padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 8 }}>
      <span className="sub">
        gcloud {gcloud.version ?? ""}{gcloud.plugin ? " · gke-gcloud-auth-plugin" : ""} ·{" "}
        {gcloud.owned ? "keel's own Google login (kept in keel's data)" : "the gcloud login of this computer"}
      </span>
      {!gcloud.plugin && <Note tone="warn">gke-gcloud-auth-plugin is missing, so kubectl cannot sign in to GKE: <span className="mono">gcloud components install gke-gcloud-auth-plugin</span>.</Note>}
      {gcloud.account ? (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Pill tone="ok">signed in</Pill>
          <span className="mono" style={{ overflowWrap: "anywhere" }}>{gcloud.account}</span>
          {gcloud.seeded && <span className="sub">copied from this computer's gcloud login</span>}
          {gcloud.owned && <button className="btn sm" type="button" onClick={() => void signOut()} disabled={!!busy}>{busy === "out" ? "Signing out…" : "Sign out"}</button>}
          {!gcloud.owned && <span className="sub">Sign out on this computer: <span className="mono">gcloud auth revoke</span></span>}
        </div>
      ) : !login ? (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button className="btn sm primary" type="button" onClick={() => void start()} disabled={!!busy}>{busy === "start" ? "Starting…" : "Sign in with Google"}</button>
          <span className="sub">{gcloud.owned ? "keel keeps this login in its own data; it does not change your computer's gcloud." : "This signs in gcloud on this computer, as gcloud auth login does."}</span>
        </div>
      ) : (
        <div style={{ display: "grid", gap: 8 }}>
          <span><b>1.</b> Open Google's page and sign in: <a href={login.url} target="_blank" rel="noreferrer noopener">Sign in with Google ↗</a></span>
          <label htmlFor="kc-gcloud-code"><b>2.</b> Paste the code Google shows at the end</label>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input id="kc-gcloud-code" className="kc-in mono" style={{ flex: "1 1 260px", minWidth: 0 }} value={code} autoComplete="off" spellCheck={false}
              onChange={(e) => setCode(e.target.value)} placeholder="4/0A…" onKeyDown={(e) => { if (e.key === "Enter" && code.trim()) void finish(); }} />
            <button className="btn sm primary" type="button" onClick={() => void finish()} disabled={!!busy || !code.trim()}>{busy === "finish" ? "Signing in…" : "Finish sign-in"}</button>
            <button className="btn sm" type="button" onClick={() => void cancel()} disabled={!!busy}>Cancel</button>
          </div>
        </div>
      )}
      {gcloud.error && !gcloud.account && <span className="sub">{gcloud.error}</span>}
      {problem && <Note tone="err" role="alert">{problem}</Note>}
      {gcloud.account && <AddGke onAdded={onAdded} />}
      <button className="btn sm" type="button" style={{ justifySelf: "start" }} onClick={() => void reload()} disabled={!!busy}>Check again</button>
    </div>
  );
}

/** Add a GKE cluster: the projects the account sees, their clusters, and get-credentials into keel's own kubeconfig. */
function AddGke({ onAdded }: { onAdded: () => Promise<void> }) {
  const { toast } = useApp();
  const [projects, setProjects] = useState<GkeProject[] | null>(null);
  const [project, setProject] = useState("");
  const [clusters, setClusters] = useState<GkeCluster[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const act = async (what: string, f: () => Promise<void>) => {
    setBusy(what);
    setProblem(null);
    try {
      await f();
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(null);
    }
  };
  const pick = (id: string) => {
    setProject(id);
    setClusters(null);
    if (id) void act("clusters", async () => setClusters((await clusterApi.gkeClusters(id)).items));
  };
  const add = (c: GkeCluster) => act(c.context, async () => {
    const r = await clusterApi.gkeAdd({ project, location: c.location, name: c.name });
    setClusters((cs) => (cs ?? []).map((x) => (x.context === r.context ? { ...x, added: true } : x)));
    await onAdded();
    toast(`Added ${c.name}. Test it and turn it on above.`);
  });

  return (
    <div style={{ display: "grid", gap: 8 }}>
      <b>Add a GKE cluster</b>
      {projects === null ? (
        <button className="btn sm" type="button" style={{ justifySelf: "start" }} disabled={!!busy}
          onClick={() => void act("projects", async () => setProjects((await clusterApi.gkeProjects()).items))}>
          {busy === "projects" ? "Loading projects…" : "Show my Google Cloud projects"}
        </button>
      ) : (
        <label className="sub" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>Project
          <select className="kc-in" aria-label="Google Cloud project" value={project} onChange={(e) => pick(e.target.value)} style={{ color: "var(--fg)", maxWidth: "100%" }}>
            <option value="">{projects.length ? "choose a project" : "no project for this account"}</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name === p.id ? p.id : `${p.name} (${p.id})`}</option>)}
          </select>
        </label>
      )}
      {busy === "clusters" && <span className="sub" role="status">Loading clusters…</span>}
      {clusters && !clusters.length && <span className="sub">This project has no GKE cluster.</span>}
      {clusters?.map((c) => (
        <div className="kc-row" key={c.context} role="group" aria-label={c.name}>
          <span style={{ flex: "1 1 220px", minWidth: 0, display: "grid" }}>
            <b className="mono">{c.name}</b>
            <span className="sub">{[c.location, c.status, c.version, c.nodes != null ? `${c.nodes} nodes` : null].filter(Boolean).join(" · ")}</span>
          </span>
          {c.added ? <Pill tone="ok">in keel</Pill> : (
            <button className="btn sm" type="button" onClick={() => void add(c)} disabled={!!busy}>{busy === c.context ? "Adding…" : "Add"}</button>
          )}
        </div>
      ))}
      {problem && <Note tone="err" role="alert">{problem}</Note>}
      <span className="sub">keel writes it to its own kubeconfig with <span className="mono">gcloud container clusters get-credentials</span>; your own kubeconfig is not changed.</span>
    </div>
  );
}
