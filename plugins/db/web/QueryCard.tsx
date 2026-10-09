// v0.10.0 KeelBot's query button (the Database plugin's card for ```keel-query {json}``` blocks, in KeelBot's slot
// keelbot.card): Run reads at once; a change is counted first (a run keel rolls back), then Run it changes the data, on
// a local or test database only. Moved from KeelBot's Actions.tsx with the plugin (plugins/db); the same card as keel
// 0.15.1. It imports only react and @keel/web-sdk.

import { useState } from "react";
import { CodeBlock, errorParts, type KeelbotCardProps } from "@keel/web-sdk";
import { dbApi, type DbResult } from "./dbApi";
import { ResultTable } from "./QueryPanel";

function parseJson<T>(body: string): T | null {
  try {
    const v = JSON.parse(body);
    return v && typeof v === "object" ? (v as T) : null;
  } catch {
    return null;
  }
}

/** A query KeelBot gives as a button (Database plugin): Run reads at once; a change is counted first (a run keel rolls
 *  back), then Run it changes the data, on a local or test database only. */
export function QueryCard({ pid, block }: KeelbotCardProps) {
  const spec = parseJson<{ sql?: string; connection?: string }>(block.body);
  const [busy, setBusy] = useState(false);
  const [r, setR] = useState<DbResult | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(
    null,
  );
  if (!spec?.sql)
    return (
      <p className="kb-card bad" role="note">
        KeelBot's query could not be read. Ask it to give it again.
      </p>
    );
  const run = async (confirm = false) => {
    setBusy(true);
    setErr(null);
    try {
      setR(
        await dbApi.query(pid, {
          connection: spec.connection ?? "",
          sql: spec.sql!,
          change: true,
          confirm,
        }),
      );
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      className="kb-card"
      aria-label={`Query on ${spec.connection || "the local database"}`}
    >
      <div className="kb-card-h">
        <span className="kb-tag">Query</span>
        <b>{spec.connection || "local"}</b>
      </div>
      <CodeBlock text={spec.sql} lang="sql" gutter={false} />
      {err && (
        <p className="kb-err" role="alert">
          <b>{err.message}</b>
          {err.hint && <span className="sub"> {err.hint}</span>}
        </p>
      )}
      {r?.kind === "change" && !r.done && (
        <div className="row" role="group" aria-label="Change data">
          <span>
            This changes{" "}
            <b>
              {r.changed} row{r.changed === 1 ? "" : "s"}
            </b>{" "}
            in {r.connection}.
          </span>
          <button
            type="button"
            className="btn sm primary"
            disabled={busy}
            onClick={() => void run(true)}
          >
            Run it ({r.changed} rows)
          </button>
          <button
            type="button"
            className="btn sm ghost"
            onClick={() => setR(null)}
          >
            Cancel
          </button>
        </div>
      )}
      {r?.kind === "change" && r.done && (
        <p className="kb-done" role="status">
          {r.changed} row{r.changed === 1 ? "" : "s"} changed in {r.connection}.
        </p>
      )}
      {r?.kind === "read" && <ResultTable r={r} />}
      {!r && (
        <div className="row">
          <button
            type="button"
            className="btn sm primary"
            disabled={busy}
            onClick={() => void run()}
          >
            {busy ? "Running…" : "Run"}
          </button>
          <span className="sub">
            A change of data is counted first; then you decide.
          </span>
        </div>
      )}
    </section>
  );
}
