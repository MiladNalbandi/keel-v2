// Control › Plugins (v0.16.0, docs/plugins/13-step4-contract.md §9): what this keel has installed, the marketplace,
// and where plugins come from. Three tabs:
//
//   Installed           each plugin: version, parts, from (image / marketplace / file), on/off, status and problems,
//                       a revoked warning, Update, Roll back, Remove; a banner when changes wait for a restart
//   Marketplace         search, categories, cards with the trust level; a plugin's details (permissions with their
//                       levels, the plugins it needs, versions, the checks keel runs); Install opens a dialog that
//                       lists everything that will be installed and what each one may do
//   Sources and rules   the catalogs (add one with its URL and public key, refresh, problems), the four rules, and
//                       install from a file in /data
//
// #/plugins?set=developer (the core-only menu's "Start with a set") offers to install what that set adds: the missing
// plugins, and the image's plugins that are off are turned on.

import { useEffect, useMemo, useState } from "react";
import { errorParts } from "../api";
import { Toolbar, SearchBox } from "../components/page";
import { PermissionList, TrustTag } from "../components/Permissions";
import {
  Confirm,
  Drawer,
  ErrorBox,
  Loading,
  PageHead,
  Panel,
  Pill,
  Tabs,
} from "../components/ui";
import {
  CATEGORIES,
  FROM,
  TRUST,
  hitsOf,
  installedVersion,
  marketApi,
  needsOf,
  newestVersion,
  pendingOf,
  permissionsOf,
  planOf,
  publisherOf,
  turnOnOf,
  revokedText,
  setsOf,
  sourceStatusOf,
  sourcesOf,
  updateOf,
  type InstalledPlugin,
  type MarketHit,
  type MarketPlugin,
  type Permissions,
  type PluginsView,
  type Rules,
  type Source,
} from "../marketplaceApi";
import { queryValue } from "../routes";
import type { PageProps } from "../sdk/registry";
import { useApp, useLoad, useRoute, type Loaded } from "../state";

type Tab = "installed" | "market" | "sources";
type Err = { message: string; hint?: string; details?: string[] } | null;

/** What the install dialog does: install these (and what they need), then turn these on. */
type InstallPlan = { install: string[]; turnOn?: string[]; title?: string };

export function PluginsPage(_props: PageProps) {
  const route = useRoute();
  const setId = queryValue(route, "set");
  const view = useLoad("plugins", () => marketApi.installed());
  const [tab, setTab] = useState<Tab>("installed");
  const [detail, setDetail] = useState<string | null>(null);
  const [plan, setPlan] = useState<InstallPlan | null>(null);
  const count = view.data?.plugins.length;
  return (
    <>
      <PageHead
        title="Plugins"
        sub="Plugins add pages, tools, workflows and agents to keel. keel checks a plugin's signature before it installs it, and loads it when it starts again."
      />
      {view.data && (
        <RestartBanner view={view.data} onDone={() => void view.reload()} />
      )}
      {setId && (
        <SetOffer
          id={setId}
          view={view.data}
          onInstall={(p) => setPlan(p)}
          onClose={() => {
            location.hash = "#/plugins";
          }}
        />
      )}
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        label="Plugins"
        options={[
          [
            "installed",
            count === undefined ? "Installed" : `Installed (${count})`,
          ],
          ["market", "Marketplace"],
          ["sources", "Sources and rules"],
        ]}
      />
      <div className="mk-tab">
        {tab === "installed" && <InstalledTab view={view} />}
        {tab === "market" && (
          <MarketTab
            view={view.data}
            onDetail={setDetail}
            onInstall={(name) => setPlan({ install: [name] })}
            onChanged={() => void view.reload()}
          />
        )}
        {tab === "sources" && (
          <SourcesTab onChanged={() => void view.reload()} />
        )}
      </div>
      {detail && (
        <PluginDetail
          name={detail}
          view={view.data}
          onClose={() => setDetail(null)}
          onInstall={(name) => {
            setDetail(null);
            setPlan({ install: [name] });
          }}
        />
      )}
      {plan && (
        <InstallDialog
          plan={plan}
          view={view.data}
          onClose={() => setPlan(null)}
          onDone={() => {
            void view.reload();
            if (setId) location.hash = "#/plugins";
          }}
        />
      )}
    </>
  );
}

// ---------- the restart banner ----------

