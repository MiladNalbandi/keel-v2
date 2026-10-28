import { useRef, useState } from "react";
import { api, type LadderRung, type Memory, type ThreadState, type Workflow } from "../../api";
import { type BlocksHandle } from "../../components/Blocks";
import { ErrorBox, GoButton, Panel, Pill, type PillTone } from "../../components/ui";
import { useLoad } from "../../state";
import { EventsPanel } from "./EventsPanel";
import { BudgetMeter, FlowBar, LiveCanvas, StatusCard, useThreadBits } from "./FlowParts";
import { GateCard } from "./Gate";
import { DockBar, DockClose, StepPanel, useStepDock } from "./StepPanel";
import { useWide } from "../../components/useWide";

// ---------- init flow: ladder ‖ knowledge build ----------

type Rung = { label: string; cmd: string; status: "ok" | "fail" | "fix" | "todo" | "skip"; note: string };

const LADDER_STATUS: Record<LadderRung["status"], Rung["status"]> = { pass: "ok", fail: "fail", fixing: "fix", waiting: "todo", skipped: "skip" };

/** The setup ladder, from the thread (the engine keeps it in the flow's state). */
export function rungsFrom(thread: ThreadState | null): Rung[] {
  return [...(thread?.ladder ?? [])].sort((a, b) => a.n - b.n).map((r) => ({
    label: r.name, cmd: r.cmd, status: LADDER_STATUS[r.status] ?? "todo", note: r.detail ?? "",
  }));
}

const RUNG_PILL: Record<Rung["status"], [PillTone, string]> = { ok: ["ok", "pass"], fail: ["bad", "fail"], fix: ["warn", "fixing"], todo: ["idle", "waiting"], skip: ["idle", "skipped"] };
const KB_PILL: Record<string, [PillTone, string]> = { written: ["ok", "written"], stale: ["warn", "stale"], missing: ["idle", "not written"], writing: ["run", "writing"] };

