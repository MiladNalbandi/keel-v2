// 0.18.57 the Tasks page's new look (the board / list of TaskBoard.tsx and the drawer of TaskPanel.tsx): what a task
// shows, without React. The status machine is the api's (TaskMachine.kt): To do → In progress → In review → Testing on PP
// → Ready for release → Done, with Blocked and Cancelled on the side. The board has five columns: Testing holds both PP
// and Ready for release (each card says which), Done holds the cancelled ones too, and Blocked shows only when a task is.

import {
  SIZE_FLOWS,
  testOnlyFiles,
  type PillTone,
  type RunMode,
  type Workflow,
} from "@keel/web-sdk";
import {
  DEFAULT_FLOW,
  type Task,
  type TaskItem,
  type TaskStatus,
} from "./tasksApi";

/** A column of the board, a group of the list. */
export type Group = {
  id: string;
  label: string;
  statuses: TaskStatus[];
  hint?: string;
  dot: string;
};

export const GROUPS: Group[] = [
  {
    id: "blocked",
    label: "Blocked",
    statuses: ["blocked"],
    dot: "bad",
    hint: "The flow stopped. Start a new one, or move it by hand.",
  },
  { id: "todo", label: "To do", statuses: ["todo"], dot: "idle" },
  {
    id: "in_progress",
    label: "In progress",
    statuses: ["in_progress"],
    dot: "run",
  },
  {
    id: "in_review",
    label: "In review",
    statuses: ["in_review"],
    dot: "accent",
  },
  {
    id: "testing",
    label: "Testing",
    statuses: ["testing_pp", "ready_prod"],
    dot: "warn",
    hint: "On PP, then ready for release",
  },
  { id: "done", label: "Done", statuses: ["done", "cancelled"], dot: "ok" },
];

/** The status in plain words (the classic page's STATUS_LABEL says "Testing (PP)" and "Ready"). */
export const STATUS_WORDS: Record<TaskStatus, string> = {
  todo: "To do",
  in_progress: "In progress",
  in_review: "In review",
  testing_pp: "Testing on PP",
  ready_prod: "Ready for release",
  done: "Done",
  cancelled: "Cancelled",
  blocked: "Blocked",
};
export const STATUS_TONE: Record<TaskStatus, PillTone> = {
  todo: "idle",
  in_progress: "run",
  in_review: "met",
  testing_pp: "warn",
  ready_prod: "warn",
  done: "ok",
  cancelled: "idle",
  blocked: "bad",
};

export const nameOf = (t: Task) => t.external_key ?? `task ${t.id.slice(0, 8)}`;
export const keyOf = (t: Task) => t.external_key ?? "local";
export const prNumber = (url: string) =>
  url.match(/\/pull\/(\d+)/)?.[1] ??
  url.match(/\/merge_requests\/(\d+)/)?.[1] ??
  null;
export const prLabel = (url: string) =>
  prNumber(url) ? `PR #${prNumber(url)}` : "PR";

/** The open items a person answers on the task (PP, release, a Jira move by hand) — not keel's proposals. */
export const answerItems = (t: Task) =>
  t.waiting.filter((it) => it.kind === "task" || it.kind === "jira-manual");
export const proposals = (t: Task) =>
  t.waiting.filter((it) => it.kind === "task-proposal");
export const reviewComments = (t: Task) =>
  t.waiting.filter((it) => it.kind === "task-review");

/** The task waits for the person: an open item (PP, release, a proposal, a review comment) or its flow at a gate. */
export const needsYou = (t: Task) =>
  t.waiting.length > 0 || t.flow?.status === "waiting";

/** What it waits for, in a few words (the "N tasks need you" box). */
export function askOf(t: Task): string {
  const it: TaskItem | undefined = answerItems(t)[0] ?? t.waiting[0];
  if (it) {
    if (it.kind === "task")
      return it.stage === "prod"
        ? "confirm the release"
        : it.stage === "pr"
          ? "paste the PR link"
          : "confirm it works on PP";
    if (it.kind === "jira-manual") return "move the ticket by hand";
    if (it.kind === "task-proposal") return "keel proposes a flow";
    if (it.kind === "task-review") return "a review comment";
    return it.title;
  }
  return "its flow waits at a gate";
}

/** The group a status sits in. */
export const groupOf = (s: TaskStatus) =>
  GROUPS.find((g) => g.statuses.includes(s))?.id ?? "todo";

/** The flow's state as a card pill: waits for you, running, stopped. Null: nothing to say. */
export function cardPill(t: Task): { text: string; tone: PillTone } | null {
  if (needsYou(t)) return { text: "waits for you", tone: "warn" };
  if (t.status === "cancelled") return { text: "cancelled", tone: "idle" };
  if (t.status === "blocked") return { text: "blocked", tone: "bad" };
  const f = t.flow;
  if (f?.status === "running")
    return {
      text: `${f.workflow_id ?? "flow"} · running`,
      tone: "run",
    };
  if (
    f &&
    (f.status === "failed" || f.status === "stopped") &&
    ["todo", "in_progress"].includes(t.status)
  ) {
    return {
      text: f.status === "failed" ? "last flow failed" : "last flow stopped",
      tone: "bad",
    };
  }
  return null;
}

