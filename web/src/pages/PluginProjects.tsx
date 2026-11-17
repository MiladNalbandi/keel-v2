// v0.17.1 a plugin that each project turns on (per_project: db, git, ci, review) is switched only in Settings › Plugins
// (docs/plugins/22-one-plugins-page.md); Tools › Plugins is gone. The pieces:
//
//   PlaceControl    one segmented control [ Off | This project | All projects ]: where the plugin is on
//   ProjectPlace    Installed's control for the project chosen now, with "on in 2 other projects" under it
//   PluginAdds      what the plugin adds (KeelBot tools, workflow blocks, commands, where it shows): its page's About
//   PluginProjects  its page's Projects section: every project with the same control
//
// They use the per-project api of v0.10.0: GET /api/projects/{pid}/plugins lists these plugins with enabled and scope
// (project: the project's own row, all: the every-project row "*"; the own row wins), and
// PUT /api/projects/{pid}/plugins/{name} {enabled, scope} sets one. A plugin loads for a project when it is on
// keel-wide (PUT /api/plugins/{name} {on}) and on for that project. The three choices (placeOf, usePlace):
//
//   shown          when                                             a click sends
//   Off            keel-wide off, or not enabled for the project    {enabled:false, scope:"project"}
//   This project   keel-wide on, enabled by the project's own row   {enabled:true, scope:"project"}
//                                                                   (the "*" row on: {enabled:false, scope:"all"} first)
//   All projects   keel-wide on, enabled by the "*" row             {enabled:true, scope:"all"} (drops the own row)
//
// This project and All projects also turn the plugin on keel-wide when it is off (it loads after the restart).
//
// v0.17.1 for a plugin keel has loaded, a project's choice applies at once: the api reads the project's plugins on every
// call (the plugin's pages and routes, each KeelBot turn, each flow it starts; a flow that runs already keeps the list it
// started with). Installed's row then says "applied" (docs/plugins/22-one-plugins-page.md §5).

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { api, errorParts, type Plugin } from "../api";
import { ErrorBox, Loading } from "../components/ui";
import { marketApi } from "../marketplaceApi";
import { queryValue } from "../routes";
import { useApp, useLoad, useRoute, type Loaded } from "../state";

const WHERE: Record<string, string> = {
  connections: "Connections",
  map: "Map › Query",
  code: "Code",
  workflows: "Workflows blocks",
  keelbot: "KeelBot",
  inbox: "Inbox",
  jobs: "Jobs",
  settings: "Settings",
};

/** Where a per-project plugin is on, seen from one project. */
export type Place = "off" | "project" | "all";
export const PLACES: Place[] = ["off", "project", "all"];
export const PLACE_WORDS: Record<Place, string> = {
  off: "Off",
  project: "This project",
  all: "All projects",
};

/** What really loads for the project: on keel-wide, and on for it (its own row, else every project's). */
export function placeOf(plugin: Plugin | undefined, keelOn: boolean): Place {
  if (!keelOn || !plugin?.enabled) return "off";
  return plugin.scope === "all" ? "all" : "project";
}

/** The per-project plugins of the project chosen now (null key: no project yet, nothing is asked). */
export function useProjectPlugins(pid: string | null): Loaded<Plugin[]> {
  return useLoad(pid ? `plugins:${pid}` : null, () => api.plugins(pid!), {
    live: false,
  });
}

/** The per-project plugins of every other project: for "on in 2 other projects". */
export function useOtherProjects(
  pid: string | null,
): Loaded<{ id: string; list: Plugin[] }[]> {
  const { projects, projectsLoaded } = useApp();
  const others = projects.filter((p) => p.id !== pid);
  const ids = others.map((p) => p.id).join(",");
  return useLoad(
    projectsLoaded ? `plugins-others:${pid ?? ""}:${ids}` : null,
    () =>
      // one project that cannot be read must not hide the rest: it counts as "not on" there
      Promise.allSettled(others.map((p) => api.plugins(p.id))).then((rs) =>
        others.map((p, i) => {
          const r = rs[i]!;
          return { id: p.id, list: r.status === "fulfilled" ? r.value : [] };
        }),
      ),
    { live: false },
  );
}

type Who = {
  name: string;
  title: string;
  enabled?: boolean;
  scope?: Plugin["scope"];
};

