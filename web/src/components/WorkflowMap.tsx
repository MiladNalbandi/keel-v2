// A workflow as a read-only panel (the Wiki): the blocks (default), a table, or the old graph with zoom. A click on a
// block opens "what this step does".

import type { ReactNode } from "react";
import type { Step } from "../api";
import { Blocks, BlocksLegend, StepTable, useMapView, ViewToggle } from "./Blocks";
import { Graph, GraphLegend } from "./Graph";
import { Zoom } from "./Zoom";

export function WorkflowMap({ id, steps, tokens, acCount, onOpenStep, title = "Workflow", label, children }: {
  /** The place: it keeps its own Blocks / Table / Graph choice and zoom. */
  id: string;
  steps: Step[];
  tokens?: Record<string, number>;
  acCount?: number;
  onOpenStep?: (id: string) => void;
  title?: string;
  label?: string;
  children?: ReactNode;
}) {
  const [view, setView] = useMapView(id);
  return (
    <section className="panel" aria-label={title}>
      <div className="panel-head">
        <h2>{title}</h2>
        <ViewToggle value={view} onChange={setView} />
      </div>
      <div className="panel-body grid" style={{ gap: 10 }}>
        {view === "blocks" ? (
          <>
            <Blocks steps={steps} tokens={tokens} acCount={acCount} onOpenStep={onOpenStep} label={label} />
            <BlocksLegend />
          </>
        ) : view === "table" ? (
          <StepTable steps={steps} tokens={tokens} onOpenStep={onOpenStep} acCount={acCount} />
        ) : (
          <>
            <Zoom id={id}><Graph steps={steps} tokens={tokens} onSelect={onOpenStep} /></Zoom>
            <GraphLegend />
          </>
        )}
        {children}
      </div>
    </section>
  );
}
