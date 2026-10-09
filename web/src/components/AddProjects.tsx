// v0.15.7 Add projects: the git repos inside the folders keel was given (KEEL_FOLDERS: the main project folder and the
// ones `keel2 add` mounted), grouped by folder, each with a checkbox. Search filters them, "Select all shown" ticks the
// ones shown, "Add N projects" adds them in one go (POST /api/projects/bulk) and says what was added and what was
// skipped, and why. "Browse…" walks a folder's tree to tick repos the list did not find; "Add by path" still takes any
// folder by hand.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  errorParts,
  type BulkAdded,
  type FolderWalk,
  type Folders,
  type FoundRepo,
} from "../api";
import { plural } from "../format";
import { useApp } from "../state";
import { Drawer, ErrorBox, Loading } from "./ui";

type Err = { message: string; hint?: string; details?: string[] };

/** The repo's path from its folder's name on ("work/clients/ios"): the group's header already shows the full folder. */
const shortPath = (r: FoundRepo) =>
  r.path.startsWith(r.root.replace(/\/+$/, "") + "/") ? `${lastPart(r.root)}/${r.path.slice(r.root.replace(/\/+$/, "").length + 1)}` : r.path;

/** A long folder path keeps its end, the part that tells folders apart: "…/demo/work". */
const shortRoot = (p: string) => (p.length > 44 ? `…/${p.replace(/\/+$/, "").split("/").slice(-2).join("/")}` : p);

const lastPart = (p: string) => p.replace(/\/+$/, "").split("/").pop() || p;

/** One folder's sub folders, with breadcrumbs from its root; repos have a checkbox, other folders open. */
function FolderWalker({
  start,
  picked,
  toggle,
  onBack,
  version,
}: {
  start: string;
  picked: Set<string>;
  toggle: (path: string) => void;
  onBack: () => void;
  /** goes up after an add, so the folder is read again (the new projects show "added") */
  version: number;
}) {
  const [path, setPath] = useState(start);
  const [walk, setWalk] = useState<FolderWalk | null>(null);
  const [err, setErr] = useState<Err | null>(null);
  useEffect(() => {
    let live = true;
    setErr(null);
    api.browseFolder(path).then(
      (w) => live && setWalk(w),
      (e) => live && setErr(errorParts(e)),
    );
    return () => {
      live = false;
    };
  }, [path, version]);
  // the crumbs: the root (its full path), then each folder below it
  const crumbs = useMemo(() => {
    if (!walk) return [];
    const rest = walk.path.slice(walk.root.length).split("/").filter(Boolean);
    return [
      { label: shortRoot(walk.root), path: walk.root },
      ...rest.map((name, i) => ({
        label: name,
        path: [walk.root, ...rest.slice(0, i + 1)]
          .join("/")
          .replace(/\/\/+/g, "/"),
      })),
    ];
  }, [walk]);
  return (
    <section className="ap-walk" aria-label="Browse folders">
      <div className="ap-walk-head">
        <button className="btn sm ghost" type="button" onClick={onBack}>
          ← Back to the list
        </button>
      </div>
      {err && <ErrorBox error={err} />}
      {!walk && !err && <Loading what="Reading the folder" />}
      {walk && (
        <>
          <nav className="ap-crumbs" aria-label="Folder path">
            {crumbs.map((c, i) => (
              <span key={c.path} className="ap-crumb">
                {i > 0 && (
                  <span className="ap-sep" aria-hidden="true">
                    /
                  </span>
                )}
                {i === crumbs.length - 1 ? (
                  <b className="mono" aria-current="location">
                    {c.label}
                  </b>
                ) : (
                  <button
                    type="button"
                    className="linkbtn mono"
                    onClick={() => setPath(c.path)}
                  >
                    {c.label}
                  </button>
                )}
              </span>
            ))}
          </nav>
          <ul className="ap-dirs" aria-label={`Folders in ${walk.path}`}>
            {walk.parent && (
              <li>
                <button
                  type="button"
                  className="ap-dir linkbtn"
                  onClick={() => setPath(walk.parent!)}
                >
                  ↑ ..
                </button>
              </li>
            )}
            {walk.dirs.map((d) =>
              d.repo ? (
                <li key={d.path}>
                  <label
                    className={`ap-repo${d.project_id ? " is-added" : ""}`}
                    title={d.path}
                  >
                    <input
                      type="checkbox"
                      aria-label={d.name}
                      checked={!!d.project_id || picked.has(d.path)}
                      disabled={!!d.project_id}
                      onChange={() => toggle(d.path)}
                    />
                    <span className="ap-name">{d.name}</span>
                    <span className="tag ap-mark" title="a git repo">
                      repo
                    </span>
                    {d.project_id && (
                      <span className="tag ap-added">added</span>
                    )}
                  </label>
                </li>
              ) : (
                <li key={d.path}>
                  <button
                    type="button"
                    className="ap-dir linkbtn"
                    onClick={() => setPath(d.path)}
                    aria-label={`Open ${d.name}`}
                  >
                    <span aria-hidden="true">▸ </span>
                    {d.name}/
                  </button>
                </li>
              ),
            )}
            {!walk.dirs.length && <li className="sub">No folders here.</li>}
          </ul>
          {walk.truncated && (
            <p className="hint">
              Only the first {walk.dirs.length} folders show.
            </p>
          )}
        </>
      )}
    </section>
  );
}

