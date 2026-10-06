// v0.7.x: the project's flows side by side, when more than one runs (one in the project folder, others in worktrees of
// their own): each flow's state, branch and files, the files two flows both change, the branches that conflict, and an
// order to merge them in. A card opens that flow on the Flow page.

import { api, errorParts, type BoardFlow, type FlowBoard } from "../api";
import { go, useApp } from "../state";
import { StatusPill } from "./ui";

/** The board is worth showing once a flow runs in a worktree, or two flows exist at once. */
export function showBoard(b: FlowBoard | null | undefined): boolean {
  return !!b && (b.flows.length > 1 || b.flows.some((f) => f.where === "worktree"));
}

export function FlowBoardView({ board, selected, onChanged }: { board: FlowBoard; selected?: string | null; onChanged: () => void }) {
  const { toast } = useApp();
  const byId = new Map(board.flows.map((f) => [f.thread_id, f]));
  const name = (tid: string) => byId.get(tid)?.title ?? tid;
  const shared = (f: BoardFlow) => board.overlaps.filter((o) => o.flows.includes(f.thread_id)).length;
  const remove = async (f: BoardFlow) => {
    try {
      await api.removeWorktree(f.thread_id);
      toast(`The worktree of "${f.title}" is gone; its branch ${f.branch ?? ""} stays.`);
      onChanged();
    } catch (e) {
      const p = errorParts(e);
      toast(p.hint ? `${p.message} ${p.hint}` : p.message);
    }
  };
  return (
    <section className="panel fb" aria-label="Flows of this project">
      <div className="panel-head"><h2>Flows of this project</h2><span className="sub">{board.flows.length} side by side</span></div>
      <ul className="fb-cards">
        {board.flows.map((f) => (
          <li key={f.thread_id}>
            <article className={`fb-card${f.thread_id === selected ? " on" : ""}`} aria-current={f.thread_id === selected ? "true" : undefined}>
              <button type="button" className="fb-open" onClick={() => go("flow", f.thread_id)} aria-label={`Open the flow ${f.title}`}>
                <b className="fb-title">{f.title}</b>
              </button>
              <div className="fb-meta">
                <StatusPill status={f.status} />
                {f.phase && f.phase !== "none" && <span className="sub">{f.phase}</span>}
                <span className="sub">{f.where === "folder" ? "project folder" : "own worktree"}</span>
              </div>
              {f.branch && <span className="fb-branch mono">{f.branch}</span>}
              {f.waiting?.title && f.status === "waiting" && <span className="fb-wait">waits: {f.waiting.title}</span>}
              <span className="sub">{f.files.length} file{f.files.length === 1 ? "" : "s"} changed{shared(f) ? ` · ${shared(f)} shared with another flow` : ""}</span>
              {f.worktree_left && (
                <button type="button" className="btn sm" onClick={() => void remove(f)}>Remove the worktree</button>
              )}
            </article>
          </li>
        ))}
      </ul>
      {board.overlaps.length > 0 && (
        <div className="fb-note warn" role="note">
          <b>Files more than one flow changes</b>
          <ul>{board.overlaps.map((o) => <li key={o.file}><code>{o.file}</code> — {o.flows.map(name).join(", ")}</li>)}</ul>
        </div>
      )}
      {board.conflicts.length > 0 && (
        <div className="fb-note bad" role="note">
          <b>Branches that will not merge cleanly</b>
          <ul>{board.conflicts.map((c) => (
            <li key={`${c.a}:${c.b}`}>{name(c.a)} and {name(c.b)}: {c.files.map((x) => <code key={x}>{x}</code>)}</li>
          ))}</ul>
        </div>
      )}
      {board.order.length > 1 && (
        <p className="fb-order sub">Merge order: {board.order.map((tid, i) => `${i + 1}. ${name(tid)}`).join("  ")}
          {board.conflicts.length ? " (fewest conflicts first)" : " (smallest change first)"}</p>
      )}
    </section>
  );
}
