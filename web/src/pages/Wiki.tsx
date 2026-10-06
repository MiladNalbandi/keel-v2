// Wiki (Project): the knowledge base the librarians write, a page for every workflow (its Steps map, read only, or
// the graph), the setup runbook and decisions. The page id is in the hash: #/wiki/kb:architecture.

import { useState } from "react";
import { api, type WikiTree } from "../api";
import { Markdown } from "../components/Markdown";
import { RefreshStaleButton } from "../components/RefreshStale";
import { StepInfoDrawer } from "../components/StepInfo";
import { EmptyState, SearchBox, Skeleton, useNarrow } from "../components/page";
import { Async, ErrorBox, Loading, PageHead, Pill } from "../components/ui";
import { tokensByStep } from "../components/workflow";
import { WorkflowMap } from "../components/WorkflowMap";
import { kfmt } from "../format";
import { go, useApp, useLoad, useRoute } from "../state";

const STATUS: Record<string, ["ok" | "run" | "warn" | "idle", string]> = {
  written: ["ok", "written"], ok: ["ok", "written"], writing: ["run", "writing"], stale: ["warn", "stale"], missing: ["idle", "not written"],
};

function Tree({ tree, cur, q }: { tree: WikiTree; cur: string; q: string }) {
  const needle = q.trim().toLowerCase();
  return (
    <nav className="wtree" aria-label="Wiki pages">
      {tree.sections.map((s) => {
        const items = s.items.filter((i) => !needle || i.title.toLowerCase().includes(needle) || i.id.toLowerCase().includes(needle));
        if (!items.length && needle) return null;
        return (
          <div key={s.id} style={{ display: "contents" }}>
            <div className="wt-h">{s.title}</div>
            {items.map((i) => (
              <a key={i.id} href={`#/wiki/${encodeURIComponent(i.id)}`} aria-current={cur === i.id ? "page" : undefined}>
                {i.title}
                {i.status === "writing" ? <span className="adot" /> : i.status === "missing" ? <span className="sub wk-miss">not written</span> : i.status === "stale" ? <span className="sub amber">stale</span> : null}
              </a>
            ))}
            {!items.length && <span className="sub" style={{ padding: "2px 10px" }}>none yet</span>}
          </div>
        );
      })}
    </nav>
  );
}

function WorkflowPage({ pid, wid }: { pid: string; wid: string }) {
  const wf = useLoad(`wf:${wid}`, () => api.workflow(wid));
  const est = useLoad(`wfest:${pid}:${wid}`, () => api.estimate(pid, wid, 3), { live: false });
  const [explain, setExplain] = useState<string | null>(null);
  return (
    <>
    {explain && <StepInfoDrawer pid={pid} workflow={wid} stepId={explain} onClose={() => setExplain(null)} />}
    <Async r={wf} what="Loading the workflow">
      {(w) => {
        const gates = w.steps.filter((s) => s.kind === "gate").length;
        const agents = new Set(w.steps.filter((s) => s.agent).map((s) => s.agent)).size;
        const tokens = tokensByStep(w, est.data);
        return (
          <>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h2 className="wh">{w.name}</h2>
              <div className="row">
                <button className="btn sm" type="button" onClick={() => go("workflows", w.id)}>Edit in builder</button>
                <a className="btn sm" href={api.exportUrl(w.id)} download={`${w.id}.workflow.yaml`} style={{ textDecoration: "none" }}>Export</a>
              </div>
            </div>
            <p className="sub" style={{ marginTop: 0 }}>
              {w.steps.length} steps · {agents} agents · {gates} gates wait for you · keel rules {w.keel_rules ? "on" : "off"} · version {w.version}
              {est.data ? ` · about ${kfmt(est.data.tokens)} tokens for 3 ACs` : ""}
            </p>
            <div style={{ margin: "12px 0" }}>
              <WorkflowMap id="wiki" title="Steps" steps={w.steps} tokens={tokens} onOpenStep={setExplain}
                label={`Steps of ${w.name}: ${w.steps.length} steps. Select one to see what it does.`} />
            </div>
            <p className="hint">This page is generated from the workflow file, so it is always the version that runs.</p>
          </>
        );
      }}
    </Async>
    </>
  );
}

