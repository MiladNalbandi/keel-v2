// 0.16.1 plugin track: the marketplace for workflows in Control › Plugins (docs/plugins/21-workflow-marketplace.md §4),
// in the look of the plugin Marketplace (docs/plugins/mockup.html):
//
//   Marketplace › Workflows   search, the category chips, cards (icon, title, publisher ✓ / community, version,
//                             summary, the review in short with a red mark when it runs commands, what it needs) and
//                             Install / Installed here / Update to x. No catalog: a note that says so, never an empty page
//   #/settings/plugins/workflow/<name> the workflow's page: its steps as a read-only graph (the builder's own Graph), the YAML
//                             (folded), the Install review, what it needs (plugins on/off with a link to the plugin's
//                             page, agents here or missing), versions and the checks keel runs
//
// Install asks: this project (the default) or all projects. A 409 that names missing plugins offers their pages (the
// plugin path installs them; this dialog never does); missing agents are named.

import { useEffect, useState } from "react";
import { api, errorParts, type Agent, type Workflow } from "../api";
import { bytes } from "../format";
import { CodeEditor } from "../components/CodeEditor";
import { Graph } from "../components/Graph";
import { TrustTag } from "../components/Permissions";
import { Drawer, ErrorBox, Loading, Pill } from "../components/ui";
import { ReviewList, UpdateDialog } from "../components/WorkflowMarket";
import { Zoom } from "../components/Zoom";
import type { PluginsView } from "../marketplaceApi";
import { useSlot } from "../sdk/registry";
import { SLOTS, type MarketKindItem } from "../sdk/slots";
import { useApp, useLoad } from "../state";
import {
  MARKET_WORKFLOWS,
  neededPlugins,
  refusalOf,
  reviewShort,
  runsCommands,
  workflowHref,
  workflowMarketApi,
  type InstallRefusal,
  type MarketWorkflow,
  type WorkflowHit,
  type WorkflowSearch,
} from "../workflowMarketApi";
import { MARKET_RULES } from "../rulePackMarketApi";
import { MARKET_SKILLS } from "../skillMarketApi";
import { MARKET_STACKS } from "../stackMarketApi";
import { PanelSection } from "../components/PanelSection";
import { useDebounced } from "../hooks";
import { type Err } from "../marketHttp";
import { publisherOf } from "../marketplaceApi";
import { PluginIcon, Publisher, fromKeel, isBeta } from "./pluginLook";



/** An installed workflow as the update dialog needs it (its id here, its name, the version it has, the update). */
const asInstalled = (h: WorkflowHit): Workflow =>
  ({
    id: h.installed?.wid ?? h.name,
    name: h.title || h.name,
    keel_rules: h.review?.keel_rules ?? true,
    version: 0,
    steps: [],
    yaml: "",
    market: {
      catalog: h.source,
      name: h.name,
      version: h.installed?.version ?? "",
      update: h.update,
    },
  }) as Workflow;

/** "6 steps · 2 gates · runs 1 command", red when it runs commands. */
export function ReviewShort({ h }: { h: Pick<WorkflowHit, "review"> }) {
  const bad = runsCommands(h.review);
  return (
    <span className={`wfm-short${bad ? " bad" : ""}`}>
      {bad && (
        <span className="wfm-mark" aria-label="Runs shell commands" title="Runs shell commands">
          !
        </span>
      )}
      {reviewShort(h.review)}
    </span>
  );
}

export type MarketKind = "plugins" | "workflows" | "rules" | "skills" | "stacks";
/** keel's own kinds; a part's kind (slot market.kind, #92) is any other id */
export const CORE_KINDS: readonly string[] = ["plugins", "workflows", "rules", "skills", "stacks"];

