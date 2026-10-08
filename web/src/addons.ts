// v0.13.0 add-ons in the web: an add-on's pages (keel Product: product/web) are found when the app is built and
// loaded only when its part of keel is on, so keel's own bundle does not grow. /api/features says what is on.

import { createElement, lazy, useEffect, useState, type ComponentType, type LazyExoticComponent } from "react";
import { api, type AddonScreen, type Features } from "./api";
import { GROUPS, type Group, type ScreenId } from "./routes";
import { useApp } from "./state";

/** What an add-on's web module (its index.tsx) exports by default. */
export type AddonWeb = { name: string; pages: Record<string, ComponentType<AddonPageProps>> };
export type AddonPageProps = { pid: string; arg?: string };

// product/web/index.tsx when it is there; {} in a build without the add-on folder
const FOUND = import.meta.glob<{ default: AddonWeb }>("../../product/web/index.tsx");
const LOADERS: Record<string, () => Promise<{ default: AddonWeb }>> = Object.fromEntries(
  Object.entries(FOUND).map(([path, load]) => [path.split("/").at(-3) ?? path, load]),
);

export const hasAddonWeb = (name: string) => name in LOADERS;

function Missing({ name }: { name: string }) {
  return createElement("div", { className: "empty", role: "status" }, `This page of ${name} is not in this build of keel.`);
}

const pages = new Map<string, LazyExoticComponent<ComponentType<AddonPageProps>>>();

/** The add-on's page component for a screen, loaded on first use. */
export function addonPage(addon: string, screen: string): LazyExoticComponent<ComponentType<AddonPageProps>> {
  const key = `${addon}:${screen}`;
  let page = pages.get(key);
  if (!page) {
    page = lazy(async () => {
      const load = LOADERS[addon];
      const mod = load ? await load() : null;
      const found = mod?.default.pages[screen];
      return { default: found ?? ((() => createElement(Missing, { name: addon })) as ComponentType<AddonPageProps>) };
    });
    pages.set(key, page);
  }
  return page;
}

const DEV_ONLY: Features = { mode: "dev", modes: ["dev"], parts: { dev: true }, addons: [], screens: [] };

// one shared copy for the whole app (the menu, the router and Settings read it), fetched once per live tick
let cache: Features | null = null;
let failed = false;
let inflight: Promise<void> | null = null;
const subscribers = new Set<() => void>();

function fetchFeatures(): Promise<void> {
  inflight ??= api.features().then(
    (f) => { cache = f; failed = false; },
    () => { failed = true; },
  ).finally(() => {
    inflight = null;
    subscribers.forEach((s) => s());
  });
  return inflight;
}

/** Read /api/features again now (after Settings › What this keel does changed). */
export const refreshFeatures = () => fetchFeatures();

/** Tests: forget what was read. */
export function resetFeatures() {
  cache = null;
  failed = false;
  inflight = null;
}

/** What this keel does now; while it loads (or without the api) keel is Dev, as it always was. */
export function useFeatures(): Features & { loaded: boolean } {
  const { tick } = useApp();
  const [, force] = useState(0);
  useEffect(() => {
    const on = () => force((n) => n + 1);
    subscribers.add(on);
    return () => { subscribers.delete(on); };
  }, []);
  useEffect(() => { void fetchFeatures(); }, [tick]);
  return { ...(cache ?? DEV_ONLY), loaded: cache !== null || failed };
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

/** keel's own screens a Product-only keel (or the Product view) keeps: the Inbox, KeelBot, and Control. */
const PRODUCT_KEEPS = new Set<ScreenId>(["inbox", "helper", "budget", "settings", "connections"]);

export type NavPage = { id: string; label: string; addon?: string };
export type NavGroup = { id: string; label: string; hint: string; pages: NavPage[] };

/** The menu for this mode and view: the add-ons' groups first, then keel's own groups with what stays. */
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
  const own = GROUPS.map((g: Group) => ({
    id: g.id, label: g.label, hint: g.hint,
    pages: g.pages.filter(([id]) => fullDev || PRODUCT_KEEPS.has(id)).map(([id, label]) => ({ id, label })),
  })).filter((g) => g.pages.length > 0);
  return [...addonGroups, ...own];
}
