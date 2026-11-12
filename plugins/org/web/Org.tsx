// The Org page (#/org, #/org/<org>, #/org/<org>~<mission>): your org of agents as a chart, the missions you gave it
// and each mission's tree, report and history, and the org's YAML. Nothing is hard-coded: the chart is drawn from the
// roles and links the YAML names. It imports only react and @keel/web-sdk.

import { useState, type ReactNode } from "react";
import {
  EmptyState, ErrorBox, errorParts, get, go, hashForScreen, kfmt, Loading, Note, PageHead, Panel, Pill, post, put,
  Since, Tabs, useApp, useLoad, useNarrow, useRoute, type PillTone,
} from "@keel/web-sdk";
import { AUTONOMY, Chart } from "./Chart";
import { TemplateCards, templatesApi, type OrgTemplate } from "./Templates";

export type Role = { id: string; title: string; human: boolean; agent: string | null; workflows: string[]; plan: string | null;
  review: string | null; autonomy: "watch" | "propose" | "act"; team: string[]; boss: string | null };
export type Link = { from: string[]; to: string[]; can: string[] };
export type OrgView = { id: string; name: string; yaml: string; updated_at: string; problems: string[]; roles: Role[]; links: Link[];
  limits?: Record<string, number>; projects?: string[] };
export type Report = { summary?: string; done?: string[]; decisions?: string[]; follow_ups?: string[]; risks?: string[];
  children?: { role: string; title?: string; ok?: boolean; note?: string }[]; flow?: string };
export type Mission = { id: string; org_id: string; role: string; parent_id: string | null; project_id: string; title: string;
  request: string; source: string | null; workflow: string | null; stage: "plan" | "work" | "review"; status: string;
  thread_id: string | null; cap_tokens: number | null; used_tokens: number; send_backs: number; depth: number;
  report: Report | null; decision: { choice: string; why: string } | null; note: string | null; created_at: string; updated_at: string };
export type MissionTree = Mission & { children: MissionTree[]; running_tokens?: number; waiting?: { step: string; title: string; person: boolean } | null };
export type Rollup = { tokens: number; counts: Record<string, number> };
export type MissionRow = Mission & { rollup: Rollup; children_count: number;
  waiting: { role: string; thread_id: string | null; step: string; title: string; person: boolean } | null };
export type MissionView = { mission: MissionTree; rollup: Rollup; flows: { thread_id: string; stage: string; at: string }[];
  history: { at: string; kind: string; text: string; data: Record<string, unknown> | null }[] };

const enc = encodeURIComponent;
export const orgApi = {
  orgs: () => get<OrgView[]>("/org/orgs"),
  org: (id: string) => get<OrgView>(`/org/orgs/${enc(id)}`),
  save: (id: string, yaml: string) => put<OrgView>(`/org/orgs/${enc(id)}`, { yaml }),
  check: (yaml: string) => post<{ ok: boolean; problems: string[]; roles: string[] }>("/org/check", { yaml }),
  example: () => get<{ yaml: string }>("/org/example"),
  missions: (id: string) => get<MissionRow[]>(`/org/orgs/${enc(id)}/missions`),
  assign: (id: string, b: Record<string, unknown>) => post<Mission>(`/org/orgs/${enc(id)}/missions`, b),
  mission: (mid: string) => get<MissionView>(`/org/missions/${enc(mid)}`),
  act: (mid: string, what: "start" | "stop" | "accept", why?: string) => post<Mission>(`/org/missions/${enc(mid)}/${what}`, why ? { why } : {}),
};

const TONE: Record<string, PillTone> = { proposed: "warn", queued: "idle", running: "run", delegated: "run", reported: "warn",
  done: "ok", failed: "bad", stopped: "idle" };
const SAYS: Record<string, string> = { proposed: "waits for you to start", queued: "in the queue", running: "working",
  delegated: "its team works", reported: "report ready", done: "done", failed: "failed", stopped: "stopped" };
const STAGE: Record<string, string> = { plan: "plans", work: "works", review: "reviews" };

export const StatusPill = ({ s }: { s: string }) => <Pill tone={TONE[s] ?? "idle"}>{SAYS[s] ?? s}</Pill>;

/** route arg: "<org>", "<org>~<mission>", or "new~<template>" */
function parseArg(arg?: string): { org?: string; mission?: string } {
  if (!arg) return {};
  const [org, mission] = arg.split("~");
  return { org: org || undefined, mission: mission || undefined };
}

