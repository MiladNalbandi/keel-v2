// Skills in the marketplace (keel 0.18.6), the fourth kind of Settings › Plugins › Marketplace, in the look of the rule
// pack pages (MarketRulePack.tsx):
//
//   Marketplace › Skills      search, the category chips, keel's own skills ("From keel": in every project, the card
//                             says which agents load it and puts it on more) and the catalogs' (Install / Installed
//                             here (+ "edited") / Update to x). No catalog: a note that says so; keel's own still show
//   #/settings/plugins/skills/<name>   a skill's page: its SKILL.md (as published, verified), keel's review (size, sections, what
//                             it is for), versions, checks; Install into this project with the agents to load it,
//                             Update (asks before it replaces a copy changed here), Agents…, Remove, Open in Skills
//
// A skill only adds words to an agent's prompt: keel's rules and the phase still decide what the agent may do. It is
// saved as one of the project's skills (source "marketplace"): no restart, an agent reads it at its next run.

import { useEffect, useState } from "react";
import { api, errorParts } from "../api";
import { k1 } from "../format";
import { TrustTag } from "../components/Permissions";
import { Confirm, Drawer, ErrorBox, Loading, Pill } from "../components/ui";
import {
  MARKET_SKILLS,
  skillHref,
  skillMarketApi,
  skillShort,
  type BuiltinSkill,
  type MarketSkill,
  type SkillHit,
  type SkillReview,
  type SkillSearch,
  type SkillVersion,
} from "../skillMarketApi";
import { useApp, useLoad } from "../state";
import { Markdown } from "../components/Markdown";
import { PanelSection } from "../components/PanelSection";
import { useDebounced } from "../hooks";
import { type Err, refusedText } from "../marketHttp";
import { publisherOf } from "../marketplaceApi";
import { PluginIcon, Publisher, fromKeel, isBeta } from "./pluginLook";



/** "1.2k tokens · 3 sections". */
export function SkillShort({ review }: { review: SkillReview | null | undefined }) {
  return <span className="wfm-short">{skillShort(review)}</span>;
}

/** The project's agents as checkboxes: who loads the skill. */
function AgentPicker({ pid, value, onChange, label = "Load it for" }: {
  pid: string;
  value: string[];
  onChange: (v: string[]) => void;
  label?: string;
}) {
  const agents = useLoad(`agents:${pid}`, () => api.agents(pid), { live: false });
  return (
    <div className="field">
      <span className="lab">{label}</span>
      {agents.error ? (
        <ErrorBox error={agents.error} />
      ) : !agents.data ? (
        <Loading what="Reading the agents" />
      ) : (
        <div className="grid" style={{ gap: 6 }} role="group" aria-label={label}>
          {agents.data.map((a) => (
            <label key={a.id} className="chk">
              <input
                type="checkbox"
                checked={value.includes(a.id)}
                onChange={(e) => onChange(e.target.checked ? [...value, a.id] : value.filter((x) => x !== a.id))}
              />{" "}
              {a.label || a.id} <span className="sub">{a.phases.join(", ")}</span>
            </label>
          ))}
          {!agents.data.length && <span className="sub">This project has no agents.</span>}
        </div>
      )}
      <span className="hint">Each agent you tick reads the skill at its next run. You can change this later on the Skills page.</span>
    </div>
  );
}

// ---------- Marketplace › Skills ----------

