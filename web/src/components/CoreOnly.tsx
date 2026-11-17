// v0.16.0 a keel with only its core (docs/plugins/13-step4-contract.md §9, the mockup's "Core-only keel" screen in
// docs/plugins/mockup.html): Code, KeelBot, Map, Graph and Wiki are plugins, so the menu's Project group has no pages.
//
//   menu       the Project group is one small box: "No pages yet" and + Add pages (Settings › Plugins, Marketplace)
//   Flow       a project with no flow opens on "Welcome to keel core": the marketplace, how plugins work, the four
//              sets (GET /api/plugins/sets: Developer, Review, Knowledge, Tickets) and what core does by itself
//   the brand  the tag next to the logo says "core"
//
// A set's "Install N" opens Settings › Plugins with that set (#/settings/plugins?set=<id>): the same offer and confirm as there,
// so nothing installs without the person's click there. N counts what the set adds: the plugins keel does not have
// (downloaded) and the image's plugins that are off (turned on, not downloaded).

import { useState, type ReactNode } from "react";
import { useFeatures, useView } from "../addons";
import type { Features } from "../api";
import { marketApi, setsOf, type PluginSet } from "../marketplaceApi";
import { SCREEN } from "../routes";
import { usePages, type PageRegistration } from "../sdk/registry";
import { useApp, useLoad } from "../state";
import { Drawer } from "./ui";

const OWN = new Set<string>(Object.values(SCREEN));

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

/** coreOnly: no Project page (the menu's box). bare: no plugin's page at all (the welcome, the "core" tag). */
export function useCoreOnly(): { coreOnly: boolean; bare: boolean } {
  const features = useFeatures();
  const view = useView();
  const pages = usePages();
  const coreOnly = isCoreOnly(features, view, pages);
  return { coreOnly, bare: coreOnly && pages.every((p) => OWN.has(p.id)) };
}

/** The link that opens Settings › Plugins on its Marketplace tab. */
export const MARKETPLACE_HASH = "#/settings/plugins?tab=market";
/** The link that offers a set in Settings › Plugins (its confirm installs it). */
export const setHash = (id: string) => `#/settings/plugins?set=${encodeURIComponent(id)}`;

// ---------- the menu ----------

/** The Project group of a keel with only its core: one small box instead of the pages. */
export function NoPagesYet({ onPick }: { onPick?: () => void }) {
  return (
    <div className="nav-sec nav-empty-sec">
      <div className="nav-h" title="what this project is">
        <span>Project</span>
      </div>
      <div className="nav-empty" role="group" aria-label="No pages yet">
        <b>No pages yet</b>
        <p>Code, KeelBot, Map, Graph and Wiki are plugins now.</p>
        <button
          type="button"
          className="btn sm"
          onClick={() => {
            location.hash = MARKETPLACE_HASH;
            onPick?.();
          }}
        >
          + Add pages
        </button>
      </div>
    </div>
  );
}

// ---------- the plugins' small icons ----------

/** keel's own plugins: their title, the short code-style mark on their icon, and its colour. */
const PLUGIN_MARK: Record<string, [title: string, mark: string, tone: string]> = {
  code: ["Code", "{ }", ""],
  git: ["Git", "git", "c3"],
  review: ["Code Review", "CR", "c2"],
  db: ["Database", "DB", "c4"],
  ci: ["CI/CD", "CI", "c3"],
  keelbot: ["KeelBot", "KB", ""],
  map: ["Map", "MAP", "c2"],
  graph: ["Graph", "GR", "c2"],
  wiki: ["Wiki", "WK", "c3"],
  tasks: ["Tasks", "TK", "c4"],
  jira: ["Jira", "JR", "c2"],
  product: ["keel Product", "PRD", ""],
};

