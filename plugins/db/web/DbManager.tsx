// Database connections, global or per project: the list on the left (grouped "All projects" and "<project> only", each
// with its database's logo and its environment), Problems at the bottom, and the editor of the chosen one on the right
// (DbEditor). Control › Databases shows every global connection and the chosen project's own; a project's Databases
// (Connections) shows what that project uses: its own and the globals for it. At a narrow width the list and the editor
// take turns. The Database plugin's part (plugins/db): it imports only react and @keel/web-sdk.

import { useEffect, useMemo, useRef, useState } from "react";
import { ErrorBox, errorParts, useApp, useLoad, useNarrow } from "@keel/web-sdk";
import { DbEditor } from "./DbEditor";
import {
  type DbDraft,
  type DbEntry,
  dbxApi,
  draftOf,
  envBadge,
  envTone,
  fieldsOf,
  groupEntries,
  newDraft,
  problems,
} from "./dbConfig";
import { DriverLogo, type Driver } from "./logos";

type Pick = { kind: "entry"; id: string } | { kind: "new"; draft: DbDraft } | null;

const GLOBE = <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></>;
const FOLDER = <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />;

export function DbManager({ pid, projectName, place, startNew, select, onChanged, onCancel }: {
  pid: string;
  projectName?: string;
  place: "control" | "project";
  /** open on a new connection draft (the Code page's popup) */
  startNew?: boolean;
  /** open with this connection chosen, by its name or id, as soon as the list has it */
  select?: string;
  /** after a save or a remove; `closed` is true when the editor said close (Save, not Apply) */
  onChanged?: (closed: boolean) => void;
  /** the editor's Cancel: given, it is called instead of going back to the list (a popup closes) */
  onCancel?: () => void;
}) {
  const { toast } = useApp();
  const narrow = useNarrow(760);
  const globals = useLoad(place === "control" ? "dbx:globals" : null, () => dbxApi.globals(), { live: false });
  const mine = useLoad(pid ? `dbx:${pid}` : null, () => dbxApi.effective(pid), { live: false });
  const projects = useLoad("dbx:projects", () => dbxApi.projects(), { live: false });
  const [filter, setFilter] = useState("");
  const [pick, setPick] = useState<Pick>(() =>
    startNew ? { kind: "new", draft: newDraft(pid ? "project" : "global") } : null);
  const selecting = useRef(startNew ? undefined : select);
  const [found, setFound] = useState<{ name: string; kind: string; url: string; shown: string; source: string }[] | null>(null);
  const [finding, setFinding] = useState(false);

  // Control: every global one (also those not for this project) and the project's own; a project: what it uses
  const all: DbEntry[] = useMemo(() => {
    const own = (mine.data ?? []).filter((c) => c.source === "project");
    if (place === "control") return [...(globals.data ?? []), ...own];
    return mine.data ?? [];
  }, [place, globals.data, mine.data]);
  useEffect(() => {
    const want = selecting.current;
    if (!want) return;
    const hit = all.find((c) => c.id === want) ?? all.find((c) => c.name === want);
    if (hit) {
      selecting.current = undefined;
      setPick({ kind: "entry", id: hit.id });
    }
  }, [all]);
  const name = pid ? projectName ?? pid : undefined;
  const groups = groupEntries(all, name, filter);
  const bad = problems(all);
  const chosen = pick?.kind === "entry" ? all.find((c) => c.id === pick.id) : undefined;
  const draft = useMemo(() => (pick?.kind === "new" ? pick.draft : chosen ? draftOf(chosen) : null), [pick, chosen]);
  const error = globals.error ?? mine.error;

  const reload = async () => {
    await Promise.all([place === "control" ? globals.reload() : Promise.resolve(), pid ? mine.reload() : Promise.resolve()]);
  };
  // keel 0.18.55: with a project chosen (also in Control › Databases), a new connection is that project's own unless the
  // person picks All projects
  const add = () => setPick({ kind: "new", draft: newDraft(pid ? "project" : "global") });
  const find = async () => {
    setFinding(true);
    try {
      setFound(await dbxApi.suggest(pid));
    } catch (e) {
      toast(errorParts(e).message);
    } finally {
      setFinding(false);
    }
  };
  const takeFound = (s: { name: string; kind: string; url: string; source: string }) => {
    setFound(null);
    setPick({ kind: "new", draft: newDraft("project", { ...fieldsOf(s.url), name: s.name.replace(/[^A-Za-z0-9 _.-]/g, "") || "local",
      import_url: s.url, found_in: s.source, sign_in: "password" }) });
  };
  const remove = async (c: DbEntry) => {
    try {
      if (c.source === "global") await dbxApi.removeGlobal(c.id);
      else await dbxApi.removeIn(c.project ?? pid, c.id);
      toast(`${c.name} removed.`);
      setPick(null);
      await reload();
      onChanged?.(false);
    } catch (e) {
      toast(errorParts(e).message);
    }
  };
  const hide = async (c: DbEntry, hidden: boolean) => {
    try {
      await dbxApi.hide(pid, c.id, hidden);
      toast(hidden ? `${c.name} is off in ${name}.` : `${c.name} is on again in ${name}.`);
      await reload();
    } catch (e) {
      toast(errorParts(e).message);
    }
  };

  const showList = !narrow || !pick;
  const showEditor = !narrow || !!pick;

  return (
    <div className={`dbx${narrow ? " narrow" : ""}`}>
      {showList && (
        <aside className="dbx-side" aria-label="Database connections">
          <div className="dbx-sidehead">
            <h2>Databases</h2>
            <div className="dbx-sideacts">
              <button type="button" className="btn sm primary" onClick={add}>+ Add</button>
              {pid && <button type="button" className="btn sm" onClick={() => void find()} disabled={finding}>
                {finding ? "Looking…" : "Find in project"}</button>}
            </div>
          </div>
          <div className="dbx-filter">
            <label className="sr-only" htmlFor="dbx-filter">Filter connections</label>
            <input id="dbx-filter" className="dbx-input" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          </div>
          {found && (
            <div className="dbx-found-list" role="group" aria-label="Databases keel found in the project">
              {found.length ? found.map((s) => (
                <div key={s.shown} className="dbx-found-row">
                  <span className="mono">{s.shown}</span>
                  <span className="sub">{s.source}</span>
                  <button type="button" className="btn sm" onClick={() => takeFound(s)}>Use it</button>
                </div>
              )) : <p className="sub">keel found no database in the project that is not here yet.</p>}
              <button type="button" className="btn sm ghost" onClick={() => setFound(null)}>Close</button>
            </div>
          )}
          {error && <ErrorBox error={error} onRetry={() => void reload()} />}
          <div className="dbx-groups">
            {groups.map((g) => (
              <section key={g.id} className="dbx-group" aria-label={`${g.title} · ${g.items.length}`}>
                <h3 className="dbx-grouphead">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                    {g.icon === "globe" ? GLOBE : FOLDER}
                  </svg>
                  {g.title} · {g.items.length}
                </h3>
                <ul>
                  {g.items.map((c) => (
                    <li key={c.id}>
                      <button type="button" className="dbx-item" aria-current={pick?.kind === "entry" && pick.id === c.id ? "true" : undefined}
                        aria-label={`${c.name}, ${c.driver}, ${c.env}${c.agent_access === "private" ? ", results only to you" : ""}`}
                        onClick={() => setPick({ kind: "entry", id: c.id })}>
                        <DriverLogo driver={c.driver as Driver} />
                        <span className="dbx-itemname">{c.name}</span>
                        {c.overridden && <span className="dbx-tag">overridden here</span>}
                        {c.hidden && <span className="dbx-tag">off here</span>}
                        {c.agent_access === "private" && (
                          <span className="dbx-tag" title="Agents run queries; the rows come only to you (#96)">results to you</span>
                        )}
                        {c.ok === false && <span className="dbx-dot bad" title="cannot connect" />}
                        <span className={`dbx-badge t-${envTone(c.env)}`}>{envBadge(c.env)}</span>
                      </button>
                    </li>
                  ))}
                  {!g.items.length && <li className="sub dbx-none">{g.id === "global" ? "None for every project yet." : "None of its own yet."}</li>}
                </ul>
              </section>
            ))}
          </div>
          <div className="dbx-problems" role="status">
            <span className={`dbx-count${bad.length ? " warn" : ""}`}>{bad.length}</span>
            <span>Problems</span>
            {bad[0] && <span className="sub">· {bad[0].name} not reachable{bad.length > 1 ? ` (+${bad.length - 1})` : ""}</span>}
          </div>
        </aside>
      )}
      {showEditor && (
        <section className="dbx-main" aria-label="Connection">
          {narrow && <button type="button" className="btn sm ghost dbx-back" onClick={() => setPick(null)}>← Back to the list</button>}
          {chosen?.source === "global" && pid && (
            <div className="dbx-shared">
              <span className="sub">{chosen.overridden ? `${name}'s own ${chosen.name} replaces this one there.`
                : chosen.projects.length ? "Shared with some projects." : "Shared with every project."}</span>
              {(place === "project" || chosen.hidden) && !chosen.overridden && (
                <button type="button" className="btn sm ghost" onClick={() => void hide(chosen, !chosen.hidden)}>
                  {chosen.hidden ? `Turn on in ${name}` : `Turn off in ${name}`}</button>
              )}
            </div>
          )}
          {draft ? (
            <DbEditor draft={draft} entry={chosen} pid={pid || undefined} projectName={name} projects={projects.data ?? []}
              onCancel={onCancel ?? (() => setPick(null))}
              onRemove={chosen ? () => void remove(chosen) : undefined}
              onSaved={(saved, close) => {
                void reload();
                setPick(close ? null : { kind: "entry", id: saved.id });
                onChanged?.(close);
              }} />
          ) : (
            <div className="dbx-empty">
              <p>Pick a connection on the left, or add one.</p>
              <p className="sub">A connection for all projects is set once and used everywhere; a project's own wins inside it.</p>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