/** The card's short meta words after the assignee: on PP / ready for release, the PR and its approvals. */
export function cardMeta(t: Task): string[] {
  const out: string[] = [];
  if (t.status === "testing_pp") out.push("on PP");
  if (t.status === "ready_prod") out.push("ready for release");
  if (t.pr_url) {
    const gh = t.reviewers.filter((r) => r.on === "github");
    const ok = gh.filter((r) => r.state === "approved").length;
    const asked = t.reviewers.some((r) => r.state === "changes_requested");
    out.push(
      asked
        ? `${prLabel(t.pr_url)} · changes asked`
        : gh.length
          ? `${prLabel(t.pr_url)} · ${ok} of ${gh.length} approved`
          : prLabel(t.pr_url),
    );
  }
  if (!out.length && t.priority && t.status === "todo")
    out.push(t.priority.toLowerCase());
  return out;
}

export const isHigh = (t: Task) =>
  /^(high|highest|critical|blocker|p0|p1)$/i.test(t.priority ?? "");

// ---------- keel suggests ----------

/** The steps of keel's own flows in plain words (else: the workflow's step and gate count). */
const STEPS: Record<string, string> = {
  fix: "a failing test → find the cause → the smallest fix → you check",
  feature:
    "a short spec → you approve → build each criterion test-first → reviews → you check",
  change: "1–3 criteria → test-first → you check",
  small: "a short plan → test-first → one review → you check",
  quick: "one agent changes it → the tests → you look once",
  implement: "the code → its tests → done when they pass",
  address:
    "one agent changes the code and its tests → the tests → you look once",
  review:
    "keel reads the pull request → its findings → you decide (it posts nothing)",
  "review-pr":
    "keel reads the pull request → its findings → you decide (it posts nothing)",
};

/** What a flow does, in one line: its steps, or how many steps and gates it has. */
export function stepsOf(id: string, wf?: Workflow | null): string {
  if (STEPS[id]) return STEPS[id];
  const about = SIZE_FLOWS.find((f) => f.id === id)?.about;
  if (about) return about;
  if (wf?.steps?.length) {
    const gates = wf.steps.filter((s) => s.kind === "gate").length;
    return `${wf.steps.length} step${wf.steps.length === 1 ? "" : "s"}, ${gates} of them ask${gates === 1 ? "s" : ""} you`;
  }
  return "";
}

const WHY: Record<string, string> = {
  bug: "This ticket is a bug. A fix flow first writes a test that fails, then makes the smallest change that makes it pass.",
  story:
    "New behaviour for users. A feature flow writes a short spec with criteria first, then builds them one by one.",
  task: "A change to the code. A change flow keeps it to a few criteria, each one test-first.",
};

/**
 * keel's suggestion when nothing was proposed yet: the address flow when the ticket names only test files (0.18.56,
 * the same rule as Start a flow), else the flow the task's type starts (the api's TaskTypes.defaultWorkflow), else
 * feature, else the first one.
 */
export function suggestFor(
  t: Task,
  wfs: Workflow[],
): { id: string; why: string } | null {
  const has = (id: string) => wfs.some((w) => w.id === id);
  const tests = testOnlyFiles(`${t.title}\n${t.description}`);
  if (tests.length && has("address")) {
    return {
      id: "address",
      why: `Only test code changes (${tests.slice(0, 2).join(", ")}${tests.length > 2 ? ", …" : ""}). The address flow changes it and runs the tests, without a test-first step.`,
    };
  }
  const want = DEFAULT_FLOW[t.type];
  if (has(want)) return { id: want, why: WHY[t.type] };
  const id = has("feature") ? "feature" : wfs[0]?.id;
  return id ? { id, why: "keel's default flow for a ticket." } : null;
}

/** How a flow runs, in plain words; the first is keel's default. */
export const RUN_WORDS: [RunMode, string][] = [
  ["manual", "Stop and ask me at each gate"],
  ["important", "Ask me only at the important gates"],
  ["auto", "Run on its own, ask me only at the end"],
  ["readonly", "Read only: agents may not change anything"],
];
export const runWords = (m?: string | null) =>
  RUN_WORDS.find(([k]) => k === m)?.[1] ?? RUN_WORDS[0][1];

/** How far the flow is (0–100) from its current step's place in the workflow; null when keel cannot tell. */
export function progressOf(t: Task, wf?: Workflow | null): number | null {
  const f = t.flow;
  if (!f) return null;
  if (f.status === "done") return 100;
  const steps = wf?.steps ?? [];
  if (!steps.length || !f.current) return null;
  const i = steps.findIndex((s) => s.id === f.current);
  if (i < 0) return null;
  return Math.max(4, Math.min(96, Math.round(((i + 1) / steps.length) * 100)));
}

/** The flow's status as a pill tone and word. */
export function flowTone(status: string): PillTone {
  return status === "running"
    ? "run"
    : status === "waiting"
      ? "warn"
      : status === "failed"
        ? "bad"
        : status === "done"
          ? "ok"
          : "idle";
}
export function flowWords(status: string): string {
  return status === "waiting" ? "waits for you" : status;
}

/** 0.21.4 the tracker a ticket lives in, for what the page says ("Open in GitHub"): its source, else its link. */
export const trackerName = (source?: string | null, url?: string | null): "GitHub" | "Jira" =>
  source === "github" || (!source && /\/issues\/\d+\/?$/.test(url ?? "")) ? "GitHub" : "Jira";
