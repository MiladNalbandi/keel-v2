// Stacks in the marketplace (keel 0.18.9), the fifth kind of Settings › Plugins › Marketplace, in the look of the skill
// pages (MarketSkill.tsx):
//
//   Marketplace › Stacks      search, the category chips, keel's own stacks ("From keel": used where detected, a keel
//                             pack installs with its skills) and the catalogs' (Install / Installed here (+ "edited") /
//                             Update to x). No catalog: a note that says so; keel's own still show
//   #/settings/plugins/stacks/<name>   a stack's page: its YAML (as published, verified), keel's review (lane, what detects it,
//                             layers, every command and tool it runs, skills), keel's check against this project,
//                             versions, checks; Install into this project, Update (asks before it replaces a copy
//                             changed here), Remove, Open in Stacks
//
// A stack is how one project builds and tests: one YAML file, saved as .keel/stacks/<name>.yml. Its commands run in
// the project's own toolchain through keel's flow, so the page shows every one of them before the install.

import { Fragment, useEffect, useState } from "react";
import { errorParts } from "../api";
import { TrustTag } from "../components/Permissions";
import { Confirm, Drawer, ErrorBox, Loading, Pill } from "../components/ui";
import {
  MARKET_STACKS,
  stackHref,
  stackMarketApi,
  stackShort,
  type BuiltinStack,
  type MarketStack,
  type StackHit,
  type StackReview,
  type StackSearch,
} from "../stackMarketApi";
import { useApp, useLoad } from "../state";
import { PanelSection } from "../components/PanelSection";
import { useDebounced } from "../hooks";
import { type Err, refusedText } from "../marketHttp";
import { publisherOf } from "../marketplaceApi";
import { PluginIcon, Publisher, fromKeel, isBeta } from "./pluginLook";


const detectText = (d: Record<string, string[]> | undefined) =>
  Object.entries(d ?? {}).filter(([, v]) => v?.length).map(([k, v]) => `${k.replace("_", " ")}: ${v.join(", ")}`).join(" · ");

// ---------- Marketplace › Stacks ----------

