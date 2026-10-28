// keel 0.18.57 Settings: one page with its own menu — General · Models & logins · Connections · Budget · Plugins ·
// Databases · Access — at #/settings/<tab> (#/settings/general/<project> is one project's own settings). The pages that
// were their own before (Budget, Connections, Plugins, the Database plugin's Databases) are tabs now; their old links
// open the tab (routes.ts). A part adds a tab of its own (slot settings.tab: the Database plugin's Databases).

import { BudgetPage } from "./Budget";
import { ConnectionsPage } from "./Connections";
import { AccessTab } from "./settings/Access";
import { SettingsFrame, useSettingsTabs } from "./settings/frame";
import { GeneralTab } from "./settings/General";
import { ModelsTab } from "./settings/Models";
import { PluginsTab } from "./settings/PluginsTab";
import { ReviewTab } from "./settings/Review";
import { settingsTabOf } from "../routes";
import { useRoute } from "../state";

export { GENERAL_SECTIONS } from "./settings/General";
export { MODEL_ROWS } from "./settings/Models";
export { BUDGET_ROWS } from "./Budget";
export { show } from "./settings/rows";

export function SettingsPage({ pid }: { pid: string }) {
  const route = useRoute();
  const [tab, rest] = settingsTabOf(route);
  const tabs = useSettingsTabs();
  switch (tab) {
    case "general": return <GeneralTab pid={pid} scopeArg={rest} />;
    case "models": return <ModelsTab pid={pid} />;
    case "review": return <ReviewTab pid={pid} />;
    case "connections": return <ConnectionsPage pid={pid} />;
    case "budget": return <BudgetPage pid={pid} />;
    case "plugins": return <PluginsTab rest={rest} />;
    case "access": return <AccessTab />;
  }
  const part = tabs.find((t) => t.id === tab)?.part;
  if (part) {
    const Body = part.component;
    const Actions = part.actions;
    return (
      <SettingsFrame tab={part.id} title={part.title} line={part.line} actions={Actions ? <Actions pid={pid} /> : undefined}>
        <Body pid={pid} />
      </SettingsFrame>
    );
  }
  // a tab of a part that is off (Databases without the Database plugin), or a link with a typo
  return (
    <SettingsFrame tab={tab} title="Not here" line="This keel has no such settings tab.">
      <div className="ss-cardp">
        <p className="ss-sub">
          {tab === "databases"
            ? <>Databases are the Database plugin's. Turn it on in <a href="#/settings/plugins">Plugins</a>, then come back.</>
            : <>Pick a tab from the menu, or go to <a href="#/settings/general">General</a>.</>}
        </p>
      </div>
    </SettingsFrame>
  );
}
