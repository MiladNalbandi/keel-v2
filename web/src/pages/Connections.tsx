// keel 0.18.57 Settings › Connections: the code hosts and ticket systems keel works with. GitHub (keel's own: the token
// a flow pushes and opens pull requests with) and GitLab (the Code Review plugin) are one login for the whole keel;
// Jira (its plugin) is set per project. Each is a part's piece (slot connections.kind); the Database plugin's piece is
// on its own tab (Settings › Databases), and the AI accounts are on Models & keys.

import { useSlot } from "../sdk/registry";
import { SLOTS, type ConnectionKindItem } from "../sdk/slots";
import { SettingsFrame } from "./settings/frame";

// the provider rows moved to Settings › Models & keys; their state is still asked for by this name
export { connState } from "./settings/Models";

/** Kinds that have their own Settings tab now (the Database plugin's Databases). */
const OWN_TAB = new Set(["databases"]);

export function ConnectionsPage({ pid }: { pid: string }) {
  const kinds = useSlot<ConnectionKindItem>(SLOTS.connectionsKind).filter((k) => !OWN_TAB.has(k.id));
  return (
    <SettingsFrame tab="connections" title="Connections"
      line="The code hosts and ticket systems keel works with. GitHub and GitLab are one login for the whole keel; Jira is set per project.">
      {kinds.map((k) => <k.component key={k.id} pid={pid} />)}
      <p className="ss-sub">
        AI accounts are in <a href="#/settings/models">Models &amp; keys</a>. Databases have <a href="#/settings/databases">their own tab</a>.
      </p>
    </SettingsFrame>
  );
}