export function StacksMarket() {
  const { pid, project, projectsLoaded, toast } = useApp();
  const [typed, setTyped] = useState("");
  const q = useDebounced(typed.trim());
  const [cat, setCat] = useState("");
  const r = useLoad(projectsLoaded ? `market-stacks:${q}:${cat}:${pid ?? ""}` : null, () => stackMarketApi.search(q, cat, pid));
  const [installing, setInstalling] = useState<StackHit | null>(null);
  const [packing, setPacking] = useState<string | null>(null);
  const [cats, setCats] = useState<string[]>([]);
  useEffect(() => {
    if (r.data?.categories?.length) setCats(r.data.categories);
  }, [r.data]);
  const data: StackSearch | null = r.data;
  const statuses = data?.sources ?? [];
  const hits = (data?.stacks ?? []).filter((h) => !cat || h.category === cat);
  const mine = data?.builtin ?? [];
  const noCatalog = !!data && (!!data.catalog_error || !statuses.length || statuses.every((s) => s.ok === false)) && !hits.length;
  const installPack = async (name: string) => {
    if (!pid) return;
    setPacking(name);
    try {
      await stackMarketApi.installPack(pid, name);
      toast(`Pack ${name} installed in ${project?.name ?? pid}: its stack file and skills are in .keel/stacks/${name}.`);
      void r.reload();
    } catch (e) {
      const p = errorParts(e);
      toast(`Not installed: ${p.message}${p.hint ? ` ${p.hint}` : ""}`);
    } finally {
      setPacking(null);
    }
  };

  const card = (h: StackHit) => {
    const p = publisherOf(h);
    const title = h.title || h.name;
    const shown = h.version ?? h.latest;
    return (
      <article key={h.name} className="mk-card" aria-label={`${title} stack`}>
        <div className="mk-card-h">
          <PluginIcon name={h.name} title={title} />
          <div style={{ minWidth: 0 }}>
            <h3>
              <a className="mk-card-link" href={stackHref(h.name)}>{title}</a>
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
          <span className="wfm-short">{stackShort(h.review)}</span>
          {detectText(h.review?.detect) && <span className="sub mono">{detectText(h.review.detect)}</span>}
        </div>
        <div className="mk-card-f">
          <span className="mk-card-tags">
            <TrustTag trust="content" />
            {h.refused && <Pill tone="bad" title={refusedText(h.refused)}>refused</Pill>}
            {h.installed?.edited && <span className="tag warn" title="Changed here. An update asks before it replaces your copy.">edited</span>}
            {h.conflict && !h.installed && <span className="tag warn" title={`This project has its own stack called ${h.name}: an install replaces it.`}>name taken here</span>}
          </span>
          {h.installed ? (
            h.update ? (
              <a className="btn sm" href={stackHref(h.name)} style={{ textDecoration: "none" }}>Update to {h.update}</a>
            ) : (
              <span title={`Installed here: ${h.installed.version}`}><Pill tone="ok">Installed here</Pill></span>
            )
          ) : (
            <button className="btn sm primary" type="button" disabled={!pid || !!h.refused || !h.version} onClick={() => setInstalling(h)}
              aria-label={`Install ${title}`}>
              Install
            </button>
          )}
        </div>
      </article>
    );
  };

  const builtinCard = (b: BuiltinStack) => (
    <article key={b.name} className="mk-card" aria-label={`${b.name} stack`}>
      <div className="mk-card-h">
        <PluginIcon name={b.name} title={b.name} />
        <div style={{ minWidth: 0 }}>
          <h3><a className="mk-card-link" href={stackHref(b.name)}>{b.name}</a></h3>
          <div className="mk-by">
            <Publisher name="keel" verified />
            <span className="mono faint">{b.source === "keel pack" ? "pack" : "built in"}</span>
          </div>
        </div>
      </div>
      <p className="mk-sum">{b.lane === "web" ? "A frontend" : "A backend"} stack. Found by {detectText(b.detect) || "nothing"}.</p>
      <div className="mk-card-f">
        <span className="mk-card-tags">
          <TrustTag trust="content" />
          {b.off && <span className="tag warn">turned off here</span>}
        </span>
        {b.installable && !b.installed ? (
          <button className="btn sm" type="button" disabled={!pid || packing !== null} onClick={() => void installPack(b.name)} aria-label={`Install pack ${b.name}`}>
            {packing === b.name ? "Installing…" : "Install pack"}
          </button>
        ) : b.installed ? (
          <Pill tone="ok">Installed here</Pill>
        ) : b.used ? (
          <Pill tone="ok">Used here</Pill>
        ) : (
          <span className="sub">used where detected</span>
        )}
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
          <input type="search" placeholder="Search stacks: laravel, rails, rust…" aria-label="Search stacks" value={typed}
            onChange={(e) => setTyped(e.target.value)} />
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
          · a stack's commands run in your project's toolchain through keel's flow and its shell guard. Open one to read
          every command first.
        </span>
      </div>
      {!pid && <p className="mk-note" role="note">Pick a project first: a stack installs into a project.</p>}
      {r.error ? (
        <ErrorBox error={r.error} onRetry={() => void r.reload()} />
      ) : !data ? (
        <Loading what="Searching the stacks" />
      ) : (
        <>
          {mine.length > 0 && !cat && (
            <section aria-labelledby="stm-builtin">
              <div className="mk-sect">
                <h2 id="stm-builtin">From keel</h2>
                <p className="sub">keel's own stacks: used in every project they detect. A pack installs its stack file and skills.</p>
              </div>
              <div className="mk-cards">{mine.map(builtinCard)}</div>
            </section>
          )}
          {keel.length > 0 && (
            <section aria-labelledby="stm-keel">
              <div className="mk-sect"><h2 id="stm-keel">From keel's catalog</h2><p className="sub">Made and signed by keel.</p></div>
              <div className="mk-cards">{keel.map(card)}</div>
            </section>
          )}
          {community.length > 0 && (
            <section aria-labelledby="stm-community">
              <div className="mk-sect"><h2 id="stm-community">From the community</h2><p className="sub">Published through a pull request to a catalog repo.</p></div>
              <div className="mk-cards">{community.map(card)}</div>
            </section>
          )}
          {noCatalog && (
            <div className="mk-note mk-local" role="note" aria-label="No stack catalog">
              <b>{data.catalog_error ? "No stack catalog answers yet." : "The catalogs list no stacks yet."}</b> Stacks from
              others come from the same signed catalogs as plugins, workflows, rule packs and skills. Add a catalog in
              Sources and rules, or write one for this project on Build › Stacks (keel reads your build files and helps).
              {data.catalog_error && <span className="sub" style={{ display: "block" }}>{refusedText(data.catalog_error)}</span>}
              <span className="row" style={{ marginTop: 8 }}>
                <a className="btn sm" href="#/settings/plugins?tab=sources" style={{ textDecoration: "none" }}>Sources and rules</a>
                <a className="btn sm" href="#/stacks" style={{ textDecoration: "none" }}>Build › Stacks</a>
              </span>
            </div>
          )}
          {!mine.length && !hits.length && (typed.trim() || cat) && (
            <div className="mk-empty">{typed.trim() ? `No stack matches “${typed.trim()}”. Try another word.` : "No stack in this category yet."}</div>
          )}
        </>
      )}
      {installing && pid && (
        <StackInstallDialog hit={installing} pid={pid} project={project?.name ?? pid} onClose={() => setInstalling(null)} onDone={() => void r.reload()} />
      )}
    </div>
  );
}

