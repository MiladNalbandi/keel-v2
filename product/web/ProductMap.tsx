// keel Product › Product map: for a product manager, three columns filtered by team. The teams (their projects, Jira
// key and ticket-key pattern), the business flows keel found in their repos (steps with file:line, endpoints, tables,
// the tickets that changed them), and the tickets and work keel found in git. "Find flows again" runs the flow
// analysts (read only); "+ New tickets" on a flow starts an initiative with that flow's repo.

import { useEffect, useState } from "react";
import { Async, Empty, Pill, useLoad } from "@keel/web-sdk";
import { NewInitiative } from "./Initiatives";
import {
  productApi,
  type FlowRun,
  type FlowView,
  type LinkedTicket,
  type MapTeam,
  type ProductMap as PMap,
  type TeamTickets,
} from "./productApi";
import {
  Avatar,
  fmtDate,
  Key,
  ProductHead,
  useAct,
  usePageKeys,
} from "./shared";

const RUN_TONE = {
  running: "run",
  done: "ok",
  failed: "bad",
  stopped: "idle",
} as const;

export default function ProductMap() {
  const [team, setTeam] = useState("");
  const r = useLoad(`product-map:${team}`, () =>
    productApi.map(team || undefined),
  );
  const act = useAct();
  const [newFor, setNewFor] = useState<FlowView | null>(null);
  const run = r.data?.runs[0];
  const running = run?.status === "running";
  const reload = r.reload;
  // while the analysts run, look again every few seconds
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => {
      if (!document.hidden) void reload(); // not while the tab is in the background
    }, 4000);
    return () => window.clearInterval(t);
  }, [running, reload]);
  const findFlows = async () => {
    if (await act.run("find", () => productApi.findFlows([]))) void r.reload();
  };
  const teams = r.data?.teams ?? [];
  const [adding, setAdding] = useState(false);
  usePageKeys({
    f: act.busy === null && !running ? () => void findFlows() : null,
    n: () => setAdding(true),
  });
  return (
    <div className="pd pdx">
      <ProductHead
        crumbs={[["Product map"]]}
        title="Product map"
        sub="The teams, the business flows in their repos, and the tickets and work keel found in git."
        actions={
          <>
            <button
              className="btn"
              type="button"
              disabled={act.busy !== null || running}
              onClick={() => void findFlows()}
            >
              Find flows again <Key k="F" />
            </button>
            <button
              className="btn primary"
              type="button"
              onClick={() => setAdding(true)}
            >
              New initiative <Key k="N" />
            </button>
          </>
        }
      />
      <div className="pd-bar rise r1">
        {teams.length > 0 && (
          <div className="pd-fchips" role="group" aria-label="Team">
            {[{ id: "", name: "All teams" }, ...teams].map((t) => (
              <button
                key={t.id || "all"}
                type="button"
                className="pd-fchip"
                aria-pressed={team === t.id}
                onClick={() => setTeam(t.id)}
              >
                {t.name}
              </button>
            ))}
          </div>
        )}
        <span className="grow" />
        {run && <RunLine run={run} />}
      </div>
      {running && <div className="pd-shim" aria-hidden="true" />}
      {act.box}
      <Async r={r} what="Loading the Product map">
        {(m) => (
          <>
            {m.not_analysed.length > 0 && (
              <div className="pd-note n-warn">
                <span>No flows yet for: {m.not_analysed.join(", ")}.</span>
                <span className="sub">
                  Find flows again: one flow analyst per repo reads it, read
                  only.
                </span>
              </div>
            )}
            <div className="pd-map">
              <MapTeams
                teams={team ? m.teams.filter((t) => t.id === team) : m.teams}
                onPick={setTeam}
              />
              <Flows
                m={m}
                onChanged={(f) =>
                  r.setData((old) =>
                    old
                      ? {
                          ...old,
                          flows: old.flows.map((x) =>
                            x.project_id === f.project_id && x.id === f.id
                              ? f
                              : x,
                          ),
                        }
                      : old,
                  )
                }
                onNew={setNewFor}
              />
              <Work t={m.tickets} board={m.board ?? []} />
            </div>
          </>
        )}
      </Async>
      {newFor && (
        <NewInitiative
          onClose={() => setNewFor(null)}
          preset={[newFor.project_id]}
          idea={`About the flow "${newFor.name}": `}
        />
      )}
      {adding && <NewInitiative onClose={() => setAdding(false)} />}
    </div>
  );
}

