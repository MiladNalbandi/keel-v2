// keel Product › Teams: who owns which repo (by hand or from CODEOWNERS), their capacity and Jira project, and the
// team's own knowledge pages (keel reads them in every stage; keel suggests, the team accepts). A team also has its
// keel projects, its own Jira (connection, project, issue types, labels), its ticket-key pattern and the ticket keys keel
// found in its git. Two cards for all teams: the keel-wide Jira connection, and where an initiative's own ticket goes.

import { useEffect, useRef, useState } from "react";
import {
  Async,
  Confirm,
  Drawer,
  Empty,
  Markdown,
  Panel,
  Pill,
  Tabs,
  useLoad,
  type AddonPageProps,
} from "@keel/web-sdk";
import {
  productApi,
  type InitiativeJira,
  type JiraConnections,
  type JiraKind,
  type JiraSave,
  type JiraTest,
  type JiraView,
  type KeyTest,
  type RepoOwner,
  type Team,
  type TeamBody,
  type TeamKnowledge,
  type TicketLink,
} from "./productApi";
import {
  Avatar,
  fmtDate,
  goTeam,
  Key,
  ProductHead,
  useAct,
  usePageKeys,
} from "./shared";

const lines = (v: string, sep: RegExp = /\n/) =>
  v
    .split(sep)
    .map((x) => x.trim())
    .filter(Boolean);

// ---------------------------------------------------------------- which Jira connection (B5)

/** The Jira connections there are (null: not known, e.g. an older keel: every repo is offered, as before). */
function useConnections() {
  return useLoad("product-jira-connections", productApi.jiraConnections, { live: false }).data ?? null;
}

/**
 * The connection a choice starts at: the saved one; else, when the keel-wide one is not connected, a connected project's
 * own (one of [prefer] first, the team's projects), so keel never defaults to a connection that does not work.
 */
function startConnection(saved: string | null | undefined, conns: JiraConnections | null, prefer: string[] = []): string {
  if (saved) return saved;
  if (!conns || conns.keel_wide.connected) return "";
  const ok = conns.projects.filter((c) => c.connected).map((c) => c.project_id!);
  return prefer.find((p) => ok.includes(p)) ?? conns.suggested ?? "";
}

/** A connection select: keel-wide, and the projects' own connections (only the connected ones, when keel knows). */
function ConnectionSelect({ id, value, onChange, conns, repos }: {
  id: string; value: string; onChange: (v: string) => void; conns: JiraConnections | null; repos: RepoOwner[];
}) {
  const wide = conns && !conns.keel_wide.connected ? "keel-wide (Jira for every project) · not connected" : "keel-wide (Jira for every project)";
  const own = conns
    ? conns.projects.filter((c) => c.connected || c.project_id === value).map((c) => [c.project_id!,
      `${c.name}'s own connection${c.connected ? (c.base_url ? ` · ${c.base_url}` : "") : " · not connected"}`] as const)
    : repos.map((r) => [r.project_id, `${r.name}'s own connection`] as const);
  const known = own.some(([p]) => p === value);
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{wide}</option>
      {own.map(([p, label]) => <option key={p} value={p}>{label}</option>)}
      {value && !known && <option value={value}>{value}'s own connection · not connected</option>}
    </select>
  );
}

/** Why the select shows what it shows: keel picked a connected project's own, or there is no connection at all. */
function ConnectionNote({ value, saved, conns }: { value: string; saved: string; conns: JiraConnections | null }) {
  if (!conns) return null;
  const name = conns.projects.find((c) => c.project_id === value)?.name ?? value;
  if (value && value !== saved && !saved && !conns.keel_wide.connected)
    return <span className="sub" data-testid="conn-note">The keel-wide connection is not set up, so keel picked {name}'s own connection (no second token). Save to keep it.</span>;
  if (!value && !conns.keel_wide.connected && !conns.projects.some((c) => c.connected))
    return <span className="sub bad" data-testid="conn-note">No Jira connection yet: connect a project's Jira in Settings › Connections › Jira.</span>;
  return null;
}

/** Sets the start value once the connections arrive, unless the person chose already. */
function useStartConnection(saved: string | null | undefined, conns: JiraConnections | null, prefer: string[], set: (v: string) => void) {
  const touched = useRef(false);
  const preferKey = prefer.join(",");
  useEffect(() => {
    if (!touched.current && conns) set(startConnection(saved, conns, preferKey ? preferKey.split(",") : []));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conns, saved, preferKey]);
  return () => { touched.current = true; };
}

export default function Teams({ arg }: AddonPageProps) {
  if (arg) return <TeamPage key={arg} id={arg} />;
  return <TeamList />;
}

