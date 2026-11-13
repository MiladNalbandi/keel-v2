// Model (in groups by provider) → effort, used everywhere a model is chosen (Agents, Settings, Connections, custom
// agents). The lists come from the model catalog (GET /api/providers/models, CONTRACT.md "v0.3 additions"). v0.15.2:
// the model is one dropdown, grouped by provider with each company's mark; "Other model…" still takes any id.
// #101 every model runs on its provider's API in keel's own harness: the only mode is "api". A model saved as
// "subscription" or "opencode" shows (and is saved again) as API.

import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent as KeyEvent } from "react";
import { createPortal } from "react-dom";
import { api, asApi, type Catalog, type CatalogModel, type CatalogProvider, type Mode, type Model, type Provider, type SavedMode } from "../api";
import { MODE_LABEL, PROV } from "../format";
import { ProviderIcon } from "./ProviderIcon";

/** The providers keel knows without asking (its built-in catalog); vertex and local show once they are set up. */
type BuiltinProvider = "fake" | "claude" | "codex" | "copilot";
export const MODEL_SUGGESTIONS: Record<BuiltinProvider, string[]> = {
  claude: ["opus", "sonnet", "haiku"],
  codex: ["gpt-5.6-sol", "gpt-5", "gpt-5-mini"],
  copilot: ["gpt-5", "claude-sonnet", "gpt-5-mini"],
  fake: ["fake"],
};
export const MODES_FOR: Record<BuiltinProvider, Mode[]> = {
  claude: ["api"],
  codex: ["api"],
  copilot: ["api"],
  fake: ["api"],
};
const BUILTIN_EFFORTS: Record<BuiltinProvider, string[]> = {
  claude: ["low", "medium", "high", "xhigh", "max"],
  codex: ["minimal", "low", "medium", "high"],
  copilot: [],
  fake: [],
};
const PROVIDER_ORDER = ["claude", "codex", "copilot", "vertex", "local", "fake"];
/** How agents reach each provider: its API, whatever mode a saved model still says. */
const REACH: Record<string, string> = { copilot: "API key (GitHub Models)", vertex: "Google Cloud", local: "Local server" };
export const modeLabel = (p: Provider | string, _m?: SavedMode | string) => REACH[p] ?? MODE_LABEL.api;

export const SOURCE_HINT: Record<string, string> = { builtin: "built-in list", server: "from the server" };

