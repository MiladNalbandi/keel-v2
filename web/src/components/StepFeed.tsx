// One agent step in a feed (Live agents, Jobs): what it said, which tool it called, the diff it wrote.

import type { JobStep } from "../api";
import { clock } from "../format";

export function Diff({ text }: { text: string }) {
  return (
    <pre className="diff">
      {text.split("\n").map((l, i) => (
        <span key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "a" : l.startsWith("-") && !l.startsWith("---") ? "d" : undefined}>
          {l}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}

const KIND_CLASS: Record<string, string> = { write: "edit", thinking: "text", result: "answer" };
export const kindClass = (k: string) => `k-${KIND_CLASS[k] ?? k}`;
const MONO = new Set(["tool", "write", "edit", "guard", "error"]);

export function StepItem({ s }: { s: JobStep }) {
  const label = s.kind === "tool" && s.tool ? `${s.server ? "mcp · " : ""}${s.tool}` : null;
  return (
    <div className="step" data-kind={s.kind}>
      <span className="t">{clock(s.at).slice(3)}</span>
      <div className="box">
        <span className={`kind ${kindClass(s.kind)}`}>{s.kind}</span>
        {s.server && <><span className={`tag ${s.server === "keel" ? "keel" : ""}`}>{s.server}</span> </>}
        {label && <span className="mono">{label} </span>}
        {s.path && (s.kind === "write" || s.kind === "edit") && <span className="mono">{s.path} </span>}
        {s.text && <span className={MONO.has(s.kind) ? "mono" : ""} style={{ whiteSpace: "pre-wrap" }}>{s.text}</span>}
        {(s.ms !== undefined || s.ok !== undefined) && (
          <span className="sub"> · {s.ok === false ? "failed" : "ok"}{s.ms !== undefined ? ` · ${s.ms}ms` : ""}</span>
        )}
        {s.diff && <Diff text={s.diff} />}
      </div>
    </div>
  );
}

export function mergeSteps(a: JobStep[], b: JobStep[]): JobStep[] {
  const m = new Map<number, JobStep>();
  [...a, ...b].forEach((s) => m.set(s.n, s));
  return [...m.values()].sort((x, y) => x.n - y.n);
}

export const filesTouched = (steps: JobStep[]) =>
  [...new Set(steps.filter((s) => (s.kind === "write" || s.kind === "edit") && s.path).map((s) => s.path!))];
