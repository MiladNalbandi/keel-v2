// v0.11.0 the Database plugin's tool window on the Code page, IntelliJ style. The side bar (DbExplorer) shows every
// connection as a tree: connection ▸ tables ▸ columns, with primary keys and foreign keys; a connection opens a query
// console, a table opens its data. The editor tab (DbTab) is a console (SQL, ⌘↵ runs the statement under the cursor or
// the selection, a history) or a table (its first 100 rows, and its structure). Reads run at once; a change of data is
// counted first and runs after Run it, on a local or test database only.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  errorParts,
  type DbConnection,
  type DbResult,
  type LiveTable,
} from "../../api";
import { go, useLoad } from "../../state";
import { Icon } from "../../pages/repo/icons";
import { ErrorBox, Pill } from "../ui";
import { ResultTable } from "./QueryPanel";

const KIND: Record<string, string> = {
  postgres: "PostgreSQL",
  mysql: "MySQL",
  sqlite: "SQLite",
};
const SEP = "::";

/** A database tab's path: `<connection>` is its console, `<connection>::<table>` a table. */
export const dbPath = (conn: string, table?: string) =>
  table ? `${conn}${SEP}${table}` : conn;
export function parseDbPath(path: string): { conn: string; table?: string } {
  const i = path.indexOf(SEP);
  return i < 0
    ? { conn: path }
    : { conn: path.slice(0, i), table: path.slice(i + SEP.length) };
}
export const dbTabTitle = (path: string) => {
  const { conn, table } = parseDbPath(path);
  return table ? `${table} · ${conn}` : `console · ${conn}`;
};

/** The statement under the cursor (statements end at `;` outside quotes), or the selection when there is one. */
export function statementAt(
  text: string,
  from: number,
  to: number = from,
): string {
  if (to > from) return text.slice(from, to).trim();
  const parts: [number, number][] = [];
  let start = 0,
    quote: string | null = null;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote) {
      if (c === quote) quote = null;
      continue;
    }
    if (c === "'" || c === '"' || c === "`") quote = c;
    else if (c === ";") {
      parts.push([start, i]);
      start = i + 1;
    }
  }
  parts.push([start, text.length]);
  const hit =
    parts.find(([a, b]) => from >= a && from <= b + 1) ??
    parts[parts.length - 1];
  const stmt = text.slice(hit[0], hit[1]).trim();
  return stmt || text.trim();
}

function readStore(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}
function writeStore(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* the console still works, unsaved */
  }
}

// ------------------------------------------------------------------ the side bar: data sources ▸ tables ▸ columns

export function DbExplorer({
  pid,
  onConsole,
  onTable,
}: {
  pid: string;
  onConsole: (conn: string) => void;
  onTable: (conn: string, table: string, pin: boolean) => void;
}) {
  const conns = useLoad(`db:${pid}`, () => api.dbConnections(pid), {
    live: false,
  });
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [find, setFind] = useState("");
  return (
    <div className="sv db-sv">
      <div className="sv-head">
        <h2 className="sv-title">Database</h2>
        <div className="sv-tools">
          <button
            type="button"
            className="sv-tool"
            title="Add a database (Connections › Databases)"
            aria-label="Add a database"
            onClick={() => go("connections")}
          >
            <Icon name="plus" size={15} />
          </button>
          <button
            type="button"
            className="sv-tool"
            title="Refresh"
            aria-label="Refresh the databases"
            onClick={() => void conns.reload()}
          >
            <Icon name="refresh" size={15} />
          </button>
        </div>
      </div>
      <div className="sv-pad">
        <input
          type="search"
          className="inline-input db-find"
          placeholder="Find a table"
          aria-label="Find a table"
          value={find}
          onChange={(e) => setFind(e.target.value)}
        />
      </div>
      {conns.error && (
        <div className="sv-pad">
          <ErrorBox error={conns.error} />
        </div>
      )}
      {conns.data && !conns.data.length && (
        <p className="sv-pad sub">
          No database yet.{" "}
          <button
            type="button"
            className="btn sm"
            onClick={() => go("connections")}
          >
            Add one
          </button>
        </p>
      )}
      <ul className="db-tree" role="tree" aria-label="Databases">
        {(conns.data ?? []).map((c) => (
          <ConnNode
            key={c.name}
            pid={pid}
            c={c}
            open={!!open[c.name] || !!find.trim()}
            find={find.trim().toLowerCase()}
            onToggle={() => setOpen((o) => ({ ...o, [c.name]: !o[c.name] }))}
            onConsole={() => onConsole(c.name)}
            onTable={(t, pin) => onTable(c.name, t, pin)}
          />
        ))}
      </ul>
    </div>
  );
}