/** The kind switch of the Marketplace tab: Plugins · Workflows · Rules. The link keeps ?tab=market. */
export function KindSwitch({ kind }: { kind: string }) {
  // #92 a part's kinds after keel's own (the Org plugin's Orgs)
  const parts = useSlot<MarketKindItem>(SLOTS.marketKind).filter((p) => !CORE_KINDS.includes(p.id));
  const to = (k: string) => {
    const h = k === "workflows" ? MARKET_WORKFLOWS : k === "rules" ? MARKET_RULES : k === "skills" ? MARKET_SKILLS
      : k === "stacks" ? MARKET_STACKS : k === "plugins" ? "#/settings/plugins?tab=market"
        : `#/settings/plugins?tab=market&kind=${encodeURIComponent(k)}`;
    if (location.hash !== h) location.hash = h;
  };
  return (
    <div className="mk-kind" role="group" aria-label="What to find">
      <button type="button" aria-pressed={kind === "plugins"} onClick={() => to("plugins")}>
        Plugins
      </button>
      <button type="button" aria-pressed={kind === "workflows"} onClick={() => to("workflows")}>
        Workflows
      </button>
      <button type="button" aria-pressed={kind === "rules"} onClick={() => to("rules")}>
        Rules
      </button>
      <button type="button" aria-pressed={kind === "skills"} onClick={() => to("skills")}>
        Skills
      </button>
      <button type="button" aria-pressed={kind === "stacks"} onClick={() => to("stacks")}>
        Stacks
      </button>
      {parts.map((p) => (
        <button key={p.id} type="button" aria-pressed={kind === p.id} onClick={() => to(p.id)}>
          {p.title}
        </button>
      ))}
    </div>
  );
}

// ---------- Marketplace › Workflows ----------

