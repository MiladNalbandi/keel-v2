// The Git plugin's api calls (keel.api.plugins.GitPluginController, /api/projects/{pid}/git/*), through keel's own
// transport (@keel/web-sdk get and post: /api + path, errors as ApiError). The Code page's branch tab (the Code plugin's,
// plugins/code) reads /git/branch and switches with /git/switch through its own calls (plugins/code/web/codeApi.ts).

import { get, post } from "@keel/web-sdk";

/** The branch against its remote and its base, and the uncommitted files. */
export type GitStatus = {
  branch: string | null;
  base: string | null;
  upstream: string | null;
  ahead: number;
  behind: number;
  base_ahead: number;
  base_behind: number;
  pushed: boolean;
  changes: { path: string; status: string }[];
};

/** A local branch against the base. */
export type GitBranch = {
  name: string;
  current: boolean;
  ahead: number;
  behind: number;
  date: string;
  subject: string;
};

/** The branch's pull request on GitHub: its checks and review comments. */
export type PullRequest = {
  number: number;
  title: string;
  url: string;
  state: string;
  draft?: boolean;
  review?: string | null;
  base?: string;
  branch?: string;
  checks: { name: string; state: string; url: string }[];
  checks_done: number;
  checks_failed: number;
  comments: {
    author: string;
    body: string;
    path?: string | null;
    line?: number | null;
  }[];
};

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/git`;

export const gitApi = {
  status: (pid: string) => get<GitStatus>(`${base(pid)}/status`),
  branches: (pid: string) => get<GitBranch[]>(`${base(pid)}/branches`),
  pr: (pid: string) => get<{ pr: PullRequest | null }>(`${base(pid)}/pr`),
  switch: (pid: string, branch: string, create = false) =>
    post<{ branch: string }>(`${base(pid)}/switch`, { branch, create }),
  commit: (pid: string, message: string) =>
    post<{ sha: string; subject: string; files: string[] }>(
      `${base(pid)}/commit`,
      { message },
    ),
  sync: (pid: string) =>
    post<{ merged: boolean; from: string; branch: string }>(
      `${base(pid)}/sync`,
    ),
  push: (pid: string) =>
    post<{ branch: string; sha: string }>(`${base(pid)}/push`),
  openPr: (
    pid: string,
    body: { title: string; body: string; draft?: boolean },
  ) =>
    post<{ url?: string; number?: number; updated: boolean }>(
      `${base(pid)}/pr`,
      body,
    ),
};