function ConnNode({
  pid,
  c,
  open,
  find,
  onToggle,
  onConsole,
  onTable,
}: {
  pid: string;
  c: DbConnection;
  open: boolean;
  find: string;
  onToggle: () => void;
  onConsole: () => void;
  onTable: (t: string, pin: boolean) => void;
}) {
  const schema = useLoad(
    open ? `db-schema:${pid}:${c.name}` : null,
    () => api.dbSchema(pid, c.name),
    { live: false },
  );
  const [tableOpen, setTableOpen] = useState<Record<string, boolean>>({});
  const tables = (schema.data?.tables ?? []).filter(
    (t) => !find || t.name.toLowerCase().includes(find),
  );
  return (
    <li role="treeitem" aria-expanded={open} aria-label={`Database ${c.name}`}>
      <div className="db-row db-conn">
        <button
          type="button"
          className="db-twist"
          onClick={onToggle}
          aria-label={open ? `Close ${c.name}` : `Open ${c.name}`}
        >
          {open ? "▾" : "▸"}
        </button>
        <span
          className={`db-dot ${c.ok === true ? "ok" : c.ok === false ? "bad" : ""}`}
          aria-hidden="true"
        />
        <Icon name="database" size={14} />
        <button
          type="button"
          className="db-name"
          onDoubleClick={onConsole}
          onClick={onToggle}
        >
          {c.name}
        </button>
        <span
          className={`db-env ${c.can_change ? "rw" : "ro"}`}
          title={
            c.can_change ? "changes of data with your OK" : "read only, always"
          }
        >
          {c.env}
        </span>
        <button
          type="button"
          className="db-act"
          title={`New console on ${c.name}`}
          aria-label={`New console on ${c.name}`}
          onClick={onConsole}
        >
          <Icon name="console" size={14} />
        </button>
      </div>
      {open && (
        <ul role="group">
          {schema.error && (
            <li className="db-row db-err-row">{schema.error.message}</li>
          )}
          {!schema.data && !schema.error && (
            <li className="db-row sub">reading the tables…</li>
          )}
          {schema.data && (
            <li className="db-row db-meta sub">
              {KIND[c.kind] ?? c.kind}
              {c.server ? ` · ${c.server}` : ""} · {schema.data.tables.length}{" "}
              tables
            </li>
          )}
          {tables.map((t) => (
            <TableNode
              key={t.name}
              t={t}
              open={!!tableOpen[t.name]}
              onToggle={() =>
                setTableOpen((o) => ({ ...o, [t.name]: !o[t.name] }))
              }
              onOpen={(pin) => onTable(t.name, pin)}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function TableNode({
  t,
  open,
  onToggle,
  onOpen,
}: {
  t: LiveTable;
  open: boolean;
  onToggle: () => void;
  onOpen: (pin: boolean) => void;
}) {
  const fkOf = (col: string) => t.fks.find((f) => f.column === col);
  return (
    <li role="treeitem" aria-expanded={open} aria-label={`Table ${t.name}`}>
      <div className="db-row db-table">
        <button
          type="button"
          className="db-twist"
          onClick={onToggle}
          aria-label={open ? `Close ${t.name}` : `Columns of ${t.name}`}
        >
          {open ? "▾" : "▸"}
        </button>
        <Icon name="table" size={14} />
        <button
          type="button"
          className="db-name"
          onClick={() => onOpen(false)}
          onDoubleClick={() => onOpen(true)}
          title="Open its data (double-click keeps the tab)"
        >
          {t.name}
        </button>
        <span className="sub db-count">{t.columns.length}</span>
      </div>
      {open && (
        <ul role="group">
          {t.columns.map((c) => {
            const fk = fkOf(c.name);
            return (
              <li
                key={c.name}
                className="db-row db-col"
                role="treeitem"
                aria-label={`Column ${c.name}`}
              >
                {c.pk ? (
                  <span className="db-key" title="primary key">
                    <Icon name="key" size={13} />
                  </span>
                ) : (
                  <span className="db-key">
                    <Icon name="column" size={13} />
                  </span>
                )}
                <span className={c.pk ? "db-pk" : undefined}>{c.name}</span>
                <span className="sub db-type">
                  {c.type}
                  {c.nullable ? "" : " · not null"}
                </span>
                {fk && (
                  <span
                    className="db-fk"
                    title={`foreign key to ${fk.table}.${fk.ref}`}
                  >
                    → {fk.table}.{fk.ref}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}

// ------------------------------------------------------------------ the editor tab: a console or a table

export function DbTab({ pid, path }: { pid: string; path: string }) {
  const { conn, table } = parseDbPath(path);
  return table ? (
    <TableTab pid={pid} conn={conn} table={table} />
  ) : (
    <ConsoleTab pid={pid} initial={conn} />
  );
}

function useRun(pid: string) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DbResult | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(
    null,
  );
  const run = async (
    conn: DbConnection | undefined,
    sql: string,
    confirm = false,
  ) => {
    if (!conn || !sql.trim()) return null;
    setBusy(true);
    setErr(null);
    try {
      const r = await api.dbQuery(pid, {
        connection: conn.name,
        sql,
        change: conn.can_change,
        confirm,
      });
      setResult(r);
      return r;
    } catch (e) {
      setResult(null);
      setErr(errorParts(e));
      return null;
    } finally {
      setBusy(false);
    }
  };
  return { busy, result, err, run, setResult };
}

function ChangeConfirm({
  r,
  busy,
  onRun,
  onCancel,
}: {
  r: DbResult;
  busy: boolean;
  onRun: () => void;
  onCancel: () => void;
}) {
  if (r.kind !== "change") return null;
  if (r.done)
    return (
      <p className="qp-done" role="status">
        {r.changed} row{r.changed === 1 ? "" : "s"} changed in {r.connection}.
      </p>
    );
  return (
    <div className="qp-confirm" role="group" aria-label="Change data">
      <span>
        This changes{" "}
        <b>
          {r.changed} row{r.changed === 1 ? "" : "s"}
        </b>{" "}
        in {r.connection} ({r.env}). keel ran it once and rolled it back to
        count.
      </span>
      <button
        type="button"
        className="btn sm primary"
        disabled={busy}
        onClick={onRun}
      >
        Run it ({r.changed} rows)
      </button>
      <button type="button" className="btn sm ghost" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}

function ConsoleTab({ pid, initial }: { pid: string; initial: string }) {
  const conns = useLoad(`db:${pid}`, () => api.dbConnections(pid), {
    live: false,
  });
  const [name, setName] = useState(initial);
  const conn = conns.data?.find((c) => c.name === name);
  const key = `keel2.db.console.${pid}.${name}`;
  const hkey = `keel2.db.history.${pid}.${name}`;
  const [sql, setSql] = useState(() => readStore(key, ""));
  const [history, setHistory] = useState<string[]>(() => {
    try {
      return JSON.parse(readStore(hkey, "[]"));
    } catch {
      return [];
    }
  });
  useEffect(() => {
    setSql(readStore(key, ""));
    try {
      setHistory(JSON.parse(readStore(hkey, "[]")));
    } catch {
      setHistory([]);
    }
  }, [key, hkey]);
  useEffect(() => {
    writeStore(key, sql);
  }, [key, sql]);
  const box = useRef<HTMLTextAreaElement>(null);
  const { busy, result, err, run, setResult } = useRun(pid);
  const [last, setLast] = useState("");
  const go_ = async () => {
    const el = box.current;
    const stmt = statementAt(
      sql,
      el?.selectionStart ?? 0,
      el?.selectionEnd ?? 0,
    );
    setLast(stmt);
    const r = await run(conn, stmt);
    if (r) {
      const next = [stmt, ...history.filter((h) => h !== stmt)].slice(0, 20);
      setHistory(next);
      writeStore(hkey, JSON.stringify(next));
    }
  };
  return (
    <div className="db-console" aria-label={`Console on ${name}`}>
      <div className="db-bar">
        <Icon name="database" size={14} />
        <select
          aria-label="Database of this console"
          value={name}
          onChange={(e) => setName(e.target.value)}
        >
          {(conns.data ?? [{ name } as DbConnection]).map((c) => (
            <option key={c.name} value={c.name}>
              {c.name}
              {c.env ? ` (${c.env})` : ""}
            </option>
          ))}
        </select>
        {conn && (
          <Pill tone={conn.can_change ? "run" : "warn"}>
            {conn.can_change ? "changes with your OK" : "read only"}
          </Pill>
        )}
        <button
          type="button"
          className="btn sm primary"
          disabled={busy || !sql.trim() || !conn}
          onClick={() => void go_()}
          title="Run the statement under the cursor, or the selection (⌘↵)"
        >
          <Icon name="play" size={12} /> {busy ? "Running…" : "Run"}
        </button>
        {history.length > 0 && (
          <select
            aria-label="History"
            value=""
            onChange={(e) => e.target.value && setSql(e.target.value)}
          >
            <option value="">History…</option>
            {history.map((h) => (
              <option key={h} value={h}>
                {h.replace(/\s+/g, " ").slice(0, 80)}
              </option>
            ))}
          </select>
        )}
      </div>
      <textarea
        ref={box}
        className="db-sql mono"
        aria-label="SQL"
        spellCheck={false}
        value={sql}
        placeholder={
          "SELECT * FROM players LIMIT 20;\n\n-- ⌘↵ runs the statement under the cursor, or the selection"
        }
        onChange={(e) => setSql(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            void go_();
          }
        }}
      />
      <div className="db-out">
        {err && <ErrorBox error={err} />}
        {result && (
          <ChangeConfirm
            r={result}
            busy={busy}
            onRun={() => void run(conn, last, true)}
            onCancel={() => setResult(null)}
          />
        )}
        {result?.kind === "read" && <ResultTable r={result} />}
        {!result && !err && <p className="sub">Results show here.</p>}
      </div>
    </div>
  );
}

function TableTab({
  pid,
  conn,
  table,
}: {
  pid: string;
  conn: string;
  table: string;
}) {
  const conns = useLoad(`db:${pid}`, () => api.dbConnections(pid), {
    live: false,
  });
  const schema = useLoad(
    `db-schema:${pid}:${conn}`,
    () => api.dbSchema(pid, conn),
    { live: false },
  );
  const c = conns.data?.find((x) => x.name === conn);
  const t = schema.data?.tables.find((x) => x.name === table);
  const [view, setView] = useState<"data" | "structure">("data");
  const [where, setWhere] = useState("");
  const { busy, result, err, run } = useRun(pid);
  const sql = useMemo(
    () =>
      `SELECT * FROM ${table}${where.trim() ? ` WHERE ${where.trim()}` : ""} LIMIT 100`,
    [table, where],
  );
  useEffect(() => {
    if (c) void run(c, sql);
  }, [c?.name, table]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="db-console" aria-label={`${table} in ${conn}`}>
      <div className="db-bar">
        <Icon name="table" size={14} />
        <b>{table}</b>
        <span className="sub">· {conn}</span>
        <div className="db-views" role="tablist" aria-label="Table view">
          <button
            type="button"
            role="tab"
            aria-selected={view === "data"}
            className={view === "data" ? "on" : ""}
            onClick={() => setView("data")}
          >
            Data
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "structure"}
            className={view === "structure" ? "on" : ""}
            onClick={() => setView("structure")}
          >
            Structure
          </button>
        </div>
      </div>
      {view === "data" ? (
        <>
          <form
            className="db-where"
            onSubmit={(e) => {
              e.preventDefault();
              void run(c, sql);
            }}
          >
            <span className="mono sub">WHERE</span>
            <input
              className="inline-input mono"
              aria-label="Filter rows"
              value={where}
              placeholder="value > 100"
              onChange={(e) => setWhere(e.target.value)}
            />
            <button type="submit" className="btn sm" disabled={busy || !c}>
              {busy ? "Reading…" : "Apply"}
            </button>
          </form>
          <div className="db-out">
            {err && <ErrorBox error={err} />}
            {result?.kind === "read" && <ResultTable r={result} />}
          </div>
        </>
      ) : !t ? (
        <p className="sub db-out">Reading the structure…</p>
      ) : (
        <div className="db-out">
          <div className="table-wrap">
            <table aria-label={`Structure of ${table}`}>
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Type</th>
                  <th>Null</th>
                  <th>Key</th>
                </tr>
              </thead>
              <tbody>
                {t.columns.map((col) => {
                  const fk = t.fks.find((f) => f.column === col.name);
                  return (
                    <tr key={col.name}>
                      <td className="mono">{col.name}</td>
                      <td className="mono">{col.type}</td>
                      <td>{col.nullable ? "yes" : "no"}</td>
                      <td>
                        {col.pk
                          ? "primary key"
                          : fk
                            ? `→ ${fk.table}.${fk.ref}`
                            : ""}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