export function WorkflowsMarket({ view }: { view: PluginsView | null }) {
  const { pid, project, projectsLoaded } = useApp();
  const [typed, setTyped] = useState("");
  const q = useDebounced(typed.trim());
  const [cat, setCat] = useState("");
  // the search names the project (what is installed there): it waits for the projects
  const r = useLoad(
    projectsLoaded ? `market-wfs:${q}:${cat}:${pid ?? ""}` : null,
    () => workflowMarketApi.search(q, cat, pid),
  );
  const [installing, setInstalling] = useState<WorkflowHit | null>(null);
  const [updating, setUpdating] = useState<WorkflowHit | null>(null);
  // the categories stay when a search or a chip narrows the list
  const [cats, setCats] = useState<string[]>([]);
  useEffect(() => {
    if (r.data?.categories?.length) setCats(r.data.categories);
  }, [r.data]);
  const data: WorkflowSearch | null = r.data;
  const statuses = data?.sources ?? [];
  const all = data?.workflows ?? [];
  const hits = all.filter((h) => !cat || h.category === cat);
  const down = statuses.length > 0 && statuses.every((s) => s.ok === false);
  const none = !!r.error || down || (!!data && !statuses.length && !all.length);
  const plugins = new Map((view?.plugins ?? []).map((p) => [p.name, p]));

  const card = (h: WorkflowHit) => {
    const p = publisherOf(h);
    const title = h.title || h.name;
    const needs = neededPlugins(h);
    const agents = h.needs?.agents ?? [];
    return (
      <article key={h.name} className="mk-card" aria-label={`${title} workflow`}>
        <div className="mk-card-h">
          <PluginIcon name={h.name} title={title} />
          <div style={{ minWidth: 0 }}>
            <h3>
              <a className="mk-card-link" href={workflowHref(h.name)}>
                {title}
              </a>
              {isBeta(h.version) && <span className="tag warn">beta</span>}
            </h3>
            <div className="mk-by">
              <Publisher name={p.name} verified={p.verified} />
              {h.version && <span className="mono faint">{h.version}</span>}
            </div>
          </div>
        </div>
        <p className="mk-sum">{h.summary}</p>
        <div className="wfm-facts">
          <ReviewShort h={h} />
          {(needs.length > 0 || agents.length > 0) && (
            <span className="mk-card-tags" aria-label="Needs">
              {needs.map((n) => {
                const mine = plugins.get(n);
                const ok = !!mine && mine.on !== false;
                return (
                  <span key={n} className={`tag${ok ? "" : " warn"}`} title={ok ? "on" : mine ? "off" : "not installed"}>
                    needs {mine?.title ?? n}
                  </span>
                );
              })}
              {agents.map((a) => (
                <span key={`a-${a}`} className="tag">
                  agent {a}
                </span>
              ))}
            </span>
          )}
        </div>
        <div className="mk-card-f">
          <span className="mk-card-tags">
            <TrustTag trust="content" />
            {h.refused && (
              <Pill tone="bad" title={h.refused}>
                refused
              </Pill>
            )}
            {!h.refused && h.fits === false && <Pill tone="warn">does not fit this keel</Pill>}
          </span>
          {h.installed ? (
            h.update ? (
              <button className="btn sm" type="button" disabled={!pid} onClick={() => setUpdating(h)}>
                Update to {h.update}
              </button>
            ) : (
              <span title={h.installed.scope === "all" ? "Installed for all projects" : undefined}>
                <Pill tone="ok">Installed here</Pill>
              </span>
            )
          ) : (
            <button
              className="btn sm primary"
              type="button"
              disabled={!pid || !!h.refused || h.fits === false}
              onClick={() => setInstalling(h)}
              aria-label={`Install ${title}`}
            >
              Install
            </button>
          )}
        </div>
      </article>
    );
  };
  const keel = hits.filter((h) => fromKeel(h));
  const community = hits.filter((h) => !fromKeel(h));
  return (
    <div className="grid mk-market">
      <div className="mk-searchrow">
        <label className="mk-search">
          <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
            <path
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              d="M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4"
            />
          </svg>
          <input
            type="search"
            placeholder="Search workflows: release, review, hotfix…"
            aria-label="Search workflows"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
          />
        </label>
        <div className="mk-chips" role="group" aria-label="Category">
          {["", ...cats].map((c) => (
            <button key={c || "all"} className="mk-chip" type="button" aria-pressed={cat === c} onClick={() => setCat(c)}>
              {c ? c[0].toUpperCase() + c.slice(1) : "All"}
            </button>
          ))}
        </div>
      </div>
      <div className="mk-legend">
        <TrustTag trust="content" />
        <span className="faint">
          · every workflow runs only through keel's flows, agents and gates. The{" "}
          <span className="wfm-short bad">
            <span className="wfm-mark" aria-hidden="true">!</span>red mark
          </span>{" "}
          means it runs shell commands.
        </span>
      </div>
      {!pid && (
        <p className="mk-note" role="note">
          Pick a project first: a workflow installs into a project (or all projects).
        </p>
      )}
      {statuses
        .filter((s) => !none && (s.old || s.problem))
        .map((s) => (
          <p key={s.id} className="mk-note" role="note">
            <b>{s.title || s.id}</b>: {s.old ? "the catalog is old: search works, installs wait until a refresh works. " : ""}
            {s.problem ?? ""}
          </p>
        ))}
      {none ? (
        <div className="mk-note mk-local wfm-none" role="note" aria-label="No workflow catalog">
          <b>
            {down || r.error
              ? "No workflow catalog answers yet."
              : "The catalogs list no workflows yet."}
          </b>{" "}
          Workflows come from the same signed catalogs as plugins. Add a catalog in Sources and rules, or make your
          own in Build › Workflows and publish it from its menu.
          {r.error && <span className="sub" style={{ display: "block" }}>{r.error.message}</span>}
          {statuses
            .filter((s) => s.problem)
            .map((s) => (
              <span key={s.id} className="sub" style={{ display: "block" }}>
                {s.id}: {s.problem}
              </span>
            ))}
          <span className="row" style={{ marginTop: 8 }}>
            <a className="btn sm" href="#/settings/plugins?tab=sources" style={{ textDecoration: "none" }}>
              Sources and rules
            </a>
            <a className="btn sm" href="#/workflows" style={{ textDecoration: "none" }}>
              Build › Workflows
            </a>
          </span>
        </div>
      ) : !data ? (
        <Loading what="Searching the workflows" />
      ) : !hits.length ? (
        <div className="mk-empty">
          {typed.trim()
            ? `No workflow matches “${typed.trim()}”. Try another word.`
            : cat
              ? "No workflow in this category yet."
              : "The catalogs list no workflows yet."}
        </div>
      ) : (
        <>
          {keel.length > 0 && (
            <section aria-labelledby="wfm-keel">
              <div className="mk-sect">
                <h2 id="wfm-keel">From keel</h2>
                <p className="sub">Made and signed by keel.</p>
              </div>
              <div className="mk-cards">{keel.map(card)}</div>
            </section>
          )}
          {community.length > 0 && (
            <section aria-labelledby="wfm-community">
              <div className="mk-sect">
                <h2 id="wfm-community">From the community</h2>
                <p className="sub">Published through a pull request to a catalog repo.</p>
              </div>
              <div className="mk-cards">{community.map(card)}</div>
            </section>
          )}
        </>
      )}
      {installing && pid && (
        <MarketInstallDialog
          hit={installing}
          pid={pid}
          project={project?.name ?? pid}
          onClose={() => setInstalling(null)}
          onDone={() => void r.reload()}
        />
      )}
      {updating && pid && (
        <UpdateDialog
          pid={pid}
          w={asInstalled(updating)}
          onClose={() => setUpdating(null)}
          onDone={() => void r.reload()}
        />
      )}
    </div>
  );
}

