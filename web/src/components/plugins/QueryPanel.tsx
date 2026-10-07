// v0.10.0 Map › Database › Query (the Database plugin): the live database next to the diagram. Pick a table to see its
// first 20 rows, or write SQL. A read runs at once (read-only, 200 rows, 15 s); a change of data runs only on a local
// or test database: keel counts the rows first (a run it rolls back), then you press Run it.

import { useEffect, useState } from "react";
import { api, errorParts, type DbResult } from "../../api";
import { useLoad } from "../../state";
import { ErrorBox } from "../ui";

export function ResultTable({ r }: { r: DbResult }) {
  if (r.kind === "change") return null;
  const cols = r.columns ?? [];
  const rows = r.rows ?? [];
  return (
    <div className="qp-result">
      <p className="sub">
        {r.count} row{r.count === 1 ? "" : "s"} · {r.connection} · {r.ms} ms
        {r.truncated ? " · more rows exist: add a WHERE or a LIMIT" : ""}
        {r.masked?.length ? ` · hidden: ${r.masked.join(", ")}` : ""}
      </p>
      {cols.length > 0 && (
        <div className="table-wrap qp-table">
          <table aria-label={`Rows from ${r.connection}`}>
            <thead>
              <tr>
                {cols.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {row.map((v, j) => (
                    <td key={j} className={v === null ? "qp-null" : undefined}>
                      {v === null ? "NULL" : String(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function QueryPanel({ pid }: { pid: string }) {
  const plugins = useLoad(
    pid ? `plugins:${pid}` : null,
    () => api.plugins(pid),
    { live: false },
  );
  const on = !!plugins.data?.find((p) => p.name === "db")?.enabled;
  const conns = useLoad(on ? `db:${pid}` : null, () => api.dbConnections(pid), {
    live: false,
  });
  const [conn, setConn] = useState("");
  const current =
    conns.data?.find((c) => c.name === conn) ??
    conns.data?.find((c) => c.env === "local") ??
    conns.data?.[0];
  const schema = useLoad(
    on && current ? `db-schema:${pid}:${current.name}` : null,
    () => api.dbSchema(pid, current!.name),
    { live: false },
  );
  const [sql, setSql] = useState("");
  const [find, setFind] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DbResult | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(
    null,
  );
  useEffect(() => {
    setResult(null);
    setErr(null);
  }, [current?.name]);
  if (!on) return null;
  if (conns.data && !conns.data.length) {
    return (
      <section className="panel qp" aria-label="Query">
        <div className="panel-body sub">
          No database connection yet: add one in Connections › Databases, then
          query it here.
        </div>
      </section>
    );
  }

  const run = async (text = sql, confirm = false) => {
    if (!current || !text.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      setResult(
        await api.dbQuery(pid, {
          connection: current.name,
          sql: text,
          change: current.can_change,
          confirm,
        }),
      );
    } catch (e) {
      setResult(null);
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const open = (table: string) => {
    const text = `SELECT * FROM ${table} LIMIT 20`;
    setSql(text);
    void run(text);
  };
  const tables = (schema.data?.tables ?? []).filter(
    (t) =>
      !find.trim() || t.name.toLowerCase().includes(find.trim().toLowerCase()),
  );

  return (
    <section className="panel qp" aria-label="Query">
      <div className="panel-head">
        <h2>Query</h2>
        <span className="sub">
          the live database
          {current
            ? ` · ${current.server ?? current.kind} · ${current.env}`
            : ""}
        </span>
        {(conns.data?.length ?? 0) > 1 && (
          <select
            aria-label="Database"
            value={current?.name ?? ""}
            onChange={(e) => setConn(e.target.value)}
            style={{ marginLeft: "auto" }}
          >
            {conns.data!.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} ({c.env})
              </option>
            ))}
          </select>
        )}
      </div>
      <div className="panel-body qp-body">
        <div className="qp-tables">
          <input
            type="search"
            className="inline-input"
            placeholder="Find a table"
            aria-label="Find a table"
            value={find}
            onChange={(e) => setFind(e.target.value)}
          />
          {schema.error ? (
            <p className="db-err">{schema.error.message}</p>
          ) : !schema.data ? (
            <span className="sub">Reading the tables…</span>
          ) : (
            <ul aria-label="Tables">
              {tables.map((t) => (
                <li key={t.name}>
                  <button
                    type="button"
                    className="qp-table-btn"
                    onClick={() => open(t.name)}
                    title={t.columns.map((c) => c.name).join(", ")}
                  >
                    {t.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="qp-main">
          <textarea
            className="qp-sql mono"
            id="qp-sql"
            rows={4}
            aria-label="SQL"
            spellCheck={false}
            value={sql}
            placeholder="SELECT * FROM scores ORDER BY created_at DESC LIMIT 20"
            onChange={(e) => setSql(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                void run();
              }
            }}
          />
          <div className="row">
            <button
              type="button"
              className="btn sm primary"
              disabled={busy || !sql.trim()}
              onClick={() => void run()}
            >
              {busy ? "Running…" : "Run"}
            </button>
            <span className="sub">
              {current?.can_change
                ? "A change of data is counted first; you press Run it."
                : "Read only on this database."}{" "}
              ⌘↵ runs.
            </span>
          </div>
          {err && <ErrorBox error={err} />}
          {result?.kind === "change" &&
            (result.done ? (
              <p className="qp-done" role="status">
                {result.changed} row{result.changed === 1 ? "" : "s"} changed in{" "}
                {result.connection}.
              </p>
            ) : (
              <div className="qp-confirm" role="group" aria-label="Change data">
                <span>
                  This changes{" "}
                  <b>
                    {result.changed} row{result.changed === 1 ? "" : "s"}
                  </b>{" "}
                  in {result.connection} ({result.env}). keel ran it once and
                  rolled it back to count.
                </span>
                <button
                  type="button"
                  className="btn sm primary"
                  disabled={busy}
                  onClick={() => void run(result.sql, true)}
                >
                  Run it ({result.changed} rows)
                </button>
                <button
                  type="button"
                  className="btn sm ghost"
                  onClick={() => setResult(null)}
                >
                  Cancel
                </button>
              </div>
            ))}
          {result && <ResultTable r={result} />}
        </div>
      </div>
    </section>
  );
}
