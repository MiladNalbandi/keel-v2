// v0.15.2 api calls for Code › Source control › Log (like JetBrains' Git log): the branch list and the log with its
// graph. Both only read; they work without the Git plugin (docs/CONTRACT.md, "v0.15.2: the Git log").

import { get } from "./api";

/** A local branch, remote branch or tag. `ahead` / `behind`: a local branch against the base (not on the base itself). */
export type RefItem = {
  name: string;
  sha: string;
  date: string;
  subject: string;
  current: boolean;
  upstream?: string;
  ahead?: number;
  behind?: number;
};

/** GET /repo/refs: `head` is the current branch (null when detached), `base` main or master. */
export type RefsView = {
  head: string | null;
  base: string | null;
  local: RefItem[];
  remote: RefItem[];
  tags: RefItem[];
};

/** A name on a commit. kind: head (a detached HEAD) | local | remote | tag; `current` = HEAD is on it. */
export type LogRef = {
  name: string;
  kind: "head" | "local" | "remote" | "tag";
  current: boolean;
};

/** One commit. `in_base` = the base branch has it already; false = only on the shown branch(es). */
export type LogCommit = {
  sha: string;
  parents: string[];
  subject: string;
  author: string;
  email: string;
  at: string;
  refs: LogRef[];
  in_base: boolean;
  keel: boolean;
};

/** GET /repo/log: `branch` is what it shows (null = all branches); ahead / behind against `base`. */
export type GitLog = {
  branch: string | null;
  head: string | null;
  base: string | null;
  ahead: number;
  behind: number;
  commits: LogCommit[];
  has_more: boolean;
};

/** The log's filters. `branch` blank = the current branch; `q` is text in the message, or a commit id. */
export type LogFilter = {
  branch?: string;
  all?: boolean;
  author?: string;
  q?: string;
  path?: string;
  limit?: number;
  skip?: number;
};

const e = encodeURIComponent;

const query = (f: LogFilter) => {
  const s = new URLSearchParams();
  const put = (k: string, v: string | number | boolean | undefined) => {
    if (v !== undefined && v !== "" && v !== false) s.set(k, String(v));
  };
  put("branch", f.all ? undefined : f.branch?.trim());
  put("all", f.all ? "true" : undefined);
  put("author", f.author?.trim());
  put("q", f.q?.trim());
  put("path", f.path?.trim());
  put("limit", f.limit);
  put("skip", f.skip || undefined);
  const str = s.toString();
  return str ? `?${str}` : "";
};

export const gitLogApi = {
  refs: (pid: string) => get<RefsView>(`/projects/${e(pid)}/repo/refs`),
  log: (pid: string, f: LogFilter = {}) =>
    get<GitLog>(`/projects/${e(pid)}/repo/log${query(f)}`),
};
