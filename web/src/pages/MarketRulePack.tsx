// Rule packs in the marketplace (docs/plugins/23-rules-in-marketplace.md §3), the third kind of Control › Plugins ›
// Marketplace, in the look of the workflow pages (MarketWorkflow.tsx):
//
//   Marketplace › Rules       search, the category chips, cards (icon, title, publisher ✓ / community, version, summary,
//                             "3 rules · bash 1 · edit 2 · every phase") and Install / Installed here (+ "edited") /
//                             Update to x. No catalog: a note that says so, never an empty page
//   #/settings/plugins/rules/<name>    a pack's page: every rule in a table (kind, what it matches, how, phases, lanes, except,
//                             the reason) and who enforces it (before the action / after the step, from the api: the
//                             engines' real support), requires, versions, checks, the YAML (folded); Install into this
//                             project, Update (asks before it replaces a copy changed here), Remove, Open in Rules
//
// A pack installs into the project's .keel/rules/ through the rules api (source "marketplace"): no restart, the guard reads
// it at the next tool call. A pack only refuses; it can never allow what keel's own rules forbid.

import { useEffect, useState } from "react";
import { errorParts } from "../api";
import { TrustTag } from "../components/Permissions";
import { Confirm, Drawer, ErrorBox, Loading, Pill } from "../components/ui";
import { useApp, useLoad } from "../state";
import {
  MARKET_RULES,
  packHref,
  packShort,
  phasesShort,
  rulePackMarketApi,
  whoEnforces,
  type MarketRulePack,
  type ReadRule,
  type RulePackHit,
  type RulePackReview,
  type RulePackSearch,
  type RulePackVersion,
} from "../rulePackMarketApi";
import { KIND_WORDS } from "../rulesApi";
import { PanelSection } from "../components/PanelSection";
import { useDebounced } from "../hooks";
import { type Err, refusedText } from "../marketHttp";
import { publisherOf } from "../marketplaceApi";
import { PluginIcon, Publisher, fromKeel, isBeta } from "./pluginLook";



/** "3 rules · bash 1 · edit 2 · every phase". */
export function PackShort({ review }: { review: RulePackReview | null | undefined }) {
  return <span className="wfm-short rpm-short">{packShort(review)}</span>;
}

// ---------- Marketplace › Rules ----------

