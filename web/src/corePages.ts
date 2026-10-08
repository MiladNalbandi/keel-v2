// keel's own pages in the menu and the router. The parts' pages (Tasks, Code, KeelBot, Map, Graph, Wiki) register
// themselves (web/src/builtins.ts) in the same list; `order` places each page in its group.
//
//   Run      Flow 10 · Tasks 20 · Inbox 30 · Live agents 40 · Jobs 50
//   Project  Code 10 · KeelBot 20 · Map 30 · Graph 40 · Wiki 50
//   Build    Workflows 10 · Agents 20 · Skill hub 30 · Stacks 40 · Tools (MCP) 50 · Quality 60
//   Control  Budget 10 · Settings 20 · Connections 30

import { AgentsPage } from "./pages/Agents";
import { BudgetPage } from "./pages/Budget";
import { ConnectionsPage } from "./pages/Connections";
import { FlowPage } from "./pages/Flow";
import { InboxPage } from "./pages/Inbox";
import { JobsPage } from "./pages/Jobs";
import { LivePage } from "./pages/Live";
import { QualityPage } from "./pages/Quality";
import { SettingsPage } from "./pages/Settings";
import { SkillsPage } from "./pages/Skills";
import { StacksPage } from "./pages/Stacks";
import { ToolsPage } from "./pages/Tools";
import { WorkflowsPage } from "./pages/Workflows";
import { SCREEN } from "./routes";
import { registerPage } from "./sdk/registry";

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

registerPage({
  id: SCREEN.budget,
  label: "Budget",
  group: "control",
  order: 10,
  product: true,
  component: BudgetPage,
});
// Connections and General settings work without a project too
registerPage({
  id: SCREEN.settings,
  label: "Settings",
  group: "control",
  order: 20,
  needsProject: "optional",
  product: true,
  component: SettingsPage,
});
registerPage({
  id: SCREEN.connections,
  label: "Connections",
  group: "control",
  order: 30,
  needsProject: "optional",
  product: true,
  component: ConnectionsPage,
});
