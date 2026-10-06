// Root: providers, shell, and the hash router (one file per screen under src/pages/).

import { useEffect, type ReactElement } from "react";
import { Shell } from "./components/Shell";
import { NoProject } from "./components/ui";
import { AgentsPage } from "./pages/Agents";
import { BudgetPage } from "./pages/Budget";
import { ConnectionsPage } from "./pages/Connections";
import { FlowPage } from "./pages/Flow";
import { InboxPage } from "./pages/Inbox";
import { JobsPage } from "./pages/Jobs";
import { LivePage } from "./pages/Live";
import { GraphPage } from "./pages/Graph";
import { MapPage } from "./pages/Map";
import { ProjectsPage } from "./pages/Projects";
import { RepoPage } from "./pages/Repo";
import { SettingsPage } from "./pages/Settings";
import { SkillsPage } from "./pages/Skills";
import { StacksPage } from "./pages/Stacks";
import { TasksPage } from "./pages/Tasks";
import { ToolsPage } from "./pages/Tools";
import { WikiPage } from "./pages/Wiki";
import { WorkflowsPage } from "./pages/Workflows";
import type { ScreenId } from "./routes";
import { PageBoundary } from "./components/PageBoundary";
import { QualityPage } from "./pages/Quality";
import { HelperPage } from "./pages/Helper";
import { AppProvider, useApp, useRoute } from "./state";

/** Screens that need a chosen project. */
const PAGES: Record<Exclude<ScreenId, "projects" | "inbox" | "quality">, (p: { pid: string }) => ReactElement> = {
  flow: FlowPage,
  tasks: TasksPage,
  live: LivePage,
  jobs: JobsPage,
  repo: RepoPage,
  helper: HelperPage,
  map: MapPage,
  graph: GraphPage,
  wiki: WikiPage,
  workflows: WorkflowsPage,
  agents: AgentsPage,
  skills: SkillsPage,
  stacks: StacksPage,
  tools: ToolsPage,
  budget: BudgetPage,
  settings: SettingsPage,
  connections: ConnectionsPage,
};

function Router() {
  const { page } = useRoute();
  const { pid, projectsLoaded, projectsError } = useApp();
  useEffect(() => {
    try {
      window.scrollTo(0, 0);
    } catch {
      /* jsdom */
    }
  }, [page]);
  if (page === "projects") return <ProjectsPage />;
  // The inbox spans every project: it needs no chosen one.
  if (page === "inbox") return <PageBoundary resetKey="inbox"><InboxPage /></PageBoundary>;
  // v0.8.0: quality runs are keel's own, not one project's
  if (page === "quality") return <PageBoundary resetKey="quality"><QualityPage /></PageBoundary>;
  // Connections and General settings work without a project, but every other screen needs one.
  if (!pid) {
    if (!projectsLoaded) return <div className="empty loading" role="status">Loading…</div>;
    if (page === "connections") return <ConnectionsPage pid="" />;
    if (page === "settings") return <SettingsPage pid="" />;
    return (
      <>
        {projectsError && <div className="errbox" role="alert" style={{ marginBottom: 16 }}><b>{projectsError}</b></div>}
        <NoProject />
      </>
    );
  }
  const Page = PAGES[page];
  return <PageBoundary resetKey={`${page}:${pid}`}><Page key={pid} pid={pid} /></PageBoundary>;
}

export function App() {
  return (
    <AppProvider>
      <Shell>
        <Router />
      </Shell>
    </AppProvider>
  );
}