/** Move a plugin to Off, This project or All projects for one project (the table at the top says what is sent). */
function usePlace(keelOn: boolean, onKeelOn?: () => void) {
  const { toast, projects } = useApp();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(
    null,
  );
  const nameOf = (pid: string) =>
    projects.find((x) => x.id === pid)?.name ?? pid;
  const choose = async (
    pid: string,
    p: Who,
    to: Place,
  ): Promise<Plugin[] | null> => {
    setBusy(true);
    setErr(null);
    try {
      let after = "";
      if (to !== "off" && !keelOn) {
        await marketApi.setOn(p.name, true);
        onKeelOn?.();
        after = " It loads after the next restart.";
      }
      let list: Plugin[];
      if (to === "off") {
        list = await api.setPlugin(pid, p.name, false, "project");
        toast(`${p.title} is off for ${nameOf(pid)}.`);
      } else if (to === "project") {
        // only this project: the every-project row goes off first (it would keep the plugin on everywhere)
        const dropAll = p.enabled && p.scope === "all";
        if (dropAll) await api.setPlugin(pid, p.name, false, "all");
        try {
          list = await api.setPlugin(pid, p.name, true, "project");
        } catch (e) {
          // the two calls are not atomic: if the second fails, put the first back, or the plugin is off everywhere
          if (dropAll) await api.setPlugin(pid, p.name, true, "all").catch(() => undefined);
          throw e;
        }
        toast(`${p.title} is on for ${nameOf(pid)}.${after}`);
      } else {
        list = await api.setPlugin(pid, p.name, true, "all");
        toast(`${p.title} is on for all projects.${after}`);
      }
      return list;
    } catch (e) {
      setErr(errorParts(e));
      return null;
    } finally {
      setBusy(false);
    }
  };
  return { busy, err, choose };
}

/**
 * [ Off | This project | All projects ]: a radio group drawn as one segmented control. The arrow keys (and Home, End)
 * move the choice and apply it; only the chosen one is in the tab order (roving tabindex).
 */