function RunLine({ run }: { run: FlowRun }) {
  const n = run.projects.length;
  const text =
    run.status === "running"
      ? `keel finds the flows of ${n} repo${n === 1 ? "" : "s"}…`
      : run.status === "failed"
        ? `Finding the flows failed${run.error ? `: ${run.error}` : "."}`
        : run.status === "stopped"
          ? "Finding the flows was stopped."
          : `Flows found ${fmtDate(run.ended_at)} in ${n} repo${n === 1 ? "" : "s"}.`;
  return (
    <div className="pd-runline" aria-label="Flow run" role="status">
      <Pill tone={RUN_TONE[run.status as keyof typeof RUN_TONE] ?? "idle"}>
        {run.status}
      </Pill>
      <span className="sub">{text}</span>
    </div>
  );
}

function MapTeams({
  teams,
  onPick,
}: {
  teams: MapTeam[];
  onPick: (id: string) => void;
}) {
  return (
    <section className="pd-map-col rise r2" aria-label="Teams">
      <h2>
        Teams <span className="pd-n">{teams.length}</span>
      </h2>
      {teams.length === 0 ? (
        <p className="sub">
          No team yet. <a href="#/teams">Add one</a>.
        </p>
      ) : (
        teams.map((t) => (
          <button
            key={t.id}
            type="button"
            className="pd-card"
            aria-label={`Team ${t.name}`}
            onClick={() => onPick(t.id)}
          >
            <span className="pd-who">
              <Avatar name={t.name} />
              <span>
                <b>{t.name}</b>
                <span className="sub">
                  {t.lead ? `lead ${t.lead}` : "no lead"}
                </span>
              </span>
            </span>
            <span className="pd-chips">
              {t.projects.map((p) => (
                <span key={p} className="chip">
                  {p}
                </span>
              ))}
            </span>
            <span className="sub">
              {t.jira_project ? `Jira ${t.jira_project}` : "no Jira project"}
              {t.keys && (
                <>
                  {" · keys "}
                  <span className="mono">{t.keys}</span>
                </>
              )}
            </span>
          </button>
        ))
      )}
      <span className="sub">
        Who owns which repo lives on <a href="#/teams">Teams ›</a>
      </span>
    </section>
  );
}

const count = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

