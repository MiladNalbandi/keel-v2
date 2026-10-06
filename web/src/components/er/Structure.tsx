// The Structure tool window of the database diagram: the selected table's columns, keys, relations both ways,
// indexes and where the migrations define it (each a link to the file in the Repo page).

import type { Cite, DbRelation, DbTable } from "../../api";
import { hashFor } from "../../routes";
import { shortType } from "./geometry";
import { Ic } from "./icons";
import { indexedColumns, schemaLabel, type Neighbours } from "./model";

export const fileHref = (c: Cite) => hashFor("repo", `${c.rel}:${c.line}`);
const base = (rel: string) => rel.split("/").pop() ?? rel;

function CiteLink({ c, label }: { c: Cite | null | undefined; label?: string }) {
  if (!c) return null;
  return (
    <a className="st-cite mono" href={fileHref(c)} title={`Open ${c.rel} at line ${c.line} in the Repo page`}>
      {label ?? `${base(c.rel)}:${c.line}`}
    </a>
  );
}

function colIcon(t: DbTable, name: string, indexed: Set<string>) {
  const c = t.columns.find((x) => x.name === name);
  if (!c) return "col";
  return c.pk && c.fk ? "pkfk" : c.pk ? "key" : c.fk ? "fkey" : c.unique ? "unique" : indexed.has(c.name.toLowerCase()) ? "index" : "col";
}

const action = (r: { on_delete?: string | null; on_update?: string | null }) =>
  [r.on_delete && `on delete ${r.on_delete.toLowerCase()}`, r.on_update && `on update ${r.on_update.toLowerCase()}`].filter(Boolean).join(", ");