/** The manual way, as before: one folder by its path inside the container, with an optional name. */
function AddByPath({ onAdded }: { onAdded: () => void }) {
  const { reloadProjects, setProjectId, toast } = useApp();
  const [root, setRoot] = useState("/workspace/");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err | null>(null);
  const add = async () => {
    setBusy(true);
    setErr(null);
    try {
      const p = await api.addProject(root.trim(), name.trim() || undefined);
      await reloadProjects();
      setProjectId(p.id);
      toast(`${p.name} added.`);
      onAdded();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <details className="ap-bypath">
      <summary>Add by path</summary>
      <div className="field">
        <label htmlFor="ar-root">Folder inside the container</label>
        <input
          type="text"
          id="ar-root"
          value={root}
          onChange={(e) => setRoot(e.target.value)}
        />
        <span className="hint">
          A git repo keel can see, like{" "}
          <span className="mono">/workspace/my-repo</span>.
        </span>
      </div>
      <div className="field">
        <label htmlFor="ar-name">Name (optional)</label>
        <input
          type="text"
          id="ar-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="the folder name"
        />
      </div>
      {err && <ErrorBox error={err} />}
      <div>
        <button
          className="btn"
          type="button"
          onClick={add}
          disabled={busy || !root.trim()}
        >
          {busy ? "Adding…" : "Add repo"}
        </button>
      </div>
    </details>
  );
}

export function AddProjectsDrawer({ onClose }: { onClose: () => void }) {
  const { reloadProjects, toast, project, setProjectId } = useApp();
  const [data, setData] = useState<Folders | null>(null);
  const [version, setVersion] = useState(0);
  const [loadErr, setLoadErr] = useState<Err | null>(null);
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err | null>(null);
  const [result, setResult] = useState<BulkAdded | null>(null);
  const [walking, setWalking] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadErr(null);
    try {
      setData(await api.folders());
    } catch (e) {
      setLoadErr(errorParts(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const f = filter.trim().toLowerCase();
  const shown = (data?.repos ?? []).filter(
    (r) => !f || `${r.name} ${r.path}`.toLowerCase().includes(f),
  );
  const free = shown.filter((r) => !r.project_id);
  const toggle = (path: string) =>
    setPicked((s) => {
      const n = new Set(s);
      if (n.has(path)) n.delete(path);
      else n.add(path);
      return n;
    });
  const allShown = free.length > 0 && free.every((r) => picked.has(r.path));
  const selectShown = () =>
    setPicked((s) => {
      const n = new Set(s);
      free.forEach((r) => (allShown ? n.delete(r.path) : n.add(r.path)));
      return n;
    });
  const n = picked.size;
  const add = async () => {
    setBusy(true);
    setErr(null);
    setResult(null);
    try {
      const r = await api.addProjects([...picked]);
      // keep showing the same project: with none chosen yet, the first in the list (which may be a new one) would show
      if (project) setProjectId(project.id);
      setResult(r);
      setVersion((v) => v + 1);
      setPicked(new Set());
      if (r.added.length) toast(`${plural(r.added.length, "project")} added.`);
      await Promise.all([reloadProjects(), load()]);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };

  const roots = data?.roots ?? [];
  const mounted = roots.filter((r) => r.exists);
  const repoRow = (r: FoundRepo) => (
    <li key={r.path}>
      <label
        className={`ap-repo${r.project_id ? " is-added" : ""}`}
        title={r.path}
      >
        <input
          type="checkbox"
          aria-label={r.name}
          checked={!!r.project_id || picked.has(r.path)}
          disabled={!!r.project_id}
          onChange={() => toggle(r.path)}
        />
        <span className="ap-name">{r.name}</span>
        <span className="ap-path mono">{shortPath(r)}</span>
        {r.project_id && (
          <span
            className="tag ap-added"
            title={`already the project ${r.project_id}`}
          >
            added
          </span>
        )}
      </label>
    </li>
  );

  return (
    <Drawer
      title="Add projects"
      id="add-projects"
      onClose={onClose}
      footer={
        <>
          <button className="btn" type="button" onClick={onClose}>
            Close
          </button>
          <button
            className="btn primary"
            type="button"
            onClick={add}
            disabled={busy || !n}
          >
            {busy
              ? "Adding…"
              : n
                ? `Add ${plural(n, "project")}`
                : "Add projects"}
          </button>
        </>
      }
    >
      {result && (
        <div className="ap-result" role="status" aria-label="Result">
          {result.added.length ? (
            <p>
              <b className="ok">Added {result.added.length}:</b>{" "}
              {result.added.map((p) => p.name).join(", ")}
            </p>
          ) : (
            <p>
              <b>Nothing added.</b>
            </p>
          )}
          {!!result.skipped.length && (
            <>
              <p>
                <b className="amber">Skipped {result.skipped.length}:</b>
              </p>
              <ul className="ap-skipped">
                {result.skipped.map((s) => (
                  <li key={s.root}>
                    <span className="mono">{s.root}</span>{" "}
                    <span className="sub">— {s.why}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      {err && <ErrorBox error={err} />}
      {walking ? (
        <FolderWalker
          start={walking}
          picked={picked}
          toggle={toggle}
          onBack={() => setWalking(null)}
          version={version}
        />
      ) : loadErr ? (
        <ErrorBox error={loadErr} onRetry={() => void load()} />
      ) : !data ? (
        <Loading what="Looking for repos in your folders" />
      ) : !mounted.length ? (
        <p className="ap-empty">
          No folders yet. On your machine run{" "}
          <code className="mono">
            keel2 add &lt;folder&gt; [&lt;folder&gt;…]
          </code>{" "}
          and its repos show here.
        </p>
      ) : (
        <>
          <div className="ap-tools">
            <input
              type="search"
              className="ap-search"
              aria-label="Search repos"
              autoFocus
              placeholder="Search repos by name or path"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <button
              className="btn sm"
              type="button"
              onClick={selectShown}
              disabled={!free.length}
            >
              {allShown ? "Unselect the shown" : "Select all shown"}
            </button>
          </div>
          {data.truncated && (
            <p className="hint">
              keel stopped at {data.repos.length} repos. Search, or use Browse…
              to find the others.
            </p>
          )}
          {f && !shown.length && (
            <p className="sub ap-none">No repo matches “{filter.trim()}”.</p>
          )}
          {roots.map((root) => {
            const list = shown.filter((r) => r.root === root.path);
            const all = data.repos.filter((r) => r.root === root.path).length;
            // while searching, a folder with no match folds away
            if (f && !list.length) return null;
            return (
              <section
                key={root.path}
                className="ap-group"
                role="group"
                aria-label={root.path}
              >
                <header className="ap-gh">
                  <span className="mono ap-root" title={root.path}>
                    {shortRoot(root.path)}
                  </span>
                  <span className="sub">
                    {root.exists ? plural(all, "repo") : "not there"}
                  </span>
                  <button
                    className="btn sm ghost"
                    type="button"
                    disabled={!root.exists}
                    onClick={() => setWalking(root.path)}
                    aria-label={`Browse ${root.path}`}
                  >
                    Browse…
                  </button>
                </header>
                {list.length ? (
                  <ul className="ap-repos">{list.map(repoRow)}</ul>
                ) : (
                  root.exists && (
                    <p className="sub ap-none">
                      No git repo found here. Browse… to look deeper.
                    </p>
                  )
                )}
              </section>
            );
          })}
        </>
      )}
      <AddByPath onAdded={onClose} />
      {n > 0 && (
        <p className="hint ap-picked" aria-live="polite">
          {plural(n, "repo")} ticked: {[...picked].map(lastPart).join(", ")}
        </p>
      )}
    </Drawer>
  );
}