export function OrgPage() {
  const { arg } = useRoute();
  const { org: oid, mission } = parseArg(arg);
  const orgs = useLoad("org:list", orgApi.orgs, { live: false });
  if (orgs.error) return <><PageHead title="Org" /><ErrorBox error={orgs.error} onRetry={() => void orgs.reload()} /></>;
  if (!orgs.data) return <><PageHead title="Org" /><Loading what="Loading your orgs" /></>;
  const list = orgs.data;
  const current = list.find((o) => o.id === oid) ?? list[0];
  // #/org/new~<template>: New org with a ready-made org filled in (Marketplace › Orgs › Use this org)
  if (!current || oid === "new")
    return <NewOrg first={!list.length} template={oid === "new" ? mission : undefined} taken={list.map((o) => o.id)}
      onSaved={(id) => { void orgs.reload(); go("org", id); }} />;
  if (mission) return <MissionPage org={current} mid={mission} />;
  return <OrgHome org={current} orgs={list} onSaved={() => void orgs.reload()} />;
}

function NewOrg({ first, template, taken, onSaved }: { first: boolean; template?: string; taken: string[]; onSaved: (id: string) => void }) {
  const tpls = useLoad("org:templates", templatesApi.list, { live: false });
  const [picked, setPicked] = useState<string | null>(null);
  const [id, setId] = useState<string | null>(null);
  const [yaml, setYaml] = useState<string | null>(null);
  const items = tpls.data ?? [];
  // the template the link names, else the small team (the example); the person's own pick wins
  const tpl: OrgTemplate | undefined = items.find((t) => t.id === (picked ?? template)) ?? items.find((t) => t.id === "small-team") ?? items[0];
  const free = (base: string) => { let n = base, i = 2; while (taken.includes(n)) n = `${base}-${i++}`; return n; };
  const orgId = id ?? (tpl ? free(tpl.id) : "my-team");
  const text = yaml ?? tpl?.yaml ?? "";
  const pick = (t: OrgTemplate) => { setPicked(t.id); setYaml(null); };
  return (
    <>
      <PageHead title="New org" sub="Agents in roles you draw. Give a mission to any role: it plans, gives work to its team, checks it and reports to you." />
      {first && <Note tone="info">You have no org yet. Start from a ready-made one, change any line, then save.</Note>}
      <Panel title={<h2>1. Start from a ready-made org</h2>}>
        {tpls.error ? <ErrorBox error={tpls.error} onRetry={() => void tpls.reload()} />
          : !tpls.data ? <Loading what="Loading the ready-made orgs" />
          : <TemplateCards items={items} picked={tpl?.id} onPick={pick} />}
      </Panel>
      {tpl && yaml === null && (
        <Panel title={<h2>{tpl.title}: its chart</h2>}><Chart roles={tpl.roles} links={tpl.links} compact /></Panel>
      )}
      <Panel title={<h2>2. Change it and save</h2>}>
        <div className="grid" style={{ gap: 10 }}>
          <div className="field" style={{ maxWidth: 320 }}>
            <label htmlFor="orgNewId">Org id (lowercase, digits, - and _)</label>
            <input id="orgNewId" type="text" value={orgId} onChange={(e) => setId(e.target.value.trim())} />
          </div>
          <YamlEditor key={tpl?.id ?? "none"} id={orgId} text={text} onText={setYaml} onSaved={(o) => onSaved(o.id)} />
        </div>
      </Panel>
    </>
  );
}