/** A plugin's title and icon mark; one keel does not know gets its name and its first letters. */
export function pluginMark(name: string): { title: string; mark: string; tone: string } {
  const own = PLUGIN_MARK[name];
  if (own) return { title: own[0], mark: own[1], tone: own[2] };
  const title = name.replace(/[-_]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  return { title, mark: name.replace(/[^a-z0-9]/gi, "").slice(0, 3).toUpperCase() || "?", tone: "c2" };
}

/** A plugin's small icon: its mark in the code font. */
export function PluginIcon({ name, size = "", label }: { name: string; size?: "" | "sm" | "lg"; label?: string }) {
  const m = pluginMark(name);
  return (
    <span className={`pl-ico ${m.tone} ${size}`} aria-hidden={label ? undefined : true} title={label}>
      {m.mark}
    </span>
  );
}

// ---------- the welcome ----------

/** What the set adds: the plugins keel does not have, and the ones it has that are off. */
export const setAdds = (s: PluginSet): number =>
  new Set([...(s.missing ?? []), ...(s.off ?? [])].filter((n) => s.plugins.includes(n))).size;

const setTitle = (s: PluginSet) => {
  const t = s.title?.trim() || s.id;
  return /\bset$/i.test(t) ? t : `${t} set`;
};

function SetCard({ set }: { set: PluginSet }) {
  const n = setAdds(set);
  const names = set.plugins.map((p) => pluginMark(p).title);
  return (
    <div className="ch-set" role="group" aria-label={setTitle(set)}>
      <h3>{setTitle(set)}</h3>
      {set.summary ? <p className="sub">{set.summary}</p> : <span />}
      <div className="ch-mini">
        {set.plugins.map((p) => (
          <PluginIcon key={p} name={p} size="sm" label={pluginMark(p).title} />
        ))}
        <span className="sub">{names.join(", ")}</span>
      </div>
      {n > 0 ? (
        <button className="btn sm" type="button" onClick={() => (location.hash = setHash(set.id))}
          title={set.off?.length ? "The plugins that are off are turned on, not downloaded" : undefined}>
          Install {n}
        </button>
      ) : (
        <button className="btn sm" type="button" disabled>
          All here
        </button>
      )}
    </div>
  );
}

/** What keel's core does by itself, with no plugin. */
const CORE_DOES = [
  "Runs flows and workflows",
  "Agents, skills and stacks",
  "Guard and gates",
  "Inbox and notifications",
  "Budget and usage",
  "Connections and secrets",
  "Projects",
  "Plugin host and marketplace",
];

/** The short answer to "How plugins work". */
const HOW = [
  ["One signed file", "Engine, api, web, content and migrations, described by keel-plugin.yml."],
  ["Loaded when keel starts", "From /data/plugins. Install means check, unpack, restart (about 30 s)."],
  ["Core never imports a plugin", "Plugins use a versioned SDK and named slots. Code hosts its own plugins."],
  ["Trust from signatures", "sha256 and Ed25519. You see the permissions before you install."],
  ["A catalog, not a server", "A git repo and one signed index.json."],
  ["Agents only ask", "They search and send a request. A person approves it in the Inbox."],
];

function HowPlugins({ onClose }: { onClose: () => void }) {
  return (
    <Drawer title="How plugins work" onClose={onClose} id="how-plugins">
      <p style={{ margin: 0 }}>
        One small core. Plugins come from their own repos as signed files. keel unpacks them into{" "}
        <span className="mono">/data/plugins</span> and loads them when it starts. The image is never rebuilt.
      </p>
      <ul className="ch-core ch-how">
        {HOW.map(([b, t]) => (
          <li key={b}>
            <span className="ok-mark" aria-hidden="true">✓</span>
            <span>
              <b>{b}</b>
              <br />
              <span className="sub">{t}</span>
            </span>
          </li>
        ))}
      </ul>
      <div className="row">
        <button className="btn primary" type="button" onClick={() => { location.hash = MARKETPLACE_HASH; onClose(); }}>
          Open the marketplace
        </button>
      </div>
    </Drawer>
  );
}

/** "Welcome to keel core": where a project of a keel with no plugins opens. `children`: the project's own part (its flow). */
export function CoreWelcome({ children }: { children?: ReactNode }) {
  const { health } = useApp();
  const sets = useLoad("plugin-sets", () => marketApi.sets(), { live: false });
  const [how, setHow] = useState(false);
  const list = setsOf(sets.data);
  const version = health?.version?.replace(/^v/, "");
  return (
    <div className="core-home" data-testid="core-welcome">
      <section className="ch-hello" aria-labelledby="ch-hello-t">
        <span className="tag keel">keel core{version ? ` ${version}` : ""}</span>
        <h1 id="ch-hello-t">Welcome to keel core</h1>
        <p>
          This keel has only the core. Flows, workflows, agents, stacks, the Inbox, the budget and your settings work now.
          Pages like Code and KeelBot are plugins. Add the ones you need. You can remove them later.
        </p>
        <div className="actions">
          <button className="btn primary" type="button" onClick={() => (location.hash = MARKETPLACE_HASH)}>
            Open the marketplace
          </button>
          <button className="btn" type="button" onClick={() => setHow(true)}>
            How plugins work
          </button>
        </div>
      </section>

      <div className="ch-sect">
        <h2>Start with a set</h2>
        <p className="sub">One click installs the set. Then keel restarts once.</p>
      </div>
      {sets.error && !sets.data ? (
        <p className="sub">
          The sets could not be read.{" "}
          <button className="btn sm ghost" type="button" onClick={() => void sets.reload()}>
            Try again
          </button>
        </p>
      ) : !sets.data ? (
        <p className="sub">Reading the sets…</p>
      ) : list.length ? (
        <div className="ch-sets">
          {list.map((s) => (
            <SetCard key={s.id} set={s} />
          ))}
        </div>
      ) : (
        <p className="sub">No sets here. Find plugins in the marketplace.</p>
      )}

      <div className="ch-sect">
        <h2>What core does without plugins</h2>
      </div>
      <div className="panel">
        <div className="panel-body">
          <ul className="ch-core" aria-label="What core does without plugins">
            {CORE_DOES.map((x) => (
              <li key={x}>
                <span className="ok-mark" aria-hidden="true">✓</span>
                {x}
              </li>
            ))}
          </ul>
        </div>
      </div>
      <p className="ch-note">A flow can ask for a plugin it needs. The request waits in the Inbox until you approve it.</p>
      {children}
      {how && <HowPlugins onClose={() => setHow(false)} />}
    </div>
  );
}
