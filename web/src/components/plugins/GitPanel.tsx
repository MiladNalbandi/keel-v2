// v0.10.0 Code › Source control › Git (the Git plugin): the branch against its remote and its base, the buttons to
// commit, bring the base in, push and switch branches, and the pull request with its CI checks and review comments.
// keel's rules hold here too: never a force push, never a push to main or master, a commit only after the secret check.

import { useState } from "react";
import { api, errorParts, type PullRequest } from "../../api";
import { askAssistant } from "../../sdk/assistant";
import { useApp, useLoad } from "../../state";
import { Pill } from "../ui";

const FAILED = new Set([
  "failure",
  "failed",
  "error",
  "cancelled",
  "timed_out",
  "action_required",
]);
const PASSED = new Set(["success", "neutral", "skipped"]);

export function addressComments(pr: PullRequest): string {
  const lines = pr.comments.map(
    (c) =>
      `- ${c.author}${c.path ? ` (${c.path}${c.line ? `:${c.line}` : ""})` : ""}: ${c.body.replace(/\s+/g, " ").slice(0, 400)}`,
  );
  return `Address the review comments on PR #${pr.number} (${pr.title}):\n${lines.join("\n")}\nSay what you would change for each one, with file:line.`;
}

export function GitPanel({
  pid,
  dirty,
  onChanged,
}: {
  pid: string;
  dirty: number;
  onChanged: () => void;
}) {
  const { toast } = useApp();
  const status = useLoad(`git:${pid}`, () => api.gitStatus(pid));
  const branches = useLoad(`git-branches:${pid}`, () => api.gitBranches(pid), {
    live: false,
  });
  const pr = useLoad(`git-pr:${pid}`, () => api.gitPr(pid), { live: false });
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [newBranch, setNewBranch] = useState<string | null>(null);
  const st = status.data;
  const reload = () => {
    void status.reload();
    void branches.reload();
    void pr.reload();
    onChanged();
  };
  const act = async (what: string, fn: () => Promise<string>) => {
    setBusy(what);
    try {
      toast(await fn());
      reload();
    } catch (e) {
      const p = errorParts(e);
      toast(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(null);
    }
  };
  if (status.error)
    return (
      <div className="gp">
        <p className="db-err">{status.error.message}</p>
      </div>
    );
  if (!st)
    return (
      <div className="gp">
        <span className="sub">Reading git…</span>
      </div>
    );
  const p = pr.data?.pr;
  const onBase = st.branch === st.base;
  return (
    <div className="gp" aria-label="Git">
      <div className="row gp-counts">
        {st.upstream ? (
          <Pill tone={st.ahead ? "run" : "idle"}>↑ {st.ahead} to push</Pill>
        ) : (
          <Pill tone="idle">not pushed yet</Pill>
        )}
        {st.behind > 0 && <Pill tone="warn">↓ {st.behind} to pull</Pill>}
        {!onBase && st.base_behind > 0 && (
          <Pill tone="warn">
            {st.base_behind} behind {st.base}
          </Pill>
        )}
      </div>
      <div className="row">
        {!onBase && (
          <button
            type="button"
            className="btn sm"
            disabled={!!busy || dirty > 0}
            title={dirty ? "Commit or stash your changes first" : undefined}
            onClick={() =>
              void act("sync", async () => {
                const r = await api.gitSync(pid);
                return r.merged
                  ? `Merged ${r.from} into ${r.branch}.`
                  : `${r.branch} already has ${r.from}.`;
              })
            }
          >
            Update from {st.base}
          </button>
        )}
        {!onBase && (
          <button
            type="button"
            className="btn sm primary"
            disabled={!!busy || (!!st.upstream && !st.ahead)}
            onClick={() =>
              void act("push", async () => {
                const r = await api.gitPush(pid);
                return `Pushed ${r.branch} (${r.sha.slice(0, 7)}).`;
              })
            }
          >
            {busy === "push" ? "Pushing…" : "Push"}
          </button>
        )}
        <select
          aria-label="Switch branch"
          value=""
          disabled={!!busy}
          onChange={(e) =>
            e.target.value &&
            void act("switch", async () => {
              const r = await api.gitSwitch(pid, e.target.value);
              return `On ${r.branch} now.`;
            })
          }
        >
          <option value="">Switch branch…</option>
          {(branches.data ?? [])
            .filter((b) => !b.current)
            .map((b) => (
              <option key={b.name} value={b.name}>
                {b.name}
              </option>
            ))}
        </select>
        {newBranch === null ? (
          <button
            type="button"
            className="btn sm ghost"
            onClick={() => setNewBranch("feat/")}
          >
            New branch
          </button>
        ) : (
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              void act("new", async () => {
                const r = await api.gitSwitch(pid, newBranch, true);
                setNewBranch(null);
                return `Created ${r.branch}.`;
              });
            }}
          >
            <input
              className="inline-input mono"
              aria-label="New branch name"
              value={newBranch}
              onChange={(e) => setNewBranch(e.target.value)}
            />
            <button
              type="submit"
              className="btn sm"
              disabled={!!busy || !newBranch.trim()}
            >
              Create
            </button>
            <button
              type="button"
              className="btn sm ghost"
              onClick={() => setNewBranch(null)}
            >
              Cancel
            </button>
          </form>
        )}
      </div>
      {dirty > 0 && (
        <form
          className="gp-commit"
          onSubmit={(e) => {
            e.preventDefault();
            void act("commit", async () => {
              const r = await api.gitCommit(pid, message);
              setMessage("");
              return `Committed ${r.sha.slice(0, 7)} ${r.subject}.`;
            });
          }}
        >
          <textarea
            className="inline-input"
            rows={2}
            aria-label="Commit message"
            placeholder={`Commit message for ${dirty} changed file${dirty === 1 ? "" : "s"}`}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
          <div className="row">
            <button
              type="submit"
              className="btn sm primary"
              disabled={!!busy || !message.trim()}
            >
              Commit
            </button>
            <button
              type="button"
              className="btn sm ghost"
              onClick={() => askAssistant("/commit")}
            >
              Ask KeelBot to write it
            </button>
          </div>
        </form>
      )}
      {p ? (
        <div
          className="gp-pr"
          role="group"
          aria-label={`Pull request #${p.number}`}
        >
          <div className="row">
            <b>
              PR #{p.number} · {p.title}
            </b>
            {p.checks.length > 0 &&
              (p.checks_failed ? (
                <Pill tone="bad">
                  {p.checks_failed} check{p.checks_failed === 1 ? "" : "s"}{" "}
                  failed
                </Pill>
              ) : p.checks_done < p.checks.length ? (
                <Pill tone="run">
                  CI running {p.checks_done}/{p.checks.length}
                </Pill>
              ) : (
                <Pill tone="ok">CI passed</Pill>
              ))}
            {p.comments.length > 0 && (
              <Pill tone="warn">
                {p.comments.length} comment{p.comments.length === 1 ? "" : "s"}
              </Pill>
            )}
            {p.review && (
              <Pill tone={p.review === "APPROVED" ? "ok" : "idle"}>
                {p.review.toLowerCase().replace(/_/g, " ")}
              </Pill>
            )}
          </div>
          <ul className="gp-checks">
            {p.checks.map((c) => (
              <li key={c.name}>
                <span
                  className={`gp-dot ${FAILED.has(c.state) ? "bad" : PASSED.has(c.state) ? "ok" : "run"}`}
                  aria-hidden="true"
                />
                {c.name}: {c.state}
              </li>
            ))}
          </ul>
          {p.comments.slice(-3).map((c, i) => (
            <p key={i} className="sub gp-comment">
              <b>{c.author}</b>
              {c.path ? ` · ${c.path}${c.line ? `:${c.line}` : ""}` : ""}:{" "}
              {c.body.slice(0, 240)}
            </p>
          ))}
          <div className="row">
            <a className="btn sm" href={p.url} target="_blank" rel="noreferrer">
              Open on GitHub
            </a>
            {p.comments.length > 0 && (
              <button
                type="button"
                className="btn sm primary"
                onClick={() => askAssistant(addressComments(p))}
              >
                Ask KeelBot to address the comments
              </button>
            )}
            {p.checks_failed > 0 && (
              <button
                type="button"
                className="btn sm"
                onClick={() => askAssistant("/ci")}
              >
                Why did CI fail?
              </button>
            )}
          </div>
        </div>
      ) : (
        !onBase &&
        st.upstream && (
          <button
            type="button"
            className="btn sm"
            onClick={() => askAssistant("/pr")}
          >
            Ask KeelBot to draft the pull request
          </button>
        )
      )}
      {pr.error && <p className="sub">Pull request: {pr.error.message}</p>}
    </div>
  );
}
