import { useEffect, useState } from "react";
import { errorParts } from "../../api";
import { ErrorBox, Pill } from "../../components/ui";
import { marketApi, pendingOf, sourcesOf, updateOf } from "../../marketplaceApi";
import { queryValue, settingsHash } from "../../routes";
import { SettingsFrame } from "../settings/frame";
import { useSlot, type PageProps } from "../../sdk/registry";
import { SLOTS, type MarketKindItem } from "../../sdk/slots";
import { useApp, useLoad, useRoute } from "../../state";
import { PluginDetailPage, titleOf, type Err } from "../PluginDetail";
import { CORE_KINDS, KindSwitch, MarketWorkflowPage, WorkflowsMarket } from "../MarketWorkflow";
import { MarketRulePackPage, RulesMarket } from "../MarketRulePack";
import { MarketSkillPage, SkillsMarket } from "../MarketSkill";
import { MarketStackPage, StacksMarket } from "../MarketStack";
import { InstallDialog } from "./InstallDialog";
import { InstalledTab } from "./InstalledTab";
import { MarketTab } from "./MarketTab";
import { RestartBanner } from "./Restart";
import { SetOffer } from "./SetOffer";
import { SourcesTab } from "./SourcesTab";

type Tab = "installed" | "market" | "sources";

/** What the install dialog does: install these (and what they need), then turn these on. */
export type InstallPlan = { install: string[]; turnOn?: string[]; title?: string };

const SUB: Record<Tab, string> = {
  installed:
    "What this keel has installed. Turn a plugin off to hide it without losing its data. Plugins that came in the image can be updated from the marketplace.",
  market:
    "Plugins add pages, tools, workflows and agents to keel. Each comes from its own git repo. keel checks its signature before it installs it.",
  sources: "Where plugins come from, and who may install them.",
};
const SUB_WORKFLOWS =
  "Workflows made by keel and the community. One installs into a project as an editable copy. keel checks its signature and shows what it runs first.";
const SUB_RULES =
  "Rule packs made by keel and the community: guard rules agents meet at every tool call. One installs into a project's .keel/rules/. keel checks its signature and shows everything it blocks first.";

const SUB_STACKS =
  "Stacks made by keel and the community: how a project builds and tests (its test layers, commands and tools). One installs into a project's .keel/stacks/. keel checks its signature and shows every command first.";

const SUB_SKILLS =
  "Skills made by keel and the community: know-how an agent reads before it works. One installs into a project, for the agents you pick. keel checks its signature and shows all of it first.";

/** keel 0.18.57 the tab's own link (#/settings/plugins); an old #/plugins link opens it too. */
export const PLUGINS_HASH = settingsHash("plugins");