function RestartBanner({
  view,
  onDone,
}: {
  view: PluginsView;
  onDone: () => void;
}) {
  const { toast } = useApp();
  const r = view.restart;
  const [busy, setBusy] = useState(false);
  const [ask, setAsk] = useState(false);
  const [gone, setGone] = useState(false);
  const [err, setErr] = useState<Err>(null);
  if (!r || (!r.pending && !r.scheduled)) return null;
  // keel-start does not run this keel (a dev run): it cannot restart itself
  const manual = r.supervised === false;
  // what loads after the restart: db 1.3.0 → 1.4.0, hello new, map gone
  const changes = (pendingOf(view)?.changes ?? []).map((c) =>
    c.now && c.next
      ? `${c.name} ${c.now} → ${c.next}`
      : c.next
        ? `${c.name} ${c.next} (new)`
        : `${c.name} (goes)`,
  );
  const restart = async (now: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      const a = await marketApi.restart(now);
      if (a.restarting) {
        setGone(true);
        toast("keel restarts now. This page comes back in about 30 seconds.");
      } else toast("keel restarts by itself when no agent step runs.");
      onDone();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
      setAsk(false);
    }
  };
  return (
    <div className="mk-banner" role="status">
      <div className="mk-banner-t">
        <b>{gone ? "keel is restarting…" : "Changes wait for a restart"}</b>
        <span className="sub">
          {gone
            ? "It loads the plugins again. This page comes back in about 30 seconds."
            : manual
              ? "keel loads new and changed plugins when it starts again. keel-start does not run this keel: restart it by hand with keel2 restart."
              : r.scheduled
                ? `keel restarts by itself when no agent step runs${r.running ? ` (${r.running} running now)` : ""}.`
                : "keel loads new and changed plugins when it starts again. It takes about 30 seconds."}
        </span>
        {!gone && changes.length > 0 && (
          <span className="sub mono" aria-label="What changes">
            {changes.join(" · ")}
          </span>
        )}
      </div>
      {!gone &&
        !manual &&
        (ask ? (
          <Confirm
            text={`${r.running} agent step${r.running === 1 ? "" : "s"} run now. They stop, and go on after the restart.`}
            yes="Restart anyway"
            busy={busy}
            onYes={() => void restart(true)}
            onNo={() => setAsk(false)}
          />
        ) : (
          <div className="row">
            <button
              className="btn warn"
              type="button"
              disabled={busy}
              onClick={() =>
                r.running > 0 ? setAsk(true) : void restart(true)
              }
            >
              Restart keel
            </button>
            {!r.scheduled && (
              <button
                className="btn"
                type="button"
                disabled={busy}
                onClick={() => void restart(false)}
              >
                Restart when no agent runs
              </button>
            )}
          </div>
        ))}
      {err && <ErrorBox error={err} />}
    </div>
  );
}

// ---------- Start with a set ----------

