// The ready-made orgs (#92): Settings › Plugins › Marketplace › Orgs (slot market.kind) and the Org page's New org.
// Each card says who is in it and how deep it is; Preview draws its chart and shows its YAML; "Use this org" opens
// New org with it filled in, where every line can change before it is saved. They come with the Org plugin
// (GET /api/org/templates).

import { useState } from "react";
import { Drawer, ErrorBox, get, hashForScreen, Loading, SearchBox, useLoad } from "@keel/web-sdk";
import { Chart, type ChartLink, type ChartRole } from "./Chart";

export type OrgTemplate = { id: string; title: string; summary: string; people: number; agents: number; levels: number;
  workflows: string[]; roles: ChartRole[]; links: ChartLink[]; yaml: string };

export const templatesApi = { list: () => get<OrgTemplate[]>("/org/templates") };

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
export const sizeOf = (t: OrgTemplate) =>
  `${plural(t.people, "person", "people")} · ${plural(t.agents, "agent")} · ${plural(t.levels, "level")}`;
export const templateHref = (id: string) => hashForScreen("org", `new~${id}`);

/** The cards. `picked` marks one (New org); `onPick` makes the card a choice, else it has Preview and Use. */
export function TemplateCards({ items, picked, onPick, onPreview }: {
  items: OrgTemplate[]; picked?: string | null; onPick?: (t: OrgTemplate) => void; onPreview?: (t: OrgTemplate) => void;
}) {
  return (
    <ul className="org-tpl-grid" aria-label="Ready-made orgs">
      {items.map((t) => (
        <li key={t.id} className={`org-tpl${picked === t.id ? " picked" : ""}`}>
          <div className="org-tpl-head">
            <b>{t.title}</b>
            <span className="sub">{sizeOf(t)}</span>
          </div>
          <p className="org-tpl-sum">{t.summary}</p>
          <div className="chips" aria-label="Roles">
            {t.roles.map((r) => <span key={r.id} className={`chip${r.human ? " org-chip-human" : ""}`}>{r.human ? "👤 " : ""}{r.title}</span>)}
          </div>
          <div className="org-tpl-actions">
            {onPick ? (
              <button type="button" className={`btn sm${picked === t.id ? " primary" : ""}`} aria-pressed={picked === t.id}
                onClick={() => onPick(t)}>{picked === t.id ? "Picked" : "Start from this"}</button>
            ) : (
              <>
                {onPreview && <button type="button" className="btn sm" onClick={() => onPreview(t)} aria-label={`Preview ${t.title}`}>Preview</button>}
                <a className="btn sm primary" href={templateHref(t.id)} aria-label={`Use ${t.title}`}>Use this org</a>
              </>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** One template's chart and YAML in a drawer. */
export function TemplatePreview({ t, onClose }: { t: OrgTemplate; onClose: () => void }) {
  return (
    <Drawer title={t.title} onClose={onClose}
      footer={<a className="btn primary" href={templateHref(t.id)} onClick={onClose}>Use this org</a>}>
      <div className="grid" style={{ gap: 12 }}>
        <p className="sub" style={{ margin: 0 }}>{t.summary} {sizeOf(t)}.</p>
        <Chart roles={t.roles} links={t.links} compact />
        <details>
          <summary>Its YAML</summary>
          <pre className="mono org-yaml-ro">{t.yaml}</pre>
        </details>
        <p className="sub" style={{ margin: 0 }}>Workflows it uses: {t.workflows.join(", ")}.</p>
      </div>
    </Drawer>
  );
}

/** Settings › Plugins › Marketplace › Orgs. */
export function OrgMarket() {
  const list = useLoad("org:templates", templatesApi.list, { live: false });
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<OrgTemplate | null>(null);
  if (list.error) return <ErrorBox error={list.error} onRetry={() => void list.reload()} />;
  if (!list.data) return <Loading what="Loading the ready-made orgs" />;
  const words = q.trim().toLowerCase();
  const shown = list.data.filter((t) => !words || [t.title, t.summary, ...t.roles.map((r) => r.title), ...t.workflows]
    .some((s) => s.toLowerCase().includes(words)));
  return (
    <div className="grid" style={{ gap: 12 }}>
      <SearchBox value={q} onChange={setQ} label="Search the orgs" />
      {shown.length ? <TemplateCards items={shown} onPreview={setOpen} /> : <p className="sub">No ready-made org matches “{q}”.</p>}
      {open && <TemplatePreview t={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
