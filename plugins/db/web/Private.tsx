// #96 Results only to you: on a connection whose Access is "private", agents write and run reads but the rows come only
// to the person. Here they see them: Settings › Databases › Results for you (the list, a result in a drawer, the link an
// MCP client gets: #/projects/<pid>/settings/databases/results/<id>), and KeelBot's card for a ```keel-result``` block.
// The person picks the rows and columns to copy, download or send to KeelBot; nothing else ever reaches a model.
// Also the switch for keel's MCP server (Claude Code, OpenCode, Codex … through keel2 mcp).
// The Database plugin's part (plugins/db): it imports only react and @keel/web-sdk.

import { useContext, useEffect, useId, useMemo, useState } from "react";
import {
  AskKeelBot,
  copyText,
  del,
  Drawer,
  ErrorBox,
  errorParts,
  get,
  Loading,
  Section,
  useApp,
  useLoad,
  type KeelbotCardProps,
} from "@keel/web-sdk";
import { dbxApi, type McpAccess } from "./dbConfig";

export type PrivateResult = {
  id: string;
  project: string;
  connection: string;
  env: string;
  kind: string;
  sql: string;
  /** who asked: KeelBot, the agent <id>, the workflow step <id>, or an MCP client's name */
  who: string;
  thread: string | null;
  at: string;
  columns: string[];
  rows?: unknown[][];
  count: number;
  truncated: boolean;
  masked: string[];
  ms?: number;
  /** the database refused the query: its full message (an agent got a short line, or none) */
  error?: string;
  hint?: string;
};

const e = encodeURIComponent;
export const privateApi = {
  list: (pid: string) => get<PrivateResult[]>(`/projects/${e(pid)}/db/results`),
  one: (pid: string, id: string) => get<PrivateResult>(`/projects/${e(pid)}/db/results/${e(id)}`),
  forget: (pid: string, id: string) => del(`/projects/${e(pid)}/db/results/${e(id)}`),
};

/** A result's link (what an MCP client gets; the Settings tab opens it). */
export const resultHash = (pid: string, id: string) =>
  `#/projects/${e(pid)}/settings/databases/results/${e(id)}`;

const cellText = (v: unknown) => (v === null || v === undefined ? "NULL" : String(v));

/** The picked part as a Markdown table (what Copy and Send to KeelBot give). */
export function asMarkdown(cols: string[], rows: unknown[][]): string {
  const esc = (v: unknown) => cellText(v).replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
  const out = [`| ${cols.map(esc).join(" | ")} |`, `|${cols.map(() => "---|").join("")}`];
  for (const r of rows) out.push(`| ${r.map(esc).join(" | ")} |`);
  return out.join("\n");
}

/** The picked part as CSV (RFC 4180: a field with a comma, a quote or a line break is quoted). */
export function asCsv(cols: string[], rows: unknown[][]): string {
  const q = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols, ...rows].map((r) => r.map(q).join(",")).join("\r\n") + "\r\n";
}