function TeamList() {
  const teams = useLoad("teams", productApi.teams);
  const repos = useLoad("team-repos", productApi.repos);
  const conns = useConnections();
  const [editing, setEditing] = useState<Team | "new" | null>(null);
  const act = useAct();
  const [imported, setImported] = useState<string | null>(null);
  const importCodeowners = async () => {
    const r = await act.run("import", () => productApi.importCodeowners());
    if (r) {
      setImported(
        r.teams.length
          ? `Read ${r.read.join(", ") || "CODEOWNERS"}: ${r.teams.length} team(s), ${r.paths} path(s).`
          : "No CODEOWNERS file names a team (@org/team) in these projects.",
      );
      void teams.reload();
      void repos.reload();
    }
  };
  usePageKeys({ n: () => setEditing("new") });
  return (
    <div className="pd pdx">
      <ProductHead
        crumbs={[["Teams"]]}
        title="Teams"
        sub="Who owns which repo. keel uses it to say which team does what, and reads each team's own pages before it plans."
        actions={
          <>
            <button
              className="btn"
              type="button"
              disabled={act.busy !== null}
              onClick={() => void importCodeowners()}
            >
              Read CODEOWNERS
            </button>
            <button
              className="btn primary"
              type="button"
              onClick={() => setEditing("new")}
            >
              <span aria-hidden="true">+</span> New team <Key k="N" />
            </button>
          </>
        }
      />
      {act.box}
      {act.busy === "import" && (
        <p className="hint" role="status">
          Working… reading the CODEOWNERS files of your projects
        </p>
      )}
      {imported && (
        <p className="pd-note n-good pd-pop" role="status">
          {imported}
        </p>
      )}
      <Async r={teams} what="Loading the teams">
        {(list) =>
          list.length === 0 ? (
            <Empty>
              No team yet. Add one by hand, or read the CODEOWNERS files of your
              projects.
            </Empty>
          ) : (
            <div className="pd-teams rise r1">
              {list.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className="pd-card"
                  onClick={() => goTeam(t.id)}
                  aria-label={`Team ${t.name}`}
                >
                  <span className="pd-who">
                    <Avatar name={t.name} />
                    <span>
                      <span className="pd-card-top">
                        <b>{t.name}</b>
                        {t.source === "codeowners" && (
                          <Pill tone="idle">CODEOWNERS</Pill>
                        )}
                      </span>
                      <span className="sub">
                        {t.lead ? `lead ${t.lead}` : "no lead"}
                        {t.jira_project ? ` · Jira ${t.jira_project}` : ""}
                        {t.capacity_days != null
                          ? ` · ${t.capacity_days} d free`
                          : ""}
                      </span>
                    </span>
                  </span>
                  <span className="pd-chips">
                    {t.owns.map((o) => (
                      <span key={o.project_id + o.glob} className="chip mono">
                        {o.project_id}
                        {o.glob !== "**" ? `: ${o.glob}` : ""}
                      </span>
                    ))}
                  </span>
                </button>
              ))}
            </div>
          )
        }
      </Async>
      <Async r={repos} what="Loading the repos">
        {(list) => {
          const loose = list.filter((r) => r.teams.length === 0);
          return loose.length === 0 ? null : (
            <Panel title="Repos without a team">
              <p className="sub">
                keel cannot say who does the work in these:{" "}
                {loose.map((r) => r.name).join(", ")}.
              </p>
            </Panel>
          );
        }}
      </Async>
      <div className="pd-cards rise r2">
        <KeelJira conns={conns} />
        <InitiativesInJira repos={repos.data ?? []} conns={conns} />
      </div>
      {editing && (
        <TeamForm
          team={editing === "new" ? null : editing}
          repos={repos.data ?? []}
          onClose={() => setEditing(null)}
          onSaved={(t) => {
            setEditing(null);
            void teams.reload();
            goTeam(t.id);
          }}
        />
      )}
    </div>
  );
}