export function Structure({ t, rel, names, onPick, onClose, onFocusColumn }: {
  t: DbTable;
  rel: Neighbours | undefined;
  names: Map<string, DbTable>;
  onPick: (id: string) => void;
  onClose: () => void;
  onFocusColumn?: (table: string, column: string) => void;
}) {
  const indexed = indexedColumns(t);
  const schema = schemaLabel(t);
  const out = rel?.out ?? [], inc = (rel?.in ?? []).filter((r) => !r.self);
  const view = t.kind !== "table";
  const label = (id: string) => names.get(id)?.name ?? id;
  const relRow = (r: DbRelation, dir: "out" | "in") => {
    const other = dir === "out" ? r.to : r.from;
    const mine = dir === "out" ? r.from_columns : r.to_columns, theirs = dir === "out" ? r.to_columns : r.from_columns;
    return (
      <li key={r.id + dir}>
        <button type="button" className="st-rel" onClick={() => onPick(other)} title={r.name ? `${r.name}` : undefined}>
          <span className="st-rel-cols mono">{r.kind === "uses" ? "reads" : `(${mine.join(", ")})`}</span>
          <span className="st-arrow" aria-hidden="true">{dir === "out" ? "→" : "←"}</span>
          <b className="mono">{label(other)}</b>
          {r.kind !== "uses" && <span className="st-rel-cols mono">({theirs.join(", ")})</span>}
          {r.kind === "fk" && <span className="st-card">{r.one_to_one ? "1 : 1" : dir === "out" ? "n : 1" : "1 : n"}</span>}
        </button>
        {(action(r) || r.name) && <div className="st-sub">{[r.name, action(r)].filter(Boolean).join(" · ")}</div>}
      </li>
    );
  };
  return (
    <aside className="st" aria-label={`Structure of ${t.name}`}>
      <header className="st-head">
        <svg width="16" height="16" aria-hidden="true" className={`st-ico ${view ? "view" : ""}`}><use href={view ? "#erd-i-view" : "#erd-i-table"} /></svg>
        <div className="st-title">
          <h3>{t.name}</h3>
          <span className="st-sub">{[schema, t.kind, `${t.columns.length} column${t.columns.length === 1 ? "" : "s"}`].filter(Boolean).join(" · ")}</span>
        </div>
        <button type="button" className="dg-btn icon" aria-label="Close the structure" onClick={onClose}><Ic name="close" /></button>
      </header>
      <div className="st-body">
        {t.comment && <p className="st-comment">{t.comment}</p>}
        <section>
          <h4>Defined in</h4>
          {t.cite ? (
            <ul className="st-list">
              <li><CiteLink c={t.cite} label={t.cite.rel} /><span className="st-sub"> line {t.cite.line}</span></li>
              {(t.changes ?? []).slice(-6).map((c, i) => (
                <li key={i} className="st-change"><span>{c.what}</span> <CiteLink c={c} /></li>
              ))}
            </ul>
          ) : <p className="st-sub">Rebuild the map to see the migration file and line.</p>}
        </section>
        <section>
          <h4>Columns</h4>
          <table className="st-cols">
            <tbody>
              {t.columns.map((c) => (
                <tr key={c.name}>
                  <td className="st-ic"><svg width="13" height="13" aria-hidden="true" className={`erd-ic ${colIcon(t, c.name, indexed)}`}><use href={`#erd-i-${colIcon(t, c.name, indexed)}`} /></svg></td>
                  <td>
                    {c.fk && !c.fk.missing ? (
                      <button type="button" className="st-colname fk" onClick={() => onPick(c.fk!.table)} title={`References ${c.fk.table}${c.fk.column ? "." + c.fk.column : ""}`}>{c.name}</button>
                    ) : (
                      <button type="button" className="st-colname" title={c.name} onClick={() => onFocusColumn?.(t.id, c.name)}>{c.name}</button>
                    )}
                    {(c.fk || c.default || c.identity || c.generated || c.comment) && (
                      <div className="st-sub st-colsub">
                        {[c.fk ? `→ ${names.get(c.fk.table)?.name ?? c.fk.table}${c.fk.column ? "." + c.fk.column : ""}${c.fk.missing ? " (not in the migrations)" : ""}` : "",
                          c.default ? `default ${c.default}` : "", c.identity ? "identity" : "", c.generated ? `generated ${c.generated}` : "", c.comment ?? ""]
                          .filter(Boolean).join(" · ")}
                      </div>
                    )}
                  </td>
                  <td className="st-type mono" title={`${c.type}${c.nullable ? ", nullable" : ", not null"}`}>{shortType(c.type) || (view ? "" : "?")}{c.nullable && c.type ? <span className="erd-q-html">?</span> : null}</td>
                  <td className="st-line"><CiteLink c={c.cite} label={c.cite ? `:${c.cite.line}` : undefined} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        {!view && (
          <section>
            <h4>Keys</h4>
            <ul className="st-list mono">
              {t.primary_key ? <li><span className="st-kw">primary key</span> ({t.primary_key.columns.join(", ")}){t.primary_key.name ? <span className="st-sub"> {t.primary_key.name}</span> : null}</li>
                : <li className="st-sub">No primary key.</li>}
              {(t.uniques ?? []).map((u, i) => <li key={i}><span className="st-kw">unique</span> ({u.columns.join(", ")}){u.name ? <span className="st-sub"> {u.name}</span> : null}</li>)}
              {(t.foreign_keys ?? []).filter((f) => f.missing).map((f, i) => (
                <li key={`m${i}`}><span className="st-kw">foreign key</span> ({f.columns.join(", ")}) → {f.ref_name} <span className="st-sub">not in the migrations</span></li>
              ))}
              {t.checks ? <li className="st-sub">{t.checks} check constraint{t.checks === 1 ? "" : "s"}</li> : null}
            </ul>
          </section>
        )}
        <section>
          <h4>References <span className="st-count">{out.length}</span></h4>
          {out.length ? <ul className="st-list">{out.map((r) => relRow(r, "out"))}</ul> : <p className="st-sub">{view ? "Reads no table the migrations define." : "No foreign keys."}</p>}
        </section>
        {!view && (
          <section>
            <h4>Referenced by <span className="st-count">{inc.length}</span></h4>
            {inc.length ? <ul className="st-list">{inc.map((r) => relRow(r, "in"))}</ul> : <p className="st-sub">Nothing points here.</p>}
          </section>
        )}
        {!view && (
          <section>
            <h4>Indexes <span className="st-count">{(t.indexes ?? []).length}</span></h4>
            {(t.indexes ?? []).length ? (
              <ul className="st-list mono">
                {(t.indexes ?? []).map((ix, i) => (
                  <li key={i}>
                    {ix.unique && <span className="st-kw">unique </span>}{ix.name ?? "(unnamed)"} ({ix.columns.join(", ")})
                    {ix.method && <span className="st-sub"> using {ix.method}</span>}
                    {ix.where && <span className="st-sub"> where {ix.where}</span>}
                    {" "}<CiteLink c={ix.cite} label={ix.cite ? `:${ix.cite.line}` : undefined} />
                  </li>
                ))}
              </ul>
            ) : <p className="st-sub">No indexes besides the keys.</p>}
          </section>
        )}
        {view && t.definition && (
          <section>
            <h4>Definition</h4>
            <pre className="st-sql">{t.definition}</pre>
          </section>
        )}
      </div>
    </aside>
  );
}
