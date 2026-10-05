// Wiki (Project): the knowledge base the librarians write, a page for every workflow (drawn with the graph
// component), the setup runbook and decisions. The page id is in the hash: #/wiki/kb:architecture.

import { useState } from "react";
import { api, type WikiTree } from "../api";
import { Graph, GraphLegend } from "../components/Graph";
import { Zoom } from "../components/Zoom";
import { Markdown } from "../components/Markdown";
import { RefreshStaleButton } from "../components/RefreshStale";
import { StepExplainDrawer } from "../components/StepExplain";
import { Async, ErrorBox, Loading, PageHead, Pill } from "../components/ui";
import { KIND, tokensByStep } from "../components/workflow";
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
                {i.status === "writing" ? <span className="adot" /> : i.status === "missing" ? <span className="sub">—</span> : i.status === "stale" ? <span className="sub amber">stale</span> : null}
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
    {explain && <StepExplainDrawer pid={pid} req={{ step_id: explain, workflow_id: wid }} onClose={() => setExplain(null)} />}
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
            <div className="panel" style={{ margin: "12px 0" }}><div className="panel-body">
              <Zoom id="wiki"><Graph steps={w.steps} tokens={tokens} onSelect={setExplain} /></Zoom>
              <GraphLegend />
            </div></div>
            <div className="table-wrap"><table>
              <thead><tr><th>Step</th><th>Type</th><th>Who</th><th>Model</th><th>Tokens</th></tr></thead>
              <tbody>{w.steps.map((s) => (
                <tr key={s.id}>
                  <td><button type="button" className="linkbtn" onClick={() => setExplain(s.id)}><b>{s.name}</b></button>{s.per_ac && <> <span className="tag">each AC</span></>}</td>
                  <td className="sub">{KIND[s.kind]}</td>
                  <td className="sub">{s.agent || (s.kind === "gate" ? "you" : "plain code")}</td>
                  <td>{s.model ? <span className="mono sub">{s.model}</span> : "—"}</td>
                  <td className="num mono">{tokens?.[s.id] ? kfmt(tokens[s.id]) : "0"}</td>
                </tr>
              ))}</tbody>
            </table></div>
            <p className="hint">This page is generated from the workflow file, so it is always the version that runs. Click a step to see what it really does.</p>
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

export function WikiPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const { arg } = useRoute();
  const tree = useLoad(`wiki:${pid}`, () => api.wiki(pid));
  const [q, setQ] = useState("");
  const first = tree.data?.sections.flatMap((s) => s.items)[0]?.id;
  const cur = arg ?? first ?? "";
  const stale = (tree.data?.sections ?? []).flatMap((s) => s.items).filter((i) => i.status === "stale" && i.id.startsWith("kb:")).map((i) => i.id.slice(3));
  return (
    <>
      <PageHead title="Wiki"
        sub={`What keel knows about ${project?.name ?? pid}: the knowledge base the librarians write, a page for every workflow, the setup runbook and decisions.`}
        actions={<>
          <input type="text" className="inline-input" placeholder="Search the wiki" aria-label="Search the wiki" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 200 }} />
          <RefreshStaleButton pid={pid} sections={stale} className="btn" />
        </>} />
      <Async r={tree} what="Loading the wiki">
        {(t) => !t.sections.some((s) => s.items.length) ? (
          <div className="panel"><div className="panel-body empty">The wiki is empty. The init flow writes the knowledge base; every workflow gets a page.</div></div>
        ) : (
          <div className="wiki-grid">
            <Tree tree={t} cur={cur} q={q} />
            <article className="panel"><div className="panel-body">
              {!cur ? <span className="sub">Pick a page.</span> : cur.startsWith("wf:") ? <WorkflowPage key={cur} pid={pid} wid={cur.slice(3)} /> : <Page key={cur} pid={pid} id={cur} />}
            </div></article>
          </div>
        )}
      </Async>
    </>
  );
}