function TeamForm({
  team,
  repos,
  onClose,
  onSaved,
}: {
  team: Team | null;
  repos: RepoOwner[];
  onClose: () => void;
  onSaved: (t: Team) => void;
}) {
  const [name, setName] = useState(team?.name ?? "");
  const [lead, setLead] = useState(team?.lead ?? "");
  const [jira, setJira] = useState(team?.jira_project ?? "");
  const [capacity, setCapacity] = useState(
    team?.capacity_days != null ? String(team.capacity_days) : "",
  );
  const [members, setMembers] = useState((team?.members ?? []).join(", "));
  const [owns, setOwns] = useState<{ project_id: string; glob: string }[]>(
    team?.owns.map((o) => ({ project_id: o.project_id, glob: o.glob })) ?? [],
  );
  const act = useAct();
  const save = async () => {
    const body: TeamBody = {
      name,
      lead: lead || null,
      jira_project: jira || null,
      capacity_days: capacity.trim() ? Number(capacity) : null,
      members: members
        .split(",")
        .map((m) => m.trim())
        .filter(Boolean),
      owns,
    };
    const t = await act.run("save", () =>
      team ? productApi.updateTeam(team.id, body) : productApi.createTeam(body),
    );
    if (t) onSaved(t);
  };
  const toggle = (pid: string, on: boolean) =>
    setOwns((o) =>
      on
        ? [...o, { project_id: pid, glob: "**" }]
        : o.filter((x) => x.project_id !== pid),
    );
  const footer = (
    <>
      <button className="btn" type="button" onClick={onClose}>
        Cancel
      </button>
      <button
        className="btn primary"
        type="button"
        disabled={!name.trim() || act.busy !== null}
        onClick={() => void save()}
      >
        Save the team
      </button>
    </>
  );
  return (
    <Drawer
      title={team ? `Edit ${team.name}` : "New team"}
      onClose={onClose}
      footer={footer}
      className="pdx"
    >
      <div className="pd-form">
        <div className="field">
          <label htmlFor="tf-name">Name</label>
          <input
            id="tf-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="tf-lead">Lead</label>
          <input
            id="tf-lead"
            type="text"
            value={lead}
            onChange={(e) => setLead(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="tf-jira">Jira project key</label>
          <input
            id="tf-jira"
            type="text"
            value={jira}
            onChange={(e) => setJira(e.target.value)}
            placeholder="PAY"
          />
        </div>
        <div className="field">
          <label htmlFor="tf-cap">
            Free developer days in the next 2 sprints
          </label>
          <input
            id="tf-cap"
            type="text"
            inputMode="decimal"
            value={capacity}
            onChange={(e) => setCapacity(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="tf-members">Members (comma separated)</label>
          <input
            id="tf-members"
            type="text"
            value={members}
            onChange={(e) => setMembers(e.target.value)}
          />
        </div>
        <fieldset className="field">
          <legend className="lab">Owns these repos</legend>
          {repos.length === 0 ? (
            <p className="sub">No project yet.</p>
          ) : (
            repos.map((r) => {
              const o = owns.find((x) => x.project_id === r.project_id);
              return (
                <div key={r.project_id} className="pd-row">
                  <label className="chk">
                    <input
                      type="checkbox"
                      checked={!!o}
                      onChange={(e) => toggle(r.project_id, e.target.checked)}
                    />{" "}
                    {r.name}
                  </label>
                  {o && (
                    <input
                      type="text"
                      aria-label={`Paths in ${r.name}`}
                      value={o.glob}
                      onChange={(e) =>
                        setOwns((all) =>
                          all.map((x) =>
                            x.project_id === r.project_id
                              ? { ...x, glob: e.target.value }
                              : x,
                          ),
                        )
                      }
                    />
                  )}
                </div>
              );
            })
          )}
        </fieldset>
        {act.box}
      </div>
    </Drawer>
  );
}

function TeamPage({ id }: { id: string }) {
  const k = useLoad(`team:${id}`, () => productApi.team(id));
  const repos = useLoad("team-repos", productApi.repos, { live: false });
  const conns = useConnections();
  const [editing, setEditing] = useState(false);
  const [asking, setAsking] = useState(false);
  const [tab, setTab] = useState<"pages" | "setup" | "tickets">("pages");
  const act = useAct();
  const remove = async () => {
    if (await act.run("delete", () => productApi.deleteTeam(id))) goTeam();
  };
  usePageKeys({ e: k.data ? () => setEditing(true) : null });
  return (
    <div className="pd pdx">
      <Async r={k} what={`Loading the team ${id}`}>
        {(t) => (
          <>
            <ProductHead
              crumbs={[["Teams", "#/teams"], [t.team.name]]}
              title={
                <>
                  <Avatar name={t.team.name} />
                  {t.team.name}
                  {t.team.source === "codeowners" && (
                    <Pill tone="idle">CODEOWNERS</Pill>
                  )}
                </>
              }
              sub={
                `${t.team.lead ? `Lead ${t.team.lead}. ` : ""}${t.team.members.length ? `Members: ${t.team.members.join(", ")}. ` : ""}` +
                `Owns ${t.team.owns.map((o) => o.project_id + (o.glob !== "**" ? ` (${o.glob})` : "")).join(", ") || "nothing yet"}.`
              }
              actions={
                <>
                  <button
                    className="btn"
                    type="button"
                    onClick={() => setEditing(true)}
                  >
                    Edit <Key k="E" />
                  </button>
                  <button
                    className="btn danger"
                    type="button"
                    disabled={act.busy !== null}
                    onClick={() => setAsking(true)}
                  >
                    Delete
                  </button>
                </>
              }
            />
            {asking && (
              <Confirm text={`Delete the team ${t.team.name}? Its pages stay in the product repo's history.`} yes="Delete the team"
                busy={act.busy !== null} onYes={() => void remove()} onNo={() => setAsking(false)} />
            )}
            {act.box}
            <Tabs
              label="Team"
              value={tab}
              onChange={setTab}
              options={[
                ["pages", "Pages"],
                ["setup", "Projects and Jira"],
                ["tickets", "Tickets"],
              ]}
            />
            <div role="tabpanel">
              {tab === "pages" && (
                <Knowledge k={t} onChanged={(n) => k.setData(n)} />
              )}
              {tab === "setup" && (
                <div className="pd-cards">
                  <TeamProjects
                    team={t.team}
                    repos={repos.data ?? []}
                    onChanged={(team) => k.setData({ ...t, team })}
                  />
                  <TeamJiraForm
                    team={t.team}
                    repos={repos.data ?? []}
                    conns={conns}
                    onChanged={(team) => k.setData({ ...t, team })}
                  />
                  <KeyPattern
                    team={t.team}
                    onChanged={(team) => k.setData({ ...t, team })}
                  />
                </div>
              )}
              {tab === "tickets" && <TeamTickets id={t.team.id} />}
            </div>
            {editing && (
              <TeamForm
                team={t.team}
                repos={repos.data ?? []}
                onClose={() => setEditing(false)}
                onSaved={() => {
                  setEditing(false);
                  void k.reload();
                }}
              />
            )}
          </>
        )}
      </Async>
    </div>
  );
}

function Knowledge({
  k,
  onChanged,
}: {
  k: TeamKnowledge;
  onChanged: (k: TeamKnowledge) => void;
}) {
  const open = k.suggestions.filter((s) => s.status === "open");
  const act = useAct();
  const decide = async (sid: string, accept: boolean) => {
    const n = await act.run(sid, () =>
      productApi.decideSuggestion(k.team.id, sid, accept),
    );
    if (n) onChanged(n);
  };
  return (
    <div className="pd-main">
      <div className="pd-left">
        <p className="sub">
          keel reads these pages in every stage that touches this team. Only the
          team writes them; keel may suggest.
        </p>
        {k.pages.map((p) => (
          <PageEditor
            key={p.name}
            teamId={k.team.id}
            name={p.name}
            title={p.title}
            text={p.text}
          />
        ))}
      </div>
      <aside className="pd-side">
        <Panel title={`keel's suggestions (${open.length})`}>
          {open.length === 0 ? (
            <p className="sub">
              None open. After an outcome keel suggests a lesson here.
            </p>
          ) : (
            <ul className="pd-qs">
              {open.map((s) => (
                <li key={s.id} className="pd-q">
                  <div className="sub">
                    for “
                    {k.pages.find((p) => p.name === s.page)?.title ?? s.page}”
                    {s.source ? ` · from ${s.source}` : ""}
                  </div>
                  <Markdown text={s.text} fold={8} />
                  <div className="pd-actions">
                    <button
                      className="btn sm primary"
                      type="button"
                      onClick={() => void decide(s.id, true)}
                    >
                      Add to the page
                    </button>
                    <button
                      className="btn sm"
                      type="button"
                      onClick={() => void decide(s.id, false)}
                    >
                      No
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {act.box}
        </Panel>
      </aside>
    </div>
  );
}

function PageEditor({
  teamId,
  name,
  title,
  text,
}: {
  teamId: string;
  name: string;
  title: string;
  text: string;
}) {
  const [value, setValue] = useState(text);
  const [saved, setSaved] = useState(text);
  // the page changed on the server (a suggestion was added): show it, unless this person has unsaved words in it
  const [seen, setSeen] = useState(text);
  if (text !== seen) {
    setSeen(text);
    if (value === saved) setValue(text);
    setSaved(text);
  }
  const act = useAct();
  const save = async () => {
    const r = await act.run("save", () =>
      productApi.savePage(teamId, name, value),
    );
    if (r) setSaved(r.text);
  };
  return (
    <Panel
      title={title}
      extra={value !== saved ? <Pill tone="warn">not saved</Pill> : undefined}
    >
      <textarea
        className="pd-text pd-page"
        aria-label={title}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={`${title}: write it as the team works today.`}
      />
      <div className="pd-actions">
        <button
          className="btn sm"
          type="button"
          disabled={value === saved || act.busy !== null}
          onClick={() => void save()}
        >
          Save
        </button>
      </div>
      {act.box}
    </Panel>
  );
}

// ---------------------------------------------------------------- a team's projects, Jira and ticket keys

/** The keel projects in the team: a whole project (all files), or the CODEOWNERS paths it has in one. */
function TeamProjects({
  team,
  repos,
  onChanged,
}: {
  team: Team;
  repos: RepoOwner[];
  onChanged: (t: Team) => void;
}) {
  const act = useAct();
  const [pick, setPick] = useState("");
  const ids = [...new Set(team.owns.map((o) => o.project_id))];
  const name = (pid: string) =>
    repos.find((r) => r.project_id === pid)?.name ?? pid;
  const free = repos.filter((r) => !ids.includes(r.project_id));
  const add = async () => {
    const t = await act.run("add", () => productApi.addProject(team.id, pick));
    if (t) {
      setPick("");
      onChanged(t);
    }
  };
  const remove = async (pid: string) => {
    const t = await act.run(pid, () => productApi.removeProject(team.id, pid));
    if (t) onChanged(t);
  };
  return (
    <Panel title="Projects">
      {ids.length === 0 ? (
        <p className="sub">No keel project yet.</p>
      ) : (
        <ul className="pd-list">
          {ids.map((pid) => {
            const globs = team.owns
              .filter((o) => o.project_id === pid && o.glob !== "**")
              .map((o) => o.glob);
            return (
              <li key={pid}>
                <span>
                  <b>{name(pid)}</b>{" "}
                  <span className="sub">
                    {globs.length ? `only ${globs.join(", ")}` : "all files"}
                  </span>
                </span>
                <button
                  className="btn sm"
                  type="button"
                  aria-label={`Remove ${name(pid)}`}
                  disabled={act.busy !== null}
                  onClick={() => void remove(pid)}
                >
                  Remove
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="pd-row">
        <select
          aria-label="Add a project"
          value={pick}
          onChange={(e) => setPick(e.target.value)}
        >
          <option value="">Add a project…</option>
          {free.map((r) => (
            <option key={r.project_id} value={r.project_id}>
              {r.name}
            </option>
          ))}
        </select>
        <button
          className="btn sm"
          type="button"
          disabled={!pick || act.busy !== null}
          onClick={() => void add()}
        >
          Add
        </button>
      </div>
      {act.box}
    </Panel>
  );
}

const ISSUE_TYPES = [
  ["initiative_type", "Initiative type", "Initiative"],
  ["epic_type", "Epic type", "Epic"],
  ["story_type", "Story type", "Story"],
  ["subtask_type", "Sub-task type", "Sub-task"],
] as const;
type IssueTypeKey = (typeof ISSUE_TYPES)[number][0];

/** Where the team's tickets go: a Jira connection, a project, the issue types, labels and a component. */
function TeamJiraForm({
  team,
  repos,
  conns,
  onChanged,
}: {
  team: Team;
  repos: RepoOwner[];
  conns: JiraConnections | null;
  onChanged: (t: Team) => void;
}) {
  const j = team.jira;
  const mine = team.owns.map((o) => o.project_id);
  const [connection, setConnection] = useState(() => startConnection(j?.connection, conns, mine));
  const chose = useStartConnection(j?.connection, conns, mine, setConnection);
  const [project, setProject] = useState(team.jira_project ?? "");
  const [types, setTypes] = useState<Record<IssueTypeKey, string>>({
    initiative_type: j?.initiative_type ?? "",
    epic_type: j?.epic_type ?? "",
    story_type: j?.story_type ?? "",
    subtask_type: j?.subtask_type ?? "",
  });
  const [epicLink, setEpicLink] = useState(j?.epic_link_field ?? "");
  const [labels, setLabels] = useState((j?.labels ?? []).join(", "));
  const [component, setComponent] = useState(j?.component ?? "");
  const [saved, setSaved] = useState(false);
  const act = useAct();
  const id = (f: string) => `tj-${team.id}-${f}`;
  const save = async () => {
    setSaved(false);
    const t = await act.run("save", () =>
      productApi.updateTeam(team.id, {
        jira_project: project.trim(),
        jira: {
          connection: connection || null,
          initiative_type: types.initiative_type.trim() || null,
          epic_type: types.epic_type.trim() || null,
          story_type: types.story_type.trim() || null,
          subtask_type: types.subtask_type.trim() || null,
          epic_link_field: epicLink.trim() || null,
          labels: lines(labels, /[,\s]+/),
          component: component.trim() || null,
        },
      }),
    );
    if (t) {
      setSaved(true);
      onChanged(t);
    }
  };
  return (
    <Panel title="Jira">
      <div className="pd-form">
        <div className="field">
          <label htmlFor={id("conn")}>Jira connection</label>
          <ConnectionSelect id={id("conn")} value={connection} conns={conns} repos={repos}
            onChange={(v) => { chose(); setConnection(v); }} />
          <ConnectionNote value={connection} saved={j?.connection ?? ""} conns={conns} />
        </div>
        <div className="field">
          <label htmlFor={id("project")}>Jira project key</label>
          <input
            id={id("project")}
            type="text"
            value={project}
            onChange={(e) => setProject(e.target.value)}
            placeholder="SHOPS"
          />
        </div>
        <div className="pd-row">
          {ISSUE_TYPES.map(([k, label, ph]) => (
            <div key={k} className="field">
              <label htmlFor={id(k)}>{label}</label>
              <input
                id={id(k)}
                type="text"
                value={types[k]}
                onChange={(e) =>
                  setTypes((t) => ({ ...t, [k]: e.target.value }))
                }
                placeholder={`${ph} (found in Jira)`}
              />
            </div>
          ))}
        </div>
        <p className="sub">
          A blank issue type is found in the Jira project.
        </p>
        <div className="field">
          <label htmlFor={id("epiclink")}>Epic Link field (Jira Server)</label>
          <input
            id={id("epiclink")}
            type="text"
            value={epicLink}
            onChange={(e) => setEpicLink(e.target.value)}
            placeholder="customfield_10014 (blank: found by name)"
          />
        </div>
        <div className="field">
          <label htmlFor={id("labels")}>Labels</label>
          <input
            id={id("labels")}
            type="text"
            value={labels}
            onChange={(e) => setLabels(e.target.value)}
            placeholder="erp, euro (one word each)"
          />
        </div>
        <div className="field">
          <label htmlFor={id("component")}>Component</label>
          <input
            id={id("component")}
            type="text"
            value={component}
            onChange={(e) => setComponent(e.target.value)}
          />
        </div>
        <div className="pd-actions">
          <button
            className="btn primary sm"
            type="button"
            disabled={act.busy !== null}
            onClick={() => void save()}
          >
            Save the Jira settings
          </button>
          {saved && <span className="sub">Saved.</span>}
        </div>
        {act.box}
      </div>
    </Panel>
  );
}

/** The pattern keel finds the team's ticket keys with, in branch names and commit messages, and a box to try it. */
function KeyPattern({
  team,
  onChanged,
}: {
  team: Team;
  onChanged: (t: Team) => void;
}) {
  const [pattern, setPattern] = useState(team.key_pattern ?? "");
  const [text, setText] = useState("");
  const [found, setFound] = useState<KeyTest | null>(null);
  const [saved, setSaved] = useState(false);
  const act = useAct();
  const id = (f: string) => `tk-${team.id}-${f}`;
  const tryIt = async () => {
    setFound(null);
    const r = await act.run("try", () =>
      productApi.testKeyPattern(pattern.trim() || team.keys || null, text),
    );
    if (r) setFound(r);
  };
  const save = async () => {
    setSaved(false);
    const t = await act.run("save", () =>
      productApi.updateTeam(team.id, { key_pattern: pattern.trim() }),
    );
    if (t) {
      setSaved(true);
      onChanged(t);
    }
  };
  return (
    <Panel title="Ticket keys">
      <div className="pd-form">
        <div className="field">
          <label htmlFor={id("pattern")}>Ticket-key pattern</label>
          <input
            id={id("pattern")}
            type="text"
            className="mono"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            placeholder={team.keys ?? "PAY-\\d+"}
          />
          <span className="sub">
            Found in branch names and commit messages, without case and on word
            edges: feature/pay-12-x gives PAY-12. Blank ={" "}
            {team.keys ? (
              <span className="mono">{team.keys}</span>
            ) : (
              "the Jira project key and a number"
            )}
            .
          </span>
        </div>
        <div className="pd-actions">
          <button
            className="btn sm"
            type="button"
            disabled={act.busy !== null}
            onClick={() => void save()}
          >
            Save the pattern
          </button>
          {saved && <span className="sub">Saved.</span>}
        </div>
        <div className="pd-row">
          <input
            className="pd-grow"
            type="text"
            aria-label="A branch name or commit message"
            placeholder="feature/pay-12-euro"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button
            className="btn sm"
            type="button"
            disabled={!text.trim() || act.busy !== null}
            onClick={() => void tryIt()}
          >
            Try it
          </button>
        </div>
        {found && (
          <p className="sub" aria-label="Keys found">
            {found.keys.length
              ? `Found ${found.keys.join(", ")}`
              : "No ticket key in this text."}
          </p>
        )}
        {act.box}
      </div>
    </Panel>
  );
}

/** The ticket keys keel found in the team's git, and the branches and commits with no key. */
function TeamTickets({ id }: { id: string }) {
  const r = useLoad(`team-tickets:${id}`, () => productApi.tickets(id), {
    live: false,
  });
  const act = useAct();
  const scan = async () => {
    if (await act.run("scan", () => productApi.scanTickets())) void r.reload();
  };
  const what = (l: TicketLink) =>
    l.kind === "branch" ? l.ref : `${l.ref.slice(0, 8)} ${l.title ?? ""}`;
  return (
    <div className="pd-doc">
      <div className="pd-doc-bar">
        <button
          className="btn sm"
          type="button"
          disabled={act.busy !== null}
          onClick={() => void scan()}
        >
          {act.busy === "scan" ? "Scanning…" : "Scan now"}
        </button>
        <span className="sub">
          keel reads the branches and the commits of the last 90 days of the
          team's projects, every 15 minutes too.
        </span>
      </div>
      {act.box}
      <Async r={r} what="Loading the tickets">
        {(t) => (
          <>
            {t.scans.length === 0 ? (
              <p className="sub">Not scanned yet.</p>
            ) : (
              <ul className="pd-notes" aria-label="Scans">
                {t.scans.map((s) => (
                  <li key={s.project_id}>
                    {s.project_id}: {s.branches} branches, {s.commits} commits,{" "}
                    {s.linked} with a key · {fmtDate(s.at)}
                    {s.error ? ` · ${s.error}` : ""}
                  </li>
                ))}
              </ul>
            )}
            {t.tickets.length === 0 ? (
              <Empty>No ticket key found yet.</Empty>
            ) : (
              <div className="pd-table-wrap">
                <table className="pd-table" aria-label="Tickets">
                  <thead>
                    <tr>
                      <th>Ticket</th>
                      <th>Branches</th>
                      <th>Commits</th>
                      <th>Last</th>
                    </tr>
                  </thead>
                  <tbody>
                    {t.tickets.map((w) => (
                      <tr key={w.key}>
                        <td className="mono">{w.key}</td>
                        <td>
                          {w.branches.length === 0
                            ? "—"
                            : w.branches.map((b) => (
                                <div key={b.project_id + b.ref} className="mono">
                                  {b.ref}
                                </div>
                              ))}
                        </td>
                        <td>{w.commits.length}</td>
                        <td>{fmtDate(w.last_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <section aria-label="No ticket key">
              <h3 className="pd-h3">No ticket key ({t.unlinked.length})</h3>
              {t.unlinked.length === 0 ? (
                <p className="sub">Every branch and commit has a key.</p>
              ) : (
                <>
                  <p className="sub">
                    These have no key of the team's pattern: the pattern or the
                    habit may be wrong.
                  </p>
                  <ul className="pd-notes">
                    {t.unlinked.slice(0, 50).map((l) => (
                      <li key={l.project_id + l.kind + l.ref}>
                        {l.kind} <span className="mono">{what(l)}</span>{" "}
                        <span className="sub">in {l.project_id}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </section>
          </>
        )}
      </Async>
    </div>
  );
}

// ---------------------------------------------------------------- Jira for all teams

/** The Jira plugin's keel-wide connection: one site and token for keel Product's tickets. */
function KeelJira({ conns }: { conns: JiraConnections | null }) {
  const r = useLoad("keel-jira", productApi.keelJira, { live: false });
  const [anyway, setAnyway] = useState(false);
  const own = conns?.projects.filter((c) => c.connected) ?? [];
  return (
    <Panel title="Jira for every project">
      <p className="sub">
        One Jira site and token for the teams' tickets. A team can use a
        project's own connection instead.
      </p>
      <Async r={r} what="Loading the Jira connection">
        {(v) => !v.connected && !v.token_set && own.length > 0 && !anyway ? (
          // B5: a project's own connection works already: no second token is needed
          <div className="pd-form" data-testid="keel-jira-reuse">
            <p className="sub">
              Not needed now: {own.map((c) => `${c.name} (${c.base_url ?? "Jira"})`).join(", ")} {own.length === 1 ? "has" : "have"} a Jira
              connection, and the teams and initiatives use it. Set a keel-wide one only for teams whose projects have none.
            </p>
            <div className="pd-actions">
              <button className="btn sm" type="button" onClick={() => setAnyway(true)}>Set up a keel-wide connection</button>
            </div>
          </div>
        ) : (
          <KeelJiraForm
            key={`${v.connected}:${v.settings.base_url}`}
            v={v}
            onSaved={(n) => r.setData(n)}
            onRemoved={() => void r.reload()}
          />
        )}
      </Async>
    </Panel>
  );
}

function KeelJiraForm({
  v,
  onSaved,
  onRemoved,
}: {
  v: JiraView;
  onSaved: (v: JiraView) => void;
  onRemoved: () => void;
}) {
  const s = v.settings;
  const [kind, setKind] = useState<JiraKind>(s.kind ?? "cloud");
  const [url, setUrl] = useState(s.base_url ?? "");
  const [email, setEmail] = useState(s.email ?? "");
  const [token, setToken] = useState("");
  const [key, setKey] = useState(s.project_key ?? "");
  const [test, setTest] = useState<JiraTest | null>(null);
  const [removing, setRemoving] = useState(false);
  const act = useAct();
  const body = (): JiraSave => ({
    kind,
    base_url: url.trim(),
    email: kind === "cloud" ? email.trim() : "",
    project_key: key.trim(),
    ...(token.trim() ? { token: token.trim() } : {}),
  });
  const runTest = async () => {
    setTest(null);
    const t = await act.run("test", () => productApi.testKeelJira(body()));
    if (t) setTest(t);
  };
  const save = async () => {
    const n = await act.run("save", () => productApi.saveKeelJira(body()));
    if (n) {
      setToken("");
      onSaved(n);
    }
  };
  const remove = async () => {
    if (await act.run("remove", () => productApi.removeKeelJira())) {
      setRemoving(false);
      onRemoved();
    }
  };
  return (
    <div className="pd-form">
      <p className="sub">
        {v.connected
          ? `Connected: ${s.kind === "server" ? "Jira Server" : "Jira Cloud"} · ${s.base_url}${v.me ? ` as ${v.me.name}` : ""}`
          : "Not connected."}
      </p>
      <div className="field">
        <label htmlFor="kj-kind">Kind</label>
        <select
          id="kj-kind"
          value={kind}
          onChange={(e) => setKind(e.target.value as JiraKind)}
        >
          <option value="cloud">Jira Cloud</option>
          <option value="server">Server / Data Center</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor="kj-url">{kind === "cloud" ? "Site URL" : "Jira URL"}</label>
        <input
          id="kj-url"
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder={
            kind === "cloud"
              ? "https://your-site.atlassian.net"
              : "https://jira.example.com"
          }
        />
      </div>
      {kind === "cloud" && (
        <div className="field">
          <label htmlFor="kj-email">Email</label>
          <input
            id="kj-email"
            type="text"
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
      )}
      <div className="field">
        <label htmlFor="kj-token">
          {kind === "cloud" ? "API token" : "Personal access token"}
        </label>
        <input
          id="kj-token"
          type="password"
          autoComplete="off"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder={
            v.token_set
              ? `${v.token_hint ?? "••••"} (saved)`
              : "paste the token"
          }
        />
        <span className="sub">Stored encrypted; never shown again.</span>
      </div>
      <div className="field">
        <label htmlFor="kj-key">Project key</label>
        <input
          id="kj-key"
          type="text"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="for teams with no key of their own"
        />
      </div>
      {test && (
        <p className={test.ok ? "sub" : "sub bad"}>
          {test.ok
            ? `Test OK: logged in as ${test.user?.name ?? "?"}.`
            : `Test failed: ${test.error ?? "no answer"}${test.hint ? ` ${test.hint}` : ""}`}
        </p>
      )}
      <div className="pd-actions">
        <button
          className="btn sm"
          type="button"
          disabled={!url.trim() || act.busy !== null}
          onClick={() => void runTest()}
        >
          Test
        </button>
        <button
          className="btn sm primary"
          type="button"
          disabled={!url.trim() || act.busy !== null}
          onClick={() => void save()}
        >
          Save
        </button>
        {(v.connected || v.token_set) && (
          <button
            className="btn sm danger"
            type="button"
            disabled={act.busy !== null}
            onClick={() => setRemoving(true)}
          >
            Remove
          </button>
        )}
      </div>
      {removing && (
        <Confirm
          text="Remove the keel-wide Jira connection and its token? Projects keep their own."
          yes="Remove"
          busy={act.busy !== null}
          onYes={() => void remove()}
          onNo={() => setRemoving(false)}
        />
      )}
      {act.box}
    </div>
  );
}

/** Where an initiative's own ticket goes, above its epics; no project = none. */
function InitiativesInJira({ repos, conns }: { repos: RepoOwner[]; conns: JiraConnections | null }) {
  const r = useLoad("initiative-jira", productApi.initiativeJira, {
    live: false,
  });
  return (
    <Panel title="Initiatives in Jira">
      <p className="sub">
        An initiative can be a ticket of its own, above its epics. Leave the
        project blank for none: the initiative id goes on every ticket as a
        label.
      </p>
      <Async r={r} what="Loading the initiative settings">
        {(v) => (
          <InitiativeJiraForm
            key={`${v.connection ?? ""}:${v.project ?? ""}:${v.type ?? ""}`}
            v={v}
            repos={repos}
            conns={conns}
            onSaved={(n) => r.setData(n)}
          />
        )}
      </Async>
    </Panel>
  );
}

function InitiativeJiraForm({
  v,
  repos,
  conns,
  onSaved,
}: {
  v: InitiativeJira;
  repos: RepoOwner[];
  conns: JiraConnections | null;
  onSaved: (v: InitiativeJira) => void;
}) {
  const [connection, setConnection] = useState(() => startConnection(v.connection, conns));
  const chose = useStartConnection(v.connection, conns, [], setConnection);
  const [project, setProject] = useState(v.project ?? "");
  const [type, setType] = useState(v.type ?? "");
  const [saved, setSaved] = useState(false);
  const act = useAct();
  const save = async () => {
    setSaved(false);
    const n = await act.run("save", () =>
      productApi.saveInitiativeJira({
        connection: connection || null,
        project: project.trim() || null,
        type: type.trim() || null,
      }),
    );
    if (n) {
      setSaved(true);
      onSaved(n);
    }
  };
  return (
    <div className="pd-form">
      <div className="field">
        <label htmlFor="ij-conn">Connection</label>
        <ConnectionSelect id="ij-conn" value={connection} conns={conns} repos={repos}
          onChange={(x) => { chose(); setConnection(x); }} />
        <ConnectionNote value={connection} saved={v.connection ?? ""} conns={conns} />
      </div>
      <div className="field">
        <label htmlFor="ij-project">Initiative project key</label>
        <input
          id="ij-project"
          type="text"
          value={project}
          onChange={(e) => setProject(e.target.value)}
          placeholder="PORT (blank: no initiative ticket)"
        />
      </div>
      <div className="field">
        <label htmlFor="ij-type">Issue type</label>
        <input
          id="ij-type"
          type="text"
          value={type}
          onChange={(e) => setType(e.target.value)}
          placeholder="Initiative (found in Jira)"
        />
      </div>
      <div className="pd-actions">
        <button
          className="btn sm primary"
          type="button"
          disabled={act.busy !== null}
          onClick={() => void save()}
        >
          Save
        </button>
        {saved && (
          <span className="sub">
            {v.project ? `Saved: initiatives go to ${v.project}.` : "Saved: no initiative ticket."}
          </span>
        )}
      </div>
      {act.box}
    </div>
  );
}
