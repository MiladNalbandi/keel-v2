// v0.16.0 a keel with only its core (docs/plugins/13-step4-contract.md §9): the menu's Project group has no pages,
// because Code, KeelBot, Map, Graph and Wiki are plugins. It offers the sets instead (GET /api/plugins/sets: Developer,
// Review, Knowledge, Tickets); each one opens Control › Plugins with that set (#/plugins?set=<id>).

import { useEffect, useState } from "react";
import type { Features } from "../api";
import { marketApi, setsOf, type PluginSet } from "../marketplaceApi";
import type { PageRegistration } from "../sdk/registry";

/** Core only: keel's own pages show, the plugins' web parts have started, and none of them added a Project page. */
export function isCoreOnly(
  features: Features & { loaded: boolean; started: boolean },
  view: string,
  pages: PageRegistration[],
): boolean {
  const devShown =
    features.parts.dev !== false &&
    (features.mode !== "both" || view !== "product");
  return (
    features.loaded &&
    features.started &&
    devShown &&
    !pages.some((p) => p.group === "know")
  );
}

export function StartWithSet({ onPick }: { onPick?: () => void }) {
  const [sets, setSets] = useState<PluginSet[] | null>(null);
  useEffect(() => {
    let live = true;
    marketApi.sets().then(
      (s) => live && setSets(setsOf(s)),
      () => live && setSets([]),
    );
    return () => {
      live = false;
    };
  }, []);
  const open = (id?: string) => {
    location.hash = id
      ? `#/plugins?set=${encodeURIComponent(id)}`
      : "#/plugins";
    onPick?.();
  };
  return (
    <div className="nav-sec nav-start-sec">
      <div className="nav-h" title="what this project is">
        <span>Project</span>
      </div>
      <div className="nav-start" role="group" aria-label="Start with a set">
        <b>Start with a set</b>
        <p>
          Code, KeelBot, Map, Graph and Wiki are plugins now. Add the ones you
          need.
        </p>
        <div className="nav-sets">
          {(sets ?? []).map((s) => (
            <button
              key={s.id}
              type="button"
              className="btn sm"
              onClick={() => open(s.id)}
            >
              {s.title ?? s.id}
            </button>
          ))}
          {sets && !sets.length && (
            <button type="button" className="btn sm" onClick={() => open()}>
              Open Plugins
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
