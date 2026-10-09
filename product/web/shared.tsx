// Small pieces the Product pages share: the page head, an action runner with its error, the stage tracker, pills.

import { useCallback, useState, type ReactNode } from "react";
import {
  ErrorBox,
  errorParts,
  hashForScreen,
  Pill,
  type PillTone,
} from "@keel/web-sdk";
import { STAGE_LABEL } from "./productApi";

export const TRACK = [
  "brief",
  "impact",
  "decision",
  "plan",
  "delivery",
  "outcome",
] as const;

export const goInitiative = (id?: string) => {
  location.hash = hashForScreen("initiatives", id);
};
export const goTeam = (id?: string) => {
  location.hash = hashForScreen("teams", id);
};

export function ProductHead({
  crumbs,
  title,
  sub,
  actions,
}: {
  crumbs: [string, string?][];
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="top">
      <div>
        <div className="crumb">
          keel Product
          {crumbs.map(([label, href]) => (
            <span key={label}>
              {" "}
              › {href ? <a href={href}>{label}</a> : label}
            </span>
          ))}
        </div>
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

/** Runs one action at a time; keeps its error to show next to the buttons. */
export function useAct() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<{
    message: string;
    hint?: string;
    details?: string[];
  } | null>(null);
  const run = useCallback(
    async <T,>(name: string, fn: () => Promise<T>): Promise<T | undefined> => {
      setBusy(name);
      setError(null);
      try {
        return await fn();
      } catch (e) {
        setError(errorParts(e));
        return undefined;
      } finally {
        setBusy(null);
      }
    },
    [],
  );
  const box = error ? <ErrorBox error={error} /> : null;
  return { busy, run, box, clear: () => setError(null) };
}

export function statusTone(status: string, waiting?: boolean): PillTone {
  if (waiting || status === "waiting") return "warn";
  if (status === "running") return "run";
  if (status === "failed") return "bad";
  if (status === "done") return "ok";
  return "idle";
}

export const STATUS_LABEL: Record<string, string> = {
  new: "new",
  running: "keel works",
  waiting: "waits for you",
  ready: "ready",
  failed: "failed",
  parked: "not now",
  done: "done",
};

export const StatusPill = ({
  status,
  waiting,
}: {
  status: string;
  waiting?: boolean;
}) => (
  <Pill tone={statusTone(status, waiting)}>
    {waiting ? "waits for you" : (STATUS_LABEL[status] ?? status)}
  </Pill>
);

/** Brief → Impact → Decision → Plan → Delivery → Outcome, with where the initiative is. */
export function Tracker({ stage, status }: { stage: string; status: string }) {
  const at =
    stage === "done"
      ? TRACK.length
      : stage === "idea"
        ? -1
        : TRACK.indexOf(stage as (typeof TRACK)[number]);
  return (
    <ol className="pd-track" aria-label="Stages">
      {TRACK.map((s, i) => {
        const state =
          i < at
            ? "done"
            : i === at
              ? status === "failed"
                ? "bad"
                : status === "waiting"
                  ? "wait"
                  : "now"
              : "todo";
        return (
          <li
            key={s}
            className={`pd-step s-${state}`}
            aria-current={i === at ? "step" : undefined}
          >
            <i aria-hidden="true">{state === "done" ? "✓" : i + 1}</i>
            <span>{STAGE_LABEL[s]}</span>
          </li>
        );
      })}
    </ol>
  );
}

export const fmtDate = (iso?: string | null) => (iso ? iso.slice(0, 10) : "—");
export const days = (d?: number[] | null) =>
  !d || !d.length
    ? "—"
    : d[0] === d[d.length - 1]
      ? `${d[0]} d`
      : `${d[0]}–${d[d.length - 1]} d`;
