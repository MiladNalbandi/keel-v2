// What a plugin's web part is: its ES module (built on its own, loaded at run time) default-exports one of these.

import type { ComponentType } from "react";

/** The props keel gives an add-on's page: the chosen project ("" when none) and the rest of the link (#/teams/web). */
export type AddonPageProps = { pid: string; arg?: string };

/** What an add-on's web module exports by default: its name and a page per screen id (the screens /api/features lists). */
export type AddonWeb = { name: string; pages: Record<string, ComponentType<AddonPageProps>> };

/** A plugin's web part: `export default definePlugin({ name: "product", pages: { initiatives: Initiatives } })`. */
export function definePlugin(plugin: AddonWeb): AddonWeb {
  return plugin;
}
