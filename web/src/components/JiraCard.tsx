// Connections › Jira: one card per project. Cloud (site URL, email, API token) or Server / Data Center (URL, personal
// access token); which tickets to bring in (project key, board, JQL); how often; the status mapping keel moves tickets
// with (found from Jira); the Jira reviewer field and the GitHub reviewers. The token is stored encrypted and never shown.

import { useEffect, useState } from "react";
import { errorParts } from "../api";
import { useApp, useLoad } from "../state";
import {
  STATUS_LABEL, TASK_STATUSES, tasksApi, type JiraDiscovery, type JiraSave, type JiraTest, type JiraView, type TaskStatus,
} from "../tasksApi";
import { agoText } from "./UsageStrip";
import { EmptyState, Section } from "./page";
import { Confirm, ErrorBox, Pill } from "./ui";

/** The Jira status keel uses when the mapping names none (the api's TaskStatus.DEFAULT_JIRA). */
const USUAL: Partial<Record<TaskStatus, string>> = {
  todo: "To Do", in_progress: "In Progress", in_review: "In Review", testing_pp: "Testing in PP", ready_prod: "Ready for Production", done: "Done",
};
const list = (s: string) => s.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);

function Mapping({ pid, view, onSaved }: { pid: string; view: JiraView; onSaved: (v: JiraView) => void }) {
  const { toast } = useApp();
  const [found, setFound] = useState<JiraDiscovery | null>(null);
  const [key, setKey] = useState("");
  const [map, setMap] = useState<Partial<Record<TaskStatus, string>>>(view.settings.status_map ?? {});
  const [field, setField] = useState(view.settings.reviewer_field ?? "");
  const [jiraRev, setJiraRev] = useState(view.settings.jira_reviewers.join(", "));
  const [ghRev, setGhRev] = useState(view.settings.github_reviewers.join(", "));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);

  const discover = async () => {
    setBusy(true);
    setErr(null);
    try {
      const d = await tasksApi.discoverJira(pid, key.trim() || undefined);
      setFound(d);
      // fill the empty rows with the suggestion; what you chose stays
      setMap((m) => ({ ...d.suggested, ...Object.fromEntries(Object.entries(m).filter(([, v]) => v)) }));
      toast(`Found ${d.statuses.length} statuses and ${d.fields.length} reviewer field${d.fields.length === 1 ? "" : "s"}.`);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const status_map = Object.fromEntries(Object.entries(map).filter(([, v]) => v)) as Partial<Record<TaskStatus, string>>;
      onSaved(await tasksApi.saveJira(pid, { status_map, reviewer_field: field, jira_reviewers: list(jiraRev), github_reviewers: list(ghRev) }));
      toast("Mapping and reviewers saved.");
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const names = found?.statuses.map((s) => s.name) ?? [];
  const fields = found?.fields ?? [];
  return (
    <div className="jr-map" data-testid="jira-mapping">
      <div className="row jr-find">
        <input type="text" className="inline-input" aria-label="A ticket key to read its transitions (optional)" placeholder="ABC-1 (optional)" value={key}
          onChange={(e) => setKey(e.target.value)} style={{ width: 150 }} />
        <button className="btn sm" type="button" onClick={discover} disabled={busy}>{busy && !found ? "Asking Jira…" : "Find statuses in Jira"}</button>
        <span className="hint">keel moves the ticket to the status (or transition) named here when the task changes.</span>
      </div>
      {found?.transitions.length ? <p className="hint jr-trans">From {key.trim().toUpperCase()} Jira offers: {found.transitions.map((t) => `${t.name} → ${t.to}`).join(" · ")}</p> : null}
      <div className="table-wrap rt-wrap">
        <table className="rt jr-table" aria-label="Status mapping">
          <thead><tr><th>keel status</th><th>Jira status</th></tr></thead>
          <tbody>
            {TASK_STATUSES.map((s) => {
              const v = map[s] ?? "";
              const opts = [...new Set([...names, ...(v && v !== "-" ? [v] : [])])];
              return (
                <tr key={s}>
                  <td className="rt-main"><b>{STATUS_LABEL[s]}</b> <span className="mono sub">{s}</span></td>
                  <td>
                    <select aria-label={`Jira status for ${STATUS_LABEL[s]}`} value={v} onChange={(e) => setMap((m) => ({ ...m, [s]: e.target.value }))}>
                      <option value="">{USUAL[s] ? `usual: ${USUAL[s]}` : "do not move (not mapped)"}</option>
                      {opts.map((n) => <option key={n} value={n}>{n}</option>)}
                      <option value="-">do not move the ticket</option>
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="jr-revs">
        <div className="field">
          <label htmlFor={`jr-field-${pid}`}>Jira reviewer field</label>
          <select id={`jr-field-${pid}`} value={field} onChange={(e) => setField(e.target.value)}>
            <option value="">none</option>
            {[...new Set([...fields.map((f) => f.id), ...(field ? [field] : [])])].map((id) => {
              const f = fields.find((x) => x.id === id);
              return <option key={id} value={id}>{f ? `${f.name} (${id})` : id}</option>;
            })}
          </select>
          <span className="hint">A user picker in your Jira; keel fills it when the PR opens. {fields.length ? "" : "Find statuses to list the fields."}</span>
        </div>
        <div className="field">
          <label htmlFor={`jr-jrev-${pid}`}>Jira reviewers</label>
          <input type="text" id={`jr-jrev-${pid}`} value={jiraRev} onChange={(e) => setJiraRev(e.target.value)}
            placeholder={view.settings.kind === "cloud" ? "email or account id" : "user names"} />
        </div>
        <div className="field">
          <label htmlFor={`jr-ghrev-${pid}`}>GitHub reviewers</label>
          <input type="text" id={`jr-ghrev-${pid}`} value={ghRev} onChange={(e) => setGhRev(e.target.value)} placeholder="octocat, org/team" />
          <span className="hint">Asked on the PR (needs a GitHub token in Connections). A task can name its own.</span>
        </div>
      </div>
      {err && <ErrorBox error={err} />}
      <div className="row"><button className="btn sm primary" type="button" onClick={save} disabled={busy}>Save mapping and reviewers</button></div>
    </div>
  );
}

function JiraForm({ pid, view, onSaved, onRemoved }: { pid: string; view: JiraView; onSaved: (v: JiraView) => void; onRemoved: () => void }) {
  const { toast } = useApp();
  const s = view.settings;
  const [kind, setKind] = useState<"cloud" | "server">(s.kind ?? "cloud");
  const [url, setUrl] = useState(s.base_url ?? "");
  const [email, setEmail] = useState(s.email ?? "");
  const [token, setToken] = useState("");
  const [projectKey, setProjectKey] = useState(s.project_key ?? "");
  const [board, setBoard] = useState(s.board_id ?? "");
  const [jql, setJql] = useState(s.jql ?? "");
  const [poll, setPoll] = useState(String(s.poll_minutes ?? 5));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [test, setTest] = useState<JiraTest | null>(null);
  const [removing, setRemoving] = useState(false);
  const id = (x: string) => `jr-${x}-${pid}`;

  const body = (): JiraSave => ({
    kind, base_url: url.trim(), email: kind === "cloud" ? email.trim() : "", project_key: projectKey.trim(), board_id: board.trim(), jql: jql.trim(),
    poll_minutes: Number(poll) || 0, ...(token.trim() ? { token: token.trim() } : {}),
  });
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const v = await tasksApi.saveJira(pid, body());
      setToken("");
      onSaved(v);
      toast("Jira connection saved. The token is encrypted.");
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const runTest = async () => {
    setBusy(true);
    setTest(null);
    try {
      setTest(await tasksApi.testJira(pid, body()));
    } catch (e) {
      const p = errorParts(e);
      setTest({ ok: false, error: p.message, hint: p.hint });
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    try {
      await tasksApi.deleteJira(pid);
      toast("Jira connection removed. Tasks stay, with their history.");
      onRemoved();
    } catch (e) {
      setErr(errorParts(e));
    }
  };

  return (
    <div className="jr-form">
      <div className="tabs" role="tablist" aria-label="Jira kind">
        <button role="tab" type="button" aria-selected={kind === "cloud"} onClick={() => setKind("cloud")}>Jira Cloud</button>
        <button role="tab" type="button" aria-selected={kind === "server"} onClick={() => setKind("server")}>Server / Data Center</button>
      </div>
      <div className="jr-grid">
        <div className="field">
          <label htmlFor={id("url")}>{kind === "cloud" ? "Site URL" : "Jira URL"}</label>
          <input type="text" id={id("url")} value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder={kind === "cloud" ? "https://your-site.atlassian.net" : "https://jira.example.com"} />
        </div>
        {kind === "cloud" && (
          <div className="field">
            <label htmlFor={id("email")}>Email</label>
            <input type="text" id={id("email")} value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" autoComplete="off" />
          </div>
        )}
        <div className="field">
          <label htmlFor={id("token")}>{kind === "cloud" ? "API token" : "Personal access token"}</label>
          <input type="password" id={id("token")} value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off"
            placeholder={view.token_set ? `${view.token_hint ?? "••••"} (saved)` : "paste the token"} />
          <span className="hint">{kind === "cloud" ? "id.atlassian.com › Security › API tokens." : "Jira › Profile › Personal Access Tokens."} Stored encrypted; only the last characters are shown.</span>
        </div>
        <div className="field">
          <label htmlFor={id("key")}>Project key</label>
          <input type="text" id={id("key")} value={projectKey} onChange={(e) => setProjectKey(e.target.value)} placeholder="ABC" />
        </div>
        <div className="field">
          <label htmlFor={id("board")}>Board id (optional)</label>
          <input type="text" id={id("board")} value={board} onChange={(e) => setBoard(e.target.value)} placeholder="from …/boards/7" />
        </div>
        <div className="field">
          <label htmlFor={id("poll")}>Sync every (minutes)</label>
          <input type="text" id={id("poll")} inputMode="numeric" value={poll} onChange={(e) => setPoll(e.target.value)} />
          <span className="hint">0 = only when you press Sync now.</span>
        </div>
      </div>
      <div className="field">
        <label htmlFor={id("jql")}>JQL (optional)</label>
        <input type="text" id={id("jql")} value={jql} onChange={(e) => setJql(e.target.value)} placeholder={view.default_jql} className="mono" />
        <span className="hint">Empty: your open tickets{projectKey.trim() && !board.trim() ? ` in ${projectKey.trim().toUpperCase()}` : ""}{board.trim() ? " on the board" : ""}. Now: <span className="mono">{view.jql}</span></span>
      </div>
      {test && (
        <p className={test.ok ? "okc" : "badc"} role="status">
          {test.ok ? `Test OK: logged in as ${test.user?.name ?? "?"}.` : `Test failed: ${test.error ?? "no answer"}`}
          {!test.ok && test.hint && <span className="sub"> {test.hint}</span>}
        </p>
      )}
      {err && <ErrorBox error={err} />}
      <div className="row">
        <button className="btn sm" type="button" onClick={runTest} disabled={busy || !url.trim()}>Test</button>
        <button className="btn sm primary" type="button" onClick={save} disabled={busy || !url.trim()}>{busy ? "Working…" : view.connected ? "Save" : "Connect"}</button>
        {view.connected && <button className="btn sm ghost" type="button" onClick={() => setRemoving(true)}>Remove</button>}
      </div>
      {removing && <Confirm text="Remove this Jira connection and its token? Tasks stay in keel." yes="Remove" onYes={() => void remove()} onNo={() => setRemoving(false)} />}
    </div>
  );
}

function JiraCard({ pid, name, current }: { pid: string; name: string; current: boolean }) {
  const r = useLoad(`jira:${pid}`, () => tasksApi.jira(pid));
  const v = r.data;
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (v && current && !v.connected) setOpen(true);
  }, [v?.connected, current]);
  const head = `jira-${pid}`;
  return (
    <article className="panel cn-card jr-card" aria-labelledby={head} data-testid={`jira-${pid}`}>
      <header className="cn-head">
        <div className="cn-id">
          <h2 id={head}>{name}</h2>
          <span className="sub">{v?.connected ? `${v.settings.kind === "server" ? "Jira Server" : "Jira Cloud"} · ${v.settings.base_url}` : "Jira is not connected: tasks stay local."}</span>
        </div>
        <div className="cn-state">
          {!v ? <span className="sub">Checking…</span> : v.connected ? <Pill tone="ok">connected</Pill> : <Pill tone="idle">not connected</Pill>}
          {v?.connected && <span className="hint">{v.last_sync_at ? `synced ${agoText(v.last_sync_at)}` : "not synced yet"}{v.me ? ` · as ${v.me.name}` : ""}</span>}
          {v?.last_sync_error && <span className="hint badc">{v.last_sync_error}</span>}
        </div>
        <div className="cn-actions">
          <button className={`btn sm${v && !v.connected ? " primary" : ""}`} type="button" aria-expanded={open} aria-controls={`${head}-body`} onClick={() => setOpen((o) => !o)}>
            {open ? "Close" : v?.connected ? "Edit" : "Set up Jira"}
          </button>
        </div>
      </header>
      {r.error && <div className="cn-body"><ErrorBox error={r.error} onRetry={() => void r.reload()} /></div>}
      {open && v && (
        <div className="cn-body jr-body" id={`${head}-body`}>
          <JiraForm pid={pid} view={v} onSaved={(n) => r.setData(n)} onRemoved={() => void r.reload()} />
          {v.connected && (
            <section className="jr-sec" aria-label="Statuses and reviewers">
              <h3>Statuses and reviewers</h3>
              <Mapping key={v.settings.base_url} pid={pid} view={v} onSaved={(n) => r.setData(n)} />
              <p className="hint">Agents can read tickets themselves with the optional Jira MCP server (mcp-atlassian): Tools (MCP) › Catalog{v.mcp_server ? ` — added as ${v.mcp_server}` : ""}.</p>
            </section>
          )}
        </div>
      )}
    </article>
  );
}

/** The Jira section of Connections: the current project's card first. */
export function JiraSection() {
  const { projects, pid } = useApp();
  const ordered = [...projects].sort((a, b) => (a.id === pid ? -1 : b.id === pid ? 1 : 0));
  return (
    <Section title="Jira" sub="Per project: where keel reads your tickets, the statuses it moves them to, and who reviews. Without it, tasks stay in keel.">
      <div className="cn-list">
        {ordered.length ? ordered.map((p) => <JiraCard key={p.id} pid={p.id} name={p.name} current={p.id === pid} />)
          : <div className="panel"><EmptyState title="No project yet">Add a repo on All projects, then connect its Jira here.</EmptyState></div>}
      </div>
    </Section>
  );
}