export function SkillsMarket() {
  const { pid, project, projectsLoaded } = useApp();
  const [typed, setTyped] = useState("");
  const q = useDebounced(typed.trim());
  const [cat, setCat] = useState("");
  const r = useLoad(projectsLoaded ? `market-skills:${q}:${cat}:${pid ?? ""}` : null, () => skillMarketApi.search(q, cat, pid));
  const [installing, setInstalling] = useState<SkillHit | null>(null);
  const [updating, setUpdating] = useState<SkillHit | null>(null);
  const [attaching, setAttaching] = useState<BuiltinSkill | null>(null);
  const [cats, setCats] = useState<string[]>([]);
  useEffect(() => {
    if (r.data?.categories?.length) setCats(r.data.categories);
  }, [r.data]);
  const data: SkillSearch | null = r.data;
  const statuses = data?.sources ?? [];
  const hits = (data?.skills ?? []).filter((h) => !cat || h.category === cat);
  const mine = (data?.builtin ?? []).filter((b) => !cat || b.category === cat);
  const noCatalog = !!data && (!!data.catalog_error || !statuses.length || statuses.every((s) => s.ok === false)) && !hits.length;

  const card = (h: SkillHit) => {
    const p = publisherOf(h);
    const title = h.title || h.name;
    const shown = h.version ?? h.latest;
    return (
      <article key={h.name} className="mk-card" aria-label={`${title} skill`}>
        <div className="mk-card-h">
          <PluginIcon name={h.name} title={title} />
          <div style={{ minWidth: 0 }}>
            <h3>
              <a className="mk-card-link" href={skillHref(h.name)}>
                {title}
              </a>
              {isBeta(shown) && <span className="tag warn">beta</span>}
            </h3>
            <div className="mk-by">
              <Publisher name={p.name} verified={p.verified} />
              {shown && <span className="mono faint">{shown}</span>}
            </div>
          </div>
        </div>
        <p className="mk-sum">{h.summary || h.review?.description}</p>
        <div className="wfm-facts">
          <SkillShort review={h.review} />
          {h.installed?.agents?.length ? <span className="sub">loaded by {h.installed.agents.join(", ")}</span> : null}
        </div>
        <div className="mk-card-f">
          <span className="mk-card-tags">
            <TrustTag trust="content" />
            {h.refused && (
              <Pill tone="bad" title={refusedText(h.refused)}>
                refused
              </Pill>
            )}
            {h.installed?.edited && (
              <span className="tag warn" title="Changed here on the Skills page. An update asks before it replaces your copy.">
                edited
              </span>
            )}
          </span>
          {h.installed ? (
            h.update ? (
              <button className="btn sm" type="button" disabled={!pid} onClick={() => setUpdating(h)}>
                Update to {h.update}
              </button>
            ) : (
              <span title={`Installed here: ${h.installed.version}`}>
                <Pill tone="ok">Installed here</Pill>
              </span>
            )
          ) : (
            <button
              className="btn sm primary"
              type="button"
              disabled={!pid || !!h.refused || !h.version}
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

  const builtinCard = (b: BuiltinSkill) => (
    <article key={b.name} className="mk-card" aria-label={`${b.title} skill`}>
      <div className="mk-card-h">
        <PluginIcon name={b.name} title={b.title} />
        <div style={{ minWidth: 0 }}>
          <h3>
            <a className="mk-card-link" href={skillHref(b.name)}>
              {b.title}
            </a>
          </h3>
          <div className="mk-by">
            <Publisher name="keel" verified />
            <span className="mono faint">{b.source === "keel pack" ? `${b.stack} pack` : "built in"}</span>
          </div>
        </div>
      </div>
      <p className="mk-sum">{b.summary}</p>
      <div className="wfm-facts">
        <span className="wfm-short">{k1(b.tokens)} tokens{b.refs ? ` · ${b.refs} references` : ""}</span>
        <span className="sub">{b.agents.length ? `loaded by ${b.agents.join(", ")}` : "no agent loads it yet"}</span>
      </div>
      <div className="mk-card-f">
        <span className="mk-card-tags">
          <TrustTag trust="content" />
          {!b.enabled && <span className="tag warn">off here</span>}
        </span>
        <button className="btn sm" type="button" disabled={!pid} onClick={() => setAttaching(b)} aria-label={`Agents for ${b.title}`}>
          Agents…
        </button>
      </div>
    </article>
  );

  const keel = hits.filter((h) => fromKeel(h));
  const community = hits.filter((h) => !fromKeel(h));
  return (
    <div className="grid mk-market">
      <div className="mk-searchrow">
        <label className="mk-search">
          <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
            <path fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" d="M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4" />
          </svg>
          <input
            type="search"
            placeholder="Search skills: testing, debugging, changelog…"
            aria-label="Search skills"
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
          · a skill only adds words to an agent's prompt: keel's rules and the phase still decide what the agent may do.
          Open one to read all of it first.
        </span>
      </div>
      {!pid && (
        <p className="mk-note" role="note">
          Pick a project first: a skill installs into a project, for the agents you pick.
        </p>
      )}
      {r.error ? (
        <ErrorBox error={r.error} onRetry={() => void r.reload()} />
      ) : !data ? (
        <Loading what="Searching the skills" />
      ) : (
        <>
          {mine.length > 0 && (
            <section aria-labelledby="skm-builtin">
              <div className="mk-sect">
                <h2 id="skm-builtin">From keel</h2>
                <p className="sub">keel's own skills: in every project already. Pick which agents load each.</p>
              </div>
              <div className="mk-cards">{mine.map(builtinCard)}</div>
            </section>
          )}
          {keel.length > 0 && (
            <section aria-labelledby="skm-keel">
              <div className="mk-sect">
                <h2 id="skm-keel">From keel's catalog</h2>
                <p className="sub">Made and signed by keel.</p>
              </div>
              <div className="mk-cards">{keel.map(card)}</div>
            </section>
          )}
          {community.length > 0 && (
            <section aria-labelledby="skm-community">
              <div className="mk-sect">
                <h2 id="skm-community">From the community</h2>
                <p className="sub">Published through a pull request to a catalog repo.</p>
              </div>
              <div className="mk-cards">{community.map(card)}</div>
            </section>
          )}
          {noCatalog && (
            <div className="mk-note mk-local" role="note" aria-label="No skill catalog">
              <b>{data.catalog_error ? "No skill catalog answers yet." : "The catalogs list no skills yet."}</b> Skills
              from others come from the same signed catalogs as plugins, workflows and rule packs. Add a catalog in Sources
              and rules, import a SKILL.md on Build › Skills, or ask KeelBot to write one.
              {data.catalog_error && (
                <span className="sub" style={{ display: "block" }}>
                  {refusedText(data.catalog_error)}
                </span>
              )}
              <span className="row" style={{ marginTop: 8 }}>
                <a className="btn sm" href="#/settings/plugins?tab=sources" style={{ textDecoration: "none" }}>
                  Sources and rules
                </a>
                <a className="btn sm" href="#/skills" style={{ textDecoration: "none" }}>
                  Build › Skills
                </a>
              </span>
            </div>
          )}
          {!mine.length && !hits.length && (typed.trim() || cat) && (
            <div className="mk-empty">
              {typed.trim() ? `No skill matches “${typed.trim()}”. Try another word.` : "No skill in this category yet."}
            </div>
          )}
        </>
      )}
      {installing && pid && (
        <SkillInstallDialog hit={installing} pid={pid} project={project?.name ?? pid} onClose={() => setInstalling(null)} onDone={() => void r.reload()} />
      )}
      {updating && pid && (
        <SkillUpdateDialog hit={updating} pid={pid} project={project?.name ?? pid} onClose={() => setUpdating(null)} onDone={() => void r.reload()} />
      )}
      {attaching && pid && (
        <SkillAgentsDialog sid={attaching.name} title={attaching.title} now={attaching.agents} pid={pid} onClose={() => setAttaching(null)} onDone={() => void r.reload()} />
      )}
    </div>
  );
}

// ---------- install, update, agents ----------

export function SkillInstallDialog({ hit, versions, pid, project, onClose, onDone }: {
  hit: SkillHit | MarketSkill;
  versions?: SkillVersion[];
  pid: string;
  project: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const { toast } = useApp();
  const [version, setVersion] = useState(hit.version ?? "");
  const [agents, setAgents] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const title = hit.title || hit.name;
  const usable = (versions ?? []).filter((v) => v.fits !== false);
  const review = versions?.find((v) => v.version === version)?.review ?? hit.review;
  const install = async () => {
    setBusy(true);
    setErr(null);
    try {
      const done = await skillMarketApi.install(pid, {
        name: hit.name,
        ...(version && version !== hit.version ? { version } : {}),
        ...(agents.length ? { agents } : {}),
      });
      toast(`${title} ${version} is installed in ${project}` + (agents.length ? `; loaded by ${agents.join(", ")} from their next run.` : "."));
      void done;
      onDone();
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer
      title={`Install ${title} ${version}?`}
      onClose={onClose}
      id="sk-install"
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
      {usable.length > 1 && (
        <div className="field">
          <label htmlFor="ski-version">Version</label>
          <select id="ski-version" value={version} onChange={(e) => setVersion(e.target.value)}>
            {usable.map((v) => (
              <option key={v.version} value={v.version}>
                {v.version}
                {v.version === hit.version ? " (newest that fits)" : ""}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="field">
        <span className="lab">Into</span>
        <span>
          <b>This project</b> — {project}: one of its skills, <span className="mono">{hit.name}</span>
        </span>
      </div>
      <div className="field">
        <span className="lab">What it is</span>
        <span>
          <SkillShort review={review} />
          {review?.sections?.length ? <span className="sub"> · {review.sections.join(", ")}</span> : null}
        </span>
      </div>
      <AgentPicker pid={pid} value={agents} onChange={setAgents} />
      <p className="hint" style={{ margin: 0 }}>
        A skill only adds words to an agent's prompt; every word costs tokens on each run of the agents that load it.
        keel checks its signature, its checksum and its review again before it saves it. No restart.
      </p>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

/** Update to the newest version: the project's copy is replaced (its agents stay). A copy changed here is replaced only
 *  when the person says so. */
export function SkillUpdateDialog({ hit, pid, project, onClose, onDone }: {
  hit: SkillHit | MarketSkill;
  pid: string;
  project: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const { toast } = useApp();
  const edited = !!hit.installed?.edited;
  const [mode, setMode] = useState<"keep" | "replace">(edited ? "keep" : "replace");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const title = hit.title || hit.name;
  const to = hit.update ?? hit.version ?? "";
  const update = async () => {
    if (mode === "keep") return onClose();
    setBusy(true);
    setErr(null);
    try {
      await skillMarketApi.install(pid, { name: hit.name, version: to, replace: true });
      toast(`${title} is ${to} now in ${project}.`);
      onDone();
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer
      title={`Update ${title} to ${to}?`}
      onClose={onClose}
      id="sk-update"
      footer={
        <>
          <button className="btn" type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn primary" type="button" onClick={() => void update()} disabled={busy}>
            {busy ? "Updating…" : mode === "keep" ? "Keep my copy" : `Update to ${to}`}
          </button>
        </>
      }
    >
      <p style={{ margin: 0 }}>
        {project} has <b>{title}</b> <span className="mono">{hit.installed?.version}</span>. The marketplace has{" "}
        <span className="mono">{to}</span>.
      </p>
      {edited ? (
        <div className="field" role="radiogroup" aria-label="Your changed copy">
          <span className="lab">You changed this skill here (Build › Skills)</span>
          <label className="radio">
            <input type="radio" name="skmode" checked={mode === "keep"} onChange={() => setMode("keep")} />
            <span>
              <b>Keep my copy</b> — it stays as it is; no update
            </span>
          </label>
          <label className="radio">
            <input type="radio" name="skmode" checked={mode === "replace"} onChange={() => setMode("replace")} />
            <span>
              <b>Replace it with {to}</b> — your changes are gone
            </span>
          </label>
        </div>
      ) : (
        <p className="hint" style={{ margin: 0 }}>
          The text is replaced by {to}, as published; the agents that load it stay. No restart.
        </p>
      )}
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

/** Which agents load a skill (keel's own, or one installed here). */
export function SkillAgentsDialog({ sid, title, now, pid, onClose, onDone }: {
  sid: string;
  title: string;
  now: string[];
  pid: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const { toast } = useApp();
  const [agents, setAgents] = useState<string[]>(now);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await skillMarketApi.agents(pid, sid, agents);
      toast(agents.length ? `${title}: loaded by ${agents.join(", ")} from their next run.` : `${title}: no agent loads it now.`);
      onDone();
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer
      title={`Agents for ${title}`}
      onClose={onClose}
      id="sk-agents"
      footer={
        <>
          <button className="btn" type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn primary" type="button" onClick={() => void save()} disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      <AgentPicker pid={pid} value={agents} onChange={setAgents} />
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

// ---------- a skill's page ----------


export function MarketSkillPage({ name }: { name: string }) {
  const [raw, setRaw] = useState(false);          // 0.18.9 SKILL.md rendered, or as the file is
  const { pid, project, projectsLoaded, toast } = useApp();
  const r = useLoad(projectsLoaded ? `market-skill:${name}:${pid ?? ""}` : null, () => skillMarketApi.skill(name, pid), { live: false });
  const [installing, setInstalling] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const w: MarketSkill | null = r.data;
  const crumb = (
    <nav className="mk-crumb" aria-label="Breadcrumb">
      <a href="#/settings/plugins">Plugins</a>
      <span aria-hidden="true">›</span>
      <a href={MARKET_SKILLS}>Skills</a>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{w?.title ?? name}</span>
    </nav>
  );
  if (!w)
    return (
      <div className="mk-detail">
        {crumb}
        {r.error ? <ErrorBox error={r.error} onRetry={() => void r.reload()} /> : <Loading what="Reading the skill" />}
      </div>
    );
  const title = w.title || w.name;
  const p = publisherOf(w);
  const sid = w.builtin ? w.name : w.installed?.sid;
  const agentsNow = w.builtin ? (w.agents ?? []) : (w.installed?.agents ?? []);
  const remove = async () => {
    if (!pid || !w.installed) return;
    setBusy(true);
    setErr(null);
    try {
      await skillMarketApi.remove(pid, w.installed.sid);
      toast(`${title} is removed from ${project?.name ?? pid}.`);
      setRemoving(false);
      void r.reload();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const openSkills = (
    <a className="btn" href="#/skills" style={{ textDecoration: "none" }}>
      Open in Skills
    </a>
  );
  const agentsBtn = sid && (
    <button className="btn" type="button" disabled={!pid} onClick={() => setAttaching(true)}>
      Agents…
    </button>
  );
  const main = w.builtin ? (
    <>
      <Pill tone="ok">In every project</Pill>
      {agentsBtn}
      {openSkills}
    </>
  ) : w.installed ? (
    <>
      <Pill tone="ok">Installed here {w.installed.version}</Pill>
      {w.installed.edited && (
        <span className="tag warn" title="Changed here on the Skills page">
          edited
        </span>
      )}
      {w.update && (
        <button className="btn primary" type="button" disabled={!pid} onClick={() => setUpdating(true)}>
          Update to {w.update}
        </button>
      )}
      {agentsBtn}
      {openSkills}
      <button className="btn ghost" type="button" disabled={!pid} onClick={() => setRemoving(true)}>
        Remove
      </button>
    </>
  ) : (
    <>
      <button
        className="btn primary"
        type="button"
        disabled={!pid || !!w.refused || !w.version || !!w.fetch_error}
        onClick={() => setInstalling(true)}
      >
        Install {w.version ?? ""}
      </button>
      {openSkills}
    </>
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
            {w.version && <span className="mono">{w.version}</span>}
            <TrustTag trust="content" />
            <span className="tag">skill</span>
            {w.category && <span className="sub">{w.category}</span>}
          </div>
        </div>
        <div className="actions mk-dact">{main}</div>
      </div>
      {removing && (
        <Confirm
          text={<>Remove {title} from {project?.name ?? pid}? Its agents stop loading it{w.installed?.edited ? ", and the changes made here are gone" : ""}.</>}
          yes="Remove"
          busy={busy}
          onYes={() => void remove()}
          onNo={() => setRemoving(false)}
        />
      )}
      {err && <ErrorBox error={err} />}
      {w.refused && (
        <p className="mk-note bad" role="note">
          keel will not install it: {refusedText(w.refused)}
        </p>
      )}
      {w.fetch_error && (
        <p className="mk-note bad" role="alert">
          keel will not install it: {refusedText(w.fetch_error)}
        </p>
      )}
      {!pid && (
        <p className="mk-note" role="note">
          Pick a project first: a skill installs into a project.
        </p>
      )}
      <div className="mk-dgrid">
        <div className="mk-stack">
          <PanelSection id="skm-text" title="SKILL.md" extra={<span className="sub">{w.builtin ? "keel's own" : "as published, verified"}
            {w.text && <button type="button" className="btn sm ghost" style={{ marginLeft: 8 }} onClick={() => setRaw((r) => !r)}>{raw ? "Rendered" : "Raw"}</button>}</span>}>
            {w.summary && <p className="mk-about">{w.summary}</p>}
            {w.text ? (raw ? (
              <pre className="mono wfm-yaml" aria-label={`Text of ${title}`} style={{ whiteSpace: "pre-wrap", maxHeight: 520, overflow: "auto" }}>
                {w.text}
              </pre>
            ) : (
              // keel 0.18.9 the skill as it reads (headings, tables, code), without its front matter and its title (the page's
              // own); Raw shows the file
              <div className="md-view" aria-label={`Text of ${title}`} style={{ maxHeight: 620, overflow: "auto" }}>
                <Markdown text={w.text.replace(/^---\n[\s\S]*?\n---\n/, "").replace(/^\s*# [^\n]*\n/, "")} />
              </div>
            )) : (
              <p className="sub" style={{ margin: 0 }}>
                keel did not read the file, so it shows no text: {w.fetch_error || w.refused ? "see above." : "the catalog sends none."}
              </p>
            )}
          </PanelSection>
        </div>
        <div className="mk-stack">
          <PanelSection id="skm-review" title="keel's review">
            <dl className="mk-kv">
              <dt>Size</dt>
              <dd className="mono">{k1(w.review?.tokens ?? 0)} tokens</dd>
              <dt>Sections</dt>
              <dd>{w.review?.sections?.length ? w.review.sections.join(", ") : "—"}</dd>
              <dt>What it is for</dt>
              <dd>{w.review?.description || "—"}</dd>
              <dt>Loaded by</dt>
              <dd>{agentsNow.length ? agentsNow.join(", ") : "no agent here"}</dd>
              {!!w.refs?.length && (
                <>
                  <dt>References</dt>
                  <dd>{w.refs.map((x) => x.path).join(", ")} <span className="sub">(read only when the agent asks)</span></dd>
                </>
              )}
            </dl>
            <p className="mk-quote">A skill only adds words to an agent's prompt. It never lets an agent do more: keel's rules and the phase decide that.</p>
          </PanelSection>
          {!!w.versions?.length && (
            <PanelSection id="skm-versions" title="Versions" extra={<span className="sub">Newest first</span>}>
              <ul className="mk-checklist">
                {w.versions.map((x) => (
                  <li key={x.version}>
                    <span className="mono">{x.version}</span> <span className="sub">{x.released ?? ""}</span>
                    {x.version === w.installed?.version && <span className="sub"> · yours</span>}
                    {x.revoked && <span className="tag warn" title={x.revoked}> revoked</span>}
                    {x.review && <span className="sub"> · {skillShort(x.review)}</span>}
                  </li>
                ))}
              </ul>
            </PanelSection>
          )}
          {!!w.checks?.length && (
            <PanelSection id="skm-checks" title={w.builtin ? "Where it comes from" : "Checks keel runs before install"}>
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
      </div>
      {installing && pid && (
        <SkillInstallDialog hit={w} versions={w.versions} pid={pid} project={project?.name ?? pid} onClose={() => setInstalling(false)} onDone={() => void r.reload()} />
      )}
      {updating && pid && (
        <SkillUpdateDialog hit={w} pid={pid} project={project?.name ?? pid} onClose={() => setUpdating(false)} onDone={() => void r.reload()} />
      )}
      {attaching && pid && sid && (
        <SkillAgentsDialog sid={sid} title={title} now={agentsNow} pid={pid} onClose={() => setAttaching(false)} onDone={() => void r.reload()} />
      )}
    </div>
  );
}
