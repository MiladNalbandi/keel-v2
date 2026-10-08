// keel Product › Initiatives: the board (one column per stage), the list with the parked and finished ones, and a new
// idea. With an id in the link (#/initiatives/INI-3) it is that initiative's page.

import { useMemo, useState } from "react";
import {
  Async,
  Drawer,
  Empty,
  Tabs,
  useLoad,
  type AddonPageProps,
} from "@keel/web-sdk";
import { InitiativePage } from "./Initiative";
import {
  productApi,
  STAGE_LABEL,
  type BoardItem,
  type RepoOwner,
} from "./productApi";
import {
  fmtDate,
  goInitiative,
  ProductHead,
  StatusPill,
  TRACK,
  useAct,
} from "./shared";

const COLUMNS = ["idea", ...TRACK] as const;

export default function Initiatives({ arg }: AddonPageProps) {
  if (arg) return <InitiativePage key={arg} id={arg} />;
  return <Board />;
}

function Board() {
  const board = useLoad("initiatives", productApi.board);
  const [tab, setTab] = useState<"board" | "list">("board");
  const [adding, setAdding] = useState(false);
  return (
    <div className="pd">
      <ProductHead
        crumbs={[["Initiatives"]]}
        title="Initiatives"
        sub="Ideas on their way from a brief to the teams' stories. keel writes and checks; people decide at every stage."
        actions={
          <button
            className="btn primary"
            type="button"
            onClick={() => setAdding(true)}
          >
            New initiative
          </button>
        }
      />
      <div className="pd-bar">
        <Tabs
          label="Show"
          value={tab}
          onChange={setTab}
          options={[
            ["board", "Board"],
            ["list", "All, with history"],
          ]}
        />
      </div>
      <Async r={board} what="Loading the initiatives">
        {(items) =>
          items.length === 0 ? (
            <Empty>
              No initiative yet. Write an idea in a few sentences: keel turns it
              into a brief and asks what it needs to know.
            </Empty>
          ) : tab === "board" ? (
            <Columns items={items} />
          ) : (
            <List items={items} />
          )
        }
      </Async>
      {adding && <NewInitiative onClose={() => setAdding(false)} />}
    </div>
  );
}

function Card({ i }: { i: BoardItem }) {
  return (
    <button
      type="button"
      className={`pd-card${i.waiting ? " is-wait" : ""}`}
      onClick={() => goInitiative(i.id)}
      aria-label={`${i.id}: ${i.title}`}
    >
      <span className="pd-card-top">
        <span className="mono">{i.id}</span>
        <StatusPill status={i.status} waiting={i.waiting} />
      </span>
      <b>{i.title}</b>
      {i.teams.length > 0 && (
        <span className="pd-chips">
          {i.teams.map((t) => (
            <span key={t} className="chip">
              {t}
            </span>
          ))}
        </span>
      )}
      {i.progress && (
        <span
          className="pd-progress"
          title={`${i.progress.done} of ${i.progress.total} stories done`}
        >
          <i
            style={{
              width: `${i.progress.total ? Math.round((100 * i.progress.done) / i.progress.total) : 0}%`,
            }}
          />
        </span>
      )}
      <span className="sub">{i.next}</span>
    </button>
  );
}

function Columns({ items }: { items: BoardItem[] }) {
  const active = items.filter(
    (i) => i.status !== "parked" && i.stage !== "done",
  );
  const parked = items.filter((i) => i.status === "parked");
  return (
    <>
      <div className="pd-board">
        {COLUMNS.map((s) => {
          const list = active.filter((i) => i.stage === s);
          return (
            <section
              key={s}
              className="pd-col"
              data-testid={`stage-${s}`}
              aria-label={STAGE_LABEL[s]}
            >
              <h2>
                {STAGE_LABEL[s]} <span className="sub">{list.length}</span>
              </h2>
              {list.map((i) => (
                <Card key={i.id} i={i} />
              ))}
            </section>
          );
        })}
      </div>
      {parked.length > 0 && (
        <section className="pd-parked" aria-label="Not now">
          <h2>Not now</h2>
          {parked.map((i) => (
            <button
              key={i.id}
              type="button"
              className="btn sm"
              onClick={() => goInitiative(i.id)}
            >
              <span className="mono">{i.id}</span> {i.title} · back{" "}
              {fmtDate(i.revisit_at)}
            </button>
          ))}
        </section>
      )}
    </>
  );
}

