// v0.8.0 Quality: keel runs its flows on small eval projects (content/evals) with the models you pick, one case at a
// time in run mode auto, and scores each run (0-100: the result, the send-backs, the tokens against the estimate).
// A score that falls 15 points or more since the run before shows as a drop: proof before a prompt or model change ships.

import { useMemo, useState } from "react";
import {
  api,
  errorParts,
  type Model,
  type QualityCase,
  type QualityLine,
  type QualityRun,
  type QualityView,
} from "../api";
import { ModelPicker } from "../components/ModelPicker";
import {
  ErrorBox,
  Loading,
  PageHead,
  Panel,
  Pill,
  type PillTone,
} from "../components/ui";
import { clock, dur, kfmt, modelLabel, provLabel, usd } from "../format";
import { useApp, useLoad } from "../state";
import "../styles/quality.css";

const toneOf = (score: number | null | undefined): PillTone =>
  score == null ? "idle" : score >= 80 ? "ok" : score >= 50 ? "warn" : "bad";
const modelName = (m: Model) => `${provLabel(m.provider)} ${modelLabel(m)}`;
const OUTCOME: Record<string, string> = {
  cap: "out of tokens",
  end: "reached the end",
  stuck: "stuck",
  failed: "failed",
  timeout: "timed out",
  refused: "did not start",
  stopped: "stopped",
};

