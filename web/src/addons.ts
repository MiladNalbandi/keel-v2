// v0.13.0 add-ons in the web: an add-on's pages (keel Product) load only when its part of keel is on, so keel's own
// bundle does not grow. /api/features says what is on.
// Plugins (step 1): an add-on's web part is the plugin's own ES module, built on its own. /api/features gives its url
// and its css. It uses keel's React and @keel/web-sdk through the import map in index.html (tools/sdkShims.ts).
// Step 3: keel loads every plugin's web part at start, once /api/features has listed them, and calls its setup(sdk)
// once: the part registers its pages and slots (the Map: its page in the menu, at its place). While they load, the menu
// shows keel's own pages; a part's pages appear when it has loaded. keel Product's shape still works: its pages load
// from the module's `pages` when one is first shown.
//
//   /api/features   plugins: [{ name: "map", web: { entry: "/plugins/map/1.0.0/web/index.js", css: [] } }, …]
//   at start        import(entry) ─▶ its default export's setup(sdk) ─▶ registerPage({ id: "map", … })
//   #/initiatives   addonPage("product", "initiatives") ─▶ the same module ─▶ its pages.initiatives

import { createElement, lazy, useEffect, useState, type ComponentType, type LazyExoticComponent } from "react";
import { api, type AddonScreen, type Features, type PluginWeb } from "./api";
import { menuGroups, type Group } from "./routes";
import * as sdk from "./sdk";
import { pageOf, usePagesVersion } from "./sdk/registry";
import type { AddonPageProps, AddonWeb } from "./sdk/plugin";
import { useApp } from "./state";

/** Loads a plugin's web module from its url. */
export type AddonImporter = (url: string) => Promise<unknown>;
const importUrl: AddonImporter = (url) => import(/* @vite-ignore */ url);
let importer = importUrl;

/** Tests: load the add-ons' web modules with `load` instead of the browser's import() (no argument: import() again). */
export function setAddonImporter(load: AddonImporter = importUrl) {
  importer = load;
}

/** The add-on's web part, as the last /api/features lists it (an older api lists no plugins). */
const webOf = (name: string): PluginWeb | null => cache?.plugins?.find((p) => p.name === name)?.web ?? null;

/** /api/features lists a web part for the add-on, so its pages can load. */
export const hasAddonWeb = (name: string) => Boolean(webOf(name)?.entry);

function Missing({ name, failed }: { name: string; failed: boolean }) {
  const text = failed ? `This page of ${name} could not be loaded.` : `This page of ${name} is not in this keel.`;
  return createElement("div", { className: "empty", role: "status" }, text);
}

/** The add-on's stylesheets, each linked once. */
function addCss(urls: string[]) {
  const links = document.head.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]');
  const have = new Set([...links].map((l) => l.getAttribute("href")));
  for (const href of urls.filter((u) => !have.has(u))) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.appendChild(link);
    have.add(href);
  }
}

// one import per web entry: the add-on's pages share it
const modules = new Map<string, Promise<AddonWeb | null>>();

/** The add-on's web module: null when it has no web part; it rejects when the module does not load or is not one. */
function loadAddon(name: string): Promise<AddonWeb | null> {
  const web = webOf(name);
  if (!web?.entry) return Promise.resolve(null);
  const { entry, css } = web;
  let mod = modules.get(entry);
  if (!mod) {
    addCss(css ?? []);
    mod = importer(entry).then((m) => {
      const found = (m as { default?: Partial<AddonWeb> } | null)?.default;
      const hasPages = typeof found?.pages === "object" && found.pages !== null;
      if (!hasPages && typeof found?.setup !== "function") {
        throw new Error(`${entry} has no default export with setup or pages (export default definePlugin({ name, setup }))`);
      }
      return found as AddonWeb;
    });
    modules.set(entry, mod);
  }
  return mod;
}

// ---------- every plugin's web part, at start ----------

// the web entries keel started (each once for this page), how many loads still run, and all of them; a load from
// before resetFeatures() (tests) changes nothing
const begun = new Set<string>();
let pending = 0;
let starting: Promise<void> = Promise.resolve();
let generation = 0;

/** Load each plugin web part /api/features lists that is not started yet, then call its setup(sdk) once, in the order
 *  of the list (the plugins' dependency order). A part that does not load or whose setup fails is logged; keel goes
 *  on without it. */
function startPlugins(f: Features) {
  const fresh = (f.plugins ?? []).filter((p) => p.web?.entry && !begun.has(p.web.entry));
  if (fresh.length === 0) return;
  fresh.forEach((p) => begun.add(p.web!.entry));
  const gen = generation;
  pending++;
  const run = Promise.all(
    fresh.map((p) =>
      loadAddon(p.name).catch((e) => {
        console.error(`keel: the web part of ${p.name} did not load`, e);
        return null;
      }),
    ),
  )
    .then((mods) =>
      fresh.forEach((p, i) => {
        if (gen !== generation) return;
        try {
          mods[i]?.setup?.(sdk);
        } catch (e) {
          console.error(`keel: the web part of ${p.name} did not start`, e);
        }
      }),
    )
    .finally(() => {
      if (gen !== generation) return;
      pending--;
      subscribers.forEach((s) => s());
    });
  starting = starting.then(() => run);
}