export function PlaceControl({
  label,
  value,
  busy,
  onChange,
}: {
  label: string;
  value: Place;
  busy?: boolean;
  onChange: (to: Place) => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const pick = (to: Place) => {
    if (!busy && to !== value) onChange(to);
  };
  const key = (e: KeyboardEvent<HTMLButtonElement>, at: number) => {
    const n = PLACES.length;
    const to =
      e.key === "ArrowRight" || e.key === "ArrowDown"
        ? (at + 1) % n
        : e.key === "ArrowLeft" || e.key === "ArrowUp"
          ? (at + n - 1) % n
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? n - 1
              : -1;
    if (to < 0) return;
    e.preventDefault();
    refs.current[to]?.focus();
    pick(PLACES[to]);
  };
  return (
    <div
      role="radiogroup"
      aria-label={label}
      aria-busy={busy || undefined}
      className={busy ? "mk-seg busy" : "mk-seg"}
    >
      {PLACES.map((p, i) => (
        <button
          key={p}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="radio"
          aria-checked={p === value}
          tabIndex={p === value ? 0 : -1}
          className={p === value ? "mk-seg-opt on" : "mk-seg-opt"}
          onClick={() => pick(p)}
          onKeyDown={(e) => key(e, i)}
        >
          {PLACE_WORDS[p]}
        </button>
      ))}
    </div>
  );
}

/** "on in 2 other projects", a link to the plugin page's Projects list. */
function Elsewhere({
  name,
  shown,
  count,
}: {
  name: string;
  shown: Place;
  count: number;
}) {
  if (shown === "all" || count < 1) return null;
  return (
    <a
      className="sub mk-place-hint"
      href={`#/settings/plugins/${encodeURIComponent(name)}?at=projects`}
    >
      {shown === "off" ? "on" : "also on"} in {count} other project
      {count === 1 ? "" : "s"}
    </a>
  );
}

/** Installed: the control for the project chosen now, and where else the plugin is on. */
export function ProjectPlace({
  pid,
  plugin,
  loaded,
  keelOn,
  onKeelOn,
  loadedNow = false,
  onApplied,
}: {
  pid: string;
  plugin: Plugin;
  loaded: Loaded<Plugin[]>;
  /** the plugin is on keel-wide (it loads at all) */
  keelOn: boolean;
  onKeelOn: () => void;
  /** v0.17.1 keel has it loaded now: a project's choice applies at once (the api reads it on every call) */
  loadedNow?: boolean;
  /** after a choice: true when it applied at once, false when it waits for the restart (or failed) */
  onApplied?: (now: boolean) => void;
}) {
  const others = useOtherProjects(pid);
  const { busy, err, choose } = usePlace(keelOn, onKeelOn);
  const [want, setWant] = useState<Place | null>(null);
  const shown = want ?? placeOf(plugin, keelOn);
  const elsewhere = keelOn
    ? (others.data ?? []).filter(
        (o) => o.list.find((x) => x.name === plugin.name)?.enabled,
      ).length
    : 0;
  const change = async (to: Place) => {
    setWant(to);
    const list = await choose(pid, plugin, to);
    if (list) loaded.setData(list);
    onApplied?.(!!list && loadedNow);
    setWant(null);
    void others.reload();
  };
  return (
    <div className="mk-place">
      <PlaceControl
        label={`${plugin.title}: where it is on`}
        value={shown}
        busy={busy}
        onChange={(to) => void change(to)}
      />
      <Elsewhere name={plugin.name} shown={shown} count={elsewhere} />
      {err && <span className="badc sub">{err.message}</span>}
    </div>
  );
}

/** What a per-project plugin adds where people already work (what Tools › Plugins' card said). */
export function PluginAdds({ plugin }: { plugin: Plugin }) {
  return (
    <dl className="mk-kv" aria-label="Where it adds things">
      {!!plugin.tools?.read?.length && (
        <>
          <dt>KeelBot tools</dt>
          <dd className="mono">{plugin.tools.read.join(", ")}</dd>
        </>
      )}
      {!!plugin.actions?.length && (
        <>
          <dt>Workflow blocks</dt>
          <dd className="mono">
            {plugin.actions.map((a) => a.name).join(", ")}
          </dd>
        </>
      )}
      {!!plugin.commands?.length && (
        <>
          <dt>Commands</dt>
          <dd className="mono">
            {plugin.commands.map((c) => `/${c.name}`).join(" ")}
          </dd>
        </>
      )}
      {!!plugin.shows_in?.length && (
        <>
          <dt>Shows in</dt>
          <dd>{plugin.shows_in.map((w) => WHERE[w] ?? w).join(" · ")}</dd>
        </>
      )}
    </dl>
  );
}

type Row = { id: string; name: string; plugin: Plugin | undefined; error?: string };

/**
 * The plugin's page: every project with the same control as Installed. Nothing shows for a plugin no project
 * switches. #/settings/plugins/<name>?at=projects (Installed's "on in 2 other projects") scrolls here.
 */
export function PluginProjects({
  name,
  keelOn,
  onKeelOn,
}: {
  name: string;
  /** the plugin is on keel-wide (it loads at all) */
  keelOn: boolean;
  onKeelOn?: () => void;
}) {
  const { projects, projectsLoaded } = useApp();
  const route = useRoute();
  const here = useRef<HTMLElement | null>(null);
  const ids = projects.map((p) => p.id).join(",");
  const rows = useLoad<Row[]>(
    projectsLoaded ? `plugin-projects:${name}:${ids}` : null,
    () =>
      // one project that cannot be read shows its own error and does not hide the others
      Promise.allSettled(projects.map((p) => api.plugins(p.id))).then((rs) =>
        projects.map((p, i): Row => {
          const r = rs[i]!;
          return r.status === "fulfilled"
            ? { id: p.id, name: p.name, plugin: r.value.find((x) => x.name === name) }
            : { id: p.id, name: p.name, plugin: undefined, error: errorParts(r.reason).message };
        }),
      ),
    { live: false },
  );
  const { busy, err, choose } = usePlace(keelOn, onKeelOn);
  const [want, setWant] = useState<{ id: string; to: Place } | null>(null);
  const linked = queryValue(route, "at") === "projects";
  const ready = !!rows.data;
  useEffect(() => {
    if (linked && ready) here.current?.scrollIntoView?.({ block: "start" });
  }, [linked, ready]);
  if (rows.data && !rows.data.some((r) => r.plugin || r.error)) return null;
  const title = rows.data?.find((r) => r.plugin)?.plugin?.title ?? name;
  const change = async (r: Row, to: Place) => {
    setWant({ id: r.id, to });
    await choose(r.id, { ...r.plugin, name, title }, to);
    await rows.reload();
    setWant(null);
  };
  return (
    <section
      className="panel"
      aria-labelledby="mk-projects"
      id="projects"
      ref={here}
    >
      <div className="panel-head">
        <h2 id="mk-projects">Projects</h2>
      </div>
      <div className="panel-body">
        <p className="sub" style={{ margin: "0 0 10px" }}>
          For each project: Off, This project (on only there) or All projects
          (on for every project).
          {!keelOn && ` ${title} is off in keel now: it loads nowhere.`}
        </p>
        {rows.error ? (
          <ErrorBox error={rows.error} onRetry={() => void rows.reload()} />
        ) : !rows.data ? (
          <Loading what="Reading the projects" />
        ) : !rows.data.length ? (
          <p className="sub" style={{ margin: 0 }}>
            No project yet.
          </p>
        ) : (
          <ul className="mk-projlist" aria-label={`Projects with ${title}`}>
            {rows.data.map((r) => (
              <li key={r.id}>
                <b>{r.name}</b>
                {r.plugin ? (
                  <PlaceControl
                    label={`${title} in ${r.name}: where it is on`}
                    value={
                      want?.id === r.id ? want.to : placeOf(r.plugin, keelOn)
                    }
                    busy={busy}
                    onChange={(to) => void change(r, to)}
                  />
                ) : r.error ? (
                  <span className="badc sub" role="alert">Could not read this project: {r.error}</span>
                ) : (
                  <span className="sub">not offered here</span>
                )}
              </li>
            ))}
          </ul>
        )}
        {err && <ErrorBox error={err} />}
      </div>
    </section>
  );
}
