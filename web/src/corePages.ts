// keel's own pages in the menu and the router. The parts' pages (Tasks, Code, KeelBot, Map, Graph, Wiki) register
// themselves (web/src/builtins.ts) in the same list; `order` places each page in its group.
//
// #93 the groups (routes.ts GROUP_HEADS):
//   Inbox    Inbox 10 (right under All projects)
//   Work     Flow 10 · Tasks 20 · Goals 22 · Org 24 · Desk 25            (group id "run")
//   Watch    Live agents 10 · Jobs 20 · Record 30 · Cluster 40
//   Project  Code 10 · Review 15 · Map 30 · Graph 40 · KeelBot 45 · Wiki 50 · Library 55   (group id "know")
//   Build    Workflows 10 · Agents 20 · Skill hub 30 · Stacks 40 · Tools (MCP) 50 · Safety & History 55 · Quality 60
//   (foot)   Settings 20 (its tabs: General · Models & logins · Review · Connections · Budget · Plugins · Databases · Access)
//            Start here 10 (keel 0.18.66; #/help/<guide> is one short guide)
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
// the Inbox spans every project: it needs no chosen one. #93 it is the first link under All projects
registerPage({
  id: SCREEN.inbox,
  label: "Inbox",
  group: "home",
  order: 10,
  needsProject: false,
  product: true,
  component: InboxPage,
});
registerPage({
  id: SCREEN.live,
  label: "Live agents",
  group: "watch",
  order: 10,
  component: LivePage,
});
registerPage({
  id: SCREEN.jobs,
  label: "Jobs",
  group: "watch",
  order: 20,
  component: JobsPage,
});

registerPage({
  id: SCREEN.workflows,
  label: "Workflows",
  group: "build",
  order: 10,
  component: WorkflowsPage,
});
// v0.17.2 keel's rules stay core: the guard that applies them is core (rule packs install into the project); #89 the
// page is Safety & History: the rules and the project's history of tool calls. #93 after what agents use, before Quality
registerPage({
  id: SCREEN.rules,
  label: "Safety & History",
  group: "build",
  order: 55,
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