export function InitFlow({ pid, f, onStart, reload }: { pid: string; f: { thread: ThreadState; workflow: Workflow }; onStart: () => void; reload: () => Promise<void> }) {
  const { thread, workflow } = f;
  const { history, est, job, jobs, actual, facts, totals, perAc, visited, started } = useThreadBits(pid, thread, workflow);
  const memory = useLoad<Memory>(`mem:${pid}`, () => api.memory(pid));
  const rungs = rungsFrom(thread);
  const passed = rungs.filter((r) => r.status === "ok").length;
  const kb = memory.data?.knowledge ?? [];
  const writing = (jobs.data ?? []).filter((j) => j.thread_id === thread.thread_id && /librarian/.test(j.agent));
  const written = kb.filter((k) => k.status === "written").length;
  // 0.18.57 the project notes are opt-in: off and no librarian at work, the lane and the panel say so
  const notesOff = !!memory.data?.notes && !memory.data.notes.on && !writing.length;
  const [explain, setExplain] = useState<string | null>(null);
  const canvas = useRef<BlocksHandle>(null);
  const waiting = thread.status === "waiting" && thread.waiting ? <GateCard thread={thread} workflow={workflow} onDone={reload} /> : null;
  const running = (jobs.data ?? []).filter((j) => j.thread_id === thread.thread_id && j.status === "running");
  // keel 0.18.66 a wide screen docks the step panel next to the steps: the clicked step, else the one that runs now (or
  // waits), else the first; » folds it to a thin bar. A narrow screen keeps the drawer.
  const wide = useWide();
  const dock = useStepDock();
  const cur = (thread.waiting?.step ?? thread.current ?? "").replace(/__fix$/, "");
  const shown = explain ?? (workflow.steps.some((s) => s.id === cur) ? cur : workflow.steps[0]?.id ?? null);
  const shownName = workflow.steps.find((s) => s.id === shown)?.name ?? shown ?? "";
  const toCurrent = () => canvas.current?.reveal(cur, true);
  const pick = (id: string) => { setExplain(id); if (wide && !dock.open) dock.setOpen(true); };
  const steps = <LiveCanvas thread={thread} workflow={workflow} actual={actual} facts={facts} totals={totals} perAc={perAc} visited={visited} waiting={waiting} canvas={canvas} onOpen={pick} selected={explain} />;
  return (
    <>
      {!wide && explain && <StepPanel drawer key={explain} pid={pid} thread={thread} workflow={workflow} stepId={explain} running={running} onClose={() => setExplain(null)}
        onJump={() => { setExplain(null); toCurrent(); }} tab={dock.tab} setTab={dock.setTab} />}
      <FlowBar thread={thread} workflow={workflow} estimate={est.data?.tokens ?? null} started={started} onStart={onStart}
        onJump={toCurrent} extra={<GoButton to="wiki" className="btn">Open wiki</GoButton>} />
      {!wide || !shown ? steps : (
        <div className={dock.open ? "fl" : "fl is-folded"}>
          {steps}
          {dock.open ? (
            <aside className="fl-side" aria-label="Step details">
              <StepPanel key={shown} pid={pid} thread={thread} workflow={workflow} stepId={shown} running={running} onClose={() => dock.setOpen(false)}
                onJump={toCurrent} tab={dock.tab} setTab={dock.setTab} tools={<DockClose what="step panel" onClose={() => dock.setOpen(false)} />} />
            </aside>
          ) : <DockBar what="step panel" label={`Step details · ${shownName}`} onOpen={() => dock.setOpen(true)} />}
        </div>
      )}
      <div className="lanes" style={{ marginTop: 12 }}>
            <div className="lane">
              <div className="lane-h"><b>Ladder</b><span className="sub num">{rungs.length ? `${passed} / ${rungs.length} rungs` : "not started"}</span></div>
              <div className="meter m-ok"><i style={{ width: `${rungs.length ? (passed / rungs.length) * 100 : 0}%` }} /></div>
              <span className="sub">plain code · each rung must pass before the next</span>
            </div>
            <div className="lane">
              <div className="lane-h"><b>Knowledge build</b><span className="sub num">{notesOff ? "off" : `${written} / ${kb.length || "?"} sections`}</span></div>
              {!notesOff && <div className="meter m-ok"><i style={{ width: `${kb.length ? (written / kb.length) * 100 : 0}%`, background: "var(--run)" }} /></div>}
              <span className="sub">{notesOff ? "project notes are off · agents read the code itself"
                : `${writing.length ? `librarian × ${writing.length} in parallel` : "librarians"} · writes the wiki`}</span>
            </div>
      </div>
      <div className="grid g2" style={{ marginTop: 16 }}>
        <Panel title="Ladder" extra={<span className="hint">the exact command that worked is saved for each rung</span>} body={false}>
          {!rungs.length ? <div className="panel-body empty">Rungs show here when the ladder runs.</div> : (
            <div className="table-wrap"><table>
              <thead><tr><th>#</th><th>Rung</th><th>Command</th><th>Status</th></tr></thead>
              <tbody>{rungs.map((r, i) => (
                <tr key={i}>
                  <td className="num sub">{i + 1}</td>
                  <td><b>{r.label}</b>{(r.status === "fix" || r.status === "fail") && r.note && <div className="sub">{r.note}</div>}</td>
                  <td className="mono sub">{r.cmd}</td>
                  <td><Pill tone={RUNG_PILL[r.status][0]}>{RUNG_PILL[r.status][1]}</Pill></td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
        </Panel>
        <div className="grid" style={{ alignContent: "start" }}>
          {thread.status !== "waiting" && <StatusCard pid={pid} thread={thread} workflow={workflow} job={job} onDone={reload} />}
          <Panel title="Knowledge base → wiki" body="grid">
            <div className="grid" style={{ gap: 8 }}>
              {memory.error ? <ErrorBox error={memory.error} /> : notesOff ? (
                <span className="sub" data-testid="init-notes-off">Project notes are off: keel's agents read the code itself. Build them at the end of init (<b>Build project notes</b>) or later from the Wiki; they help most in a large repo.</span>
              ) : !kb.length ? <span className="sub">No section yet.</span> : kb.map((k) => {
                const live = writing.some((j) => j.agent.includes(k.id) || j.step.includes(k.id));
                const [tone, lab] = live ? KB_PILL.writing : KB_PILL[k.status] ?? ["idle", k.status];
                return <div key={k.id} className="row" style={{ justifyContent: "space-between" }}><span><b>{k.id}</b> <span className="sub">{k.words} words · {k.cites} citations</span></span><Pill tone={tone}>{lab}</Pill></div>;
              })}
              <GoButton to="wiki">Read what is written so far</GoButton>
            </div>
          </Panel>
          <BudgetMeter thread={thread} estimate={est.data?.tokens ?? null} title="Budget for init" />
          <EventsPanel pid={pid} thread={thread} history={history.error ? [] : history.data} />
        </div>
      </div>
    </>
  );
}