export function PluginsPage({ rest }: PageProps & { rest?: string }) {
  const route = useRoute();
  // #/settings/plugins/<name>: the Settings page hands the rest of its link (the router hands a keel-wide page no arg)
  const arg = rest;
  const { toast } = useApp();
  const setId = queryValue(route, "set");
  const view = useLoad("plugins", () => marketApi.installed());
  // #/settings/plugins?tab=market (the core welcome's "Open the marketplace", the menu's "+ Add pages"), tab=installed|sources
  const asked = queryValue(route, "tab");
  const linked: Tab | null =
    asked === "market" || asked === "installed" || asked === "sources"
      ? asked
      : null;
  const [tab, setTab] = useState<Tab>(linked ?? "installed");
  // the Marketplace's kind: #/settings/plugins?tab=market&kind=workflows|rules (the link is the truth); #92 or a part's
  // own kind (slot market.kind: the Org plugin's orgs)
  const askedKind = queryValue(route, "kind");
  const partKinds = useSlot<MarketKindItem>(SLOTS.marketKind).filter((p) => !CORE_KINDS.includes(p.id));
  const partKind = partKinds.find((p) => p.id === askedKind) ?? null;
  const kind: string =
    askedKind && (CORE_KINDS.includes(askedKind) || partKind) ? askedKind : "plugins";
  useEffect(() => {
    if (linked) setTab(linked);
  }, [linked, route.query]);
  const [plan, setPlan] = useState<InstallPlan | null>(null);
  // the list was shown before a plugin's page: its crumb goes back to the tab the person came from
  const [listed, setListed] = useState(!arg);
  useEffect(() => {
    if (!arg) setListed(true);
  }, [arg]);
  const [checking, setChecking] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const count = view.data?.plugins.length;
  const waiting = view.data ? (pendingOf(view.data)?.changes ?? []).length : 0;

  const back = (t: Tab) => {
    setTab(t);
    if (location.hash !== PLUGINS_HASH) location.hash = PLUGINS_HASH;
  };
  // "Check for updates": read the catalogs again, then show what has an update
  const check = async () => {
    setChecking(true);
    setErr(null);
    try {
      await marketApi.refresh();
      const fresh = await marketApi.installed();
      view.setData(fresh);
      const ready = fresh.plugins.filter((p) => updateOf(p));
      setTab("installed");
      toast(
        ready.length
          ? `${ready.length} update${ready.length === 1 ? "" : "s"} ready: ${ready.map(titleOf).join(", ")}.`
          : "Every plugin is up to date.",
      );
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setChecking(false);
    }
  };

  const dialog = plan && (
    <InstallDialog
      plan={plan}
      view={view.data}
      onClose={() => setPlan(null)}
      onDone={() => {
        void view.reload();
        if (setId) location.hash = PLUGINS_HASH;
      }}
    />
  );

  // #/settings/plugins/stacks/<name>: a stack of the marketplace, or keel's own
  if (arg?.startsWith("stacks/"))
    return <MarketStackPage key={arg} name={arg.slice("stacks/".length)} />;

  // #/settings/plugins/skills/<name>: a skill of the marketplace, or keel's own
  if (arg?.startsWith("skills/"))
    return <MarketSkillPage key={arg} name={arg.slice("skills/".length)} />;

  // #/settings/plugins/rules/<name>: a rule pack of the marketplace
  if (arg?.startsWith("rules/"))
    return <MarketRulePackPage key={arg} name={arg.slice("rules/".length)} />;

  // #/settings/plugins/workflow/<name>: a workflow of the marketplace (a plugin's name never has a "/")
  if (arg?.startsWith("workflow/"))
    return (
      <MarketWorkflowPage
        key={arg}
        name={arg.slice("workflow/".length)}
        view={view.data}
      />
    );

  if (arg)
    return (
      <>
        {view.data && (
          <RestartBanner view={view.data} onDone={() => void view.reload()} />
        )}
        <PluginDetailPage
          key={arg}
          name={arg}
          view={view}
          back={
            listed
              ? tab
              : view.data?.plugins.some((p) => p.name === arg)
                ? "installed"
                : "market"
          }
          onBack={back}
          onInstall={(name) => setPlan({ install: [name] })}
        />
        {dialog}
      </>
    );

  const sub =
    tab === "market" && kind === "workflows" ? SUB_WORKFLOWS
      : tab === "market" && kind === "rules" ? SUB_RULES
        : tab === "market" && kind === "skills" ? SUB_SKILLS
          : tab === "market" && kind === "stacks" ? SUB_STACKS
            : tab === "market" && partKind ? partKind.line
              : SUB[tab];
  const TABS: [Tab, string][] = [
    ["installed", count === undefined ? "Installed" : `Installed (${count})`],
    ["market", "Marketplace"],
    ["sources", "Sources and rules"],
  ];
  return (
    <SettingsFrame tab="plugins" title="Plugins"
      line="Plugins add pages, tools and workflows to keel. keel checks each one's signature before it installs it."
      actions={
        <>
          <button className="ss-btn" type="button" disabled={checking} onClick={() => void check()}>
            {checking ? "Checking…" : "Check for updates"}
          </button>
          <button className="ss-btn p" type="button" onClick={() => setTab("market")}>Browse the marketplace</button>
        </>
      }
      after={dialog}>
      {err && <ErrorBox error={err} />}
      {view.data && <RestartBanner view={view.data} onDone={() => void view.reload()} />}
      {setId && (
        <SetOffer id={setId} view={view.data} onInstall={(p) => setPlan(p)} onClose={() => { location.hash = PLUGINS_HASH; }} />
      )}
      <div className="ss-inline" style={{ alignItems: "flex-end" }}>
        <div className="ss-utabs" role="tablist" aria-label="Plugins" style={{ flex: 1 }}>
          {TABS.map(([k, l]) => (
            <button key={k} role="tab" type="button" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>
          ))}
        </div>
        {waiting > 0 && <Pill tone="warn">{waiting} waiting for restart</Pill>}
      </div>
      <p className="ss-sub">
        {sub} <span className="mk-intro">Everything is on by default, like 0.16.1. Start with fewer plugins: <code>keel2 start --core</code>.</span>
      </p>
      <div className="mk-tab">
        {tab === "installed" && <InstalledTab view={view} />}
        {tab === "market" && <CatalogLine onSources={() => setTab("sources")} />}
        {tab === "market" && <KindSwitch kind={kind} />}
        {tab === "market" && kind === "workflows" && <WorkflowsMarket view={view.data} />}
        {tab === "market" && kind === "rules" && <RulesMarket />}
        {tab === "market" && kind === "skills" && <SkillsMarket />}
        {tab === "market" && kind === "stacks" && <StacksMarket />}
        {tab === "market" && partKind && kind === partKind.id && <partKind.component />}
        {tab === "market" && kind === "plugins" && (
          <MarketTab view={view.data} onInstall={(name) => setPlan({ install: [name] })} onChanged={() => void view.reload()} />
        )}
        {tab === "sources" && <SourcesTab onChanged={() => void view.reload()} />}
      </div>
    </SettingsFrame>
  );
}

/** "Catalog keel official · Sources": the catalogs keel reads, and a way to them. */
function CatalogLine({ onSources }: { onSources: () => void }) {
  const src = useLoad("plugin-sources", () => marketApi.sources(), {
    live: false,
  });
  const on = sourcesOf(src.data).filter((s) => s.on !== false);
  return (
    <div className="mk-catalog">
      <span className="sub">Catalog</span>
      {on.map((s) => (
        <span
          key={s.id}
          className={`tag ${s.ok === false || s.problem ? "warn" : "keel"}`}
          title={s.problem ?? undefined}
        >
          {s.title || s.id}
        </span>
      ))}
      {src.data && !on.length && <span className="tag warn">none</span>}
      <button className="btn sm ghost" type="button" onClick={onSources}>
        Sources
      </button>
    </div>
  );
}
