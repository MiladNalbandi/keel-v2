// The Database part (web/src/builtins.ts loads this file): its section in Connections, its view in the Code page's
// activity bar (while the plugin is on), a table or a console as an editor tab, its workflow blocks (db:check, …), and
// the Query panel under the Map plugin's database diagram (map.er.query).

import { registerSlot } from "../../../sdk/registry";
import {
  SLOTS,
  type CodeActivityItem,
  type CodeActivityProps,
  type CodeTabItem,
  type CodeTabProps,
  type ConnectionKindItem,
  type MapErQueryItem,
  type WorkflowActionsItem,
} from "../../../sdk/slots";
import { DatabasesSection } from "../Databases";
import { DbExplorer, DbTab, dbPath, dbTabTitle } from "../DbTool";
import { QueryPanel } from "../QueryPanel";

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

registerSlot<ConnectionKindItem>(SLOTS.connectionsKind, {
  id: "databases",
  title: "Databases",
  order: 40,
  component: DatabasesSection,
});

registerSlot<CodeActivityItem>(SLOTS.codeActivity, {
  id: "db",
  title: "Database",
  icon: "database",
  short: "DB",
  order: 50,
  plugin: "db",
  component: DbActivity,
});

registerSlot<CodeTabItem>(SLOTS.codeTab, {
  id: "db",
  tabTitle: dbTabTitle,
  component: DbEditorTab,
});

registerSlot<WorkflowActionsItem>(SLOTS.workflowActions, {
  id: "db",
  plugin: "db",
  prefix: "db:",
});

// Map › Database (ER): the live database next to the diagram (it says itself when the plugin is off)
registerSlot<MapErQueryItem>(SLOTS.mapErQuery, {
  id: "db",
  component: QueryPanel,
});
