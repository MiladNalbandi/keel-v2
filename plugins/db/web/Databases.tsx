// The Database plugin's places for connections: Settings › Databases (DatabasesTab: the global ones and the chosen
// project's own, then the live database's Query; keel 0.18.57) and a project's Databases (DatabasesSection: what that
// project uses, while the plugin is on for it). Both are DbManager: a grouped list with each database's logo and environment, and the editor. A local or
// test database may get changes of data (always after an OK); staging and prod are read only for agents, always.
// The Database plugin's part (plugins/db): it imports only react and @keel/web-sdk.

import { PageHead, Section, useApp, useLoad, useRoute } from "@keel/web-sdk";
import { QueryPanel } from "./QueryPanel";
import { McpSwitch, PrivateResults } from "./Private";
import { dbApi } from "./dbApi";
import { DbManager } from "./DbManager";

export function DatabasesSection({ pid }: { pid: string }) {
  const { project } = useApp();
  const plugins = useLoad(pid ? `plugins:${pid}` : null, () => dbApi.plugins(pid), { live: false });
  const on = !!plugins.data?.find((p) => p.name === "db")?.enabled;
  if (!on) return null;
  return (
    <Section title="Databases"
      sub="The databases this project uses: its own, and the ones for all projects (Settings › Databases). For KeelBot, workflows and Map › Query.">
      <DbManager pid={pid} projectName={project?.name ?? pid} place="project" />
    </Section>
  );
}

export function DatabasesPage({ pid }: { pid: string }) {
  const { project } = useApp();
  return (
    <>
      <PageHead title="Databases"
        sub="Database connections for all projects, or for one. keel keeps each password encrypted; agents never see it." />
      <DbManager pid={pid} projectName={pid ? project?.name ?? pid : undefined} place="control" />
    </>
  );
}

/** Settings › Databases: the connections (the list and the editor), what MCP clients may do (#96), the results only
 *  the person sees (#96; #/settings/databases/results/<id> opens one), then the Query of the chosen project's live
 *  database. keel draws the tab's head and menu. */
export function DatabasesTab({ pid }: { pid: string }) {
  const { project } = useApp();
  const route = useRoute();
  const plugins = useLoad(pid ? `plugins:${pid}` : null, () => dbApi.plugins(pid), { live: false });
  const on = !!plugins.data?.find((p) => p.name === "db")?.enabled;
  const linked = route.page === "settings" ? /^databases\/results\/([^/]+)$/.exec(route.arg ?? "")?.[1] : undefined;
  return (
    <>
      <DbManager pid={pid} projectName={pid ? project?.name ?? pid : undefined} place="control" />
      <McpSwitch />
      {pid && on && <PrivateResults pid={pid} open={linked} />}
      {pid && <QueryPanel pid={pid} />}
    </>
  );
}