/** The scores of a line, oldest first, as a small line chart (0-100). */
function Spark({ points }: { points: { score: number }[] }) {
  if (points.length < 2) return <span className="q-sub">one run</span>;
  const w = 120,
    h = 28;
  const xy = points
    .map(
      (p, i) =>
        `${(i / (points.length - 1)) * (w - 4) + 2},${h - 2 - (p.score / 100) * (h - 4)}`,
    )
    .join(" ");
  return (
    <svg
      className="q-spark"
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`Scores: ${points.map((p) => p.score).join(", ")}`}
    >
      <polyline
        points={xy}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Lines({ lines }: { lines: QualityLine[] }) {
  if (!lines.length)
    return (
      <p className="q-sub">
        No run has finished yet. Start one below: the scores of each flow and
        model show here.
      </p>
    );
  return (
    <div className="q-table" role="table" aria-label="Scores by flow and model">
      <div className="q-row q-head" role="row">
        <span role="columnheader">Flow</span>
        <span role="columnheader">Model</span>
        <span role="columnheader">Score</span>
        <span role="columnheader">Change</span>
        <span role="columnheader">Trend</span>
      </div>
      {lines.map((l) => {
        const change =
          l.last != null && l.previous != null ? l.last - l.previous : null;
        return (
          <div
            key={`${l.workflow_id}|${modelName(l.model)}`}
            className={`q-row${l.drop ? " drop" : ""}`}
            role="row"
          >
            <span role="cell" className="mono">
              {l.workflow_id}
            </span>
            <span role="cell">{modelName(l.model)}</span>
            <span role="cell">
              <Pill tone={toneOf(l.last)}>{l.last ?? "—"}</Pill>
            </span>
            <span role="cell">
              {change == null ? (
                <span className="q-sub">—</span>
              ) : (
                <span className={change < 0 ? "q-down" : "q-up"}>
                  {change > 0 ? `+${change}` : change}
                </span>
              )}
              {l.drop && (
                <>
                  {" "}
                  <Pill tone="bad">dropped</Pill>
                </>
              )}
            </span>
            <span role="cell">
              <Spark points={l.points} />
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Cases({ cases }: { cases: QualityCase[] }) {
  return (
    <div className="q-table q-cases" role="table" aria-label="Cases">
      <div className="q-row q-head" role="row">
        <span role="columnheader">Case</span>
        <span role="columnheader">Flow · model</span>
        <span role="columnheader">Result</span>
        <span role="columnheader">Score</span>
        <span role="columnheader">Used</span>
      </div>
      {cases.map((c) => (
        <div key={c.id} className="q-row" role="row">
          <span role="cell">
            <b>{c.title}</b> <span className="q-sub">{c.eval_set}</span>
          </span>
          <span role="cell">
            <span className="mono">{c.workflow_id}</span> · {modelName(c.model)}
          </span>
          <span role="cell">
            {c.status === "queued" ? (
              <span className="q-sub">waiting its turn</span>
            ) : c.status === "running" ? (
              <Pill tone="run">running</Pill>
            ) : (
              <>
                {OUTCOME[c.outcome ?? ""] ?? c.outcome}
                {c.reached && c.outcome !== "end" ? (
                  <span className="q-sub"> · {c.reached}</span>
                ) : null}
                {c.outcome === "end" && c.reached ? (
                  <span className="q-sub"> · {c.reached}</span>
                ) : null}
              </>
            )}
          </span>
          <span role="cell">
            {c.score != null ? (
              <Pill tone={toneOf(c.score)}>{c.score}</Pill>
            ) : (
              "—"
            )}
          </span>
          <span role="cell" className="q-sub">
            {c.tokens != null ? `${kfmt(c.tokens)} tokens` : ""}
            {c.cost_usd ? ` · ${usd(c.cost_usd)}` : ""}
            {c.ms ? ` · ${dur(c.ms)}` : ""}
            {c.sendbacks ? ` · ${c.sendbacks} sent back` : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

function RunPanel({ run, onStop }: { run: QualityRun; onStop?: () => void }) {
  const done = run.cases.filter(
    (c) => c.status === "done" || c.status === "stopped",
  ).length;
  const active = run.status === "queued" || run.status === "running";
  return (
    <Panel
      title={active ? "The run now" : `Run of ${clock(run.created_at, false)}`}
      extra={
        active && onStop ? (
          <button className="btn sm" type="button" onClick={onStop}>
            Stop the run
          </button>
        ) : (
          <Pill tone={run.status === "done" ? "ok" : "warn"}>{run.status}</Pill>
        )
      }
    >
      <p className="q-sub">
        {run.trigger === "nightly" ? "Nightly run" : "Started by hand"} · {done}{" "}
        of {run.cases.length} cases done
        {run.error ? ` · ${run.error}` : ""}
      </p>
      {run.scores.length > 0 && (
        <ul className="q-scores">
          {run.scores.map((s) => (
            <li key={`${s.workflow_id}|${modelName(s.model)}`}>
              <span className="mono">{s.workflow_id}</span> ·{" "}
              {modelName(s.model)}:{" "}
              <Pill tone={toneOf(s.score)}>{s.score}</Pill>
              <span className="q-sub">
                {" "}
                {s.reached_end} of {s.cases} reached the end · {kfmt(s.tokens)}{" "}
                tokens
              </span>
            </li>
          ))}
        </ul>
      )}
      <Cases cases={run.cases} />
    </Panel>
  );
}

function StartPanel({
  view,
  onStarted,
}: {
  view: QualityView;
  onStarted: () => void;
}) {
  const { toast } = useApp();
  const good = view.sets.filter((s) => !s.problems.length);
  const broken = view.sets.filter((s) => s.problems.length);
  const flowChoices = useMemo(
    () => [
      ...new Set([
        "change",
        "feature",
        "fix",
        ...good.flatMap((s) => s.cases.map((c) => c.workflow)),
      ]),
    ],
    [good],
  );
  const [flows, setFlows] = useState<string[]>(
    view.schedule.flows.length ? view.schedule.flows : ["change"],
  );
  const [a, setA] = useState<Model>(
    view.schedule.models[0] ?? {
      provider: "claude",
      mode: "subscription",
      model: "haiku",
    },
  );
  const [two, setTwo] = useState(view.schedule.models.length > 1);
  const [b, setB] = useState<Model>(
    view.schedule.models[1] ?? {
      provider: "claude",
      mode: "subscription",
      model: "sonnet",
    },
  );
  const [sets, setSets] = useState<string[]>(good.map((s) => s.name));
  const [nightly, setNightly] = useState(view.schedule.enabled);
  const [at, setAt] = useState(view.schedule.at);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(
    null,
  );
  const models = two ? [a, b] : [a];
  const cases =
    good
      .filter((s) => sets.includes(s.name))
      .reduce((n, s) => n + s.cases.length, 0) *
    flows.length *
    models.length;
  const toggle = (list: string[], v: string) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v];

  const start = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.qualityStart({ flows, models, sets });
      toast(
        `The quality run started: ${cases} case${cases === 1 ? "" : "s"}, one at a time.`,
      );
      onStarted();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const saveNightly = async (enabled: boolean) => {
    setErr(null);
    try {
      await api.qualitySchedule({ enabled, at, flows, models });
      setNightly(enabled);
      toast(
        enabled
          ? `Every night at ${at} UTC keel runs these flows and models.`
          : "No nightly run.",
      );
      onStarted();
    } catch (e) {
      setErr(errorParts(e));
    }
  };

  return (
    <Panel title="Run the eval cases">
      <div className="q-form">
        <fieldset className="q-field">
          <legend>Flows</legend>
          {flowChoices.map((f) => (
            <label key={f} className="q-check">
              <input
                type="checkbox"
                checked={flows.includes(f)}
                onChange={() => setFlows((x) => toggle(x, f))}
              />{" "}
              {f}
            </label>
          ))}
        </fieldset>
        <fieldset className="q-field">
          <legend>Eval sets</legend>
          {good.map((s) => (
            <label key={s.name} className="q-check">
              <input
                type="checkbox"
                checked={sets.includes(s.name)}
                onChange={() => setSets((x) => toggle(x, s.name))}
              />{" "}
              {s.name}{" "}
              <span className="q-sub">
                {s.cases.length} case{s.cases.length === 1 ? "" : "s"} ·{" "}
                {s.description}
              </span>
            </label>
          ))}
          {broken.map((s) => (
            <p key={s.name} className="q-sub q-down">
              {s.name} is broken: {s.problems.join("; ")}
            </p>
          ))}
        </fieldset>
        <div className="q-field">
          <label htmlFor="q-model-a">Model</label>
          <ModelPicker
            id="q-model-a"
            value={a}
            onChange={setA}
            effort={false}
          />
          <label className="q-check">
            <input
              type="checkbox"
              checked={two}
              onChange={(e) => setTwo(e.target.checked)}
            />{" "}
            Compare with a second model
          </label>
          {two && (
            <>
              <label htmlFor="q-model-b">Second model</label>
              <ModelPicker
                id="q-model-b"
                value={b}
                onChange={setB}
                effort={false}
              />
            </>
          )}
        </div>
      </div>
      <p className="q-sub">
        {cases} case{cases === 1 ? "" : "s"} will run, one at a time, each in a
        fresh copy of its project, in run mode auto (keel approves what it can
        and never opens a PR). Their tokens count in the budget.
      </p>
      {err && <ErrorBox error={err} />}
      <div className="row">
        <button
          className="btn primary"
          type="button"
          disabled={busy || !cases || !!view.active}
          onClick={() => void start()}
        >
          {busy ? "Starting…" : view.active ? "A run is on" : "Run now"}
        </button>
        <label className="q-check q-night">
          <input
            type="checkbox"
            checked={nightly}
            onChange={(e) => void saveNightly(e.target.checked)}
          />{" "}
          Every night at
        </label>
        <input
          className="q-at"
          aria-label="Nightly time (UTC)"
          value={at}
          onChange={(e) => setAt(e.target.value)}
          onBlur={() => nightly && void saveNightly(true)}
        />
        <span className="q-sub">
          UTC
          {view.schedule.last_run_date
            ? ` · last nightly run ${view.schedule.last_run_date}`
            : ""}
        </span>
      </div>
    </Panel>
  );
}

export function QualityPage() {
  const { toast } = useApp();
  const r = useLoad("quality", () => api.quality());
  const view = r.data;
  const drops = (view?.lines ?? []).filter((l) => l.drop);
  const past = (view?.runs ?? [])
    .filter((x) => x.id !== view?.active?.id)
    .slice(0, 5);
  const stop = async (id: string) => {
    try {
      await api.qualityStop(id);
      toast("The run stops after the case that runs now.");
      await r.reload();
    } catch (e) {
      toast(errorParts(e).message);
    }
  };
  return (
    <>
      <PageHead
        title="Quality"
        sub="keel runs its flows on small eval projects with the models you pick and scores every run, so a change to a prompt, an agent or a model shows up before it ships."
      />
      {r.error && !view ? (
        <ErrorBox error={r.error} onRetry={() => void r.reload()} />
      ) : !view ? (
        <Loading what="Loading the scores" />
      ) : (
        <>
          {drops.length > 0 && (
            <div className="wbar warn q-drop" role="alert">
              <span>
                <b>A score dropped.</b>{" "}
                {drops
                  .map(
                    (l) =>
                      `${l.workflow_id} · ${modelName(l.model)} went from ${l.previous} to ${l.last}`,
                  )
                  .join("; ")}{" "}
                since the run before. Check the last change to a prompt, an
                agent, a skill or a model.
              </span>
            </div>
          )}
          <Panel title="Scores">
            <Lines lines={view.lines} />
          </Panel>
          {view.active && (
            <RunPanel
              run={view.active}
              onStop={() => void stop(view.active!.id)}
            />
          )}
          <StartPanel
            key={view.schedule.at + String(view.schedule.enabled)}
            view={view}
            onStarted={() => void r.reload()}
          />
          {past.map((run) => (
            <RunPanel key={run.id} run={run} />
          ))}
        </>
      )}
    </>
  );
}

