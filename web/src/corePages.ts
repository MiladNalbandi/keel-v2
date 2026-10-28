// keel's own pages in the menu and the router. The parts' pages (Tasks, Code, KeelBot, Map, Graph, Wiki) register
// themselves (web/src/builtins.ts) in the same list; `order` places each page in its group.
//
//   Run      Flow 10 · Tasks 20 · Inbox 30 · Live agents 40 · Jobs 50
//   Project  Code 10 · KeelBot 20 · Map 30 · Graph 40 · Wiki 50
//   Build    Workflows 10 · Rules 15 · Agents 20 · Skill hub 30 · Stacks 40 · Tools (MCP) 50 · Quality 60
//   Control  Settings 20 (its tabs: General · Models & logins · Review · Connections · Budget · Plugins · Databases · Access)
//   Help     Start here 10 (keel 0.18.66; #/help/<guide> is one short guide)
//
// And keel's own piece in a part's place: Settings › Connections › GitHub (slot connections.kind, order 5: first, then
// GitLab and Jira). Its token is core: the Git plugin pushes with it, and a flow's ship step opens the pull request.

import { createElement, Fragment } from "react";
import { AgentsPage } from "./pages/Agents";
import { HelpPage } from "./pages/Help";
import { GitHubSection } from "./components/GitHubToken";
import { FlowPage } from "./pages/Flow";
import { InboxPage } from "./pages/Inbox";
import { JobsPage } from "./pages/Jobs";
import { LivePage } from "./pages/Live";
import { QualityPage } from "./pages/Quality";
import { RulesPage } from "./pages/Rules";
import { SettingsPage } from "./pages/Settings";
import { SkillsPage } from "./pages/Skills";
import { StacksPage } from "./pages/Stacks";
import { ToolsPage } from "./pages/Tools";
import { WorkflowsPage } from "./pages/Workflows";
import { SCREEN } from "./routes";
import { registerPage, registerSlot } from "./sdk/registry";
import { SLOTS, type ConnectionKindItem } from "./sdk/slots";

registerPage({
  id: SCREEN.flow,
  label: "Flow",
  group: "run",
  order: 10,
  component: FlowPage,
});
// the Inbox spans every project: it needs no chosen one
registerPage({
  id: SCREEN.inbox,
  label: "Inbox",
  group: "run",
  order: 30,
  needsProject: false,
  product: true,
  component: InboxPage,
});
registerPage({
  id: SCREEN.live,
  label: "Live agents",
  group: "run",
  order: 40,
  component: LivePage,
});
registerPage({
  id: SCREEN.jobs,
  label: "Jobs",
  group: "run",
  order: 50,
  component: JobsPage,
});

registerPage({
  id: SCREEN.workflows,
  label: "Workflows",
  group: "build",
  order: 10,
  component: WorkflowsPage,
});
// v0.17.2 keel's rules stay core: the guard that applies them is core (rule packs install into the project)
registerPage({
  id: SCREEN.rules,
  label: "Rules",
  group: "build",
  order: 15,
  component: RulesPage,
});
registerPage({
  id: SCREEN.agents,
  label: "Agents",
  group: "build",
  order: 20,
  component: AgentsPage,
});
registerPage({
  id: SCREEN.skills,
  label: "Skill hub",
  group: "build",
  order: 30,
  component: SkillsPage,
});
registerPage({
  id: SCREEN.stacks,
  label: "Stacks",
  group: "build",
  order: 40,
  component: StacksPage,
});
registerPage({
  id: SCREEN.tools,
  label: "Tools (MCP)",
  group: "build",
  order: 50,
  component: ToolsPage,
});
// v0.8.0: quality runs are keel's own, not one project's
registerPage({
  id: SCREEN.quality,
  label: "Quality",
  group: "build",
  order: 60,
  needsProject: false,
  component: QualityPage,
});

// keel 0.18.57 one Settings page with its own menu: General, Models & logins, Connections, Budget, Plugins, (the
// Database plugin's) Databases and Devices are its tabs (#/settings/<tab>). Budget, Connections and Plugins were pages
// of their own before; their old links (#/budget, #/connections, #/plugins/<name>) open the tab (routes.ts).
registerPage({
  id: SCREEN.settings,
  label: "Settings",
  group: "control",
  order: 20,
  needsProject: "optional",
  product: true,
  component: SettingsPage,
});

// keel 0.18.66 Help › Start here: the five set-up steps and the short guides (#/help/<guide>); it needs no project
registerPage({
  id: SCREEN.help,
  label: "Start here",
  group: "help",
  order: 10,
  needsProject: false,
  product: true,
  icon: createElement(Fragment, null,
    createElement("circle", { cx: 12, cy: 12, r: 9 }),
    createElement("path", { d: "M9.6 9.3a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.1-2.4 3.6" }),
    createElement("path", { d: "M12 17h.01" })),
  component: HelpPage,
});

registerSlot<ConnectionKindItem>(SLOTS.connectionsKind, {
  id: "github",
  title: "GitHub",
  order: 5,
  component: GitHubSection,
});
