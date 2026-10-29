// The new Review view's Problems tab (0.18.57): keel's checked findings. "Find problems" starts the run (the keel
// reviewers of Settings › Review read the change, a second check removes false alarms); the findings come in groups you
// decide on: Blocking, Should fix, Small things, and the ones that were there before this change (never blocking). Each
// one: Add as comment (a pending comment on its line), Fix it (0.18.65: a flow on the branch, the one Settings › Review
// names, or quick / small / change when it asks each time) or Dismiss. Nothing is posted by itself.
//
// The same api and store as the classic Findings tab: R.startAi("findings"), R.addDraft + R.decide.

import { ErrorBox } from "@keel/web-sdk";
import { type AiState, type Finding, type ReviewView } from "../reviewApi";
import { findingComment } from "../ReviewPanel";
import * as R from "../store";
import { problemGroups, whereOf } from "./model";

export function ProblemsTab({
  pid,
  reviewKey,
  view,
  ai,
  running,
  onShowInFile,
}: {
  pid: string;
  reviewKey: string;
  view: ReviewView;
  ai: AiState | null;
  running: string[];
  onShowInFile: (path: string, line: number | null) => void;
}) {
  const run = ai?.findings ?? null;
  const r = run?.status === "done" ? run.result : null;
  const starting = running.includes("findings");
  const start = () => void R.startAi(pid, reviewKey, "findings");
  const groups = problemGroups(ai);
  const comment = async (f: Finding) => {
    const at =
      f.path && f.line ? { path: f.path, line: f.line, side: f.side } : null;
    if (
      await R.addDraft(
        pid,
        reviewKey,
        findingComment(f, view.host?.kind === "gitlab"),
        at,
        f.id,
      )
    )
      await R.decide(pid, reviewKey, f.id, "commented");
  };
  const live = groups.filter((g) => g.actions).flatMap((g) => g.items);
  // 0.18.65 what Settings › Review says: the text above Find problems, and how a finding is fixed
  const find = ai?.config?.find;
  const lead = find?.text ?? "Two keel reviewers read the change, a second check removes false alarms, and you decide " +
    "what to post. Nothing is posted by itself.";
  const n = find?.reviewers.length ?? 2;
  const many = `${["", "One", "Two", "Three", "Four"][n] ?? n} keel reviewer${n === 1 ? " reads" : "s read"}`;
  const fixWith = find?.fix.flow ?? "small";
  const fix = (f: Finding, workflow?: string) => void R.fixFinding(pid, reviewKey, f.id, workflow);
  const blocking = live.filter(
    (f) =>
      f.severity === "blocking" &&
      ai?.decisions[f.id]?.decision !== "dismissed",
  ).length;

  if (!run)
    return (
      <div className="rvn-call">
        <b>keel has not looked for problems yet</b>
        <span className="rvn-dim">{lead}</span>
        <button
          type="button"
          className="rvn-btn primary"
          disabled={starting}
          onClick={start}
        >
          {starting ? "Starting…" : "Find problems"}
        </button>
      </div>
    );
  // 0.18.67 Find problems as the review flow: every step is on its Flow page
  const flowId = run?.stage === "flow" || run?.result?.flow_id ? run?.result?.flow_id ?? null : null;
  const flowLink = flowId && (
    <a className="rvn-link" href={`#/flow/${encodeURIComponent(flowId)}`}>
      Open the review flow ▸
    </a>
  );
  if (run.status === "running")
    return (
      <div className="rvn-call" role="status">
        <span className="pg-spin">
          {run.stage === "flow"
            ? `The review flow${run.result?.depth ? ` (${run.result.depth})` : ""} reads the change with your project's rules…`
            : run.stage === "verify"
              ? "keel checks each problem against the code…"
              : `${many} the change…`}
        </span>
        {flowLink}
        <span className="rvn-dim">
          It takes a few minutes. You can keep reviewing meanwhile.
        </span>
      </div>
    );
  if (run.status === "failed")
    return (
      <div className="rvn-call">
        <ErrorBox
          error={{ message: run.error ?? "keel could not look for problems" }}
        />
        {flowLink}
        <button type="button" className="rvn-btn" onClick={start}>
          Try again
        </button>
      </div>
    );
  return (
    <div className="rvn-stack">
      {run.stale && (
        <p className="rvn-warnline">
          New commits since keel looked.{" "}
          <button type="button" className="rvn-link" onClick={start}>
            Look again
          </button>
        </p>
      )}
      <p className="rvn-dim rvn-lead">
        {blocking ? `${blocking} blocking` : "No blocking problem"}
        {" · "}security: {r?.security || "nothing reported"}
        {" · "}tests: {r?.tests || "nothing reported"}
        {r?.depth ? ` · ${r.depth} review` : ""}
        {flowLink ? <> · {flowLink}</> : null}
      </p>
      {!groups.length && (
        <p className="rvn-empty">keel read the change: no problems found.</p>
      )}
      {groups.map((g) => (
        <section key={g.id} className="rvn-pgroup" aria-label={g.label}>
          <h3>
            <span className={`rvn-gdot t-${g.tone}`} aria-hidden="true" />
            {g.label} <span className="rvn-dim">{g.items.length}</span>
          </h3>
          {g.items.map((f) => {
            const dec = ai?.decisions[f.id]?.decision;
            const decided = dec === "dismissed" || dec === "commented";
            return (
              <article
                key={f.id}
                className={`rvn-problem${dec === "dismissed" ? " dim" : ""}`}
                aria-label={f.title}
              >
                <b>{f.title}</b>
                {f.path && (
                  <button
                    type="button"
                    className="rvn-link rvn-mono rvn-where"
                    onClick={() => onShowInFile(f.path!, f.line)}
                  >
                    {whereOf(f.path, f.line)}
                  </button>
                )}
                <span>{f.why}</span>
                {(f.suggestion || f.fix) && (
                  <span className="rvn-dim">
                    Fix: {f.fix || "see the suggested change"}
                  </span>
                )}
                {g.actions && !decided && (
                  <div className="rvn-row">
                    <button
                      type="button"
                      className="rvn-btn"
                      disabled={
                        running.includes("draft") || running.includes("decide")
                      }
                      onClick={() => void comment(f)}
                    >
                      Add as comment
                    </button>
                    {f.severity !== "nit" && !ai?.fixes?.[f.id] &&
                      (fixWith === "ask" ? (
                        (["quick", "small", "change"] as const).map((w) => (
                          <button
                            key={w}
                            type="button"
                            className="rvn-btn"
                            disabled={running.includes(`fix:${f.id}`)}
                            onClick={() => fix(f, w)}
                          >
                            Fix: {w}
                          </button>
                        ))
                      ) : (
                        <button
                          type="button"
                          className="rvn-btn"
                          disabled={running.includes(`fix:${f.id}`)}
                          onClick={() => fix(f)}
                        >
                          Fix it ({fixWith})
                        </button>
                      ))}
                    <button
                      type="button"
                      className="rvn-link quiet"
                      disabled={running.includes("decide")}
                      onClick={() =>
                        void R.decide(
                          pid,
                          reviewKey,
                          f.id,
                          "dismissed",
                          "not a problem here",
                        )
                      }
                    >
                      Dismiss
                    </button>
                  </div>
                )}
                {ai?.fixes?.[f.id] && (
                  <p className="rvn-flow" role="status">
                    The {ai.fixes[f.id].workflow} flow fixes it
                    {(ai.fixes[f.id].with ?? 1) > 1 ? ` with ${(ai.fixes[f.id].with ?? 1) - 1} more in this file` : ""}.{" "}
                    <a href={`#/flow/${encodeURIComponent(ai.fixes[f.id].flow_id)}`}>Open flow ▸</a>
                  </p>
                )}
                {decided && (
                  <span
                    className={`rvn-done${dec === "commented" ? " ok" : ""}`}
                  >
                    {dec === "commented"
                      ? "Added as a pending comment."
                      : "Dismissed: not a problem here."}{" "}
                    {dec === "dismissed" && (
                      <button
                        type="button"
                        className="rvn-link quiet"
                        onClick={() =>
                          void R.decide(pid, reviewKey, f.id, "open")
                        }
                      >
                        Undo
                      </button>
                    )}
                  </span>
                )}
              </article>
            );
          })}
        </section>
      ))}
    </div>
  );
}
