// The Database plugin's web part (plugins/db): `npm run build:plugin -- db` (in web/) builds this file on its own into
// plugins/db/web/dist/index.js. keel loads it at start from the url /api/features gives and calls setup() once. It puts
// in the same places as keel 0.15.1, with the same ids, titles and order (and since keel 0.18.57 its tab Settings ›
// Databases, for connections global or per project, with the Query panel): its
// view in the Code page's activity bar (Database, while the plugin is on), a table or a console as an editor tab, its
// workflow blocks (db:check, …), the Query panel under the Map plugin's database diagram (map.er.query), and KeelBot's
// query button (keelbot.card, for keel-query blocks). It imports only @keel/web-sdk and react: keel's page shares its
// own copies (the import map).

import {
  definePlugin,
  SLOTS,
  type CodeActivityItem,
  type CodeActivityProps,
  type CodeTabItem,
  type CodeTabProps,
  type KeelbotCardItem,
  type KeelSdk,
  type MapErQueryItem,
  type SettingsTabItem,
  type WorkflowActionsItem,
} from "@keel/web-sdk";
import { DatabasesTab } from "./Databases";
import { DbExplorer, DbTab, dbPath, dbTabTitle } from "./DbTool";
import { QueryCard } from "./QueryCard";
import { ResultCard } from "./Private";
import { QueryPanel } from "./QueryPanel";

/** Code › Database: the connections and their tables; a click opens a table or a console as a tab. */
function DbActivity({ pid, open }: CodeActivityProps) {
  return (
    <DbExplorer
      pid={pid}
      onConsole={(c) => open({ kind: "db", path: dbPath(c) }, { pin: true })}
      onTable={(c, t, pin) => open({ kind: "db", path: dbPath(c, t) }, { pin })}
    />
  );
}

/** A table or a console; a console that moves to another database takes its tab with it. */
function DbEditorTab({ pid, tab, retarget }: CodeTabProps) {
  return (
    <DbTab
      pid={pid}
      path={tab.path}
      onConn={(c) => retarget({ kind: "db", path: dbPath(c) })}
    />
  );
}

export function setup(sdk: KeelSdk) {
  // keel 0.18.57 Settings › Databases: connections for all projects (or some), and the chosen project's own, with the
  // live database's Query under them. It was Control › Databases (#/databases opens the tab now) and a section of
  // Connections before.
  sdk.registerSlot<SettingsTabItem>(SLOTS.settingsTab, {
    id: "databases",
    title: "Databases",
    order: 60,
    line: "Database connections for all projects, or for one. keel keeps each password encrypted; agents never see it.",
    component: DatabasesTab,
  });

  sdk.registerSlot<CodeActivityItem>(SLOTS.codeActivity, {
    id: "db",
    title: "Database",
    icon: "database",
    short: "DB",
    order: 50,
    plugin: "db",
    component: DbActivity,
  });

  sdk.registerSlot<CodeTabItem>(SLOTS.codeTab, {
    id: "db",
    tabTitle: dbTabTitle,
    component: DbEditorTab,
  });

  sdk.registerSlot<WorkflowActionsItem>(SLOTS.workflowActions, {
    id: "db",
    plugin: "db",
    prefix: "db:",
  });

  // Map › Database (ER): the live database next to the diagram (it says itself when the plugin is off)
  sdk.registerSlot<MapErQueryItem>(SLOTS.mapErQuery, {
    id: "db",
    component: QueryPanel,
  });

  // KeelBot: a keel-query block in an answer is the query button (Run; a change is counted first)
  sdk.registerSlot<KeelbotCardItem>(SLOTS.keelbotCard, {
    id: "db",
    kind: "keel-query",
    component: QueryCard,
  });

  // #96 KeelBot: a keel-result block is a private result's card (rows only the person sees; they pick what to send)
  sdk.registerSlot<KeelbotCardItem>(SLOTS.keelbotCard, {
    id: "db-result",
    kind: "keel-result",
    component: ResultCard,
  });
}

export default definePlugin({ name: "db", setup });
