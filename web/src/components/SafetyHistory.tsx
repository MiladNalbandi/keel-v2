// Safety & History › History: every tool call an agent made in this project and what keel's guard decided, newest
// first, grouped by run, with the safety tier of each call and a check that no line was changed afterwards.

import { useEffect, useState } from "react";
import { errorParts } from "../api";
import { clock } from "../format";
import { historyApi, TIER_WORDS, type HistoryFilter, type HistoryLine, type HistoryPage, type Tier } from "../historyApi";
import { go } from "../state";
import { ErrorBox, Loading, Panel, Pill } from "./ui";

const KIND_WORDS: Record<HistoryLine["kind"], string> = { read: "read", write: "write", edit: "edit", tool: "tool", approval: "your answer" };
const ANSWER_WORDS: Record<string, string> = { once: "approved once", always: "always allowed", deny: "denied" };

function TierTag({ tier }: { tier?: Tier | null }) {
  if (!tier) return <span className="sh-tier sh-tnone" title="Not judged by tier (an MCP tool)">—</span>;
  return <span className={`sh-tier sh-${tier.toLowerCase()}`} title={TIER_WORDS[tier]}>{tier} <span className="sh-tword">{TIER_WORDS[tier]}</span></span>;
}

function Outcome({ l }: { l: HistoryLine }) {
  if (l.kind === "approval") {
    const said = l.answer ? ANSWER_WORDS[l.answer] ?? l.answer : l.status === "expired" ? "nobody answered" : l.status ?? "closed";
    return <Pill tone={l.action === "allow" ? "ok" : "bad"}>{said}</Pill>;
  }
  if (l.action === "deny") return <Pill tone="bad" title="keel's guard stopped this call before it ran">blocked</Pill>;
  if (l.ok === false) return <Pill tone="warn" title="It ran and failed">ran, failed</Pill>;
  return <Pill tone="ok">ran</Pill>;
}

type Group = { thread: string; source?: string; step?: string; agent?: string; lines: HistoryLine[] };

function groups(items: HistoryLine[]): Group[] {
  const out: Group[] = [];
  for (const l of items) {
    const last = out[out.length - 1];
    if (last && last.thread === (l.thread ?? "")) last.lines.push(l);
    else out.push({ thread: l.thread ?? "", source: l.source, step: l.step, agent: l.agent, lines: [l] });
  }
  return out;
}