function SetOffer({
  id,
  view,
  onInstall,
  onClose,
}: {
  id: string;
  view: PluginsView | null;
  onInstall: (p: InstallPlan) => void;
  onClose: () => void;
}) {
  const sets = useLoad("plugin-sets", () => marketApi.sets(), { live: false });
  const set = setsOf(sets.data).find((s) => s.id === id);
  const have = new Map((view?.plugins ?? []).map((p) => [p.name, p]));
  const missing = set ? set.plugins.filter((n) => !have.has(n)) : [];
  const off = set ? set.plugins.filter((n) => have.get(n)?.on === false) : [];
  const title = set?.title ?? id;
  return (
    <Panel
      className="mk-set"
      title={<h2>Start with the {title} set</h2>}
      extra={
        <button className="btn sm ghost" type="button" onClick={onClose}>
          Not now
        </button>
      }
    >
      {sets.error && !sets.data ? (
        <ErrorBox error={sets.error} onRetry={() => void sets.reload()} />
      ) : !sets.data || !view ? (
        <Loading what="Reading the set" />
      ) : !set ? (
        <p className="sub">There is no set called {id}.</p>
      ) : (
        <div className="grid mk-set-body">
          {set.summary && (
            <p className="sub" style={{ margin: 0 }}>
              {set.summary}
            </p>
          )}
          <ul className="mk-set-list" aria-label={`The ${title} set`}>
            {set.plugins.map((n) => {
              const p = have.get(n);
              return (
                <li key={n}>
                  <b>{p?.title ?? n}</b>{" "}
                  {!p ? (
                    <Pill tone="warn">will be installed</Pill>
                  ) : p.on === false ? (
                    <Pill tone="idle">off: turns on</Pill>
                  ) : (
                    <Pill tone="ok">here</Pill>
                  )}
                </li>
              );
            })}
          </ul>
          {missing.length + off.length === 0 ? (
            <p className="sub" style={{ margin: 0 }}>
              Everything in this set is here already.
            </p>
          ) : (
            <div className="row">
              <button
                className="btn primary"
                type="button"
                onClick={() =>
                  onInstall({
                    install: missing,
                    turnOn: off,
                    title: `Install the ${title} set?`,
                  })
                }
              >
                {missing.length ? `Install ${missing.length}` : ""}
                {missing.length && off.length ? " and " : ""}
                {off.length ? `turn on ${off.length}` : ""}
              </button>
              <span className="sub">Then keel restarts once.</span>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

// ---------- Installed ----------

/** A plugin's status in words: loaded (runs now), restart (loads at the next start), off, left out, removed. */
function StatusOf({ p, problems }: { p: InstalledPlugin; problems: boolean }) {
  const s = p.status ?? "";
  if (s === "left out" || (problems && s !== "restart"))
    return <Pill tone="bad">left out</Pill>;
  if (s === "restart")
    return (
      <Pill tone="warn">
        {p.on === false ? "off after restart" : "after restart"}
      </Pill>
    );
  if (s === "removed") return <Pill tone="warn">removed at restart</Pill>;
  if (s === "off" || p.on === false) return <Pill tone="idle">off</Pill>;
  return <Pill tone="ok">on</Pill>;
}

function InstalledTab({ view }: { view: Loaded<PluginsView> }) {
  const { toast } = useApp();
  const [busy, setBusy] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [dropData, setDropData] = useState(false);
  const [err, setErr] = useState<Err>(null);
  if (view.error && !view.data)
    return <ErrorBox error={view.error} onRetry={() => void view.reload()} />;
  if (!view.data) return <Loading what="Reading the installed plugins" />;
  const v = view.data;
  const act = async (
    name: string,
    run: () => Promise<unknown>,
    done: string,
  ) => {
    setBusy(name);
    setErr(null);
    try {
      await run();
      toast(done);
      await view.reload();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(null);
    }
  };
  const titleOf = (p: InstalledPlugin) => p.title || p.name;
  return (
    <div className="grid">
      {v.engine === false && (
        <p className="sub">
          The engine does not answer: this is what keel loaded when it started.
        </p>
      )}
      {err && <ErrorBox error={err} />}
      {!v.plugins.length ? (
        <Panel>
          <p className="sub" style={{ margin: 0 }}>
            No plugins yet. Find one in the Marketplace tab.
          </p>
        </Panel>
      ) : (
        <Panel body={false}>
          <div className="table-wrap">
            <table className="mk-table">
              <thead>
                <tr>
                  <th>Plugin</th>
                  <th>Version</th>
                  <th>Parts</th>
                  <th>From</th>
                  <th>Status</th>
                  <th>On</th>
                  <th>
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {v.plugins.map((p) => {
                  const upd = updateOf(p);
                  const revoked = revokedText(p.revoked);
                  const from = p.from ?? p.source ?? "";
                  const problems = (p.problems ?? [])
                    .map((x) => (typeof x === "string" ? x : (x.error ?? "")))
                    .filter(Boolean);
                  return (
                    <tr
                      key={p.name}
                      className={p.on === false ? "off" : ""}
                      data-testid={`plugin-${p.name}`}
                    >
                      <td>
                        <b>{titleOf(p)}</b>
                        <div className="sub mono">{p.name}</div>
                      </td>
                      <td className="mono">
                        {p.version}
                        {upd && (
                          <div>
                            <span className="tag keel">{upd} ready</span>
                          </div>
                        )}
                      </td>
                      <td>
                        <div className="mk-parts">
                          {(p.parts ?? []).map((x) => (
                            <span key={x} className="tag">
                              {x}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="sub">{FROM[from] ?? from}</td>
                      <td>
                        {revoked ? (
                          <Pill tone="bad">revoked</Pill>
                        ) : (
                          <StatusOf p={p} problems={problems.length > 0} />
                        )}
                        {revoked && (
                          <div className="mk-warn">
                            This version was revoked: {revoked}
                          </div>
                        )}
                        {problems.map((x, i) => (
                          <div key={i} className="sub">
                            {x}
                          </div>
                        ))}
                        {!!p.needed_by?.length && (
                          <div className="sub">
                            {p.needed_by.join(", ")} need
                            {p.needed_by.length === 1 ? "s" : ""} it
                          </div>
                        )}
                      </td>
                      <td>
                        <input
                          type="checkbox"
                          role="switch"
                          aria-label={`${titleOf(p)} on`}
                          checked={p.on !== false}
                          disabled={busy === p.name}
                          onChange={(e) =>
                            void act(
                              p.name,
                              () => marketApi.setOn(p.name, e.target.checked),
                              `${titleOf(p)} is ${e.target.checked ? "on" : "off"} after the next restart.`,
                            )
                          }
                        />
                      </td>
                      <td>
                        <div className="row mk-rowact">
                          {upd && (
                            <button
                              className="btn sm"
                              type="button"
                              disabled={busy === p.name}
                              onClick={() =>
                                void act(
                                  p.name,
                                  () => marketApi.update(p.name),
                                  `${titleOf(p)} is updated. Restart keel to use it.`,
                                )
                              }
                            >
                              Update
                            </button>
                          )}
                          {p.previous && (
                            <button
                              className="btn sm ghost"
                              type="button"
                              disabled={busy === p.name}
                              onClick={() =>
                                void act(
                                  p.name,
                                  () => marketApi.rollback(p.name),
                                  `${titleOf(p)} goes back to ${p.previous} after the restart.`,
                                )
                              }
                            >
                              Roll back
                            </button>
                          )}
                          {(p.can_remove ?? from !== "image") && (
                            <button
                              className="btn sm ghost"
                              type="button"
                              disabled={busy === p.name}
                              onClick={() => {
                                setRemoving(p.name);
                                setDropData(false);
                              }}
                            >
                              Remove
                            </button>
                          )}
                        </div>
                        {removing === p.name && (
                          <Confirm
                            busy={busy === p.name}
                            yes="Remove"
                            text={
                              <>
                                Remove {titleOf(p)}? Its tables in keel's
                                database stay.
                                <label className="chk">
                                  <input
                                    type="checkbox"
                                    checked={dropData}
                                    onChange={(e) =>
                                      setDropData(e.target.checked)
                                    }
                                  />{" "}
                                  Also delete its files in /data
                                </label>
                              </>
                            }
                            onNo={() => setRemoving(null)}
                            onYes={() =>
                              void act(
                                p.name,
                                () =>
                                  marketApi.remove(
                                    p.name,
                                    dropData ? "delete" : "keep",
                                  ),
                                `${titleOf(p)} is removed after the restart.`,
                              ).then(() => setRemoving(null))
                            }
                          />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
      {v.problems.length > 0 && (
        <Panel title="Left out at start">
          <ul className="mk-problems">
            {v.problems.map((x, i) => (
              <li key={i}>
                <b>{x.name}</b>
                {x.version ? (
                  <span className="mono sub"> {x.version}</span>
                ) : null}
                : {x.error}
              </li>
            ))}
          </ul>
        </Panel>
      )}
      <p className="sub">
        Data stays when you turn a plugin off. A plugin that came in keel's
        image can be turned off, not removed.
      </p>
    </div>
  );
}

// ---------- Marketplace ----------

function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

function MarketTab({
  view,
  onDetail,
  onInstall,
  onChanged,
}: {
  view: PluginsView | null;
  onDetail: (name: string) => void;
  onInstall: (name: string) => void;
  onChanged: () => void;
}) {
  const { toast } = useApp();
  const [typed, setTyped] = useState("");
  const q = useDebounced(typed.trim());
  const [cat, setCat] = useState("");
  const r = useLoad(`market:${cat}:${q}`, () => marketApi.search(q, cat));
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<Err>(null);
  const hits = hitsOf(r.data);
  const statuses = sourceStatusOf(r.data);
  const have = useMemo(
    () => new Map((view?.plugins ?? []).map((p) => [p.name, p])),
    [view],
  );
  const update = async (h: MarketHit, to: string) => {
    setBusy(h.name);
    setErr(null);
    try {
      await marketApi.update(h.name, to);
      toast(`${h.title ?? h.name} is updated. Restart keel to use it.`);
      onChanged();
      await r.reload();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="grid">
      <Toolbar label="Search the marketplace">
        <SearchBox
          value={typed}
          onChange={setTyped}
          label="Search plugins: database, review, tickets…"
        />
        <div className="chips" role="group" aria-label="Category">
          {CATEGORIES.map(([id, label]) => (
            <button
              key={id || "all"}
              className="chip"
              type="button"
              aria-pressed={cat === id}
              onClick={() => setCat(id)}
            >
              {label}
            </button>
          ))}
        </div>
      </Toolbar>
      <div className="legend mk-legend">
        {Object.keys(TRUST).map((t) => (
          <TrustTag key={t} trust={t} />
        ))}
        <span className="sub">· what a plugin may bring</span>
      </div>
      {statuses
        .filter((s) => s.old || s.problem || s.ok === false)
        .map((s) => (
          <p key={s.id} className="mk-note" role="note">
            <b>{s.id}</b>:{" "}
            {s.old
              ? "the catalog is old: search works, installs wait until a refresh works. "
              : ""}
            {s.problem ?? ""}
          </p>
        ))}
      {err && <ErrorBox error={err} />}
      {r.error && !r.data ? (
        <ErrorBox error={r.error} onRetry={() => void r.reload()} />
      ) : !r.data ? (
        <Loading what="Searching the catalogs" />
      ) : !hits.length ? (
        <Panel>
          <p className="sub" style={{ margin: 0 }}>
            {typed.trim()
              ? `No plugin matches “${typed.trim()}”. Try another word.`
              : "The catalogs list no plugins yet."}
          </p>
        </Panel>
      ) : (
        <div className="mk-cards">
          {hits.map((h) => {
            const pub = publisherOf(h);
            const mine = have.get(h.name);
            const inst =
              installedVersion(h) ?? mine?.version ?? (mine ? "" : null);
            const upd = updateOf(h);
            const needs = needsOf(h);
            const revoked = revokedText(h.revoked);
            const title = h.title ?? h.name;
            return (
              <article
                key={h.name}
                className="mk-card"
                aria-label={`${title} plugin`}
              >
                <div className="mk-card-h">
                  <h3>{title}</h3>
                  <span className="sub">
                    {pub.name}
                    {pub.verified ? (
                      <span className="tick" title="Verified publisher">
                        {" "}
                        ✓
                      </span>
                    ) : pub.name ? (
                      <span className="tag">community</span>
                    ) : null}
                    {(h.version ?? h.latest) && (
                      <span className="mono"> · {h.version ?? h.latest}</span>
                    )}
                  </span>
                </div>
                {h.summary && <p className="mk-sum">{h.summary}</p>}
                <div className="row">
                  <TrustTag trust={h.trust} />
                  {needs.length > 0 && (
                    <span className="tag">needs {needs.join(", ")}</span>
                  )}
                  {h.fits === false && (
                    <Pill tone="warn" title={h.why_not ?? undefined}>
                      does not fit this keel
                    </Pill>
                  )}
                  {revoked && <Pill tone="bad">revoked</Pill>}
                </div>
                {h.fits === false && h.why_not && (
                  <p className="sub mk-sum">{h.why_not}</p>
                )}
                <div className="row mk-card-a">
                  <button
                    className="btn sm ghost"
                    type="button"
                    onClick={() => onDetail(h.name)}
                    aria-label={`Details of ${title}`}
                  >
                    Details
                  </button>
                  {inst !== null ? (
                    upd ? (
                      <button
                        className="btn sm"
                        type="button"
                        disabled={busy === h.name}
                        onClick={() =>
                          void update(
                            h,
                            typeof h.update === "string" ? h.update : "",
                          )
                        }
                      >
                        Update to {upd}
                      </button>
                    ) : (
                      <Pill tone="ok">Installed{inst ? ` ${inst}` : ""}</Pill>
                    )
                  ) : (
                    <button
                      className="btn sm primary"
                      type="button"
                      disabled={h.fits === false}
                      onClick={() => onInstall(h.name)}
                      aria-label={`Install ${title}`}
                    >
                      Install
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** A plugin's details: what it does, its permissions with their levels, the plugins it needs, versions, checks. */
function PluginDetail({
  name,
  view,
  onClose,
  onInstall,
}: {
  name: string;
  view: PluginsView | null;
  onClose: () => void;
  onInstall: (name: string) => void;
}) {
  const r = useLoad(`market-plugin:${name}`, () => marketApi.plugin(name), {
    live: false,
  });
  const p = r.data;
  const mine = view?.plugins.find((x) => x.name === name);
  const inst = p
    ? (installedVersion(p) ?? mine?.version ?? (mine ? "" : null))
    : null;
  const pub = p ? publisherOf(p) : null;
  const version = p ? newestVersion(p) : undefined;
  return (
    <Drawer
      title={p?.title ?? name}
      onClose={onClose}
      id="plugin-detail"
      footer={
        p ? (
          inst !== null ? (
            <Pill tone="ok">Installed{inst ? ` ${inst}` : ""}</Pill>
          ) : (
            <button
              className="btn primary"
              type="button"
              disabled={!!p.refused}
              onClick={() => onInstall(name)}
            >
              Install{version ? ` ${version}` : ""}
            </button>
          )
        ) : undefined
      }
    >
      {r.error && !p ? (
        <ErrorBox error={r.error} onRetry={() => void r.reload()} />
      ) : !p ? (
        <Loading what="Reading the plugin" />
      ) : (
        <>
          {p.summary && <p style={{ margin: 0 }}>{p.summary}</p>}
          {p.refused && inst === null && (
            <ErrorBox
              error={{
                message: p.refused.error,
                hint: p.refused.hint ?? undefined,
              }}
            />
          )}
          <dl className="mk-kv">
            <dt>Publisher</dt>
            <dd>
              {pub?.name || "—"}{" "}
              {pub?.verified ? (
                <span className="tick">✓ verified</span>
              ) : (
                <span className="tag">not verified</span>
              )}
            </dd>
            <dt>Trust</dt>
            <dd>
              <TrustTag trust={p.trust} />{" "}
              <span className="sub">{TRUST[p.trust ?? ""]?.about}</span>
            </dd>
            {version && (
              <>
                <dt>Version</dt>
                <dd className="mono">{version}</dd>
              </>
            )}
            {p.repo && (
              <>
                <dt>Repo</dt>
                <dd className="mono">{p.repo}</dd>
              </>
            )}
          </dl>
          <section aria-label="Permissions">
            <h3 className="mk-h3">Permissions</h3>
            <PermissionList permissions={permissionsOf(p)} />
            {p.trust === "code" && (
              <p className="mk-note">
                A plugin that runs code is trusted like keel itself. Install it
                only from a publisher you trust.
              </p>
            )}
          </section>
          <section aria-label="Needs">
            <h3 className="mk-h3">Needs</h3>
            {needsOf(p).length === 0 ? (
              <p className="sub">No other plugin.</p>
            ) : (
              <ul className="mk-needs">
                {needsOf(p).map((n) => {
                  const there = view?.plugins.some((x) => x.name === n);
                  return (
                    <li key={n}>
                      <b>{n}</b>{" "}
                      <span className="sub">
                        {there ? "installed" : "will be installed too"}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
          {!!p.versions?.length && (
            <section aria-label="Versions">
              <h3 className="mk-h3">Versions</h3>
              <div className="table-wrap">
                <table className="mk-table">
                  <thead>
                    <tr>
                      <th>Version</th>
                      <th>Works with</th>
                      <th>Released</th>
                    </tr>
                  </thead>
                  <tbody>
                    {p.versions.map((x) => (
                      <tr key={x.version}>
                        <td className="mono">
                          {x.version}
                          {x.revoked ? (
                            <>
                              {" "}
                              <Pill
                                tone="bad"
                                title={
                                  typeof x.revoked === "string"
                                    ? x.revoked
                                    : undefined
                                }
                              >
                                revoked
                              </Pill>
                            </>
                          ) : null}
                        </td>
                        <td className="mono">
                          {x.requires?.keel ? `keel ${x.requires.keel}` : "—"}
                          {x.fits === false && (
                            <div className="sub">
                              {x.why_not ?? "does not fit"}
                            </div>
                          )}
                        </td>
                        <td className="sub">{x.released ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          {!!p.checks?.length && (
            <section aria-label="Checks keel runs">
              <h3 className="mk-h3">Checks keel runs before it installs</h3>
              <ul className="mk-checks">
                {p.checks.map((c, i) => (
                  <li key={i}>
                    {typeof c === "string" ? c : (c.text ?? c.name)}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </Drawer>
  );
}

// ---------- the install dialog ----------

type Item = {
  name: string;
  title: string;
  version?: string;
  trust?: string;
  permissions?: Permissions;
  publisher?: string;
  verified?: boolean;
  target: boolean;
  /** why it cannot be installed (not in the catalogs): it is left out */
  problem?: string;
};

function InstallDialog({
  plan,
  view,
  onClose,
  onDone,
}: {
  plan: InstallPlan;
  view: PluginsView | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { toast } = useApp();
  const [items, setItems] = useState<Item[] | null>(null);
  // the targets no catalog has (or keel would refuse): never installed on their own
  const [unlisted, setUnlisted] = useState<Set<string>>(new Set());
  // what the installs turn on by themselves (needed plugins that are off)
  const [alsoOn, setAlsoOn] = useState<string[]>([]);
  const [loadErr, setLoadErr] = useState<Err>(null);
  const [state, setState] = useState<
    Record<string, "waiting" | "installing" | "done" | "failed">
  >({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const have = useMemo(
    () => new Set((view?.plugins ?? []).map((p) => p.name)),
    [view],
  );
  const turnOn = plan.turnOn ?? [];

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        // a set may name a plugin no catalog has: it shows with the reason and is left out
        const found = await Promise.all(
          plan.install.map((n) =>
            marketApi.plugin(n).then(
              (d) => ({ n, d }),
              (e: unknown) => ({ n, problem: errorParts(e).message }),
            ),
          ),
        );
        // one keel would refuse (revoked, the catalog is old, it does not fit) is left out too, with the reason
        const details = found.flatMap((f) =>
          "d" in f && !f.d.refused ? [f.d] : [],
        );
        const byName = new Map<string, MarketPlugin>(
          details.map((d) => [d.name, d]),
        );
        const left: Item[] = found.flatMap((f) =>
          "problem" in f
            ? [{ name: f.n, title: f.n, target: true, problem: f.problem }]
            : f.d.refused
              ? [
                  {
                    name: f.n,
                    title: f.d.title ?? f.n,
                    target: true,
                    problem: [f.d.refused.error, f.d.refused.hint]
                      .filter(Boolean)
                      .join(" "),
                  },
                ]
              : [],
        );
        const turning = details.flatMap(turnOnOf);
        const out: Item[] = [];
        for (const d of details) {
          for (const it of planOf(d)) {
            if (
              out.some((x) => x.name === it.name) ||
              (have.has(it.name) && !plan.install.includes(it.name))
            )
              continue;
            let own = byName.get(it.name);
            if (!own)
              own = await marketApi.plugin(it.name).catch(() => undefined);
            if (own) byName.set(it.name, own);
            const pub = it.publisher
              ? publisherOf(it)
              : own
                ? publisherOf(own)
                : null;
            out.push({
              name: it.name,
              title: it.title ?? own?.title ?? it.name,
              version: it.version ?? (own ? newestVersion(own) : undefined),
              trust:
                it.trust ??
                (typeof own?.trust === "string" ? own.trust : undefined),
              permissions:
                it.permissions ?? (own ? permissionsOf(own) : undefined),
              publisher: pub?.name,
              verified: pub?.verified,
              target: plan.install.includes(it.name),
            });
          }
        }
        // one another plugin's plan brings in is not left out, but it is never installed on its own
        if (live) {
          setAlsoOn(
            turning.filter(
              (n, i) => turning.indexOf(n) === i && !turnOn.includes(n),
            ),
          );
          setUnlisted(new Set(left.map((l) => l.name)));
          setItems([
            ...out,
            ...left.filter((l) => !out.some((o) => o.name === l.name)),
          ]);
        }
      } catch (e) {
        if (live) setLoadErr(errorParts(e));
      }
    })();
    return () => {
      live = false;
    };
    // the plan is fixed while the dialog is open
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const main = items?.find((x) => x.target && !x.problem);
  const extra = items?.filter((x) => !x.target) ?? [];
  const nothing = !!items && !items.some((x) => !x.problem) && !turnOn.length;
  // one plugin (and what it needs): its name and version; several: how many
  const title =
    plan.title ??
    (plan.install.length > 1
      ? `Install ${plan.install.length} plugins?`
      : main
        ? `Install ${main.title}${main.version ? " " + main.version : ""}?`
        : `Install ${plan.install[0] ?? ""}?`);
  const runsCode = items?.some((x) => x.trust === "code");
  const unverified = items?.some(
    (x) => (x.trust === "code" || x.trust === "web") && x.verified === false,
  );

  const install = async () => {
    setBusy(true);
    setErr(null);
    let done = 0;
    try {
      for (const name of plan.install) {
        if (unlisted.has(name)) continue;
        // a set's earlier install may have brought this one in already
        const now = await marketApi.installed();
        if (now.plugins.some((p) => p.name === name)) continue;
        setState((s) => ({ ...s, [name]: "installing" }));
        const it = items?.find((x) => x.name === name);
        try {
          await marketApi.install(
            name,
            plan.install.length === 1 ? it?.version : undefined,
          );
        } catch (e) {
          setState((s) => ({ ...s, [name]: "failed" }));
          throw e;
        }
        setState((s) => ({ ...s, [name]: "done" }));
        done++;
      }
      for (const name of turnOn) await marketApi.setOn(name, true);
      const names = [
        ...(items ?? [])
          .filter((x) => x.target && !x.problem)
          .map((x) => x.title),
        ...turnOn,
      ];
      toast(
        names.length === 1 && done === 1
          ? `${names[0]} is installed. Restart keel to use it.`
          : "The plugins are ready. Restart keel to use them.",
      );
      onDone();
      onClose();
    } catch (e) {
      setErr(errorParts(e));
      onDone();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer
      title={title}
      onClose={onClose}
      id="install-dialog"
      footer={
        <>
          <button
            className="btn"
            type="button"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            className="btn primary"
            type="button"
            onClick={() => void install()}
            disabled={busy || !items || nothing}
          >
            {busy ? "Installing…" : "Install"}
          </button>
        </>
      }
    >
      {loadErr ? (
        <ErrorBox error={loadErr} />
      ) : !items ? (
        <Loading what="Reading what will be installed" />
      ) : (
        <>
          {extra.length > 0 && plan.install.length === 1 && (
            <div className="mk-note" role="note">
              <b>Also installs {extra.map((x) => x.title).join(", ")}</b>{" "}
              <span className="sub">
                {main?.title ?? plan.install[0]} needs{" "}
                {extra.length === 1 ? "it" : "them"} to work.
              </span>
            </div>
          )}
          <p className="sub" style={{ margin: 0 }}>
            Everything that will be installed, and what each one may do:
          </p>
          <ul className="mk-install-list" aria-label="What will be installed">
            {items.map((x) => (
              <li key={x.name} className="mk-install-item">
                <div className="row">
                  <b>{x.title}</b>
                  {x.version && <span className="mono sub">{x.version}</span>}
                  <TrustTag trust={x.trust} />
                  {x.publisher && (
                    <span className="sub">
                      by {x.publisher}
                      {x.verified
                        ? " ✓"
                        : x.verified === false
                          ? " (not verified)"
                          : ""}
                    </span>
                  )}
                  {state[x.name] === "installing" && (
                    <Pill tone="run">installing</Pill>
                  )}
                  {state[x.name] === "done" && <Pill tone="ok">installed</Pill>}
                  {state[x.name] === "failed" && <Pill tone="bad">failed</Pill>}
                </div>
                {x.problem ? (
                  <span className="mk-warn">Left out: {x.problem}</span>
                ) : (
                  <PermissionList permissions={x.permissions} compact />
                )}
              </li>
            ))}
          </ul>
          {turnOn.length > 0 && (
            <p style={{ margin: 0 }}>
              Turns on (they came in keel's image): <b>{turnOn.join(", ")}</b>
            </p>
          )}
          {alsoOn.length > 0 && (
            <p style={{ margin: 0 }}>
              Also turns on what it needs: <b>{alsoOn.join(", ")}</b>
            </p>
          )}
          {runsCode && (
            <p className="mk-note">
              {unverified
                ? "A publisher here is not verified. Its code would run inside keel with keel's rights."
                : "Code from these plugins runs inside keel. keel checks that their publisher signed them."}
            </p>
          )}
          <p className="sub" style={{ margin: 0 }}>
            Nothing runs while it installs. keel loads the plugins when it
            restarts.
          </p>
          {err && <ErrorBox error={err} />}
        </>
      )}
    </Drawer>
  );
}

// ---------- Sources and rules ----------

const RULES: [keyof Rules, string, string][] = [
  [
    "agents_may_ask",
    "Agents may ask to install plugins",
    "KeelBot and flow agents send a request to the Inbox. Nothing installs until a person approves it.",
  ],
  [
    "check_daily",
    "Check for updates every day",
    "Updates never install by themselves. An update that asks for more permissions always asks you.",
  ],
  [
    "allow_unverified",
    "Allow unverified publishers",
    "Off: plugins with pages or code install only from publishers a catalog you trust has verified.",
  ],
  [
    "restart_when_idle",
    "Restart by itself after an approved install",
    "keel waits until no agent step runs, then restarts to load the new plugin.",
  ],
];

const shortKey = (k?: string) =>
  !k ? "—" : k.length > 16 ? `${k.slice(0, 8)}…${k.slice(-6)}` : k;
const slug = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 32) || "catalog";

function SourcesTab({ onChanged }: { onChanged: () => void }) {
  const { toast } = useApp();
  const src = useLoad("plugin-sources", () => marketApi.sources());
  const status = useLoad("plugin-source-status", () => marketApi.search(), {
    live: false,
  });
  const rules = useLoad("plugin-rules", () => marketApi.rules());
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [key, setKey] = useState("");
  const [file, setFile] = useState("");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState<Err>(null);
  const sources = sourcesOf(src.data);
  const statusOf = (id: string) =>
    sourceStatusOf(status.data).find((s) => s.id === id);

  const run = async (
    what: string,
    fn: () => Promise<unknown>,
    done: string,
  ) => {
    setBusy(what);
    setErr(null);
    try {
      await fn();
      toast(done);
      return true;
    } catch (e) {
      setErr(errorParts(e));
      return false;
    } finally {
      setBusy("");
    }
  };
  const save = (list: Source[], done: string) =>
    run(
      "sources",
      async () => src.setData(await marketApi.saveSources(list)),
      done,
    );
  const add = async () => {
    const u = url.trim();
    let host = "";
    try {
      host = new URL(u).host;
    } catch {
      /* the api says what is wrong */
    }
    const name = title.trim() || host || u;
    const id = slug(title.trim() || host.split(".")[0] || "catalog");
    if (
      await save(
        [...sources, { id, title: name, url: u, key: key.trim(), on: true }],
        `${name} is added. keel reads it now.`,
      )
    ) {
      setTitle("");
      setUrl("");
      setKey("");
      await run(
        "refresh",
        () => marketApi.refresh(),
        "The catalogs are read again.",
      );
      await status.reload();
    }
  };
  const refresh = async () => {
    if (
      await run(
        "refresh",
        () => marketApi.refresh(),
        "The catalogs are read again.",
      )
    )
      await Promise.all([src.reload(), status.reload()]);
  };
  const setRule = async (k: keyof Rules, on: boolean) => {
    if (!rules.data) return;
    const next = { ...rules.data, [k]: on };
    await run(
      "rules",
      async () => rules.setData(await marketApi.saveRules(next)),
      "Saved.",
    );
  };
  const fromFile = async () => {
    if (
      await run(
        "file",
        () => marketApi.installFile(file.trim()),
        "The file is installed, as unsigned. Restart keel to use it.",
      )
    ) {
      setFile("");
      onChanged();
    }
  };

  return (
    <div className="mk-g2">
      <div className="grid">
        {err && <ErrorBox error={err} />}
        <Panel
          title="Catalogs"
          extra={
            <button
              className="btn sm"
              type="button"
              disabled={busy === "refresh"}
              onClick={() => void refresh()}
            >
              {busy === "refresh" ? "Reading…" : "Refresh"}
            </button>
          }
        >
          {src.error && !src.data ? (
            <ErrorBox error={src.error} onRetry={() => void src.reload()} />
          ) : !src.data ? (
            <Loading what="Reading the catalogs" />
          ) : (
            <ul className="mk-sources" aria-label="Catalogs">
              {sources.map((s) => {
                const st = statusOf(s.id);
                const problem = st?.problem ?? s.problem;
                const official = s.official || s.id === "keel";
                return (
                  <li key={s.id} className="mk-source">
                    <div className="mk-source-t">
                      <b>{s.title || s.id}</b>{" "}
                      {official && <span className="tag keel">built in</span>}
                      <div className="sub mono mk-url">{s.url}</div>
                      <div className="sub">
                        Key <span className="mono">{shortKey(s.key)}</span>
                        {st?.old ? " · the copy is old" : ""}
                        {st?.ok ? " · read" : ""}
                      </div>
                      {problem && <div className="mk-warn">{problem}</div>}
                    </div>
                    <div className="row">
                      <input
                        type="checkbox"
                        role="switch"
                        aria-label={`${s.title || s.id} on`}
                        checked={s.on !== false}
                        disabled={busy === "sources"}
                        onChange={(e) =>
                          void save(
                            sources.map((x) =>
                              x.id === s.id
                                ? { ...x, on: e.target.checked }
                                : x,
                            ),
                            e.target.checked
                              ? `${s.title || s.id} is on.`
                              : `${s.title || s.id} is off.`,
                          )
                        }
                      />
                      {!official && (
                        <button
                          className="btn sm ghost"
                          type="button"
                          disabled={busy === "sources"}
                          onClick={() =>
                            void save(
                              sources.filter((x) => x.id !== s.id),
                              `${s.title || s.id} is removed.`,
                            )
                          }
                        >
                          Remove
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          <form
            className="mk-add"
            onSubmit={(e) => {
              e.preventDefault();
              void add();
            }}
            aria-label="Add a catalog"
          >
            <div className="field">
              <label htmlFor="mk-src-title">Name</label>
              <input
                id="mk-src-title"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Acme catalog"
              />
            </div>
            <div className="field">
              <label htmlFor="mk-src-url">Catalog URL</label>
              <input
                id="mk-src-url"
                type="text"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://…/v1/index.json"
              />
            </div>
            <div className="field">
              <label htmlFor="mk-src-key">Its public key</label>
              <input
                id="mk-src-key"
                type="text"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="RWQ…"
              />
            </div>
            <div className="row">
              <button
                className="btn"
                type="submit"
                disabled={!url.trim() || !key.trim() || busy === "sources"}
              >
                Add the catalog
              </button>
            </div>
          </form>
        </Panel>
        <Panel title="Install from a file">
          <p className="sub" style={{ marginTop: 0 }}>
            For plugin authors: a .kplug file in /data. keel shows it as
            unsigned.
          </p>
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              void fromFile();
            }}
          >
            <label htmlFor="mk-file" className="sr-only">
              The file's path in /data
            </label>
            <input
              id="mk-file"
              type="text"
              className="inline-input"
              style={{ flex: "1 1 240px" }}
              value={file}
              onChange={(e) => setFile(e.target.value)}
              placeholder="/data/my-plugin-0.1.0.kplug"
            />
            <button
              className="btn"
              type="submit"
              disabled={!file.trim() || busy === "file"}
            >
              {busy === "file" ? "Installing…" : "Install the file"}
            </button>
          </form>
        </Panel>
      </div>
      <Panel title="Rules">
        {rules.error && !rules.data ? (
          <ErrorBox error={rules.error} onRetry={() => void rules.reload()} />
        ) : !rules.data ? (
          <Loading what="Reading the rules" />
        ) : (
          <div className="grid mk-rules">
            {RULES.map(([k, label, about]) => (
              <label key={k} className="chk mk-rule">
                <input
                  type="checkbox"
                  checked={Boolean(rules.data![k])}
                  disabled={busy === "rules"}
                  onChange={(e) => void setRule(k, e.target.checked)}
                />
                <span>
                  <b>{label}</b>
                  <br />
                  <span className="sub">{about}</span>
                </span>
              </label>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