function OrgHome({ org, orgs, onSaved }: { org: OrgView; orgs: OrgView[]; onSaved: () => void }) {
  const [tab, setTab] = useState<"missions" | "chart" | "yaml">("missions");
  const missions = useLoad(`org:missions:${org.id}`, () => orgApi.missions(org.id), { live: true });
  return (
    <>
      <PageHead title={org.name || org.id}
        sub="Your org of agents. Work goes down the links; reports come back up. You are called only where a role may not decide."
        actions={<div className="row" style={{ gap: 6 }}>
          {orgs.length > 1 && (
            <select aria-label="Org" value={org.id} onChange={(e) => go("org", e.target.value)}>
              {orgs.map((o) => <option key={o.id} value={o.id}>{o.name || o.id}</option>)}
            </select>
          )}
          <a className="btn sm ghost" href={hashForScreen("org", "new")}>New org</a>
        </div>} />
      {org.problems.length > 0 && <Note tone="warn">This org has problems, so it cannot take missions: {org.problems.join("; ")}</Note>}
      <Tabs label="Org" value={tab} onChange={setTab}
        options={[["missions", "Missions"], ["chart", "Chart"], ["yaml", "YAML"]]} />
      {tab === "missions" && (
        <div className="grid org-cols">
          <Panel title={<h2>Missions</h2>} body={false}>
            {missions.error ? <ErrorBox error={missions.error} onRetry={() => void missions.reload()} />
              : !missions.data ? <Loading what="Loading the missions" />
              : !missions.data.length ? <EmptyState compact title="No missions yet">Give the first one to a role with the form.</EmptyState>
              : <MissionList org={org} rows={missions.data} />}
          </Panel>
          <Panel title={<h2>Give a mission</h2>}><Assign org={org} onDone={(m) => go("org", `${org.id}~${m.id}`)} /></Panel>
        </div>
      )}
      {tab === "chart" && <Panel title={<h2>Chart</h2>}><Chart roles={org.roles} links={org.links} /></Panel>}
      {tab === "yaml" && (
        <Panel title={<h2>The org's YAML</h2>}>
          <YamlEditor id={org.id} text={org.yaml} onSaved={onSaved} />
        </Panel>
      )}
    </>
  );
}

function MissionList({ org, rows }: { org: OrgView; rows: MissionRow[] }) {
  const title = (r: string) => org.roles.find((x) => x.id === r)?.title ?? r;
  const narrow = useNarrow(720);
  const status = (m: MissionRow) => (
    <>{m.waiting ? <Pill tone="warn">waits for you</Pill> : <StatusPill s={m.status} />}
      {m.waiting && <div className="sub">{roleTitle(org, m.waiting.role)}: {m.waiting.title}</div>}</>
  );
  if (narrow) {
    return (
      <ul className="org-cards">
        {rows.map((m) => (
          <li key={m.id}>
            <a href={hashForScreen("org", `${org.id}~${m.id}`)}><b>{m.title}</b></a>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>{status(m)}</div>
            <span className="sub">{title(m.role)}{m.source ? ` · ${m.source}` : ""} · {kfmt(m.rollup.tokens)} tokens · <Since from={m.created_at} /></span>
          </li>
        ))}
      </ul>
    );
  }
  return (
    <table className="tbl org-table">
      <thead><tr><th>Mission</th><th>To</th><th>Status</th><th>Team</th><th className="num">Tokens</th><th>Given</th></tr></thead>
      <tbody>
        {rows.map((m) => (
          <tr key={m.id}>
            <td><a href={hashForScreen("org", `${org.id}~${m.id}`)}>{m.title}</a>{m.source && <span className="sub mono"> · {m.source}</span>}</td>
            <td>{title(m.role)}</td>
            <td>{status(m)}</td>
            <td className="sub">{Object.entries(m.rollup.counts).map(([s, n]) => `${n} ${SAYS[s] ?? s}`).join(" · ") || "—"}</td>
            <td className="num">{kfmt(m.rollup.tokens)}</td>
            <td className="sub"><Since from={m.created_at} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Assign({ org, onDone }: { org: OrgView; onDone: (m: Mission) => void }) {
  const { projects } = useApp();
  const me = org.roles.filter((r) => r.human).map((r) => r.id);
  const mine = org.roles.filter((r) => !r.human && (me.length === 0 || (r.boss !== null && me.includes(r.boss))));
  const shown = projects.filter((p) => !org.projects?.length || org.projects.includes(p.id));
  const [role, setRole] = useState(mine[0]?.id ?? "");
  const [project, setProject] = useState(shown[0]?.id ?? "");
  const [title, setTitle] = useState("");
  const [request, setRequest] = useState("");
  const [source, setSource] = useState("");
  const [cap, setCap] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ReturnType<typeof errorParts> | null>(null);
  const r = org.roles.find((x) => x.id === role);
  if (org.problems.length) return <p className="sub">Fix the org's YAML first.</p>;
  if (!mine.length) return <p className="sub">No role takes work from you. Add a link from your human role with <code>can: [delegate]</code>.</p>;
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const capN = cap.trim() ? Math.round(Number(cap.replace(/k$/i, "")) * (/k$/i.test(cap.trim()) ? 1000 : 1)) : undefined;
      onDone(await orgApi.assign(org.id, { role, project_id: project, title: title.trim(), request: request.trim(),
        source: source.trim() || undefined, cap_tokens: capN && capN > 0 ? capN : undefined }));
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="grid" style={{ gap: 10 }} onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
        <div className="field" style={{ flex: "1 1 160px" }}>
          <label htmlFor="orgRole">To</label>
          <select id="orgRole" value={role} onChange={(e) => setRole(e.target.value)}>
            {mine.map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}
          </select>
        </div>
        <div className="field" style={{ flex: "1 1 160px" }}>
          <label htmlFor="orgProject">Project</label>
          <select id="orgProject" value={project} onChange={(e) => setProject(e.target.value)}>
            {shown.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      </div>
      {r && <p className="sub" style={{ margin: 0 }}>{r.title} {AUTONOMY[r.autonomy]}. Team: {r.team.length ? r.team.join(", ") : "none"}.</p>}
      <div className="field">
        <label htmlFor="orgTitle">Title</label>
        <input id="orgTitle" type="text" required maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Show the cart count in the header" />
      </div>
      <div className="field">
        <label htmlFor="orgRequest">What you want, in your words</label>
        <textarea id="orgRequest" className="org-plain" rows={5} value={request} onChange={(e) => setRequest(e.target.value)}
          placeholder="What should be true when it is done, and anything the lead must know." />
      </div>
      <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
        <div className="field" style={{ flex: "1 1 140px" }}>
          <label htmlFor="orgSource">Ticket (optional)</label>
          <input id="orgSource" type="text" value={source} onChange={(e) => setSource(e.target.value)} placeholder="SHOP-142" />
        </div>
        <div className="field" style={{ flex: "1 1 140px" }}>
          <label htmlFor="orgCap">Budget in tokens (optional)</label>
          <input id="orgCap" type="text" value={cap} onChange={(e) => setCap(e.target.value)} placeholder="400k" inputMode="numeric" />
        </div>
      </div>
      {err && <ErrorBox error={err} />}
      <div className="row"><button className="btn primary" type="submit" disabled={busy || !title.trim() || !project}>{busy ? "Giving it…" : "Give the mission"}</button></div>
    </form>
  );
}

function YamlEditor({ id, text, onText, onSaved }: { id: string; text: string; onText?: (t: string) => void; onSaved: (o: OrgView) => void }) {
  const [value, setValue] = useState(text);
  const [problems, setProblems] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<ReturnType<typeof errorParts> | null>(null);
  const shown = onText ? text : value;
  const change = (t: string) => { setSaved(false); setProblems(null); if (onText) onText(t); else setValue(t); };
  const check = async () => setProblems((await orgApi.check(shown)).problems);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const v = await orgApi.save(id, shown);
      setSaved(true);
      setProblems([]);
      onSaved(v);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid" style={{ gap: 8 }}>
      <textarea id="orgYaml" className="mono org-yaml" spellCheck={false} rows={22} value={shown} onChange={(e) => change(e.target.value)}
        aria-label="The org's YAML" />
      <p className="sub" style={{ margin: 0 }}>Roles are boxes; links say what may pass between them: delegate, report, ask, approve, notify. keel checks every hand-over against them. Agents cannot change this.</p>
      {problems && problems.length > 0 && <Note tone="warn"><ul style={{ margin: 0, paddingLeft: 18 }}>{problems.map((p) => <li key={p}>{p}</li>)}</ul></Note>}
      {problems && problems.length === 0 && !saved && <Note tone="ok">No problems.</Note>}
      {err && <ErrorBox error={err} />}
      <div className="row" style={{ gap: 6 }}>
        <button className="btn" type="button" onClick={() => void check()}>Check</button>
        <button className="btn primary" type="button" disabled={busy || !id} onClick={() => void save()}>{busy ? "Saving…" : "Save"}</button>
        {saved && <span className="sub" role="status">Saved.</span>}
      </div>
    </div>
  );
}

function MissionPage({ org, mid }: { org: OrgView; mid: string }) {
  const view = useLoad(`org:mission:${mid}`, () => orgApi.mission(mid), { live: true });
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<ReturnType<typeof errorParts> | null>(null);
  const back = <a className="btn sm ghost" href={hashForScreen("org", org.id)}>All missions</a>;
  if (view.error) return <><PageHead title="Mission" actions={back} /><ErrorBox error={view.error} onRetry={() => void view.reload()} /></>;
  if (!view.data) return <><PageHead title="Mission" actions={back} /><Loading what="Loading the mission" /></>;
  const { mission: m, rollup, history } = view.data;
  const act = async (what: "start" | "stop" | "accept") => {
    setBusy(what);
    setErr(null);
    try { await orgApi.act(m.id, what); await view.reload(); } catch (e) { setErr(errorParts(e)); } finally { setBusy(null); }
  };
  const open = ["proposed", "queued", "running", "delegated"].includes(m.status);
  const waits = waitingFlow(m);
  return (
    <>
      <PageHead title={m.title} sub={<>{roleTitle(org, m.role)} · {m.project_id}{m.source ? <> · <span className="mono">{m.source}</span></> : null} · given <Since from={m.created_at} /></>}
        actions={<div className="row" style={{ gap: 6 }}>
          {back}
          {m.status === "proposed" && <button className="btn sm primary" type="button" disabled={!!busy} onClick={() => void act("start")}>Start</button>}
          {m.status === "reported" && <button className="btn sm primary" type="button" disabled={!!busy} onClick={() => void act("accept")}>Accept the report</button>}
          {open && <button className="btn sm" type="button" disabled={!!busy} onClick={() => void act("stop")}>Stop</button>}
        </div>} />
      {err && <ErrorBox error={err} />}
      {waits && (
        <Note tone="warn">{roleTitle(org, waits.who)} waits at “{waits.title}”.{" "}
          <a className="btn sm" href={hashForScreen("flow", waits.thread)}>Open it and answer</a></Note>
      )}
      <div className="grid org-cols">
        <Panel title={<h2>Work</h2>} extra={<span className={`sub num${m.cap_tokens && rollup.tokens > m.cap_tokens ? " org-over" : ""}`}
          title={m.cap_tokens && rollup.tokens > m.cap_tokens ? "Over the mission's budget: a flow stops at its own cap only after the agent call that crosses it." : undefined}>
          {kfmt(rollup.tokens)} tokens{m.cap_tokens ? ` of ${kfmt(m.cap_tokens)}` : ""}</span>}>
          <Tree org={org} m={m} />
        </Panel>
        <Panel title={<h2>Report</h2>}>
          {m.report ? <ReportView r={m.report} /> : <p className="sub">The report comes when the work has ended{m.status === "delegated" ? " and the lead has checked it" : ""}.</p>}
          {m.decision && <p className="sub">You chose <b>{m.decision.choice === "send_back" ? "send back" : m.decision.choice}</b>{m.decision.why ? `: ${m.decision.why}` : "."}</p>}
        </Panel>
      </div>
      <Panel title={<h2>History</h2>} body={false}>
        <ol className="org-history">
          {history.map((h, i) => (
            <li key={i}><span className="sub mono"><Since from={h.at} /></span> <span>{h.text}</span>
              {typeof h.data?.thread_id === "string" && <> · <a href={hashForScreen("flow", h.data.thread_id)}>flow</a></>}</li>
          ))}
        </ol>
      </Panel>
    </>
  );
}

function roleTitle(org: OrgView, id: string) {
  return org.roles.find((r) => r.id === id)?.title ?? id;
}

/** The first mission in the tree whose flow waits at a gate: a person may be needed there. */
function waitingFlow(m: MissionTree): { who: string; thread: string; title: string } | null {
  if (m.waiting && m.thread_id) return { who: m.role, thread: m.thread_id, title: m.waiting.title };
  for (const c of m.children) {
    const w = waitingFlow(c);
    if (w) return w;
  }
  return null;
}

function Tree({ org, m }: { org: OrgView; m: MissionTree }): ReactNode {
  return (
    <ul className="org-tree">
      <TreeRow org={org} m={m} />
    </ul>
  );
}

function TreeRow({ org, m }: { org: OrgView; m: MissionTree }) {
  return (
    <li>
      <div className="org-node">
        <b>{roleTitle(org, m.role)}</b>
        <span className="sub">{m.parent_id ? m.title : `${STAGE[m.stage] ?? m.stage}`}</span>
        <StatusPill s={m.status} />
        {m.workflow && <span className="chip">{m.stage === "review" ? (org.roles.find((r) => r.id === m.role)?.review ?? m.workflow) : m.workflow}</span>}
        {m.thread_id && <a className="sub" href={hashForScreen("flow", m.thread_id)}>flow</a>}
        <span className="sub num">{kfmt(m.used_tokens + (m.running_tokens ?? 0))}</span>
      </div>
      {m.children.length > 0 && <ul>{m.children.map((c) => <TreeRow key={c.id} org={org} m={c} />)}</ul>}
    </li>
  );
}

function ReportView({ r }: { r: Report }) {
  const list = (label: string, items?: string[]) => items && items.length > 0 && (
    <div><b>{label}</b><ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{items.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
  );
  return (
    <div className="grid" style={{ gap: 10 }}>
      {r.summary && <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{r.summary}</p>}
      {list("Done", r.done)}
      {list("Decided", r.decisions)}
      {list("Next", r.follow_ups)}
      {list("Risks", r.risks)}
      {r.children && r.children.length > 0 && (
        <div><b>Checked</b><ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
          {r.children.map((c, i) => <li key={i}>{c.ok === false ? "✗" : "✓"} {c.role}{c.title ? `: ${c.title}` : ""}{c.note ? ` · ${c.note}` : ""}</li>)}
        </ul></div>
      )}
    </div>
  );
}
