// keel 0.18.65 Settings › Review: the api calls and the words the tab and its tests share. The settings live in the
// project's .keel/config.yml under `review:` (engine runtime/review_config.py); lenses are files (runtime/lenses.py).

import { get, post, put, type Model } from "../../api";

/** default: the project's model · stronger: keel's raise (Opus at high effort for Claude) · or a model it names */
export type ModelChoice = "default" | "stronger" | Model;

export type Reviewer = {
  name: string;
  focus: string;
  model: ModelChoice;
  effort: string | null;
  agent: string | null;
  lenses: string[];
};

export type ReviewConfig = {
  reviewers: Reviewer[];
  checker: { model: ModelChoice; effort: string | null; should_fix: boolean };
  fix: { flow: "quick" | "small" | "change" | "ask"; model: "flow" | Model; run_mode: string | null; one_flow_per_file: boolean };
  final: { model: ModelChoice; effort: string | null };
  drop_correctness: boolean;
  /** 0.18.67 how hard a review looks, and what Find problems runs: the review flow or the classic reviewers */
  depth: Depth;
  find_runs: "harness" | "flow" | "classic";
};

export type Depth = "quick" | "standard" | "full";
export type DepthInfo = { title: string; tokens: string; time: string; what: string };

export type LensRow = {
  name: string;
  source: "keel" | "project";
  path: string;
  title: string;
  description: string;
  checklist: string;
  reviewer: string | null;
};

export type ReviewSetup = {
  configured: boolean;
  stored: Partial<ReviewConfig>;
  config: ReviewConfig;
  defaults: ReviewConfig;
  lenses: LensRow[];
  missing: string[];
  uses: { reviewers: Model[]; checker: Model; final: Model; fix: Model | null };
  models: { default: Model | null; reviewer: Model | null };
  find_text: string;
  ship_lenses: string[];
  ship_lenses_from: string;
  limits: { reviewers: number; efforts: string[]; fix_flows: string[]; run_modes: string[]; depths?: Depth[] };
  depths?: Record<Depth, DepthInfo>;
  /** what every review reads besides the change: the project's written rules, its own lenses, its stacks */
  standards?: { rules: string[]; lenses: string[]; stack: string[] };
  agents: { id: string; label: string; custom: boolean; keel: boolean }[];
};

export type LensFile = { name: string; source: string; path: string; title: string; description: string; checklist: string; text: string };

const R = (pid: string) => `/projects/${encodeURIComponent(pid)}/review-setup`;

export const reviewSetup = {
  view: (pid: string) => get<ReviewSetup>(R(pid)),
  save: (pid: string, patch: Partial<ReviewConfig> | { reset: true }) => put<ReviewSetup>(R(pid), patch),
  newLens: (pid: string, body: { title: string; reviewer: string | null; checklist: string; description?: string }) =>
    post<{ lens: { name: string; path: string }; view: ReviewSetup }>(`${R(pid)}/lenses`, body),
  lens: (pid: string, name: string) => get<LensFile>(`${R(pid)}/lenses/${encodeURIComponent(name)}`),
  saveLens: (pid: string, name: string, text: string) => put<LensFile>(`${R(pid)}/lenses/${encodeURIComponent(name)}`, { text }),
};

export const LETTERS = ["A", "B", "C", "D"];
const WORDS = ["", "One", "Two", "Three", "Four"];

/** "claude · opus · high" */
export function modelText(m: Model | null | undefined): string {
  if (!m) return "the model keel picks";
  return [m.provider, m.model, m.effort].filter(Boolean).join(" · ");
}

/** The lens file name a title gives (the engine's lenses.slug): "Money rounding" → money-rounding. */
export function slug(title: string): string {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48).replace(/-+$/, "");
}

/** What the Review page says above Find problems (the engine's review_config.find_text), for the draft on screen. */
export function findText(reviewers: { name: string }[]): string {
  const n = reviewers.length;
  const who = reviewers.map((r, i) => `${LETTERS[i]}: ${r.name}`).join(", ");
  return `${WORDS[n] ?? n} keel reviewer${n === 1 ? "" : "s"} ${n === 1 ? "reads" : "read"} the change (${who}). Then a fresh ` +
    "run checks every serious claim against the code. Only checked findings are shown here; nothing is posted.";
}

/** A new reviewer when the person asks for one more. */
export const NEW_REVIEWER: Reviewer = {
  name: "your own reviewer",
  focus: "what the other reviewers do not look at",
  model: "default",
  effort: null,
  agent: null,
  lenses: [],
};
