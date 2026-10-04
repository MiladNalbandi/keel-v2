// One agent step, shown the same way everywhere (Live agents, the Jobs step feed): Markdown for what the
// agent says, a file chip + highlighted content for a read, a file header + unified diff for a write or edit,
// `$ command` + output for a shell call, a server/tool badge for an MCP call. Newlines are kept everywhere,
// long content folds after FOLD lines. Also the Outcome card and the "Files touched" list.

import { useCallback, useMemo, type ReactNode } from "react";
import type { Job, JobStep } from "../api";
import { clock, kfmt, since } from "../format";
import { CodeBlock, DiffView, FoldedText, linesOf, parseDiff } from "./Code";
import { Markdown } from "./Markdown";
import { Pill } from "./ui";

export const KIND_LABEL: Record<string, string> = {
  text: "says", thinking: "thinks", tool: "tool", read: "read", write: "write", edit: "edit",
  answer: "answer", guard: "guard", error: "error",
};
export const kindLabel = (k: string) => KIND_LABEL[k] ?? k;

const SHELL = /^(bash|shell|sh|zsh|run_command|run|exec|exec_command|command|terminal|local_shell)$/i;
export const isShell = (tool?: string) => !tool || SHELL.test(tool);

/** MCP server and tool name: from `server`, or from a `mcp__server__tool` name. */
export function mcpOf(s: JobStep): { server: string; tool: string } | null {
  if (s.server) return { server: s.server, tool: s.tool ?? "" };
  const m = s.tool?.match(/^mcp__([^_]+(?:_[^_]+)*?)__(.+)$/);
  return m ? { server: m[1], tool: m[2] } : null;
}

export const ms = (n: number) => (n < 1000 ? `${n} ms` : n < 60_000 ? `${(n / 1000).toFixed(1)} s` : `${Math.floor(n / 60_000)}m ${Math.round((n % 60_000) / 1000)}s`);
const shortPath = (p: string) => p.split("/").slice(-3).join("/");

