// Extension points (docs/plugins/09-step2-contract.md §5): the pages of the menu, and slots. A slot is a named place in
// a page where another part puts a piece (a section, a tab, a card). Core reads these lists; it never imports a part.
// keel's built-in parts register in web/src/builtins.ts; a plugin's web part does the same through @keel/web-sdk.
//
//   a part:  registerPage({ id: "map", label: "Map", group: "know", order: 30, component: MapPage })
//            registerSlot("jobs.tab", { id: "pipelines", title: "Pipelines", component: PipelinesView })
//   core:    const tabs = useSlot("jobs.tab")     live: it renders again when a part registers later
//
// Both lists are plain module state, shared by everything that imports this file (a plugin gets the same copy through
// window.__keel.sdk). Nothing here imports a page or a part.

import {
  useSyncExternalStore,
  type ComponentType,
  type ReactNode,
} from "react";

/** What a page gets: the chosen project ("" when none) and the rest of the link (#/wiki/kb:architecture). */
export type PageProps = { pid: string; arg?: string };

/** A page of the menu and the router. */
export type PageRegistration = {
  /** the link's name: #/map */
  id: string;
  /** its name in the menu */
  label: string;
  /** the menu group: "run", "know", "build" or "control" (routes.ts GROUP_HEADS) */
  group: string;
  /** its place in the group: lower first */
  order: number;
  /** true (the default): the page needs a chosen project. false: it spans every project (the Inbox). "optional": it
   *  works without one too, with pid "" (Settings). */
  needsProject?: boolean | "optional";
  /** other names that open it: #/code is the Code page (id repo) */
  aliases?: string[];
  /** a Product-only keel (and the Product view) keeps it in the menu */
  product?: boolean;
  /** the folded menu's icon: what goes inside a 24×24 stroke-only svg */
  icon?: ReactNode;
  component: ComponentType<PageProps>;
};

/** A piece in a slot. Each slot says what else its pieces carry (sdk/slots.ts); `id` is unique in the slot. */
export type SlotItem = {
  id: string;
  title?: string;
  /** its place: lower first; the same order keeps the order they registered in */
  order?: number;
  /** what it shows; the slot's own type names its props */
  component?: ComponentType<any>;
};

// ---------- change notices (for the hooks) ----------

let version = 0;
const listeners = new Set<() => void>();
const sorted = new Map<string, SlotItem[]>();

function changed() {
  version++;
  sorted.clear();
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const snapshot = () => version;

// ---------- pages ----------

const pages = new Map<string, PageRegistration>();

/** Add a page (a page with the same id is replaced). Returns a function that takes it away again. */
export function registerPage(page: PageRegistration): () => void {
  pages.set(page.id, page);
  changed();
  return () => {
    if (pages.get(page.id) !== page) return;
    pages.delete(page.id);
    changed();
  };
}

/** The page with this id, or null. */
export const pageOf = (id: string): PageRegistration | null =>
  pages.get(id) ?? null;

/** The page an id or an alias names (#/code → repo), or null. */
export function pageFor(name: string): PageRegistration | null {
  return (
    pages.get(name) ??
    [...pages.values()].find((p) => p.aliases?.includes(name)) ??
    null
  );
}

/** Every page, in the order they registered. */
export const allPages = (): PageRegistration[] => [...pages.values()];

/** Every page; the component renders again when a page comes or goes. */
export function usePages(): PageRegistration[] {
  useSyncExternalStore(subscribe, snapshot, snapshot);
  return allPages();
}

// ---------- slots ----------

const slots = new Map<string, SlotItem[]>();

/** Put a piece in a slot (a piece with the same id is replaced). Returns a function that takes it away again. */
export function registerSlot<T extends SlotItem>(
  slot: string,
  item: T,
): () => void {
  slots.set(slot, [
    ...(slots.get(slot) ?? []).filter((i) => i.id !== item.id),
    item,
  ]);
  changed();
  return () => {
    const list = slots.get(slot) ?? [];
    if (!list.includes(item)) return;
    slots.set(
      slot,
      list.filter((i) => i !== item),
    );
    changed();
  };
}

/** The pieces in a slot by their order (the same list until something changes). */
export function slotItems<T extends SlotItem = SlotItem>(slot: string): T[] {
  let list = sorted.get(slot);
  if (!list) {
    list = [...(slots.get(slot) ?? [])].sort(
      (a, b) => (a.order ?? 0) - (b.order ?? 0),
    );
    sorted.set(slot, list);
  }
  return list as T[];
}

/** The pieces in a slot, live: the component renders again when a piece comes or goes. */
export function useSlot<T extends SlotItem = SlotItem>(slot: string): T[] {
  useSyncExternalStore(subscribe, snapshot, snapshot);
  return slotItems<T>(slot);
}