function download(name: string, text: string) {
  try {
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch {
    /* no download here (an old browser, a test): Copy still works */
  }
}

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** One result: pick the rows and columns, then copy them, download them, or (in KeelBot's chat) send them to KeelBot. */
export function ResultView({ r, compact }: { r: PrivateResult; compact?: boolean }) {
  const ask = useContext(AskKeelBot);
  const { toast } = useApp();
  const ids = useId();
  const cols = r.columns;
  const rows = useMemo(() => r.rows ?? [], [r.rows]);
  const [offCols, setOffCols] = useState<Set<number>>(new Set());
  const [offRows, setOffRows] = useState<Set<number>>(new Set());
  useEffect(() => {
    setOffCols(new Set());
    setOffRows(new Set());
  }, [r.id]);
  if (r.error) {
    return (
      <div className="pr-error" role="alert">
        <b>The query failed.</b> {r.error}
        {r.hint ? <span className="sub"> {r.hint}</span> : null}
      </div>
    );
  }
  const keepCols = cols.map((_, i) => i).filter((i) => !offCols.has(i));
  const keepRows = rows.map((_, i) => i).filter((i) => !offRows.has(i));
  const picked = () => ({
    cols: keepCols.map((i) => cols[i]),
    rows: keepRows.map((i) => keepCols.map((j) => rows[i][j])),
  });
  const flip = (set: Set<number>, i: number) => {
    const n = new Set(set);
    if (n.has(i)) n.delete(i);
    else n.add(i);
    return n;
  };
  const nothing = !keepCols.length || !keepRows.length;
  const copy = async () => {
    const p = picked();
    toast((await copyText(asMarkdown(p.cols, p.rows))) ? "Copied as a table" : "Copy did not work here");
  };
  const send = () => {
    const p = picked();
    ask?.(`These are rows I picked from result ${r.id} (${r.connection}): ${plural(p.rows.length, "row")}, `
      + `${plural(p.cols.length, "column")}.\n\n${asMarkdown(p.cols, p.rows)}`);
  };
  return (
    <div className={`pr-view${compact ? " compact" : ""}`}>
      <p className="sub pr-meta">
        {plural(r.count, "row")}
        {r.truncated ? ` (keel kept the first ${r.count})` : ""} · {r.connection}
        {r.ms != null ? ` · ${r.ms} ms` : ""}
        {r.masked.length ? ` · hidden as •••: ${r.masked.join(", ")}` : ""}
      </p>
      {cols.length > 0 && (
        <fieldset className="pr-cols">
          <legend className="sub">Columns</legend>
          {cols.map((c, i) => (
            <label key={c + i} className="pr-col">
              <input type="checkbox" checked={!offCols.has(i)} onChange={() => setOffCols(flip(offCols, i))} />
              {c}
            </label>
          ))}
        </fieldset>
      )}
      {rows.length > 0 && keepCols.length > 0 && (
        <div className="table-wrap pr-table">
          <table aria-label={`Result ${r.id}`}>
            <thead>
              <tr>
                <th className="pr-pick">
                  <input type="checkbox" aria-label="All rows" checked={!offRows.size}
                    onChange={() => setOffRows(offRows.size ? new Set() : new Set(rows.map((_, i) => i)))} />
                </th>
                {keepCols.map((j) => <th key={j}>{cols[j]}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i} className={offRows.has(i) ? "off" : undefined}>
                  <td className="pr-pick">
                    <input type="checkbox" aria-label={`Row ${i + 1}`} checked={!offRows.has(i)}
                      onChange={() => setOffRows(flip(offRows, i))} />
                  </td>
                  {keepCols.map((j) => (
                    <td key={j} className={row[j] === null ? "qp-null" : undefined}>{cellText(row[j])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!rows.length && <p className="sub">No rows came back.</p>}
      <div className="row pr-actions" role="group" aria-labelledby={`${ids}-a`}>
        <span id={`${ids}-a`} className="sub">
          Picked: {plural(keepRows.length, "row")}, {plural(keepCols.length, "column")}
        </span>
        <button type="button" className="btn sm" disabled={nothing} onClick={() => void copy()}>Copy as table</button>
        <button type="button" className="btn sm" disabled={nothing}
          onClick={() => { const p = picked(); download(`${r.connection}-${r.id}.csv`, asCsv(p.cols, p.rows)); }}>
          Download CSV
        </button>
        {ask && (
          <button type="button" className="btn sm primary" disabled={nothing} onClick={send}
            title="Only the rows and columns you picked go to KeelBot, and so to its AI provider">
            Send to KeelBot
          </button>
        )}
      </div>
    </div>
  );
}

function ResultHead({ r }: { r: PrivateResult }) {
  return (
    <>
      <p className="sub pr-who">
        Asked by <b>{r.who}</b> · {r.connection} ({r.env}) · {new Date(r.at).toLocaleString()}
      </p>
      <pre className="mono pr-sql">{r.sql}</pre>
    </>
  );
}

/** Settings › Databases › Results for you: the newest first; one opens in a drawer (also from its link). */
export function PrivateResults({ pid, open: linked }: { pid: string; open?: string }) {
  const { toast } = useApp();
  const list = useLoad(pid ? `db-private:${pid}` : null, () => privateApi.list(pid));
  const [open, setOpen] = useState<string | null>(linked ?? null);
  useEffect(() => {
    if (linked) setOpen(linked);
  }, [linked]);
  const one = useLoad(open ? `db-private:${pid}:${open}` : null, () => privateApi.one(pid, open!), { live: false });
  const forget = async (id: string) => {
    await privateApi.forget(pid, id);
    setOpen(null);
    toast("Forgotten");
    await list.reload();
  };
  const items = list.data ?? [];
  return (
    <Section title="Results for you" className="pr-sec"
      sub="What agents asked on a connection whose results only you see. Agents never see these rows. keel keeps them in memory for a day, and forgets them when it restarts.">
      {list.error ? (
        <ErrorBox error={list.error} onRetry={() => void list.reload()} />
      ) : !list.data ? (
        <Loading what="Loading the results" />
      ) : !items.length ? (
        <p className="sub">
          No results yet. Set a connection's Access to "Run queries, results only to you"; when an agent queries it, the
          result shows here.
        </p>
      ) : (
        <ul className="pr-list" aria-label="Results for you">
          {items.map((r) => (
            <li key={r.id}>
              <button type="button" className="pr-item" onClick={() => setOpen(r.id)}
                aria-label={`Open result ${r.id} from ${r.connection}, asked by ${r.who}`}>
                <span className="pr-item-h">
                  <b>{r.connection}</b>
                  <span className="sub">{r.who}</span>
                  <span className={`chip${r.error ? " bad" : ""}`}>{r.error ? "failed" : plural(r.count, "row")}</span>
                </span>
                <code className="pr-item-sql">{r.sql}</code>
              </button>
            </li>
          ))}
        </ul>
      )}
      {open && (
        <Drawer title={`Result ${open}`} onClose={() => setOpen(null)} className="pr-drawer"
          footer={one.data ? <button type="button" className="btn sm danger" onClick={() => void forget(open)}>Forget it</button> : undefined}>
          {one.error ? (
            <ErrorBox error={one.error} />
          ) : !one.data ? (
            <Loading what="Loading the result" />
          ) : (
            <div className="grid" style={{ gap: 10 }}>
              <ResultHead r={one.data} />
              <ResultView r={one.data} />
            </div>
          )}
        </Drawer>
      )}
    </Section>
  );
}

/** KeelBot's card for a ```keel-result {"id": "r_…"}``` block: the rows only the person sees, under KeelBot's answer. */
export function ResultCard({ pid, block }: KeelbotCardProps) {
  let id = "";
  try {
    id = String((JSON.parse(block.body) as { id?: string }).id ?? "");
  } catch {
    id = "";
  }
  const r = useLoad(id ? `db-private:${pid}:${id}` : null, () => privateApi.one(pid, id), { live: false });
  if (!id) return <p className="kb-card bad" role="note">KeelBot's result could not be read. Ask it to give it again.</p>;
  return (
    <section className="kb-card pr-card" aria-label={`Result ${id}`}>
      <div className="kb-card-h">
        <span className="kb-tag">Only you see this</span>
        <b>{r.data?.connection ?? "Result"}</b>
        <span className="sub">{id}</span>
      </div>
      {r.error ? (
        <p className="kb-err" role="alert"><b>{r.error.message}</b>{r.error.hint ? <span className="sub"> {r.error.hint}</span> : null}</p>
      ) : !r.data ? (
        <span className="sub">Loading the result…</span>
      ) : (
        <ResultView r={r.data} compact />
      )}
    </section>
  );
}

const MCP: { v: McpAccess; title: string; sub: string }[] = [
  { v: "connection", title: "Follow each connection's Access", sub: "The same as keel's own agents." },
  {
    v: "private",
    title: "Results only to you, at most",
    sub: "They write and run reads. The rows come to you under Results for you; the tool gets only a link.",
  },
  { v: "none", title: "No databases", sub: "keel's database tools refuse every call from them." },
];

/** Settings › Databases › MCP clients: the most keel's MCP server may do with databases. Only the person changes it. */
export function McpSwitch() {
  const { toast } = useApp();
  const ids = useId();
  const cur = useLoad("db-mcp", () => dbxApi.mcp(), { live: false });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const pick = async (access: McpAccess) => {
    setBusy(true);
    setErr(null);
    try {
      await dbxApi.setMcp(access);
      await cur.reload();
      toast("Saved");
    } catch (x) {
      setErr(errorParts(x));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Section title="MCP clients" className="pr-mcp"
      sub="Claude Code, OpenCode, Codex, Cursor and other tools you connect with keel2 mcp. What they read goes to their AI provider, and keel cannot stop that: keep databases from them here.">
      {cur.error ? (
        <ErrorBox error={cur.error} onRetry={() => void cur.reload()} />
      ) : !cur.data ? (
        <Loading what="Loading" />
      ) : (
        <div className="dbx-card" role="radiogroup" aria-labelledby={`${ids}-h`}>
          <h3 id={`${ids}-h`}>What MCP clients may do with databases</h3>
          {MCP.map((m) => (
            <label key={m.v} className={`dbx-choice${cur.data!.access === m.v ? " on" : ""}`}>
              <input type="radio" name={`${ids}-mcp`} value={m.v} checked={cur.data!.access === m.v} disabled={busy}
                onChange={() => void pick(m.v)} />
              <span>
                <b>{m.title}</b>
                <span className="sub">{m.sub}</span>
              </span>
            </label>
          ))}
          <p className="sub">
            keel's own agents (KeelBot, flow agents) follow each connection's Access. They use an AI provider too: for data
            no AI may read, set the connection to "Run queries, results only to you" or "Nothing".
          </p>
          {err && <ErrorBox error={err} />}
        </div>
      )}
    </Section>
  );
}