/** Tests: wait until the plugins' web parts started so far have loaded and registered their pieces. */
export const pluginsStarted = () => starting;

const pages = new Map<string, LazyExoticComponent<ComponentType<AddonPageProps>>>();

/** The add-on's page component for a screen, loaded on first use. */
export function addonPage(addon: string, screen: string): LazyExoticComponent<ComponentType<AddonPageProps>> {
  const key = `${addon}:${screen}`;
  let page = pages.get(key);
  if (!page) {
    page = lazy(async () => {
      let found: ComponentType<AddonPageProps> | undefined;
      let failed = false;
      try {
        found = (await loadAddon(addon))?.pages?.[screen];
      } catch (e) {
        failed = true;
        console.error(`keel: the web part of ${addon} did not load`, e);
      }
      return { default: found ?? ((() => createElement(Missing, { name: addon, failed })) as ComponentType<AddonPageProps>) };
    });
    pages.set(key, page);
  }
  return page;
}

const DEV_ONLY: Features = { mode: "dev", modes: ["dev"], parts: { dev: true }, addons: [], screens: [], plugins: [] };

// one shared copy for the whole app (the menu, the router and Settings read it), fetched once per live tick
let cache: Features | null = null;
let failed = false;
let inflight: Promise<void> | null = null;
const subscribers = new Set<() => void>();

function fetchFeatures(): Promise<void> {
  inflight ??= api.features().then(
    (f) => { cache = f; failed = false; void startPlugins(f); },
    () => { failed = true; },
  ).finally(() => {
    inflight = null;
    subscribers.forEach((s) => s());
  });
  return inflight;
}

/** Read /api/features again now (after Settings › What this keel does changed). */
export const refreshFeatures = () => fetchFeatures();

/** Tests: forget what was read, and the add-ons' pages loaded with it. */
export function resetFeatures() {
  cache = null;
  failed = false;
  inflight = null;
  pages.clear();
  modules.clear();
  begun.clear();
  pending = 0;
  starting = Promise.resolve();
  generation++;
  importer = importUrl;
}

/** What this keel does now; while it loads (or without the api) keel is Dev, as it always was. `started`: the
 *  plugins' web parts have loaded and registered their pages (or there are none, or the api did not answer). The
 *  menu and the router read it again when a page registers. */
export function useFeatures(): Features & { loaded: boolean; started: boolean } {
  const { tick } = useApp();
  const [, force] = useState(0);
  usePagesVersion();
  useEffect(() => {
    const on = () => force((n) => n + 1);
    subscribers.add(on);
    return () => { subscribers.delete(on); };
  }, []);
  useEffect(() => { void fetchFeatures(); }, [tick]);
  return { ...(cache ?? DEV_ONLY), loaded: cache !== null || failed, started: pending === 0 };
}

// ---------- the View (Product and Dev on: each person shows All, Product or Dev) ----------

export type View = "all" | "product" | "dev";
const VIEW_KEY = "keel2.view";

export function readView(): View {
  try {
    const v = localStorage.getItem(VIEW_KEY);
    return v === "product" || v === "dev" ? v : "all";
  } catch {
    return "all";
  }
}

/** The View this person picked (All when Product and Dev are not both on). */
export function useView(): View {
  const [v, setV] = useState<View>(readView);
  useEffect(() => {
    const on = (e: Event) => setV(((e as CustomEvent).detail as View) ?? readView());
    window.addEventListener("keel:view", on);
    return () => window.removeEventListener("keel:view", on);
  }, []);
  return v;
}

export function saveView(v: View) {
  try {
    localStorage.setItem(VIEW_KEY, v);
  } catch {
    /* private window */
  }
  window.dispatchEvent(new CustomEvent("keel:view", { detail: v }));
}

/** The screens a Product-only keel (or the Product view) keeps: the pages that say so (the Inbox, KeelBot, Control). */
const productKeeps = (id: string) => pageOf(id)?.product === true;

export type NavPage = { id: string; label: string; addon?: string };
export type NavGroup = { id: string; label: string; hint: string; pages: NavPage[] };

/** The menu for this mode and view: the add-ons' groups first, then keel's own groups (with the parts' pages) with
 *  what stays. */
export function navGroups(features: Features, view: View): NavGroup[] {
  const productOn = Object.entries(features.parts).some(([p, on]) => p !== "dev" && on);
  const devOn = features.parts.dev !== false;
  const showProduct = productOn && (features.mode !== "both" || view !== "dev");
  const fullDev = devOn && (features.mode !== "both" || view !== "product");
  const byGroup = new Map<string, AddonScreen[]>();
  if (showProduct) for (const s of features.screens) byGroup.set(s.group, [...(byGroup.get(s.group) ?? []), s]);
  const addonGroups: NavGroup[] = [...byGroup.entries()].map(([label, screens]) => ({
    id: `addon-${label.toLowerCase()}`, label, hint: "",
    pages: screens.map((s) => ({ id: s.id, label: s.label, addon: s.addon })),
  }));
  const own = menuGroups().map((g: Group) => ({
    id: g.id, label: g.label, hint: g.hint,
    pages: g.pages.filter(([id]) => fullDev || productKeeps(id)).map(([id, label]) => ({ id, label })),
  })).filter((g) => g.pages.length > 0);
  return [...addonGroups, ...own];
}
