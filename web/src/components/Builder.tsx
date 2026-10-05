// The diagram-first workflow builder: click a step to edit it, + on an arrow inserts, × removes (with Undo),
// a padlock marks a keel rule — removing it asks to turn keel rules off first.

import { useState } from "react";
import type { StepKind, Workflow } from "../api";
import { insertStep, removeStep, undoRemove, type Removed } from "./builderOps";
import { Graph, GraphLegend } from "./Graph";
import { Zoom } from "./Zoom";
import { KIND } from "./workflow";

export type BuilderState = { insertAt: number | null; removed: Removed | null; lockAsk: string | null };
export const emptyBuilderState: BuilderState = { insertAt: null, removed: null, lockAsk: null };

export function useBuilder(w: Workflow, onChange: (w: Workflow) => void, onSelect: (id: string | null) => void) {
  const [st, setSt] = useState<BuilderState>(emptyBuilderState);
  const remove = (id: string, wf: Workflow = w) => {
    const r = removeStep(wf, id);
    if (!r) return;
    if ("locked" in r) {
      setSt({ insertAt: null, removed: null, lockAsk: id });
      return;
    }
    onChange(r.w);
    setSt({ insertAt: null, removed: r.removed, lockAsk: null });
    const next = r.w.steps[r.removed.index] ?? r.w.steps[r.removed.index - 1];
    onSelect(next?.id ?? null);
  };
  return {
    st,
    insertAt: (i: number) => setSt({ insertAt: i, removed: null, lockAsk: null }),
    add: (kind: StepKind) => {
      const at = st.insertAt ?? w.steps.length - 1;
      const r = insertStep(w, at, kind);
      onChange(r.w);
      onSelect(r.id);
      setSt(emptyBuilderState);
    },
    remove,
    undo: () => {
      if (!st.removed) return;
      onChange(undoRemove(w, st.removed));
      onSelect(st.removed.step.id);
      setSt(emptyBuilderState);
    },
    rulesOffAndRemove: () => {
      if (!st.lockAsk) return;
      const off = { ...w, keel_rules: false };
      remove(st.lockAsk, off);
    },
    cancel: () => setSt(emptyBuilderState),
    clear: () => setSt(emptyBuilderState),
    closeInsert: () => setSt((s) => ({ ...s, insertAt: null })),
  };
}

export function Builder({ w, sel, onSelect, b, tokens, customAgents }: {
  w: Workflow; sel: string | null; onSelect: (id: string | null) => void; b: ReturnType<typeof useBuilder>;
  tokens?: Record<string, number>; customAgents?: Set<string>;
}) {
  const { st } = b;
  const lockStep = st.lockAsk ? w.steps.find((x) => x.id === st.lockAsk) : null;
  const insertName = st.insertAt !== null ? (w.steps[st.insertAt]?.name ?? "start") : "";
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Diagram</h2>
        <span className="hint">click a step to edit · <b>+</b> on an arrow inserts · <b>×</b> removes · padlock = keel rule</span>
      </div>
      <div className="panel-body grid" style={{ gap: 10 }}>
        {st.insertAt !== null && (
          <div className="wbar" role="group" aria-label="Insert a step">
            <span>Insert after <b>{insertName}</b>:</span>
            {(["agent", "code", "gate", "branch", "parallel"] as StepKind[]).map((k) => (
              <button key={k} className="btn sm" type="button" onClick={() => b.add(k)}>{KIND[k].replace("◆ ", "")}</button>
            ))}
            <button className="btn sm ghost" type="button" onClick={b.cancel}>Cancel</button>
          </div>
        )}
        {lockStep && (
          <div className="wbar warn" role="alert">
            <span><b>{lockStep.name}</b> is a keel rule: {lockStep.kind === "gate" ? "a person must approve here" : "it proves the tests are real"}. To remove it, turn keel rules off for this workflow.</span>
            <button className="btn sm warn" type="button" onClick={b.rulesOffAndRemove}>Turn rules off and remove</button>
            <button className="btn sm ghost" type="button" onClick={b.cancel}>Keep it</button>
          </div>
        )}
        {st.removed && (
          <div className="wbar ok" role="status">
            <span>Removed <b>{st.removed.step.name}</b>. Arrows were reconnected.</span>
            <button className="btn sm" type="button" onClick={b.undo}>Undo</button>
          </div>
        )}
        <Zoom id="builder">
          <Graph steps={w.steps} edit per={6} selected={sel} insertAt={st.insertAt} keel={w.keel_rules} tokens={tokens} customAgents={customAgents}
            onSelect={(id) => { onSelect(id); b.closeInsert(); }} onInsert={b.insertAt} onRemove={(id) => b.remove(id)} />
        </Zoom>
        <GraphLegend />
      </div>
    </div>
  );
}
