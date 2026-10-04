// Provider → runs on (mode) → model → effort, used everywhere a model is chosen (Agents, Settings,
// Connections, custom agents). The lists come from the model catalog (GET /api/providers/models,
// CONTRACT.md "v0.3 additions"); the model field stays free text.

import { useEffect, useState } from "react";
import { api, type Catalog, type CatalogModel, type CatalogProvider, type Mode, type Model, type Provider } from "../api";
import { MODE_LABEL, PROV } from "../format";

export const MODEL_SUGGESTIONS: Record<Provider, string[]> = {
  claude: ["opus", "sonnet", "haiku"],
  codex: ["gpt-5.6-sol", "gpt-5", "gpt-5-mini"],
  copilot: ["gpt-5", "claude-sonnet", "gpt-5-mini"],
  fake: ["fake"],
};
export const MODES_FOR: Record<Provider, Mode[]> = {
  claude: ["subscription", "api"],
  codex: ["subscription", "api"],
  copilot: ["subscription", "opencode", "api"],
  fake: ["api"],
};
const BUILTIN_EFFORTS: Record<Provider, string[]> = {
  claude: ["low", "medium", "high", "max"],
  codex: ["minimal", "low", "medium", "high"],
  copilot: [],
  fake: [],
};
const PROVIDER_ORDER = ["claude", "codex", "copilot", "fake"];
const MODE_COPILOT: Record<string, string> = { subscription: "Copilot CLI", opencode: "OpenCode", api: "API key (GitHub Models)" };
export const modeLabel = (p: Provider | string, m: Mode | string) => (p === "copilot" ? MODE_COPILOT[m] : MODE_LABEL[m]) ?? m;

export const SOURCE_HINT: Record<string, string> = { cli: "from the CLI", cache: "from the CLI (cached)", builtin: "built-in list" };

/** The catalog keel ships with, used until (or when) the api does not answer. */
export const BUILTIN_CATALOG: Catalog = Object.fromEntries(
  (Object.keys(MODEL_SUGGESTIONS) as Provider[]).map((p) => {
    const list = MODEL_SUGGESTIONS[p].map((id) => ({ id, label: id }));
    const modes = Object.fromEntries(MODES_FOR[p].map((m) => [m, list]));
    const entry: CatalogProvider = { label: PROV[p] ?? p, modes, efforts: BUILTIN_EFFORTS[p], default: { mode: MODES_FOR[p][0], model: list[0].id }, source: "builtin" };
    return [p, entry];
  }),
);

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const strings = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const models = (v: unknown): CatalogModel[] =>
  Array.isArray(v)
    ? v.filter(isObj).filter((m) => typeof m.id === "string" && m.id).map((m) => ({
      id: m.id as string,
      label: typeof m.label === "string" && m.label ? m.label : (m.id as string),
      ...(Array.isArray(m.efforts) ? { efforts: strings(m.efforts) } : {}),
    }))
    : [];

/** Accepts the v0.3 catalog and the older `{provider: [{id,label}]}` answer; drops anything malformed. */
export function normalizeCatalog(raw: unknown): Catalog {
  if (!isObj(raw)) return BUILTIN_CATALOG;
  const out: Catalog = {};
  for (const [p, v] of Object.entries(raw)) {
    const builtin = BUILTIN_CATALOG[p];
    if (Array.isArray(v)) {
      // pre-v0.3: one list per provider, for every mode it runs on
      const list = models(v);
      if (!list.length) continue;
      const modeIds = MODES_FOR[p as Provider] ?? ["api"];
      out[p] = {
        label: builtin?.label ?? PROV[p] ?? p, modes: Object.fromEntries(modeIds.map((m) => [m, list])),
        efforts: builtin?.efforts ?? [], default: { mode: modeIds[0], model: list[0].id }, source: "builtin",
      };
      continue;
    }
    if (!isObj(v) || !isObj(v.modes)) continue;
    const modes: CatalogProvider["modes"] = {};
    for (const [m, list] of Object.entries(v.modes)) if (Array.isArray(list)) modes[m as Mode] = models(list);  // the catalog's order
    const modeIds = Object.keys(modes) as Mode[];
    if (!modeIds.length) continue;
    const d = isObj(v.default) ? v.default : {};
    const dMode = typeof d.mode === "string" && d.mode in modes ? (d.mode as Mode) : modeIds[0];
    out[p] = {
      label: typeof v.label === "string" && v.label ? v.label : PROV[p] ?? p,
      modes,
      efforts: strings(v.efforts),
      default: {
        mode: dMode,
        model: typeof d.model === "string" && d.model ? d.model : modes[dMode]?.[0]?.id ?? "",
        ...(typeof d.effort === "string" && d.effort ? { effort: d.effort } : {}),
      },
      source: v.source === "cli" || v.source === "cache" ? v.source : "builtin",
    };
  }
  return Object.keys(out).length ? out : BUILTIN_CATALOG;
}

// One request per page load. If it fails, the built-in catalog stays; the model field is free text either way.
let catalogPromise: Promise<Catalog> | null = null;
let catalogCache: Catalog | null = null;
export function resetProviderModels() {
  catalogPromise = null;
  catalogCache = null;
}

/** The model catalog: null while the first request runs. */
export function useCatalog(): Catalog | null {
  const [c, setC] = useState<Catalog | null>(catalogCache);
  useEffect(() => {
    if (catalogCache) {
      setC(catalogCache);
      return;
    }
    let live = true;
    catalogPromise ??= api.providerModels().then((r) => {
      catalogCache = normalizeCatalog(r);
      return catalogCache;
    }, (e) => {
      catalogPromise = null;
      throw e;
    });
    catalogPromise.then((r) => live && setC(r), () => live && setC(BUILTIN_CATALOG));
    return () => {
      live = false;
    };
  }, []);
  return c;
}

