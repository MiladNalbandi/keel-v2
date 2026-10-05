// The workflow builder's edit state and operations: add a block (after a block, or dropped from the palette into a
// loop or not), move (by one, or dragged to a place), remove with Undo; a padlock marks a keel rule — removing it asks
// to turn keel rules off first. The page (pages/Workflows.tsx) draws the blocks and the graph with these.

import { useState } from "react";
import type { StepKind, Workflow } from "../api";
import { insertStep, moveStep, moveStepTo, removeStep, undoRemove, type LoopCtx, type Removed } from "./builderOps";

export type BuilderState = { insertAt: number | null; insertLoop?: LoopCtx; removed: Removed | null; lockAsk: string | null };
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
  const addAt = (at: number, kind: StepKind, loop?: LoopCtx) => {
    const r = insertStep(w, at, kind, loop);
    onChange(r.w);
    onSelect(r.id);
    setSt(emptyBuilderState);
  };
  return {
    st,
    insertAt: (i: number, loop?: LoopCtx) => setSt({ insertAt: i, insertLoop: loop, removed: null, lockAsk: null }),
    add: (kind: StepKind) => addAt(st.insertAt ?? w.steps.length - 1, kind, st.insertLoop),
    addAt,
    remove,
    move: (id: string, d: -1 | 1) => {
      onChange(moveStep(w, id, d));
      setSt((s) => ({ ...s, insertAt: null }));
    },
    moveTo: (id: string, after: number, loop?: LoopCtx) => {
      onChange(moveStepTo(w, id, after, loop));
      onSelect(id);
      setSt((s) => ({ ...s, insertAt: null }));
    },
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

export type BuilderApi = ReturnType<typeof useBuilder>;