/** The catalog keel ships with, used until (or when) the api does not answer. */
export const BUILTIN_CATALOG: Catalog = Object.fromEntries(
  (Object.keys(MODEL_SUGGESTIONS) as BuiltinProvider[]).map((p) => {
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
      ...(typeof m.badge === "string" && m.badge ? { badge: m.badge } : {}),
      ...(typeof m.tools === "boolean" ? { tools: m.tools } : {}),
      ...(typeof m.context === "number" && m.context > 0 ? { context: m.context } : {}),
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
      const modeIds = MODES_FOR[p as BuiltinProvider] ?? ["api"];
      out[p] = {
        label: builtin?.label ?? PROV[p] ?? p, modes: Object.fromEntries(modeIds.map((m) => [m, list])),
        efforts: builtin?.efforts ?? [], default: { mode: modeIds[0], model: list[0].id }, source: "builtin",
      };
      continue;
    }
    if (!isObj(v) || !isObj(v.modes)) continue;
    const modes: CatalogProvider["modes"] = {};
    // #101 only the API mode: an older keel's subscription or OpenCode lists are not shown
    for (const [m, list] of Object.entries(v.modes)) if (m === "api" && Array.isArray(list)) modes[m] = models(list);  // the catalog's order
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
      source: v.source === "server" ? "server" : "builtin",
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
  return e ? (Object.keys(e.modes) as Mode[]) : MODES_FOR[p as BuiltinProvider] ?? ["api"];
}
/** Model options for one provider and mode (the default mode's list when that mode has none). */
export function modelOptions(c: Catalog | null, p: string, mode?: Mode): CatalogModel[] {
  const e = entryOf(c, p);
  if (!e) return [];
  return (mode && e.modes[mode]) || e.modes[e.default.mode] || Object.values(e.modes)[0] || [];
}
/** Effort choices: the model's own list, else the provider's. Empty = no effort setting. */
export function effortsOf(c: Catalog | null, p: string, mode: SavedMode, model: string): string[] {
  const e = entryOf(c, p);
  if (!e) return [];
  const m = modelOptions(c, p, mode === "api" ? mode : undefined).find((x) => x.id === model);
  return m?.efforts ?? e.efforts ?? [];
}
const bare = (m: Model): Model => ({ provider: m.provider, mode: "api", model: m.model });
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

// v0.15.2 The dropdown's list: one group per provider (only the current one when the place fixes the provider), each
// with its API models.
type Opt = { key: string; provider: string; mode: Mode; id: string; label: string; custom?: boolean; badge?: string; tools?: boolean };
type Group = { key: string; provider: string; label: string; mode: Mode; opts: Opt[]; /** v0.15.5 the company's mark, for a sub-group */ icon?: string };

// v0.15.5 a host with many companies' models (GitHub Models, a local server) shows them in one sub-group per company
const VENDORS: [RegExp, string, string][] = [
  [/claude|anthropic/, "claude", "Claude"], [/gpt|openai|codex|^o[134]\b/, "openai", "GPT"], [/gemini|google/, "gemini", "Gemini"],
  [/grok|xai/, "grok", "Grok"], [/llama|meta\//, "meta", "Llama"], [/deepseek/, "deepseek", "DeepSeek"], [/kimi|moonshot/, "kimi", "Kimi"],
  [/mistral|codestral/, "mistral", "Mistral"], [/^mai-|microsoft|phi-/, "microsoft", "Microsoft"],
];
const vendorOf = (id: string) => {
  const v = VENDORS.find(([re]) => re.test(id.toLowerCase()));
  return v ? { key: v[1], name: v[2] } : { key: "other", name: "Other" };
};
const OTHER: Opt = { key: "other", provider: "", mode: "api", id: "", label: "Other model…" };

function groupsOf(c: Catalog | null, value: Model, all: boolean): Group[] {
  const p = value.provider;
  const ps = all ? providersOf(c) : [p];
  if (!ps.includes(p)) ps.unshift(p);
  return ps.flatMap((q) => {
    const md: Mode = "api";
    const list = modelOptions(c, q, md);
    const opts: Opt[] = list.map((m) => ({ key: `${q}:${m.id}`, provider: q, mode: md, id: m.id, label: m.label || m.id, badge: m.badge, tools: m.tools }));
    // a model the list does not have (typed by hand, or from an older keel) still shows, as the chosen one
    if (q === p && value.model && !list.some((m) => m.id === value.model)) opts.unshift({ key: `${q}:${value.model}`, provider: q, mode: md, id: value.model, label: value.model, custom: true });
    const base = { provider: q, label: providerLabel(c, q), mode: md };
    // #101 a short id the company's own API takes ("opus" on Claude, "gpt-5.5" on Codex) is that company's model: Claude's
    // list is not split into "Claude" and "Other"
    const own = vendorOf(q);
    const vendor = (id: string) => (vendorOf(id).key === "other" && own.key !== "other" ? own : vendorOf(id));
    const vendors = [...new Set(opts.map((o) => vendor(o.id).key))];
    if (vendors.length < 2) return [{ ...base, key: q, opts }];
    return vendors.map((v) => {
      const vo = opts.filter((o) => vendor(o.id).key === v);
      return { ...base, key: `${q}:${v}`, label: `${base.label} · ${vendor(vo[0].id).name}`, icon: v, opts: vo };
    });
  }).filter((g) => g.opts.length || g.provider === p);
}

/** Typing finds a model whose name, id or a word of its name starts with the letters. */
const hits = (o: Opt, q: string) =>
  o.label.toLowerCase().startsWith(q) || o.id.toLowerCase().startsWith(q) || `${o.label} ${o.id}`.toLowerCase().split(/[\s/_.:()·-]+/).some((w) => w.startsWith(q));
const printable = (e: KeyEvent) => e.key.length === 1 && e.key !== " " && !e.ctrlKey && !e.metaKey && !e.altKey;

export function ModelPicker({ value: saved, onChange, id, effort = true, provider = true, mode = true }: {
  value: Model;
  onChange: (m: Model) => void;
  id: string;
  /** Show the effort select (it also hides itself when the model has no effort choices). */
  effort?: boolean;
  /** List every provider's models (off when the place already fixes the provider: only its own models show). */
  provider?: boolean;
  /** Say in each group's head how agents reach the provider (API key, Google Cloud, Local server). */
  mode?: boolean;
}) {
  const catalog = useCatalog();
  // #101 a model saved as "subscription" or "opencode" runs on the API: shown, and changed, as API
  const value = asApi(saved);
  const p = value.provider;
  const efforts = effortsOf(catalog, p, value.mode, value.model);
  const source = catalog ? entryOf(catalog, p)?.source : undefined;
  const pLabel = providerLabel(catalog, p);
  // v0.15.2 one dropdown for the model: a button, and a list of models grouped by provider with each company's mark
  const groups = groupsOf(catalog, value, provider);
  const all = groups.flatMap((g) => g.opts);
  const current = all.find((o) => o.provider === p && o.id === value.model);
  // v0.15.5 typing in the open list filters it (a word of the name or id, or any part of it); the count says how many
  const [filter, setFilter] = useState("");
  const f = filter.trim().toLowerCase();
  const fits = (o: Opt) => !f || hits(o, f) || `${o.label} ${o.id}`.toLowerCase().includes(f);
  const shownGroups = groups.map((g) => ({ ...g, opts: g.opts.filter(fits) })).filter((g) => g.opts.length);
  const opts = [...shownGroups.flatMap((g) => g.opts), OTHER];
  const chosen = opts.findIndex((o) => o !== OTHER && o.provider === p && o.id === value.model);
  const shown = current?.label ?? (value.model || "Choose a model");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [typed, setTyped] = useState<string | null>(null); // the "Other model…" box; null while it is closed
  const [place, setPlace] = useState<CSSProperties>({});
  const btn = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLInputElement>(null);
  const cancel = useRef(false);
  const act = Math.min(active, opts.length - 1);
  const typing = typed !== null;

  const show = (i: number) => {
    setActive(Math.max(0, Math.min(i, opts.length - 1)));
    setOpen(true);
  };
  const close = (focus = true) => {
    setOpen(false);
    setFilter("");
    if (focus) btn.current?.focus();
  };
  const pick = (o: Opt) => {
    close();
    if (o === OTHER) {
      cancel.current = false;
      setTyped(current?.custom ? value.model : "");
    } else if (o.provider === p) onChange(keepEffort(catalog, { ...value, model: o.id }));
    else {
      // another provider: its default model comes with its default effort, any other model starts on "effort: default"
      const d = defaultModel(catalog, o.provider, o.mode);
      onChange(d.model === o.id ? d : bare({ ...d, model: o.id }));
    }
  };
  const onButtonKey = (e: KeyEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      show(chosen >= 0 ? chosen : e.key === "ArrowUp" ? opts.length - 2 : 0);
    } else if (printable(e)) {
      e.preventDefault();
      setFilter(e.key);
      show(0);
    }
  };
  const onListKey = (e: KeyEvent) => {
    const last = opts.length - 1;
    const k = e.key;
    const to = k === "ArrowDown" ? Math.min(act + 1, last) : k === "ArrowUp" ? Math.max(act - 1, 0) : k === "Home" ? 0 : k === "End" ? last
      : k === "PageDown" ? Math.min(act + 8, last) : k === "PageUp" ? Math.max(act - 8, 0) : null;
    if (printable(e) || (k === "Backspace" && filter)) {
      e.preventDefault();
      setFilter((x) => (k === "Backspace" ? x.slice(0, -1) : x + k));
      setActive(0);
    } else if (to !== null) {
      e.preventDefault();
      if (to >= 0) setActive(to);
    } else if (k === "Enter" || k === " ") {
      e.preventDefault();
      pick(opts[act]);
    } else if (k === "Escape") {
      // only the list closes, not the drawer or the page mode around it; a filter is cleared first
      e.preventDefault();
      e.stopPropagation();
      if (filter) { setFilter(""); setActive(Math.max(0, chosen)); } else close();
    } else if (k === "Tab") close(); // back on the button, so Tab goes on from there
  };

  useEffect(() => {
    if (open) list.current?.focus({ preventScroll: true });
  }, [open]);
  useEffect(() => {
    if (open) list.current?.querySelector<HTMLElement>(".is-active")?.scrollIntoView?.({ block: "nearest" });
  }, [open, act]);
  useEffect(() => {
    if (typing) box.current?.focus();
  }, [typing]);
  // the list floats under the button (over it when there is no room below) and stays inside the screen; a click
  // anywhere else closes it
  useLayoutEffect(() => {
    if (!open) return;
    const placeIt = (e?: Event) => {
      const b = btn.current?.getBoundingClientRect();
      const l = list.current;
      if (!b || !l || (e && l.contains(e.target as Node))) return;
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const gap = 8;
      const below = vh - b.bottom - gap;
      const above = b.top - gap;
      const up = below < 240 && above > below;
      const v = { maxHeight: Math.max(120, Math.min(560, (up ? above : below) - 4)), ...(up ? { bottom: vh - b.top + 4 } : { top: b.bottom + 4 }) };
      if (vw <= 600) return setPlace({ ...v, left: gap, width: vw - 2 * gap }); // a phone: the screen's width
      const minWidth = Math.min(Math.max(b.width, 250), vw - 2 * gap);
      setPlace({ ...v, minWidth, left: Math.max(gap, Math.min(b.left, vw - gap - Math.max(l.offsetWidth, minWidth))) });
    };
    const away = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!btn.current?.contains(t) && !list.current?.contains(t)) setOpen(false);
    };
    placeIt();
    window.addEventListener("resize", placeIt);
    window.addEventListener("scroll", placeIt, true);
    document.addEventListener("mousedown", away);
    return () => {
      window.removeEventListener("resize", placeIt);
      window.removeEventListener("scroll", placeIt, true);
      document.removeEventListener("mousedown", away);
    };
  }, [open]);

  const option = (o: Opt) => {
    const i = opts.indexOf(o);
    return (
      <div key={o.key} id={`${id}-o${i}`} role="option" aria-selected={i === chosen} aria-label={o.label}
        className={`mp-opt${i === act ? " is-active" : ""}${o === OTHER ? " mp-other" : ""}`}
        onMouseDown={(e) => e.preventDefault()} onMouseMove={() => i !== act && setActive(i)} onClick={() => pick(o)}>
        <span className="mp-ol">{o.label}{o.custom && <span className="mp-tag">other</span>}
          {o.badge && <span className="mp-tag mp-badge" data-badge={o.badge}>{o.badge}</span>}
          {o.tools === false && <span className="mp-tag" title="It cannot call tools: KeelBot Ask, the review check and summaries only, no code steps.">no tools</span>}</span>
        <span className="mp-oid mono">{o === OTHER ? "type an id" : o.label !== o.id ? o.id : ""}</span>
        <span className="mp-check" aria-hidden="true">{i === chosen ? "✓" : ""}</span>
      </div>
    );
  };
  // in a drawer the list lives inside it (it is modal); elsewhere on the body, so no section clips it
  const host = open ? btn.current?.closest<HTMLElement>('[role="dialog"]') ?? document.body : null;
  return (
    <div className="row model-picker" data-testid={`${id}-picker`}>
      <button ref={btn} type="button" id={`${id}-m`} className="mp-btn" data-testid={`${id}-model`}
        aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? `${id}-list` : undefined}
        aria-label={`Model: ${shown}, ${pLabel}`} title={`${pLabel} · ${shown}`}
        onMouseDown={(e) => open && e.preventDefault()} onClick={() => (open ? close() : show(chosen >= 0 ? chosen : 0))} onKeyDown={onButtonKey}>
        <ProviderIcon provider={p} label={pLabel} />
        <span className="mp-bl">{shown}</span>
        <svg className="mp-caret" viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4" /></svg>
      </button>
      {host && createPortal(
        <div ref={list} className="mp-list" role="listbox" id={`${id}-list`} aria-label="Models" tabIndex={-1} style={place}
          aria-activedescendant={`${id}-o${act}`} onKeyDown={onListKey}
          onBlur={(e) => {
            const t = e.relatedTarget as Node | null;
            if (!t || (!btn.current?.contains(t) && !list.current?.contains(t))) setOpen(false);
          }}>
          <div className="mp-filter" role="presentation">
            <span aria-hidden="true">⌕</span>
            {filter ? <b className="mono">{filter}</b> : <span className="mp-ph">Type to filter</span>}
            <span className="mp-count">{opts.length - 1} {opts.length - 1 === 1 ? "model" : "models"}</span>
          </div>
          {shownGroups.map((g) => (
            <div key={g.key} className="mp-group" role="group" aria-labelledby={`${id}-g-${g.key}`} data-provider={g.provider}>
              <div className="mp-gh" role="presentation">
                <ProviderIcon provider={g.icon ?? g.provider} label={g.label} />
                <span id={`${id}-g-${g.key}`}>{g.label}</span>
                {mode && <span className="mp-gm">{modeLabel(g.provider, g.mode)}</span>}
              </div>
              {g.opts.map(option)}
            </div>
          ))}
          {option(OTHER)}
        </div>,
        host,
      )}
      {typing && (
        <input ref={box} type="text" className="model-input mp-box" aria-label="Model id" id={`${id}-other`} value={typed ?? ""}
          placeholder="type a model id" spellCheck={false} autoComplete="off" onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter" && e.key !== "Escape") return;
            // Enter keeps the id, Esc drops it; both go back to the button, and the blur below does the rest
            e.preventDefault();
            if (e.key === "Escape") e.stopPropagation();
            cancel.current = e.key === "Escape";
            btn.current?.focus();
          }}
          onBlur={() => {
            const t = (typed ?? "").trim();
            if (!cancel.current && t && t !== value.model) onChange(keepEffort(catalog, { ...value, model: t }));
            cancel.current = false;
            setTyped(null);
          }} />
      )}
      {effort && efforts.length > 0 && (
        <select aria-label="Effort" id={`${id}-e`} value={value.effort ?? ""}
          onChange={(e) => onChange(e.target.value ? { ...value, effort: e.target.value } : bare(value))}>
          <option value="">effort: default</option>
          {efforts.map((x) => <option key={x} value={x}>{x}</option>)}
        </select>
      )}
      {/* the engine ignores an effort for a model without one (Claude Haiku 4.5, GitHub Models) */}
      {effort && catalog && value.model && efforts.length === 0 && <span className="hint" data-testid={`${id}-noeffort`}>no effort setting</span>}
      {source && <span className="hint" data-testid={`${id}-source`}>{SOURCE_HINT[source] ?? source}</span>}
      {current?.tools === false && <span className="hint amber" data-testid={`${id}-notools`}>no tool calls: Ask, review check and summaries only</span>}
    </div>
  );
}