export function RulesMarket() {
  const { pid, project, projectsLoaded } = useApp();
  const [typed, setTyped] = useState("");
  const q = useDebounced(typed.trim());
  const [cat, setCat] = useState("");
  // the search names the project (what is installed there): it waits for the projects
  const r = useLoad(projectsLoaded ? `market-rules:${q}:${cat}:${pid ?? ""}` : null, () =>
    rulePackMarketApi.search(q, cat, pid),
  );
  const [installing, setInstalling] = useState<RulePackHit | null>(null);
  const [updating, setUpdating] = useState<RulePackHit | null>(null);
  const [cats, setCats] = useState<string[]>([]);
  useEffect(() => {
    if (r.data?.categories?.length) setCats(r.data.categories);
  }, [r.data]);
  const data: RulePackSearch | null = r.data;
  const statuses = data?.sources ?? [];
  const all = data?.rules ?? [];
  const hits = all.filter((h) => !cat || h.category === cat);
  const down = statuses.length > 0 && statuses.every((s) => s.ok === false);
  const none = !!r.error || down || (!!data && !statuses.length && !all.length);

  const card = (h: RulePackHit) => {
    const p = publisherOf(h);
    const title = h.title || h.name;
    const shown = h.version ?? h.latest;
    return (
      <article key={h.name} className="mk-card" aria-label={`${title} rule pack`}>
        <div className="mk-card-h">
          <PluginIcon name={h.name} title={title} />
          <div style={{ minWidth: 0 }}>
            <h3>
              <a className="mk-card-link" href={packHref(h.name)}>
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
        <p className="mk-sum">{h.summary}</p>
        <div className="wfm-facts">
          <PackShort review={h.review} />
          {h.review?.lanes?.length > 0 && (
            <span className="mk-card-tags">
              {h.review.lanes.map((l) => (
                <span key={l} className="tag">
                  {l} lane
                </span>
              ))}
            </span>
          )}
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
              <span className="tag warn" title="Changed here on the Rules page. An update asks before it replaces your copy.">
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
            placeholder="Search rule packs: dependencies, secrets, git…"
            aria-label="Search rule packs"
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
          · a rule pack only refuses: it can never allow what keel's own rules forbid. Open one to see every rule it
          blocks, in which phases, and which part of keel checks it.
        </span>
      </div>
      {!pid && (
        <p className="mk-note" role="note">
          Pick a project first: a rule pack installs into a project (its .keel/rules/ folder).
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
        <div className="mk-note mk-local wfm-none" role="note" aria-label="No rule pack catalog">
          <b>{down || r.error ? "No rule pack catalog answers yet." : "The catalogs list no rule packs yet."}</b> Rule packs
          come from the same signed catalogs as plugins and workflows. Add a catalog in Sources and rules, or install a
          pack from keel's library on Build › Safety & History.
          {r.error && (
            <span className="sub" style={{ display: "block" }}>
              {r.error.message}
            </span>
          )}
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
            <a className="btn sm" href="#/rules" style={{ textDecoration: "none" }}>
              Build › Safety & History
            </a>
          </span>
        </div>
      ) : !data ? (
        <Loading what="Searching the rule packs" />
      ) : !hits.length ? (
        <div className="mk-empty">
          {typed.trim()
            ? `No rule pack matches “${typed.trim()}”. Try another word.`
            : cat
              ? "No rule pack in this category yet."
              : "The catalogs list no rule packs yet."}
        </div>
      ) : (
        <>
          {keel.length > 0 && (
            <section aria-labelledby="rpm-keel">
              <div className="mk-sect">
                <h2 id="rpm-keel">From keel</h2>
                <p className="sub">Made and signed by keel.</p>
              </div>
              <div className="mk-cards">{keel.map(card)}</div>
            </section>
          )}
          {community.length > 0 && (
            <section aria-labelledby="rpm-community">
              <div className="mk-sect">
                <h2 id="rpm-community">From the community</h2>
                <p className="sub">Published through a pull request to a catalog repo.</p>
              </div>
              <div className="mk-cards">{community.map(card)}</div>
            </section>
          )}
        </>
      )}
      {installing && pid && (
        <PackInstallDialog
          hit={installing}
          pid={pid}
          project={project?.name ?? pid}
          onClose={() => setInstalling(null)}
          onDone={() => void r.reload()}
        />
      )}
      {updating && pid && (
        <PackUpdateDialog
          hit={updating}
          pid={pid}
          project={project?.name ?? pid}
          onClose={() => setUpdating(null)}
          onDone={() => void r.reload()}
        />
      )}
    </div>
  );
}

// ---------- install and update ----------

/** What the pack blocks, in short, from the catalog's review (a page shows keel's own reading). */
function BlocksList({ review }: { review: RulePackReview }) {
  const blocks = review.blocks ?? [];
  if (!blocks.length) return <PackShort review={review} />;
  return (
    <ul className="perms rpm-blocks" aria-label="What it blocks">
      {blocks.map((b) => (
        <li key={b.id} className="perm">
          <span className="lvl low">{b.kind}</span>
          <span>
            <b className="mono">{b.match.join(", ")}</b>
            <span className="why">
              {b.how} · {phasesShort(b.phases)}
              {b.lanes?.length ? ` · ${b.lanes.join(", ")} lane` : ""}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

export function PackInstallDialog({
  hit,
  versions,
  pid,
  project,
  onClose,
  onDone,
}: {
  hit: RulePackHit;
  versions?: RulePackVersion[];
  pid: string;
  project: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const { toast } = useApp();
  const [version, setVersion] = useState(hit.version ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const title = hit.title || hit.name;
  const usable = (versions ?? []).filter((v) => v.fits !== false);
  const review = versions?.find((v) => v.version === version)?.review ?? hit.review;
  const install = async () => {
    setBusy(true);
    setErr(null);
    try {
      await rulePackMarketApi.install(pid, { name: hit.name, ...(version && version !== hit.version ? { version } : {}) });
      toast(`${title} ${version} is installed in ${project}. Agents meet it at their next tool call.`);
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
      id="rp-install"
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
          <label htmlFor="rpi-version">Version</label>
          <select id="rpi-version" value={version} onChange={(e) => setVersion(e.target.value)}>
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
          <b>This project</b> — {project}: <span className="mono">.keel/rules/{hit.name}.yaml</span>
        </span>
      </div>
      <div className="field">
        <span className="lab">What it blocks</span>
        <BlocksList review={review} />
      </div>
      <p className="hint" style={{ margin: 0 }}>
        A rule pack only refuses: it never allows what keel's own rules forbid. keel checks its signature, its checksum
        and its rules again before it saves the file. No restart: agents meet it at their next tool call.
      </p>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

/** Update to the newest version: the rules api replaces the file. A copy changed here is replaced only when the person
 *  says so (else it stays as it is, and the update waits). */
export function PackUpdateDialog({
  hit,
  pid,
  project,
  onClose,
  onDone,
}: {
  hit: RulePackHit;
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
      await rulePackMarketApi.install(pid, { name: hit.name, version: to, replace: true });
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
      id="rp-update"
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
          <span className="lab">You changed this pack here (Build › Safety & History)</span>
          <label className="radio">
            <input type="radio" name="rpmode" checked={mode === "keep"} onChange={() => setMode("keep")} />
            <span>
              <b>Keep my copy</b> — it stays as it is; no update
            </span>
          </label>
          <label className="radio">
            <input type="radio" name="rpmode" checked={mode === "replace"} onChange={() => setMode("replace")} />
            <span>
              <b>Replace it with {to}</b> — your changes are gone (export it first from Build › Safety & History to keep them)
            </span>
          </label>
        </div>
      ) : (
        <p className="hint" style={{ margin: 0 }}>
          The file <span className="mono">.keel/rules/{hit.name}.yaml</span> is replaced by {to}, as published. No
          restart.
        </p>
      )}
      <div className="field">
        <span className="lab">What {to} blocks</span>
        <BlocksList review={hit.review} />
      </div>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

// ---------- a pack's page ----------

const bytes = (n?: number) => (!n ? null : `${Math.max(1, Math.round(n / 1024))} KB`);


const list = (xs?: string[]) => (xs?.length ? xs.join(", ") : "—");
const phasesCell = (p: string | string[]) =>
  Array.isArray(p) ? p.join(", ") : ({ all: "every phase", "in-flow": "in a flow", none: "outside a flow" }[p] ?? p);

/** Every rule of the pack, as keel reads it, with who enforces it. */
function RulesTable({ rules, w }: { rules: ReadRule[]; w: MarketRulePack }) {
  return (
    <div className="table-wrap">
      <table className="mk-table rpm-table" aria-label={`Rules of ${w.title || w.name}`}>
        <thead>
          <tr>
            <th>Rule</th>
            <th>Kind</th>
            <th>What it matches</th>
            <th>How</th>
            <th>Phases</th>
            <th>Lanes</th>
            <th>Except</th>
            <th>The reason (the agent reads it)</th>
            <th>Who enforces it</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => {
            const who = whoEnforces(r, w.engines);
            return (
              <tr key={r.id} data-rule={r.id}>
                <td className="mono">{r.id}</td>
                <td>
                  <span className="tag" title={KIND_WORDS[r.kind]}>
                    {r.kind}
                  </span>
                </td>
                <td className="mono">{r.match.join(", ")}</td>
                <td className="mono">{r.how}</td>
                <td>{phasesCell(r.phases)}</td>
                <td>{list(r.lanes)}</td>
                <td className="mono">{list(r.except)}</td>
                <td>{r.reason}</td>
                <td className="rpm-who" data-testid={`who-${r.id}`}>
                  {who.before.length > 0 && (
                    <div>
                      <span className="rpm-when before">before the action</span> {who.before.join(", ")}
                    </div>
                  )}
                  {who.after.length > 0 && (
                    <div>
                      <span className="rpm-when after">after the step</span> {who.after.join(", ")}
                    </div>
                  )}
                  {!who.before.length && !who.after.length && <span className="sub">no engine checks it</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Who enforces the pack, summed up over its rules (from the api's `by`, never a list of its own). */
function Enforcers({ w }: { w: MarketRulePack }) {
  const before = new Map<string, string[]>();
  const after = new Map<string, string[]>();
  for (const r of w.rules) {
    const who = whoEnforces(r, w.engines);
    for (const b of who.before) before.set(b, [...(before.get(b) ?? []), r.id]);
    for (const a of who.after) after.set(a, [...(after.get(a) ?? []), r.id]);
  }
  const line = (m: Map<string, string[]>) =>
    [...m.entries()].map(([what, ids]) => (
      <li key={what}>
        {what} <span className="sub">· {ids.length === w.rules.length ? "every rule" : ids.join(", ")}</span>
      </li>
    ));
  return (
    <div className="rpm-enforcers" aria-label="Who enforces it">
      <div>
        <span className="rpm-when before">before the action</span>
        {before.size ? <ul>{line(before)}</ul> : <p className="sub">Nothing checks these rules before the action.</p>}
      </div>
      <div>
        <span className="rpm-when after">after the step</span>
        {after.size ? (
          <ul>{line(after)}</ul>
        ) : (
          <p className="sub">Nothing after the step: the diff guard checks only file changes (edit rules).</p>
        )}
      </div>
      {!!w.engines?.length && (
        <dl className="mk-kv">
          {w.engines.map((e) => (
            <span key={e.engine} style={{ display: "contents" }}>
              <dt>{e.title}</dt>
              <dd>{e.words}</dd>
            </span>
          ))}
        </dl>
      )}
    </div>
  );
}

export function MarketRulePackPage({ name }: { name: string }) {
  const { pid, project, health, projectsLoaded, toast } = useApp();
  const r = useLoad(projectsLoaded ? `market-rule:${name}:${pid ?? ""}` : null, () => rulePackMarketApi.pack(name, pid), {
    live: false,
  });
  const [installing, setInstalling] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const w: MarketRulePack | null = r.data;
  const crumb = (
    <nav className="mk-crumb" aria-label="Breadcrumb">
      <a href="#/settings/plugins">Plugins</a>
      <span aria-hidden="true">›</span>
      <a href={MARKET_RULES}>Rules</a>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{w?.title ?? name}</span>
    </nav>
  );
  if (!w)
    return (
      <div className="mk-detail">
        {crumb}
        {r.error ? <ErrorBox error={r.error} onRetry={() => void r.reload()} /> : <Loading what="Reading the rule pack" />}
      </div>
    );
  const title = w.title || w.name;
  const p = publisherOf(w);
  const v = w.versions?.find((x) => x.version === w.version) ?? w.versions?.[0];
  const requires = w.pack?.requires ?? w.requires;
  const plugins = Object.entries(requires?.plugins ?? {});
  const remove = async () => {
    if (!pid) return;
    setBusy(true);
    setErr(null);
    try {
      await rulePackMarketApi.remove(pid, w.name);
      toast(`${title} is removed from ${project?.name ?? pid} (.keel/rules/${w.name}.yaml).`);
      setRemoving(false);
      void r.reload();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const openRules = (
    <a className="btn" href="#/rules" style={{ textDecoration: "none" }}>
      Open in Rules
    </a>
  );
  const main = w.installed ? (
    <>
      <Pill tone="ok">Installed here {w.installed.version}</Pill>
      {w.installed.edited && (
        <span className="tag warn" title="Changed here on the Rules page">
          edited
        </span>
      )}
      {w.update && (
        <button className="btn primary" type="button" disabled={!pid} onClick={() => setUpdating(true)}>
          Update to {w.update}
        </button>
      )}
      {openRules}
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
      {openRules}
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
            <span className="tag">rule pack</span>
            {w.category && <span className="sub">{w.category}</span>}
          </div>
        </div>
        <div className="actions mk-dact">{main}</div>
      </div>
      {removing && (
        <Confirm
          text={
            <>
              Remove {title} from {project?.name ?? pid}? This deletes <span className="mono">.keel/rules/{w.name}.yaml</span>
              {w.installed?.edited ? ", with the changes made here" : ""}.
            </>
          }
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
          Pick a project first: a rule pack installs into a project.
        </p>
      )}
      <div className="wfm-steps">
        <PanelSection id="rpm-rules" title="Rules" extra={<PackShort review={w.review} />} body={false}>
          <div className="panel-body">
            {w.summary && <p className="mk-about">{w.summary}</p>}
            {!w.rules.length && (
              <p className="sub" style={{ margin: 0 }}>
                keel did not read the file, so it shows no rules: {w.fetch_error || w.refused ? "see above." : "the catalog sends none."}
              </p>
            )}
          </div>
          {w.rules.length > 0 && <RulesTable rules={w.rules} w={w} />}
          {w.yaml && (
            <div className="panel-body">
              <details className="wfm-yaml">
                <summary>The YAML (installed as it is)</summary>
                <pre className="mono" aria-label={`YAML of ${title}`}>
                  {w.yaml}
                </pre>
              </details>
            </div>
          )}
        </PanelSection>
      </div>
      <div className="mk-dgrid">
        <div className="mk-stack">
          <PanelSection id="rpm-who" title="Who enforces it" extra={<span className="sub">from each engine's real support</span>}>
            {w.rules.length ? <Enforcers w={w} /> : <p className="sub" style={{ margin: 0 }}>—</p>}
            <p className="mk-quote">
              A pack only refuses: it never allows what keel's own rules forbid. The refusal the agent reads ends with
              (rule {w.name}/&lt;rule&gt;).
            </p>
          </PanelSection>
          <PanelSection id="rpm-versions" title="Versions" extra={<span className="sub">Newest first</span>} body={false}>
            <div className="table-wrap">
              <table className="mk-table mk-vtable">
                <thead>
                  <tr>
                    <th>Version</th>
                    <th>Works with</th>
                    <th>Released</th>
                    <th>What it blocks</th>
                  </tr>
                </thead>
                <tbody>
                  {(w.versions ?? []).map((x) => (
                    <tr key={x.version}>
                      <td className="mono">
                        {x.version}
                        {x.version === w.installed?.version && <span className="sub"> · yours</span>}
                        {x.revoked && <span className="tag warn" title={x.revoked}> revoked</span>}
                      </td>
                      <td className="mono">{x.requires?.keel ? `keel ${x.requires.keel}` : "—"}</td>
                      <td className="num sub">{x.released ?? "—"}</td>
                      <td>{x.review ? <PackShort review={x.review} /> : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </PanelSection>
        </div>
        <div className="mk-stack">
          <PanelSection id="rpm-requires" title="Requires">
            <div className="mk-checklist" aria-label="What it needs">
              <div>
                <span className="mk-ok" aria-hidden="true">✓</span>
                <span>
                  keel core {requires?.keel && <span className="mono">{requires.keel}</span>}
                  {health?.version && (
                    <>
                      {" "}· you have <span className="mono">{health.version.replace(/^v/, "")}</span>
                    </>
                  )}
                </span>
              </div>
              {plugins.map(([n, range]) => (
                <div key={n}>
                  <span className="mk-ok" aria-hidden="true">·</span>
                  <span>
                    <b>{n}</b> {range && <span className="mono sub">{range}</span>}{" "}
                    <span className="sub">plugin · shown for people; keel's guard applies the pack without it</span>
                  </span>
                </div>
              ))}
            </div>
            <dl className="mk-kv">
              {bytes(v?.size) && (
                <>
                  <dt>Size</dt>
                  <dd className="mono">{bytes(v?.size)}</dd>
                </>
              )}
              <dt>Installs as</dt>
              <dd className="mono">.keel/rules/{w.name}.yaml</dd>
              <dt>After install</dt>
              <dd>Used at the next tool call, no restart</dd>
              <dt>From</dt>
              <dd className="mono">{w.source}</dd>
            </dl>
          </PanelSection>
          {!!w.checks?.length && (
            <PanelSection id="rpm-checks" title="Checks keel runs before install">
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
        <PackInstallDialog
          hit={w}
          versions={w.versions}
          pid={pid}
          project={project?.name ?? pid}
          onClose={() => setInstalling(false)}
          onDone={() => void r.reload()}
        />
      )}
      {updating && pid && (
        <PackUpdateDialog
          hit={w}
          pid={pid}
          project={project?.name ?? pid}
          onClose={() => setUpdating(false)}
          onDone={() => void r.reload()}
        />
      )}
    </div>
  );
}