// ---------- the install dialog ----------

export function MarketInstallDialog({
  hit,
  versions,
  pid,
  project,
  onClose,
  onDone,
}: {
  hit: WorkflowHit;
  versions?: MarketWorkflow["versions"];
  pid: string;
  project: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const { toast } = useApp();
  const [scope, setScope] = useState<"project" | "all">("project");
  const [version, setVersion] = useState(hit.version);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const [refused, setRefused] = useState<InstallRefusal | null>(null);
  const [done, setDone] = useState<Workflow | null>(null);
  const title = hit.title || hit.name;
  const review = versions?.find((v) => v.version === version)?.review ?? hit.review;
  const install = async () => {
    setBusy(true);
    setErr(null);
    setRefused(null);
    try {
      const r = await workflowMarketApi.install(pid, {
        name: hit.name,
        ...(version && version !== hit.version ? { version } : {}),
        scope,
      });
      setDone(r.workflow);
      toast(`${title} is installed ${scope === "all" ? "for all projects" : `in ${project}`}.`);
      onDone();
    } catch (e) {
      const no = refusalOf(e);
      if (no && (no.missing_plugins?.length || no.missing_agents?.length)) setRefused(no);
      else setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  if (done)
    return (
      <Drawer
        title={`${title} is installed`}
        onClose={onClose}
        id="wf-install"
        footer={
          <>
            <button className="btn" type="button" onClick={onClose}>
              Close
            </button>
            <a className="btn primary" href={`#/workflows/${encodeURIComponent(done.id)}`} style={{ textDecoration: "none" }}>
              Open in Build › Workflows
            </a>
          </>
        }
      >
        <p style={{ margin: 0 }}>
          <b>{done.name}</b> is in the workflow list {scope === "all" ? "of every project" : `of ${project}`}, with the
          badge “from marketplace · {version}”. Nothing ran yet: start a flow with it when you want.
        </p>
      </Drawer>
    );
  return (
    <Drawer
      title={`Install ${title} ${version}?`}
      onClose={onClose}
      id="wf-install"
      footer={
        <>
          <button className="btn" type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn primary" type="button" onClick={() => void install()} disabled={busy}>
            {busy ? "Installing…" : "Install"}
          </button>
        </>
      }
    >
      <div className="row">
        <span className="mk-by">
          <Publisher {...publisherOf(hit)} />
        </span>
        <TrustTag trust="content" />
      </div>
      {versions && versions.length > 1 && (
        <div className="field">
          <label htmlFor="wfi-version">Version</label>
          <select id="wfi-version" value={version} onChange={(e) => setVersion(e.target.value)}>
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                {v.version}
                {v.version === hit.version ? " (newest that fits)" : ""}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="field">
        <span className="lab">Install for</span>
        <label className="radio">
          <input type="radio" name="wfscope" checked={scope === "project"} onChange={() => setScope("project")} />
          <span>
            <b>This project</b> — {project} only
          </span>
        </label>
        <label className="radio">
          <input type="radio" name="wfscope" checked={scope === "all"} onChange={() => setScope("all")} />
          <span>
            <b>All projects</b> on this keel
          </span>
        </label>
      </div>
      <div className="field">
        <span className="lab">Check before you install</span>
        <ReviewList review={review} />
      </div>
      <p className="hint" style={{ margin: 0 }}>
        A workflow is data, not code: no restart. keel checks its signature, its checksum and the review again before it
        installs it.
      </p>
      {refused && (
        <div className="errbox" role="alert" aria-label="Missing to install">
          <b>{refused.error}</b>
          {refused.hint && <span className="sub">{refused.hint}</span>}
          {!!refused.missing_plugins?.length && (
            <div className="wfm-missing">
              <span>It needs these plugins first. Install or turn them on on their page, then come back:</span>
              <ul>
                {refused.missing_plugins.map((n) => (
                  <li key={n}>
                    <a href={`#/settings/plugins/${encodeURIComponent(n)}`}>Open the {n} plugin's page</a>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!!refused.missing_agents?.length && (
            <div className="wfm-missing">
              <span>
                keel has no agent named <b>{refused.missing_agents.join(", ")}</b>. Agents come with plugins, or make
                one as a custom agent in Build › Agents.
              </span>
            </div>
          )}
        </div>
      )}
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

// ---------- a workflow's page ----------



export function MarketWorkflowPage({ name, view }: { name: string; view: PluginsView | null }) {
  const { pid, project, health, projectsLoaded } = useApp();
  const r = useLoad(projectsLoaded ? `market-wf:${name}:${pid ?? ""}` : null, () => workflowMarketApi.workflow(name, pid), {
    live: false,
  });
  const agents = useLoad<Agent[]>(pid ? `agents:${pid}` : null, () => api.agents(pid!), { live: false });
  const [installing, setInstalling] = useState(false);
  const [updating, setUpdating] = useState(false);
  const w: MarketWorkflow | null = r.data;
  const crumb = (
    <nav className="mk-crumb" aria-label="Breadcrumb">
      <a href="#/settings/plugins">Plugins</a>
      <span aria-hidden="true">›</span>
      <a href={MARKET_WORKFLOWS}>Workflows</a>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{w?.title ?? name}</span>
    </nav>
  );
  if (!w)
    return (
      <div className="mk-detail">
        {crumb}
        {r.error ? <ErrorBox error={r.error} onRetry={() => void r.reload()} /> : <Loading what="Reading the workflow" />}
      </div>
    );
  const title = w.title || w.name;
  const p = publisherOf(w);
  const needs = neededPlugins(w);
  const wantAgents = w.needs?.agents ?? [];
  const have = new Set((agents.data ?? []).map((a) => a.id));
  const plugins = new Map((view?.plugins ?? []).map((x) => [x.name, x]));
  const v = w.versions?.find((x) => x.version === w.version) ?? w.versions?.[0];
  const keelRange = v?.requires?.keel;

  const main = w.installed ? (
    <>
      <Pill tone="ok">Installed here {w.installed.version}</Pill>
      {w.update && (
        <button className="btn primary" type="button" disabled={!pid} onClick={() => setUpdating(true)}>
          Update to {w.update}
        </button>
      )}
      <a className="btn" href={`#/workflows/${encodeURIComponent(w.installed.wid)}`} style={{ textDecoration: "none" }}>
        Open in Build › Workflows
      </a>
    </>
  ) : (
    <button
      className="btn primary"
      type="button"
      disabled={!pid || !!w.refused || w.fits === false}
      onClick={() => setInstalling(true)}
    >
      Install {w.version}
    </button>
  );

  return (
    <div className="mk-detail">
      {crumb}
      <div className="mk-dhead">
        <PluginIcon name={w.name} title={title} size="lg" />
        <div style={{ minWidth: 0 }}>
          <h1>
            {title}
            {isBeta(w.version) && <span className="tag warn">beta</span>}
          </h1>
          <div className="mk-meta">
            {p.name && (
              <span className="mk-by">
                <Publisher name={p.name} verified={p.verified} />
              </span>
            )}
            <span className="mono">{w.version}</span>
            <TrustTag trust="content" />
            <span className="tag">workflow</span>
            {w.category && <span className="sub">{w.category}</span>}
          </div>
        </div>
        <div className="actions mk-dact">{main}</div>
      </div>
      {w.refused && (
        <p className="mk-note bad" role="note">
          keel will not install it: {w.refused}
        </p>
      )}
      {!pid && (
        <p className="mk-note" role="note">
          Pick a project first: a workflow installs into a project (or all projects).
        </p>
      )}
      {/* the steps take the page's width: the graph reads as on the builder */}
      <div className="wfm-steps">
        <PanelSection id="wfm-steps" title="Steps" extra={<ReviewShort h={w} />}>
          {w.summary && <p className="mk-about">{w.summary}</p>}
          {w.steps?.length ? (
            <Zoom id="market-workflow">
              <Graph steps={w.steps} per={6} label={`Steps of ${title}`} />
            </Zoom>
          ) : (
            <p className="sub" style={{ margin: 0 }}>The catalog sends no steps for it.</p>
          )}
          <details className="wfm-yaml">
            <summary>The YAML</summary>
            <CodeEditor id="wfm-yaml" label={`YAML of ${title}`} value={w.yaml ?? ""} onChange={() => undefined} readOnly rows={18} />
          </details>
        </PanelSection>
      </div>
      <div className="mk-dgrid">
        <div className="mk-stack">
          <PanelSection id="wfm-versions" title="Versions" extra={<span className="sub">Newest first</span>} body={false}>
            <div className="table-wrap">
              <table className="mk-table mk-vtable">
                <thead>
                  <tr>
                    <th>Version</th>
                    <th>Works with</th>
                    <th>Released</th>
                    <th>What it does</th>
                  </tr>
                </thead>
                <tbody>
                  {(w.versions ?? []).map((x) => (
                    <tr key={x.version}>
                      <td className="mono">
                        {x.version}
                        {x.version === w.installed?.version && <span className="sub"> · yours</span>}
                      </td>
                      <td className="mono">{x.requires?.keel ? `keel ${x.requires.keel}` : "—"}</td>
                      <td className="num sub">{x.released ?? "—"}</td>
                      <td>{x.review ? <ReviewShort h={{ review: x.review }} /> : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </PanelSection>
          {!!w.checks?.length && (
            <PanelSection id="wfm-checks" title="Checks keel runs before install">
              <div className="mk-checklist">
                {w.checks.map((c, i) => (
                  <div key={i}>
                    <span className="mk-ok" aria-hidden="true">✓</span>
                    <span>{c}</span>
                  </div>
                ))}
              </div>
            </PanelSection>
          )}
        </div>
        <div className="mk-stack">
          <PanelSection id="wfm-review" title="Install review" extra={<span className="sub">what it does when it runs</span>}>
            <ReviewList review={w.review} />
            <p className="mk-quote">
              Content only: it runs through keel's flows, agents and gates. Signed by its publisher, but read the shell
              commands before you install it.
            </p>
          </PanelSection>
          <PanelSection id="wfm-needs" title="Needs">
            <div className="mk-checklist" aria-label="What it needs">
              <div>
                <span className="mk-ok" aria-hidden="true">✓</span>
                <span>
                  keel core {keelRange && <span className="mono">{keelRange}</span>}
                  {health?.version && (
                    <>
                      {" "}· you have <span className="mono">{health.version.replace(/^v/, "")}</span>
                    </>
                  )}
                </span>
              </div>
              {needs.map((n) => {
                const mine = plugins.get(n);
                const on = !!mine && mine.on !== false;
                return (
                  <div key={n} data-testid={`needs-plugin-${n}`}>
                    {on ? <span className="mk-ok" aria-hidden="true">✓</span> : <span className="tag warn" aria-hidden="true">!</span>}
                    <span>
                      <b>{mine?.title ?? n}</b> <span className="mono sub">{w.needs.plugins[n]}</span>{" "}
                      <span className="sub">{on ? "plugin · on" : mine ? "plugin · off" : "plugin · not installed"}</span>{" "}
                      {!on && (
                        <a href={`#/settings/plugins/${encodeURIComponent(n)}`}>{mine ? "turn on" : "install it"}</a>
                      )}
                    </span>
                  </div>
                );
              })}
              {wantAgents.map((a) => {
                const here = have.has(a);
                return (
                  <div key={`a-${a}`} data-testid={`needs-agent-${a}`}>
                    {here ? <span className="mk-ok" aria-hidden="true">✓</span> : <span className="tag warn" aria-hidden="true">✗</span>}
                    <span>
                      <b>{a}</b> <span className="sub">agent · {here ? "here" : agents.data ? "missing" : "…"}</span>
                    </span>
                  </div>
                );
              })}
              {!needs.length && !wantAgents.length && (
                <p className="sub" style={{ margin: 0 }}>No plugin and no special agent.</p>
              )}
            </div>
            {wantAgents.some((a) => agents.data && !have.has(a)) && (
              <p className="sub" style={{ margin: "10px 0 0" }}>
                A missing agent comes with a plugin, or make it as a custom agent in Build › Agents. keel refuses the
                install until it is there.
              </p>
            )}
            <dl className="mk-kv">
              {bytes(v?.size) && (
                <>
                  <dt>Size</dt>
                  <dd className="mono">{bytes(v?.size)}</dd>
                </>
              )}
              <dt>After install</dt>
              <dd>Ready at once, no restart</dd>
              <dt>From</dt>
              <dd className="mono">{w.source}</dd>
            </dl>
          </PanelSection>
        </div>
      </div>
      {installing && pid && (
        <MarketInstallDialog
          hit={w}
          versions={w.versions}
          pid={pid}
          project={project?.name ?? pid}
          onClose={() => setInstalling(false)}
          onDone={() => void r.reload()}
        />
      )}
      {updating && pid && (
        <UpdateDialog pid={pid} w={asInstalled(w)} onClose={() => setUpdating(false)} onDone={() => void r.reload()} />
      )}
    </div>
  );
}
