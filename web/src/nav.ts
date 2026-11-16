// keel 0.28 the menu after the redesign: six main places a person comes to (they follow the view), the pages they
// pinned, and everything else one ⌘K away. The pins live in this browser; the menu, the page head's pin button, ⌘K's
// Pages and Add features all read and change the same list.
//
//   All / Dev     Home · Inbox · Flow · Code · KeelBot · Live agents
//   Product       Inbox · the add-on's pages (Initiatives, Product map, Teams) · Goals (Home starts dev flows: not here)
//   Pinned        what the person pinned (Review, Tasks, Wiki at first, when this keel has them)

import { useSyncExternalStore } from "react";
import type { NavGroup, NavPage, View } from "./addons";

export const PINS_KEY = "keel.nav.pins";
const PINS_EVENT = "keel:pins";
/** the first pins, before a person changes them: the pages most people open every day, when this keel has them */
export const DEFAULT_PINS = ["review", "tasks", "wiki"];

/** The main places, in order, for this view. Pages this keel does not have drop out. */
export function mainIds(view: View, groups: NavGroup[]): string[] {
  const have = new Set(groups.flatMap((g) => g.pages.map((p) => p.id)));
  // the Product view, or a keel without Dev (no Flow page): the product places
  if (view === "product" || !have.has("flow")) {
    const addon = groups.filter((g) => g.id.startsWith("addon-")).flatMap((g) => g.pages.map((p) => p.id));
    return ["inbox", ...addon, "goals"].filter((id, i, all) => all.indexOf(id) === i && have.has(id));
  }
  return ["home", "inbox", "flow", "repo", "helper", "live"].filter((id) => id === "home" || have.has(id));
}

/** Every page of the menu, by id (with its group's label), so pins and ⌘K can name them. */
export function pageIndex(groups: NavGroup[]): Map<string, NavPage & { group: string }> {
  const m = new Map<string, NavPage & { group: string }>();
  for (const g of groups) for (const p of g.pages) if (!m.has(p.id)) m.set(p.id, { ...p, group: g.label });
  return m;
}

const isNames = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === "string");

function readPins(): string[] {
  try {
    const raw = localStorage.getItem(PINS_KEY);
    if (raw == null) return DEFAULT_PINS;
    const v = JSON.parse(raw) as unknown;
    return isNames(v) ? v : DEFAULT_PINS;
  } catch {
    return DEFAULT_PINS;
  }
}

let cache: string[] | null = null;
const snapshot = () => (cache ??= readPins());

function subscribe(on: () => void) {
  const bump = () => {
    cache = null;
    on();
  };
  window.addEventListener(PINS_EVENT, bump);
  window.addEventListener("storage", bump);
  return () => {
    window.removeEventListener(PINS_EVENT, bump);
    window.removeEventListener("storage", bump);
  };
}

export function setPins(next: string[]) {
  try {
    localStorage.setItem(PINS_KEY, JSON.stringify(next));
  } catch {
    /* a private window: the pins last until the page reloads */
  }
  cache = next;
  window.dispatchEvent(new Event(PINS_EVENT));
}

/** The pinned page ids, and a switch for one of them. */
export function usePins(): { pins: string[]; pinned: (id: string) => boolean; toggle: (id: string) => boolean } {
  const pins = useSyncExternalStore(subscribe, snapshot, snapshot);
  return {
    pins,
    pinned: (id) => pins.includes(id),
    /** returns true when the page is pinned now */
    toggle: (id) => {
      const on = !pins.includes(id);
      setPins(on ? [...pins, id] : pins.filter((x) => x !== id));
      return on;
    },
  };
}