// ---------- install (and update) ----------

export function StackInstallDialog({ hit, pid, project, to, onClose, onDone }: {
  hit: StackHit | MarketStack;
  pid: string;
  project: string;
  /** an update to this version (replaces the copy here) */
  to?: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const { toast } = useApp();
  const installed = typeof hit.installed === "object" ? hit.installed : null;
  const replacing = !!to || !!hit.conflict;
  const edited = !!installed?.edited;
  const [sure, setSure] = useState(!edited && !hit.conflict);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const title = hit.title || hit.name;
  const version = to ?? hit.version ?? "";
  const install = async () => {
    setBusy(true);
    setErr(null);
    try {
      const done = await stackMarketApi.install(pid, { name: hit.name, ...(to ? { version: to } : {}), ...(replacing ? { replace: true } : {}) });
      toast(`${title} ${done.market.version} is ${to ? "updated" : "installed"} in ${project} (.keel/stacks/${hit.name}.yml).`
        + (done.warnings?.length ? ` keel says: ${done.warnings[0]}` : ""));
      onDone();
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const review: StackReview | undefined = hit.review;
  return (
    <Drawer title={to ? `Update ${title} to ${to}?` : `Install ${title} ${version}?`} onClose={onClose} id="st-install"
      footer={<>
        <button className="btn" type="button" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn primary" type="button" onClick={() => void install()} disabled={busy || !sure}>
          {busy ? "Installing…" : to ? `Update to ${to}` : "Install"}
        </button>
      </>}>
      <div className="row"><span className="mk-by"><Publisher {...publisherOf(hit)} /></span><TrustTag trust="content" /></div>
      <div className="field">
        <span className="lab">Into</span>
        <span><b>This project</b> — {project}: <span className="mono">.keel/stacks/{hit.name}.yml</span></span>
      </div>
      <div className="field">
        <span className="lab">What it runs</span>
        {review && Object.keys(review.commands ?? {}).length ? (
          <div className="kv kv-col">{Object.entries(review.commands).map(([k, v]) => <Fragment key={k}><span>{k}</span><b className="mono">{v}</b></Fragment>)}</div>
        ) : <span className="sub">no commands</span>}
        {review?.tools?.length ? <span className="hint">Tools: {review.tools.map((t) => `${t.name} (${t.on})`).join(", ")}</span> : null}
      </div>
      {(edited || hit.conflict) && (
        <label className="chk">
          <input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} />{" "}
          {edited ? "You changed this stack here: replace your copy (your changes are gone)" : `${project} has its own stack called ${hit.name}: replace it`}
        </label>
      )}
      <p className="hint" style={{ margin: 0 }}>
        keel checks its signature, its checksum, its review and the stack itself again before it saves it. No restart.
      </p>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

// ---------- a stack's page ----------


export function MarketStackPage({ name }: { name: string }) {
  const { pid, project, projectsLoaded, toast } = useApp();
  const r = useLoad(projectsLoaded ? `market-stack:${name}:${pid ?? ""}` : null, () => stackMarketApi.stack(name, pid), { live: false });
  const [installing, setInstalling] = useState<null | "install" | "update">(null);
  const [removing, setRemoving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const w: MarketStack | null = r.data;
  const crumb = (
    <nav className="mk-crumb" aria-label="Breadcrumb">
      <a href="#/settings/plugins">Plugins</a><span aria-hidden="true">›</span>
      <a href={MARKET_STACKS}>Stacks</a><span aria-hidden="true">›</span>
      <span aria-current="page">{w?.title ?? name}</span>
    </nav>
  );
  if (!w)
    return <div className="mk-detail">{crumb}{r.error ? <ErrorBox error={r.error} onRetry={() => void r.reload()} /> : <Loading what="Reading the stack" />}</div>;
  const title = w.title || w.name;
  const p = publisherOf(w);
  const installed = typeof w.installed === "object" ? w.installed : null;
  const remove = async () => {
    if (!pid) return;
    setBusy(true);
    setErr(null);
    try {
      await stackMarketApi.remove(pid, w.name);
      toast(`${title} is removed from ${project?.name ?? pid}.`);
      setRemoving(false);
      void r.reload();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const packInstall = async () => {
    if (!pid) return;
    setBusy(true);
    setErr(null);
    try {
      await stackMarketApi.installPack(pid, w.name);
      toast(`Pack ${w.name} installed in ${project?.name ?? pid}.`);
      void r.reload();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const openStacks = <a className="btn" href="#/stacks" style={{ textDecoration: "none" }}>Open in Stacks</a>;
  const main = w.builtin ? (
    <>
      {w.installable && !w.installed ? (
        <button className="btn primary" type="button" disabled={!pid || busy} onClick={() => void packInstall()}>Install pack</button>
      ) : w.installed ? <Pill tone="ok">Installed here</Pill> : w.used ? <Pill tone="ok">Used here</Pill> : <span className="sub">Used where detected</span>}
      {w.off && <span className="tag warn">turned off here</span>}
      {openStacks}
    </>
  ) : installed ? (
    <>
      <Pill tone="ok">Installed here {installed.version}</Pill>
      {installed.edited && <span className="tag warn" title="Changed here">edited</span>}
      {w.update && <button className="btn primary" type="button" disabled={!pid} onClick={() => setInstalling("update")}>Update to {w.update}</button>}
      {openStacks}
      <button className="btn ghost" type="button" disabled={!pid} onClick={() => setRemoving(true)}>Remove</button>
    </>
  ) : (
    <>
      <button className="btn primary" type="button" disabled={!pid || !!w.refused || !w.version || !!w.fetch_error || w.check?.ok === false}
        onClick={() => setInstalling("install")}>
        Install {w.version ?? ""}
      </button>
      {openStacks}
    </>
  );
  const rv = w.review;
  return (
    <div className="mk-detail">
      {crumb}
      <div className="mk-dhead">
        <PluginIcon name={w.name} title={title} size="lg" />
        <div style={{ minWidth: 0 }}>
          <h1>{title}{isBeta(w.version ?? undefined) && <span className="tag warn">beta</span>}</h1>
          <div className="mk-meta">
            {p.name && <span className="mk-by"><Publisher name={p.name} verified={p.verified} /></span>}
            {w.version && <span className="mono">{w.version}</span>}
            <TrustTag trust="content" />
            <span className="tag">stack</span>
            {(rv?.lane || w.lane) && <span className="sub">{rv?.lane || w.lane}</span>}
          </div>
        </div>
        <div className="actions mk-dact">{main}</div>
      </div>
      {removing && (
        <Confirm text={<>Remove {title} from {project?.name ?? pid}? keel deletes .keel/stacks/{w.name}.yml{installed?.edited ? ", with the changes made here" : ""}.</>}
          yes="Remove" busy={busy} onYes={() => void remove()} onNo={() => setRemoving(false)} />
      )}
      {err && <ErrorBox error={err} />}
      {w.refused && <p className="mk-note bad" role="note">keel will not install it: {refusedText(w.refused)}</p>}
      {w.fetch_error && <p className="mk-note bad" role="alert">keel will not install it: {refusedText(w.fetch_error)}</p>}
      {w.check && !w.check.ok && (
        <p className="mk-note bad" role="alert">keel's check finds errors: {w.check.errors.join("; ")}</p>
      )}
      {!pid && <p className="mk-note" role="note">Pick a project first: a stack installs into a project.</p>}
      <div className="mk-dgrid">
        <div className="mk-stack">
          <PanelSection id="stm-yaml" title="Stack YAML" extra={<span className="sub">{w.builtin ? "keel's own" : "as published, verified"}</span>}>
            {w.summary && <p className="mk-about">{w.summary}</p>}
            {w.yaml ? (
              <pre className="mono wfm-yaml" aria-label={`YAML of ${title}`} style={{ whiteSpace: "pre-wrap", maxHeight: 520, overflow: "auto" }}>{w.yaml}</pre>
            ) : (
              <p className="sub" style={{ margin: 0 }}>keel did not read the file, so it shows no YAML: {w.fetch_error || w.refused ? "see above." : "the catalog sends none."}</p>
            )}
          </PanelSection>
        </div>
        <div className="mk-stack">
          <PanelSection id="stm-review" title="keel's review">
            <dl className="mk-kv">
              <dt>Lane</dt><dd>{rv?.lane || "—"}</dd>
              <dt>Found by</dt><dd className="mono">{detectText(rv?.detect) || "—"}</dd>
              <dt>Test layers</dt><dd>{rv?.layers?.length ? rv.layers.join(" → ") : "—"}</dd>
              <dt>Commands</dt>
              <dd>{rv && Object.keys(rv.commands ?? {}).length ? (
                <ul className="mk-checklist">{Object.entries(rv.commands).map(([k, v]) => <li key={k}><span className="sub">{k}</span> <span className="mono">{v}</span></li>)}</ul>
              ) : "—"}</dd>
              <dt>Tools</dt>
              <dd>{rv?.tools?.length ? rv.tools.map((t) => `${t.name} (${t.on}, ${t.fail})`).join(", ") : "—"}</dd>
              <dt>Skills</dt><dd>{rv?.skills?.length ? rv.skills.join(", ") : "—"}</dd>
            </dl>
            {w.check && (
              <div className="grid" style={{ gap: 4 }} aria-label="keel's check">
                {w.check.detects_here !== null && <span className="sub">Its detect block {w.check.detects_here ? "matches" : "does not match"} this project's files.</span>}
                {w.check.warnings.map((x) => <span key={x} className="sub">warning: {x}</span>)}
                {!!w.check.skills_missing?.length && <span className="sub">Skills this project does not have: {w.check.skills_missing.join(", ")} (Marketplace › Skills).</span>}
              </div>
            )}
          </PanelSection>
          {!!w.versions?.length && (
            <PanelSection id="stm-versions" title="Versions" extra={<span className="sub">Newest first</span>}>
              <ul className="mk-checklist">
                {w.versions.map((x) => (
                  <li key={x.version}>
                    <span className="mono">{x.version}</span> <span className="sub">{x.released ?? ""}</span>
                    {x.version === installed?.version && <span className="sub"> · yours</span>}
                    {x.revoked && <span className="tag warn" title={x.revoked}> revoked</span>}
                  </li>
                ))}
              </ul>
            </PanelSection>
          )}
          {!!w.checks?.length && (
            <PanelSection id="stm-checks" title={w.builtin ? "Where it comes from" : "Checks keel runs before install"}>
              <div className="mk-checklist">{w.checks.map((c, i) => <div key={i}><span className="mk-ok" aria-hidden="true">✓</span><span>{c}</span></div>)}</div>
            </PanelSection>
          )}
        </div>
      </div>
      {installing && pid && (
        <StackInstallDialog hit={w} pid={pid} project={project?.name ?? pid} to={installing === "update" ? (w.update ?? undefined) : undefined}
          onClose={() => setInstalling(null)} onDone={() => void r.reload()} />
      )}
    </div>
  );
}