const entryOf = (c: Catalog | null, p: string): CatalogProvider | undefined => (c ?? BUILTIN_CATALOG)[p] ?? BUILTIN_CATALOG[p];

/** Providers in a stable order: the known ones first, then whatever else the catalog lists. */
export function providersOf(c: Catalog | null): string[] {
  const keys = Object.keys(c ?? BUILTIN_CATALOG);
  return [...PROVIDER_ORDER.filter((p) => keys.includes(p)), ...keys.filter((p) => !PROVIDER_ORDER.includes(p))];
}
export const providerLabel = (c: Catalog | null, p: string) => entryOf(c, p)?.label ?? PROV[p] ?? p;
export function modesOf(c: Catalog | null, p: string): Mode[] {
  const e = entryOf(c, p);
  return e ? (Object.keys(e.modes) as Mode[]) : MODES_FOR[p as Provider] ?? ["api"];
}
/** Model options for one provider and mode (the default mode's list when that mode has none). */
export function modelOptions(c: Catalog | null, p: string, mode?: Mode): CatalogModel[] {
  const e = entryOf(c, p);
  if (!e) return [];
  return (mode && e.modes[mode]) || e.modes[e.default.mode] || Object.values(e.modes)[0] || [];
}
/** Effort choices: the model's own list, else the provider's. Empty = no effort setting. */
export function effortsOf(c: Catalog | null, p: string, mode: Mode, model: string): string[] {
  const e = entryOf(c, p);
  if (!e) return [];
  const m = modelOptions(c, p, mode).find((x) => x.id === model);
  return m?.efforts ?? e.efforts ?? [];
}
const bare = (m: Model): Model => ({ provider: m.provider, mode: m.mode, model: m.model });
const keepEffort = (c: Catalog | null, m: Model): Model =>
  m.effort && !effortsOf(c, m.provider, m.mode, m.model).includes(m.effort) ? bare(m) : m;

/** The provider's default model, on `mode` when given (and the provider has it). */
export function defaultModel(c: Catalog | null, p: string, mode?: Mode): Model {
  const e = entryOf(c, p);
  const modes = modesOf(c, p);
  const md = mode && modes.includes(mode) ? mode : e?.default.mode ?? modes[0] ?? "api";
  const list = modelOptions(c, p, md);
  const onDefault = !!e && md === e.default.mode;
  const model = onDefault && e.default.model ? e.default.model : list[0]?.id ?? e?.default.model ?? "";
  const effort = onDefault ? e.default.effort : undefined;
  return keepEffort(c, { provider: p as Provider, mode: md, model, ...(effort ? { effort } : {}) });
}

export function ModelPicker({ value, onChange, id, effort = true, provider = true, mode = true }: {
  value: Model;
  onChange: (m: Model) => void;
  id: string;
  /** Show the effort select (it also hides itself when the model has no effort choices). */
  effort?: boolean;
  /** Show the provider select (off when the place already fixes the provider). */
  provider?: boolean;
  /** Show the runs-on select (off when the place already picks the mode). */
  mode?: boolean;
}) {
  const catalog = useCatalog();
  const p = value.provider;
  const providers = providersOf(catalog);
  const modes = modesOf(catalog, p);
  const opts = modelOptions(catalog, p, value.mode);
  const efforts = effortsOf(catalog, p, value.mode, value.model);
  const source = catalog ? entryOf(catalog, p)?.source : undefined;
  return (
    <div className="row model-picker" data-testid={`${id}-picker`}>
      {provider && (
        <select aria-label="Provider" id={`${id}-p`} value={p} onChange={(e) => onChange(defaultModel(catalog, e.target.value))}>
          {!providers.includes(p) && <option value={p}>{PROV[p] ?? p}</option>}
          {providers.map((k) => <option key={k} value={k}>{providerLabel(catalog, k)}</option>)}
        </select>
      )}
      {mode && (
        <select aria-label="Runs on" id={`${id}-mode`} value={value.mode} onChange={(e) => {
          const md = e.target.value as Mode;
          const model = modelOptions(catalog, p, md).some((x) => x.id === value.model) ? value.model : defaultModel(catalog, p, md).model;
          onChange(keepEffort(catalog, { ...value, mode: md, model }));
        }}>
          {!modes.includes(value.mode) && <option value={value.mode}>{modeLabel(p, value.mode)}</option>}
          {modes.map((m) => <option key={m} value={m}>{modeLabel(p, m)}</option>)}
        </select>
      )}
      <input type="text" aria-label="Model" id={`${id}-m`} className="model-input" list={`${id}-models`} value={value.model}
        onChange={(e) => onChange(keepEffort(catalog, { ...value, model: e.target.value }))} />
      <datalist id={`${id}-models`} data-testid={`${id}-models`}>
        {opts.map((m) => <option key={m.id} value={m.id} label={m.label !== m.id ? m.label : undefined} />)}
      </datalist>
      {effort && efforts.length > 0 && (
        <select aria-label="Effort" id={`${id}-e`} value={value.effort ?? ""}
          onChange={(e) => onChange(e.target.value ? { ...value, effort: e.target.value } : bare(value))}>
          <option value="">effort: default</option>
          {efforts.map((x) => <option key={x} value={x}>{x}</option>)}
        </select>
      )}
      {source && <span className="hint" data-testid={`${id}-source`}>{SOURCE_HINT[source] ?? source}</span>}
    </div>
  );
}