function pretty(text: string): { json: boolean; text: string } {
  const t = text.trim();
  if (!/^[[{]/.test(t)) return { json: false, text };
  try {
    return { json: true, text: JSON.stringify(JSON.parse(t), null, 2) };
  } catch {
    return { json: false, text };
  }
}

function FileIcon() {
  return (
    <svg className="file-ico" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
      <path fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" d="M4 1.5h5.5L13 5v9.5H4zM9.5 1.5V5H13" />
    </svg>
  );
}

function Badges({ s, shell }: { s: JobStep; shell: boolean }) {
  return (
    <>
      {s.ok !== undefined && (
        <span className={`xbadge ${s.ok ? "ok" : "bad"}`} data-testid="exit">
          {shell ? (s.ok ? "exit 0" : "exit ≠ 0") : s.ok ? "ok" : "failed"}
        </span>
      )}
      {s.ms !== undefined && <span className="sub num">{ms(s.ms)}</span>}
    </>
  );
}

/** Tool output: collapsible, with exit badge and duration; JSON is pretty-printed and highlighted. */
function Output({ s, shell }: { s: JobStep; shell: boolean }) {
  const out = s.output ?? "";
  const p = pretty(out);
  const n = linesOf(out).length;
  return (
    <details className="tool-out" open>
      <summary>
        <span className="lab-s">Output</span> <span className="sub">{n} line{n === 1 ? "" : "s"}</span> <Badges s={s} shell={shell} />
      </summary>
      {p.json ? <CodeBlock text={p.text} lang="json" gutter={false} /> : <FoldedText text={out} />}
    </details>
  );
}

function ToolStep({ s }: { s: JobStep }) {
  const mcp = mcpOf(s);
  const hasOut = !!s.output;
  if (mcp) {
    const args = pretty(s.text ?? "");
    return (
      <>
        <div className="tool-line">
          <span className={`tool-badge mcp ${mcp.server === "keel" ? "keel" : ""}`}><b>{mcp.server}</b> · {mcp.tool || "tool"}</span>
          {!hasOut && <Badges s={s} shell={false} />}
        </div>
        {s.text?.trim() && (args.json || args.text.includes("\n")
          ? <CodeBlock text={args.text} lang={args.json ? "json" : null} gutter={false} className="tool-args" />
          : <code className="mono tool-arg1">{s.text}</code>)}
        {hasOut && <Output s={s} shell={false} />}
      </>
    );
  }
  if (isShell(s.tool)) {
    return (
      <>
        <div className="cmd" data-testid="cmd">
          <span className="prompt" aria-hidden="true">$</span>
          <CodeBlock text={s.text ?? ""} lang="bash" gutter={false} className="cmd-code" />
          {!hasOut && <span className="cmd-badges"><Badges s={s} shell /></span>}
        </div>
        {hasOut && <Output s={s} shell />}
      </>
    );
  }
  return (
    <>
      <div className="tool-line">
        <span className={`tool-badge t-${(s.tool ?? "").toLowerCase()}`}>{s.tool}</span>
        {!s.text?.includes("\n") && s.text && <code className="mono tool-arg1">{s.text}</code>}
        {!hasOut && <Badges s={s} shell={false} />}
      </div>
      {s.text?.includes("\n") && <FoldedText text={s.text} className="tool-args" />}
      {hasOut && <Output s={s} shell={false} />}
    </>
  );
}

function ReadStep({ s }: { s: JobStep }) {
  const n = linesOf(s.text).length;
  return (
    <details className="file-card read">
      <summary className="file-bar">
        <span className="file-chip" title={s.path}><FileIcon />{s.path ? shortPath(s.path) : "file"}</span>
        <span className="sub">{n} line{n === 1 ? "" : "s"}</span>
      </summary>
      {s.text ? <CodeBlock text={s.text} path={s.path} /> : <div className="sub" style={{ padding: "6px 10px" }}>(empty)</div>}
    </details>
  );
}

function FileStep({ s }: { s: JobStep }) {
  const d = useMemo(() => (s.diff ? parseDiff(s.diff) : null), [s.diff]);
  const isNew = s.kind === "write" && (!d || d.removed === 0);
  const path = s.path ?? d?.files[0] ?? "";
  return (
    <div className="file-card">
      <div className="file-bar">
        <span className="file-chip" title={path}><FileIcon />{path ? shortPath(path) : "file"}</span>
        <span className={`tag ${isNew ? "new" : ""}`}>{isNew ? "new file" : "edit"}</span>
        {d && <><span className="plus num">+{d.added}</span><span className="minus num">−{d.removed}</span></>}
        {s.text && !s.text.includes("\n") && <span className="sub file-sum">{s.text}</span>}
      </div>
      {s.diff ? <DiffView diff={s.diff} path={path} />
        : s.text?.includes("\n") ? <CodeBlock text={s.text} path={path} /> : null}
    </div>
  );
}

function stepBody(s: JobStep): ReactNode {
  switch (s.kind) {
    case "text":
      return <div className="md"><Markdown text={s.text} breaks /></div>;
    case "thinking": {
      const first = linesOf(s.text)[0] ?? "";
      return (
        <details className="thinking">
          <summary>{first.length > 110 ? first.slice(0, 110) + "…" : first || "thinking"}</summary>
          <div className="md"><Markdown text={s.text} breaks /></div>
        </details>
      );
    }
    case "answer":
      return <div className="answer-card md"><Markdown text={s.text} breaks /></div>;
    case "read":
      return <ReadStep s={s} />;
    case "write":
    case "edit":
      return <FileStep s={s} />;
    case "tool":
      return <ToolStep s={s} />;
    case "guard":
    case "error":
      return (
        <div className={`step-alert ${s.kind}`}>
          {s.path && <span className="file-chip"><FileIcon />{s.path}</span>}
          <FoldedText text={s.text || s.output || ""} />
        </div>
      );
    default:
      return (
        <>
          {s.path && <span className="file-chip"><FileIcon />{s.path}</span>}
          {s.text && <FoldedText text={s.text} />}
          {s.diff && <DiffView diff={s.diff} path={s.path} />}
        </>
      );
  }
}

/** One step of an agent's feed. `idPrefix` makes the anchor `${idPrefix}-${n}` for "Files touched" links. */
export function StepView({ s, idPrefix = "step" }: { s: JobStep; idPrefix?: string }) {
  return (
    <div className="step" data-kind={s.kind} id={`${idPrefix}-${s.n}`}>
      <span className="t">{clock(s.at).slice(3)}</span>
      <div className="box">
        <span className={`kind k-${s.kind}`}>{kindLabel(s.kind)}</span>
        <div className="step-body">{stepBody(s)}</div>
      </div>
    </div>
  );
}

// ---------- files touched + outcome ----------

export type Touched = { path: string; last: number; count: number; added: number; removed: number };

/** Files written or edited, in first-touch order, each with the step of its last change. */
export function touchedFiles(steps: JobStep[]): Touched[] {
  const m = new Map<string, Touched>();
  for (const s of steps) {
    if ((s.kind !== "write" && s.kind !== "edit") || !s.path) continue;
    const d = s.diff ? parseDiff(s.diff) : { added: 0, removed: 0 };
    const t = m.get(s.path) ?? { path: s.path, last: s.n, count: 0, added: 0, removed: 0 };
    m.set(s.path, { ...t, last: s.n, count: t.count + 1, added: t.added + d.added, removed: t.removed + d.removed });
  }
  return [...m.values()];
}

/** Scroll to a step anchor and flash it. `before` runs first (e.g. show every kind, stop following). */
export function useJumpToStep(idPrefix: string, before?: () => void) {
  return useCallback((n: number) => {
    before?.();
    window.setTimeout(() => {
      const el = document.getElementById(`${idPrefix}-${n}`);
      if (!el) return;
      el.scrollIntoView?.({ block: "start", behavior: "smooth" });
      el.classList.remove("flash");
      void el.offsetWidth;
      el.classList.add("flash");
    }, 30);
  }, [idPrefix, before]);
}

export function FilesTouched({ steps, onJump, label = "Files touched" }: { steps: JobStep[]; onJump: (n: number) => void; label?: string }) {
  const files = touchedFiles(steps);
  return (
    <div className="row touched">
      <span className="lab-s">{label}</span>
      {files.length ? files.map((f) => (
        <button key={f.path} type="button" className="file-link" title={`${f.path} — show the last change`} onClick={() => onJump(f.last)}
          aria-label={`Show the last change to ${f.path}`}>
          <FileIcon />{shortPath(f.path)}{f.count > 1 ? <span className="sub"> ×{f.count}</span> : null}
          {f.added + f.removed > 0 && <span className="num"> <span className="plus">+{f.added}</span> <span className="minus">−{f.removed}</span></span>}
        </button>
      )) : <span className="sub">no files yet</span>}
    </div>
  );
}

/** The final card of a finished agent: status, its answer (Markdown), the files it changed, tokens and time. */
export function Outcome({ job, steps, onJump }: { job: Job; steps: JobStep[]; onJump: (n: number) => void }) {
  // engine v0.3: the agent's final kind:"answer" step (tool output lives inside each tool step)
  const last = [...steps].reverse().find((s) => s.kind === "answer");
  const tone = job.status === "done" ? "ok" : job.status === "running" ? "run" : "bad";
  return (
    <section className={`result outcome t-${tone}`} aria-label="Outcome">
      <div className="row" style={{ justifyContent: "space-between" }}><b>Outcome</b><Pill tone={tone}>{job.status}</Pill></div>
      {last ? <div className="md outcome-answer"><Markdown text={last.text} breaks /></div> : <span className="sub">The agent returned no answer.</span>}
      <FilesTouched steps={steps} onJump={onJump} label="Files changed" />
      <div className="kv">
        <span>Tokens in / out</span><b className="num">{kfmt(job.tokens_in)} / {kfmt(job.tokens_out)}{job.tokens_cached ? ` (+${kfmt(job.tokens_cached)} cached)` : ""}</b>
        <span>Time</span><b className="num">{since(job.started_at, job.ended_at)}</b>
      </div>
    </section>
  );
}
