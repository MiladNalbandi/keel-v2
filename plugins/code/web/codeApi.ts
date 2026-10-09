// The Code page's api calls, through keel's own transport (@keel/web-sdk get and post: /api + path, errors as ApiError),
// with the same urls as keel 0.15.1's api.ts:
//   - its own endpoints, keel.api.repo.RepoController in this plugin's api part (/api/projects/{pid}/repo/*);
//   - keel's core endpoints it reads: the code graph index, keel's files and Memory (the knowledge base), unlock, the
//     plugins that are on;
//   - the Git plugin's /git/branch and /git/switch (the branch tab; they answer 409 while Git is off).

import {
  del,
  get,
  getText,
  post,
  put,
  type Commit,
  type Fact,
  type FactKind,
  type FileDiff,
  type IndexStatus,
  type KeelDoc,
  type Memory,
} from "@keel/web-sdk";

export type { IndexStatus };

/** The branch, its base, how far apart they are, the remote, the worktrees and the local branches. */
export type RepoInfo = {
  branch: string;
  base: string;
  ahead: number;
  behind: number;
  remote: string;
  worktrees: { branch: string; path: string }[];
  branches: { name: string; note: string }[];
};
export type TreeNode = {
  path: string;
  name: string;
  depth: number;
  kind: "dir" | "file";
  mark?: "A" | "M" | "D";
  keel: boolean;
  frozen: boolean;
  ac?: string;
};
export type RepoFile = {
  path: string;
  size: number;
  mark?: "A" | "M" | "D";
  frozen: boolean;
  keel: boolean;
  ac?: string | null;
  head: string;
  last_commit?: Commit | null;
  /** v0.5.1: a NUL byte in the first 8 KB; mtime; the keel rule that applies in the active phase. */
  binary?: boolean;
  modified?: number;
  phase?: string;
  bucket?: string;
  verdict?: string;
};
/** v0.5.1 Repo IDE: git status, diffs, a commit's files, search (git grep), the quick-open file list. */
export type Change = {
  path: string;
  from?: string;
  staged?: string;
  unstaged?: string;
  untracked?: boolean;
  conflict?: boolean;
};
export type CommitView = Commit & {
  body: string;
  keel: boolean;
  files: { path: string; status: string; from?: string }[];
};
/** v0.12.0 one local branch against the base (Git plugin): its own commits and the files it changed since it left the
 *  base. */
export type BranchView = {
  name: string;
  base: string | null;
  current: boolean;
  ahead: number;
  behind: number;
  commits: Commit[];
  files: { path: string; status: string; from?: string }[];
  truncated?: boolean;
};
export type SearchMatch = {
  line: number;
  column: number;
  length: number;
  text: string;
  ranges: [number, number][];
};
export type SearchResult = {
  results: { path: string; matches: SearchMatch[] }[];
  matches: number;
  files: number;
  truncated: boolean;
  timed_out: boolean;
  took_ms: number;
};
export type SearchQuery = {
  q: string;
  regex?: boolean;
  case?: boolean;
  word?: boolean;
  include?: string;
  exclude?: string;
  max?: number;
};
export type UpdateFromBase = {
  ok: boolean;
  merged: boolean;
  conflicts: string[];
  output: string;
};
export type Unlock = { path: string; phase: string };
/** Tools › Plugins: a plugin, and whether it is on for this project (the Code page reads only these two). */
export type PluginOn = { name: string; enabled?: boolean };

const e = encodeURIComponent;

/** "?a=1&b=x" without the empty values, written as keel's api.ts writes a query. */
function q(params: Record<string, string | number | undefined | null>): string {
  const s = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  });
  const str = s.toString();
  return str ? "?" + str : "";
}

export const codeApi = {
  // the repo (this plugin's api part)
  repo: (pid: string) => get<RepoInfo>(`/projects/${e(pid)}/repo`),
  tree: (pid: string, depth = 4, dir?: string) =>
    get<TreeNode[]>(`/projects/${e(pid)}/repo/tree${q({ depth, dir })}`),
  file: (pid: string, path: string) =>
    get<RepoFile>(`/projects/${e(pid)}/repo/file${q({ path })}`),
  commits: (pid: string, limit = 30, range?: "branch") =>
    get<Commit[]>(`/projects/${e(pid)}/repo/commits${q({ limit, range })}`),
  rawUrl: (pid: string, path: string) =>
    `/api/projects/${e(pid)}/repo/raw${q({ path })}`,
  raw: (pid: string, path: string) =>
    getText(`/projects/${e(pid)}/repo/raw${q({ path })}`),
  repoFiles: (pid: string) =>
    get<{ files: string[]; truncated: boolean }>(
      `/projects/${e(pid)}/repo/files`,
    ),
  search: (pid: string, s: SearchQuery) =>
    get<SearchResult>(
      `/projects/${e(pid)}/repo/search${q({ q: s.q, regex: s.regex ? "true" : null, case: s.case ? "true" : null, word: s.word ? "true" : null, include: s.include, exclude: s.exclude, max: s.max })}`,
    ),
  changes: (pid: string) => get<Change[]>(`/projects/${e(pid)}/repo/changes`),
  diff: (
    pid: string,
    path: string,
    against: "head" | "base",
    sha?: string,
    branch?: string,
  ) =>
    get<FileDiff>(
      `/projects/${e(pid)}/repo/diff${q({ path, against: sha || branch ? null : against, sha, branch })}`,
    ),
  commit: (pid: string, sha: string) =>
    get<CommitView>(`/projects/${e(pid)}/repo/commit${q({ sha })}`),
  updateFromBase: (pid: string) =>
    post<UpdateFromBase>(`/projects/${e(pid)}/repo/update-from-base`),
  fileHistory: (pid: string, path: string) =>
    get<Commit[]>(`/projects/${e(pid)}/repo/history${q({ path })}`),

  // keel's core: the code graph index, unlock, keel's files and Memory, the plugins that are on
  index: (pid: string) => get<IndexStatus>(`/projects/${e(pid)}/index`),
  rebuildIndex: (pid: string) =>
    post<IndexStatus>(`/projects/${e(pid)}/index/rebuild`),
  unlock: (pid: string, path: string, phase?: string) =>
    post<{ unlocks: Unlock[] }>(
      `/projects/${e(pid)}/unlock`,
      phase ? { path, phase } : { path },
    ),
  keelDocs: (pid: string) => get<KeelDoc[]>(`/projects/${e(pid)}/keel-docs`),
  memory: (pid: string) => get<Memory>(`/projects/${e(pid)}/memory`),
  addFact: (pid: string, f: { title: string; text: string; kind: FactKind }) =>
    post<Fact>(`/projects/${e(pid)}/memory`, f),
  editFact: (
    pid: string,
    fid: string,
    f: { title: string; text: string; kind: FactKind },
  ) => put<Fact>(`/projects/${e(pid)}/memory/${e(fid)}`, f),
  forgetFact: (pid: string, fid: string) =>
    del(`/projects/${e(pid)}/memory/${e(fid)}`),
  plugins: (pid: string) => get<PluginOn[]>(`/projects/${e(pid)}/plugins`),

  // the Git plugin's: a branch's tab reads the branch and switches to it
  gitSwitch: (pid: string, branch: string, create = false) =>
    post<{ branch: string }>(`/projects/${e(pid)}/git/switch`, {
      branch,
      create,
    }),
  gitBranch: (pid: string, name: string) =>
    get<BranchView>(`/projects/${e(pid)}/git/branch${q({ name })}`),
};