function List({ items }: { items: BoardItem[] }) {
  return (
    <div className="pd-table-wrap">
      <table className="pd-table">
        <thead>
          <tr>
            <th>Initiative</th>
            <th>Stage</th>
            <th>Status</th>
            <th>Teams</th>
            <th>Next</th>
            <th>Changed</th>
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.id}>
              <td>
                <a href={`#/initiatives/${i.id}`}>
                  <span className="mono">{i.id}</span> {i.title}
                </a>
              </td>
              <td>
                {STAGE_LABEL[i.stage] ?? i.stage}
                {i.option ? ` · option ${i.option}` : ""}
              </td>
              <td>
                <StatusPill status={i.status} waiting={i.waiting} />
              </td>
              <td>{i.teams.join(", ") || "—"}</td>
              <td className="sub">{i.next}</td>
              <td className="sub">{fmtDate(i.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NewInitiative({ onClose }: { onClose: () => void }) {
  const repos = useLoad("initiative-repos", productApi.repos, { live: false });
  const [title, setTitle] = useState("");
  const [idea, setIdea] = useState("");
  const [whyNow, setWhyNow] = useState("");
  const [hope, setHope] = useState("");
  const [owner, setOwner] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [start, setStart] = useState(true);
  const act = useAct();
  const save = async () => {
    const d = await act.run("save", () =>
      productApi.create({
        title,
        idea,
        why_now: whyNow,
        outcome_hope: hope,
        owner,
        repos: picked,
        start,
      }),
    );
    if (d) {
      onClose();
      goInitiative(d.initiative.id);
    }
  };
  const footer = (
    <>
      <button className="btn" type="button" onClick={onClose}>
        Cancel
      </button>
      <button
        className="btn primary"
        type="button"
        disabled={!title.trim() || !idea.trim() || act.busy !== null}
        onClick={() => void save()}
      >
        {start ? "Start discovery" : "Save the idea"}
      </button>
    </>
  );
  return (
    <Drawer title="New initiative" onClose={onClose} footer={footer}>
      <div className="pd-form">
        <div className="field">
          <label htmlFor="ni-title">Title</label>
          <input
            id="ni-title"
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Euro prices for EU visitors"
          />
        </div>
        <div className="field">
          <label htmlFor="ni-idea">The idea</label>
          <textarea
            id="ni-idea"
            className="pd-text"
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
            placeholder="What is the problem, and what should change? Two or three sentences are enough."
          />
        </div>
        <div className="field">
          <label htmlFor="ni-why">Why now (optional)</label>
          <input
            id="ni-why"
            type="text"
            value={whyNow}
            onChange={(e) => setWhyNow(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="ni-hope">The outcome you hope for (optional)</label>
          <input
            id="ni-hope"
            type="text"
            value={hope}
            onChange={(e) => setHope(e.target.value)}
            placeholder="Conversion in the EU from 2.1% to 2.5%"
          />
        </div>
        <div className="field">
          <label htmlFor="ni-owner">Owner (optional)</label>
          <input
            id="ni-owner"
            type="text"
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            placeholder="the PO or PM"
          />
        </div>
        <fieldset className="field pd-repos">
          <legend className="lab">Repos keel may read for the impact</legend>
          <Async r={repos} what="Loading the repos">
            {(list) => (
              <RepoPicker list={list} picked={picked} onChange={setPicked} />
            )}
          </Async>
        </fieldset>
        <label className="chk">
          <input
            type="checkbox"
            checked={start}
            onChange={(e) => setStart(e.target.checked)}
          />{" "}
          Start discovery now (keel writes the brief)
        </label>
        {act.box}
      </div>
    </Drawer>
  );
}

export function RepoPicker({
  list,
  picked,
  onChange,
}: {
  list: RepoOwner[];
  picked: string[];
  onChange: (p: string[]) => void;
}) {
  const sorted = useMemo(
    () => [...list].sort((a, b) => a.name.localeCompare(b.name)),
    [list],
  );
  if (sorted.length === 0)
    return (
      <p className="sub">
        No project yet. Add the repos in All projects first.
      </p>
    );
  return (
    <div className="pd-repo-list">
      {sorted.map((r) => (
        <label key={r.project_id} className="chk">
          <input
            type="checkbox"
            checked={picked.includes(r.project_id)}
            onChange={(e) =>
              onChange(
                e.target.checked
                  ? [...picked, r.project_id]
                  : picked.filter((p) => p !== r.project_id),
              )
            }
          />{" "}
          {r.name}{" "}
          <span className="sub">
            {r.teams.length ? r.teams.join(", ") : "no team yet"}
          </span>
        </label>
      ))}
    </div>
  );
}