function Flows({
  m,
  onChanged,
  onNew,
}: {
  m: PMap;
  onChanged: (f: FlowView) => void;
  onNew: (f: FlowView) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const hidden = m.flows.filter((f) => f.hidden).length;
  const flows = m.flows.filter((f) => showHidden || !f.hidden);
  return (
    <section className="pd-map-col rise r3" aria-label="Business flows">
      <h2>
        Business flows <span className="pd-n">{flows.length}</span>
      </h2>
      {m.flows.length === 0 ? (
        <Empty>No flow found yet. Find flows again to look in the repos.</Empty>
      ) : (
        flows.map((f) => {
          const key = `${f.project_id}/${f.id}`;
          const steps = f.steps ?? [];
          const more = [
            count(steps.length, "step"),
            f.endpoints?.length ? count(f.endpoints.length, "endpoint") : "",
            f.tables?.length ? count(f.tables.length, "table") : "",
          ]
            .filter(Boolean)
            .join(" · ");
          return (
            <div key={key} className={`pd-flow${f.hidden ? " is-hidden" : ""}`}>
              <button
                type="button"
                className="pd-card"
                aria-expanded={open === key}
                aria-label={`Flow ${f.name}`}
                onClick={() => setOpen(open === key ? null : key)}
              >
                <span className="pd-card-top">
                  <b>{f.name}</b>
                  <span className="mono">{f.project_id}</span>
                </span>
                <span className="sub">{more}</span>
                {f.tickets.length > 0 && (
                  <span className="pd-chips" aria-label="Tickets">
                    {f.tickets.map((k) => (
                      <span key={k} className="chip mono">
                        {k}
                      </span>
                    ))}
                  </span>
                )}
              </button>
              {open === key && (
                <FlowDetail
                  f={f}
                  onChanged={onChanged}
                  onNew={() => onNew(f)}
                />
              )}
            </div>
          );
        })
      )}
      {hidden > 0 && (
        <button
          className="btn sm ghost"
          type="button"
          onClick={() => setShowHidden((v) => !v)}
        >
          {showHidden ? "Hide the hidden flows" : `Show ${hidden} hidden`}
        </button>
      )}
    </section>
  );
}

function FlowDetail({
  f,
  onChanged,
  onNew,
}: {
  f: FlowView;
  onChanged: (f: FlowView) => void;
  onNew: () => void;
}) {
  const [name, setName] = useState(f.name);
  const act = useAct();
  const edit = async (b: { name?: string; hidden?: boolean }) => {
    const n = await act.run("edit", () =>
      productApi.editFlow(f.project_id, f.id, b),
    );
    if (n) onChanged(n);
  };
  return (
    <div className="pd-flow-detail pd-pop">
      {f.summary && <p>{f.summary}</p>}
      <ol className="pd-steps">
        {(f.steps ?? []).map((s, n) => (
          <li key={n}>
            {s.text}{" "}
            {s.cite ? (
              <span className="mono sub">{s.cite}</span>
            ) : (
              <span className="sub">no file:line</span>
            )}
          </li>
        ))}
      </ol>
      {!!f.endpoints?.length && (
        <p className="sub">
          Endpoints: <span className="mono">{f.endpoints.join(", ")}</span>
        </p>
      )}
      {!!f.tables?.length && (
        <p className="sub">
          Tables: <span className="mono">{f.tables.join(", ")}</span>
        </p>
      )}
      <div className="pd-row">
        <input
          type="text"
          aria-label={`Name of ${f.name}`}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button
          className="btn sm"
          type="button"
          disabled={!name.trim() || name === f.name || act.busy !== null}
          onClick={() => void edit({ name: name.trim() })}
        >
          Rename
        </button>
        <button
          className="btn sm ghost"
          type="button"
          disabled={act.busy !== null}
          onClick={() => void edit({ hidden: !f.hidden })}
        >
          {f.hidden ? "Show" : "Hide"}
        </button>
        <button className="btn sm primary" type="button" onClick={onNew}>
          + New tickets
        </button>
      </div>
      {act.box}
    </div>
  );
}

function Work({ t, board }: { t: TeamTickets; board: LinkedTicket[] }) {
  return (
    <section className="pd-map-col rise r4" aria-label="Tickets and work">
      <h2>Tickets and work</h2>
      {/* B6: the Jira tickets on the team projects' Tasks boards, with the initiative each belongs to */}
      <h3 className="pd-map-h">On the Tasks boards</h3>
      {board.length === 0 ? (
        <p className="sub">No Jira ticket on these projects' Tasks boards.</p>
      ) : (
        <ul className="pd-list" aria-label="On the Tasks boards">
          {board.map((b) => (
            <li key={b.key} className="pd-card pd-ticket">
              <span className="pd-card-top">
                {b.url ? <a className="mono" href={b.url} target="_blank" rel="noreferrer">{b.key}</a> : <b className="mono">{b.key}</b>}
                {b.external_status && <Pill tone="idle">{b.external_status}</Pill>}
              </span>
              {b.title && <span>{b.title}</span>}
              <span className="sub">
                {[b.initiatives.length ? b.initiatives.join(", ") : "no initiative", b.project_id ?? "",
                  b.commits ? count(b.commits, "commit") : ""].filter(Boolean).join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      )}
      <h3 className="pd-map-h">Keys found in git</h3>
      {t.tickets.length === 0 ? (
        <p className="sub">No ticket key found in git yet.</p>
      ) : (
        t.tickets.map((w) => (
          <div key={w.key} className="pd-card pd-ticket">
            <b className="mono">{w.key}</b>
            {w.branches.map((b) => (
              <span key={b.project_id + b.ref} className="sub">
                branch <span className="mono">{b.ref}</span>
              </span>
            ))}
            <span className="sub">
              {count(w.commits.length, "commit")}
              {w.last_at ? ` · last ${fmtDate(w.last_at)}` : ""}
            </span>
          </div>
        ))
      )}
      {t.unlinked.length > 0 && (
        <p className="pd-warn">
          {t.unlinked.length} branches or commits with no ticket key.{" "}
          <a href="#/teams">See them on Teams › Tickets</a>
        </p>
      )}
    </section>
  );
}
