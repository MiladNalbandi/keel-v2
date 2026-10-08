// keel Product › Teams: who owns which repo (by hand or from CODEOWNERS), their capacity and Jira project, and the
// team's own knowledge pages (keel reads them in every stage; keel suggests, the team accepts).

import { useState } from "react";
import { Markdown } from "../../web/src/components/Markdown";
import { Async, Confirm, Drawer, Empty, Panel, Pill } from "../../web/src/components/ui";
import type { AddonPageProps } from "../../web/src/addons";
import { useLoad } from "../../web/src/state";
import {
  productApi,
  type RepoOwner,
  type Team,
  type TeamBody,
  type TeamKnowledge,
} from "./productApi";
import { goTeam, ProductHead, useAct } from "./shared";

export default function Teams({ arg }: AddonPageProps) {
  if (arg) return <TeamPage key={arg} id={arg} />;
  return <TeamList />;
}

function TeamList() {
  const teams = useLoad("teams", productApi.teams);
  const repos = useLoad("team-repos", productApi.repos);
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
  return (
    <div className="pd">
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
              New team
            </button>
          </>
        }
      />
      {act.box}
      {imported && (
        <p className="hint" role="status">
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
            <div className="pd-teams">
              {list.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className="pd-card"
                  onClick={() => goTeam(t.id)}
                  aria-label={`Team ${t.name}`}
                >
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
                  <span className="pd-chips">
                    {t.owns.map((o) => (
                      <span key={o.project_id + o.glob} className="chip">
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
  const [editing, setEditing] = useState(false);
  const [asking, setAsking] = useState(false);
  const act = useAct();
  const remove = async () => {
    if (await act.run("delete", () => productApi.deleteTeam(id))) goTeam();
  };
  return (
    <div className="pd">
      <Async r={k} what={`Loading the team ${id}`}>
        {(t) => (
          <>
            <ProductHead
              crumbs={[["Teams", "#/teams"], [t.team.name]]}
              title={t.team.name}
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
                    Edit
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
            <Knowledge k={t} onChanged={(n) => k.setData(n)} />
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
        className="pd-text"
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