export function SafetyHistory({ pid, rule }: { pid: string; rule?: string }) {
  const [filter, setFilter] = useState<HistoryFilter>(rule ? { action: "deny", q: rule } : {});
  const [q, setQ] = useState(filter.q ?? "");
  const [page, setPage] = useState<HistoryPage | null>(null);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);
  const [chain, setChain] = useState<{ ok: boolean; text: string } | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    let live = true;
    setError(null);
    historyApi.list(pid, filter).then((p) => { if (live) setPage(p); }).catch((e) => { if (live) setError(errorParts(e)); });
    return () => { live = false; };
  }, [pid, filter]);
  useEffect(() => {
    let live = true;
    historyApi.verify(pid).then((c) => { if (live) setChain(c); }).catch(() => { if (live) setChain(null); });
    return () => { live = false; };
  }, [pid]);

  const set = (patch: HistoryFilter) => setFilter((f) => ({ ...f, ...patch }));
  const loadMore = async () => {
    if (!page?.next) return;
    setMore(true);
    try {
      const p = await historyApi.list(pid, { ...filter, before: page.next });
      setPage({ ...p, items: [...page.items, ...p.items] });
    } catch (e) {
      setError(errorParts(e));
    } finally {
      setMore(false);
    }
  };

  const c = page?.counts;
  return (
    <div className="grid sh-page">
      <div className="sh-tiles" aria-label="Totals">
        <div className="sh-tile"><b>{c?.total ?? "…"}</b><span>calls recorded</span></div>
        <div className="sh-tile"><b>{c?.action.allow ?? 0}</b><span>ran</span></div>
        <div className="sh-tile sh-bad"><b>{c?.action.deny ?? 0}</b><span>blocked or denied</span></div>
        <div className="sh-tile sh-warn"><b>{c?.tier.T2 ?? 0}</b><span>hard to undo (T2)</span></div>
        <div className="sh-tile sh-bad"><b>{c?.tier.T3 ?? 0}</b><span>cannot be undone (T3)</span></div>
      </div>
      {chain && (
        <p className={`sh-chain ${chain.ok ? "sh-ok" : "sh-broken"}`} role={chain.ok ? undefined : "alert"}>
          {chain.ok ? "✓ Record intact: " : "⚠ The record was changed: "}{chain.text}.
          <span className="sub"> Each line holds the hash of the line before it, so a changed, removed or moved line shows here.</span>
        </p>
      )}
      <Panel title={<h2>History <span className="sub">every tool call of this project's agents, newest first</span></h2>}>
        <form className="row sh-filters" onSubmit={(e) => { e.preventDefault(); set({ q: q.trim() || undefined }); }} role="search">
          <label className="sr-only" htmlFor="shQ">Search the history</label>
          <input id="shQ" type="search" className="inline-input" placeholder="Search: a command, a file, a rule…" value={q}
            onChange={(e) => setQ(e.target.value)} onBlur={() => set({ q: q.trim() || undefined })} />
          <label className="sh-sel">Decision{" "}
            <select value={filter.action ?? ""} onChange={(e) => set({ action: e.target.value || undefined })}>
              <option value="">all</option><option value="allow">ran</option><option value="deny">blocked or denied</option>
            </select>
          </label>
          <label className="sh-sel">Tier{" "}
            <select value={filter.tier ?? ""} onChange={(e) => set({ tier: e.target.value || undefined })}>
              <option value="">all</option>
              {(Object.keys(TIER_WORDS) as Tier[]).map((t) => <option key={t} value={t}>{t} {TIER_WORDS[t]}</option>)}
            </select>
          </label>
          <label className="sh-sel">Kind{" "}
            <select value={filter.kind ?? ""} onChange={(e) => set({ kind: e.target.value || undefined })}>
              <option value="">all</option>
              {(Object.keys(KIND_WORDS) as HistoryLine["kind"][]).map((k) => <option key={k} value={k}>{KIND_WORDS[k]}</option>)}
            </select>
          </label>
          {filter.thread && <button className="btn sm ghost" type="button" onClick={() => set({ thread: undefined })}>Run {filter.thread} ✕</button>}
        </form>
        {error && <ErrorBox error={error} />}
        {!page && !error && <Loading what="Loading the history" />}
        {page && page.items.length === 0 && (
          <div className="empty">{c?.total ? "No call matches these filters." : "Nothing recorded yet. Every tool call of a flow's agents and of KeelBot shows here once it runs."}</div>
        )}
        {page && groups(page.items).map((g, i) => (
          <section key={`${g.thread}-${i}`} className="sh-group" aria-label={`Run ${g.thread}`}>
            <div className="sh-ghead">
              <b>{g.source === "keelbot" ? "KeelBot" : "Flow"}</b> <span className="mono sub">{g.thread}</span>
              {g.step && <span className="tag">{g.step}</span>}{g.agent && g.agent !== "KeelBot" && <span className="sub">{g.agent}</span>}
              <span className="row" style={{ marginLeft: "auto", gap: 6 }}>
                {!filter.thread && g.thread && <button className="btn sm ghost" type="button" onClick={() => set({ thread: g.thread })}>Only this run</button>}
                {g.source === "flow" && g.thread && <button className="btn sm ghost" type="button" onClick={() => go("flow", g.thread)}>Open the flow</button>}
              </span>
            </div>
            <ul className="sh-lines">
              {g.lines.map((l) => (
                <li key={l.seq} className={`sh-line${l.action === "deny" ? " sh-denied" : ""}`} data-seq={l.seq}>
                  <button type="button" className="sh-row" aria-expanded={open === l.seq} onClick={() => setOpen(open === l.seq ? null : l.seq)}>
                    <span className="sh-time mono">{clock(l.at, false)}</span>
                    <TierTag tier={l.tier} />
                    <Outcome l={l} />
                    <span className="sh-kind">{l.kind === "tool" ? (l.server ? `${l.server} · ${l.tool}` : l.tool) : KIND_WORDS[l.kind]}</span>
                    <span className="sh-text mono">{l.text}</span>
                  </button>
                  {(l.action === "deny" || open === l.seq) && l.reason && <div className="sh-why">{l.reason}</div>}
                  {open === l.seq && (
                    <dl className="sh-detail">
                      {l.path && <><dt>File</dt><dd className="mono">{l.path}</dd></>}
                      {l.rule && <><dt>Rule</dt><dd className="mono">{l.rule}</dd></>}
                      {l.undo && <><dt>Can it be undone</dt><dd>{l.undo}</dd></>}
                      {l.action === "deny" && l.kind !== "approval" && <><dt>Sent back to the model</dt><dd>The reason above, as the tool's error: the agent is told to stop and ask, not to try another way.</dd></>}
                      {l.by && <><dt>Answered by</dt><dd>{l.by === "person" ? "you" : "keel"}</dd></>}
                      <dt>Line</dt><dd className="mono">#{l.seq}{l.hash ? ` · ${l.hash.slice(0, 12)}` : ""}</dd>
                    </dl>
                  )}
                </li>
              ))}
            </ul>
          </section>
        ))}
        {page?.more && <button className="btn" type="button" disabled={more} onClick={loadMore}>{more ? "Loading…" : "Show older calls"}</button>}
      </Panel>
    </div>
  );
}