function Page({ pid, id }: { pid: string; id: string }) {
  const page = useLoad(`wikipage:${pid}:${id}`, () => api.wikiPage(pid, id));
  if (page.error) return <ErrorBox error={page.error} onRetry={() => void page.reload()} />;
  if (!page.data) return <Loading />;
  const p = page.data;
  const meta = p.meta ?? {};
  const status = typeof meta.status === "string" ? STATUS[meta.status] : undefined;
  const facts = [
    meta.what, meta.words !== undefined && `${meta.words} words`, meta.cites !== undefined && `${meta.cites} citations`,
    meta.by && `by ${meta.by}`, meta.sha && `checked against HEAD ${String(meta.sha).slice(0, 7)}`, meta.updated && `updated ${meta.updated}`,
  ].filter(Boolean) as string[];
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 className="wh">{p.title}</h2>
        {status && <Pill tone={status[0]}>{status[1]}</Pill>}
      </div>
      {facts.length > 0 && <p className="sub" style={{ marginTop: 0 }}>{facts.join(" · ")}</p>}
      <div className="wdoc">{p.markdown ? <Markdown text={p.markdown} /> : <p className="sub">This page is empty.</p>}</div>
      {id.startsWith("kb:") && <p className="hint">Every claim cites <span className="mono">file:line</span>. <span className="mono">memory check</span> opens each citation; a broken one fails the check.</p>}
    </>
  );
}

/** On a phone: the pages as one dropdown (grouped by section) instead of a long list above the page. */
function PagePicker({ tree, cur, q }: { tree: WikiTree; cur: string; q: string }) {
  const needle = q.trim().toLowerCase();
  const secs = tree.sections.map((s) => ({ ...s, items: s.items.filter((i) => !needle || i.title.toLowerCase().includes(needle) || i.id.toLowerCase().includes(needle) || i.id === cur) }))
    .filter((s) => s.items.length);
  return (
    <div className="wk-pick">
      <label className="lab-s" htmlFor="wk-page">Page</label>
      <select id="wk-page" value={cur} onChange={(e) => go("wiki", e.target.value)}>
        {secs.map((s) => (
          <optgroup key={s.id} label={s.title}>
            {s.items.map((i) => <option key={i.id} value={i.id}>{i.title}{i.status === "missing" ? " (not written)" : i.status === "stale" ? " (stale)" : ""}</option>)}
          </optgroup>
        ))}
      </select>
    </div>
  );
}

export function WikiPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const { arg } = useRoute();
  const tree = useLoad(`wiki:${pid}`, () => api.wiki(pid));
  const [q, setQ] = useState("");
  const narrow = useNarrow(900);
  const first = tree.data?.sections.flatMap((s) => s.items)[0]?.id;
  const cur = arg ?? first ?? "";
  const stale = (tree.data?.sections ?? []).flatMap((s) => s.items).filter((i) => i.status === "stale" && i.id.startsWith("kb:")).map((i) => i.id.slice(3));
  return (
    <>
      <PageHead title="Wiki"
        sub={`What keel knows about ${project?.name ?? pid}: the knowledge base the librarians write, a page for every workflow, the setup runbook and decisions.`}
        actions={<RefreshStaleButton pid={pid} sections={stale} className="btn" />} />
      {tree.error ? <ErrorBox error={tree.error} onRetry={() => void tree.reload()} /> : !tree.data ? <div className="panel"><Skeleton lines={6} label="Loading the wiki" /></div> : (() => {
        const t = tree.data;
        if (!t.sections.some((s) => s.items.length)) {
          return <div className="panel"><EmptyState title="The wiki is empty" action={<a className="btn" href="#/flow">Open Flow</a>}>The init flow writes the knowledge base, and every workflow gets a page here. Start init from the Flow page.</EmptyState></div>;
        }
        return (
          <div className="wiki-grid">
            <div>
              <div className="wk-search"><SearchBox value={q} onChange={setQ} label="Search the wiki" /></div>
              {narrow ? <PagePicker tree={t} cur={cur} q={q} /> : <Tree tree={t} cur={cur} q={q} />}
            </div>
            <article className="panel"><div className="panel-body">
              {!cur ? <span className="sub">Pick a page.</span> : cur.startsWith("wf:") ? <WorkflowPage key={cur} pid={pid} wid={cur.slice(3)} /> : <Page key={cur} pid={pid} id={cur} />}
            </div></article>
          </div>
        );
      })()}
    </>
  );
}
