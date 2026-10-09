// v0.10.0 KeelBot's git button (the Git plugin, moved from keel's components/helper/Actions.tsx): an answer's
// ```keel-git {json}``` block becomes a card the person presses: commit, push, open the pull request, switch branch,
// sync. KeelBot never runs them itself. keel's KeelBot shows it through the slot keelbot.card (kind "keel-git").

import { useState } from "react";
import { errorParts, Markdown, type KeelbotCardProps } from "@keel/web-sdk";
import { gitApi } from "./gitApi";

type GitOp = {
  op?: string;
  message?: string;
  title?: string;
  body?: string;
  draft?: boolean;
  branch?: string;
  create?: boolean;
};

function parseJson<T>(body: string): T | null {
  try {
    const v = JSON.parse(body);
    return v && typeof v === "object" ? (v as T) : null;
  } catch {
    return null;
  }
}

/** A git step KeelBot gives as a button (Git plugin): commit, push, open the pull request, switch branch, sync. */
export function GitCard({ pid, block }: KeelbotCardProps) {
  const spec = parseJson<GitOp>(block.body);
  const [message, setMessage] = useState(spec?.message ?? "");
  const [title, setTitle] = useState(spec?.title ?? "");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(
    null,
  );
  const op = spec?.op ?? "";
  const label: Record<string, string> = {
    commit: "Commit",
    push: "Push",
    pr: "Open the pull request",
    switch: spec?.create ? "Create and switch" : "Switch",
    sync: "Update from the base branch",
  };
  if (!spec || !label[op])
    return (
      <p className="kb-card bad" role="note">
        KeelBot's git button could not be read. Ask it to give it again.
      </p>
    );
  const go = async () => {
    setBusy(true);
    setErr(null);
    try {
      if (op === "commit") {
        const r = await gitApi.commit(pid, message);
        setDone(
          `Committed ${r.sha.slice(0, 7)} ${r.subject} (${r.files.length} file${r.files.length === 1 ? "" : "s"}).`,
        );
      } else if (op === "push") {
        const r = await gitApi.push(pid);
        setDone(`Pushed ${r.branch} (${r.sha.slice(0, 7)}).`);
      } else if (op === "pr") {
        const r = await gitApi.openPr(pid, {
          title,
          body: spec.body ?? "",
          draft: !!spec.draft,
        });
        setDone(
          `${r.updated ? "Updated" : "Opened"} the pull request${r.url ? `: ${r.url}` : "."}`,
        );
      } else if (op === "switch") {
        const r = await gitApi.switch(pid, spec.branch ?? "", !!spec.create);
        setDone(`On ${r.branch} now.`);
      } else {
        const r = await gitApi.sync(pid);
        setDone(
          r.merged
            ? `Merged ${r.from} into ${r.branch}.`
            : `${r.branch} already has ${r.from}.`,
        );
      }
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="kb-card" aria-label={`Git: ${label[op]}`}>
      <div className="kb-card-h">
        <span className="kb-tag">Git</span>
        <b>
          {label[op]}
          {op === "switch" ? ` ${spec.branch ?? ""}` : ""}
        </b>
      </div>
      {op === "commit" && !done && (
        <textarea
          className="inline-input kb-msg"
          rows={3}
          aria-label="Commit message"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
        />
      )}
      {op === "pr" && !done && (
        <>
          <label className="kb-field">
            <span>Title</span>
            <input
              className="inline-input"
              aria-label="Pull request title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          {spec.body && (
            <details className="kb-yaml">
              <summary>Show the body</summary>
              <Markdown text={spec.body} />
            </details>
          )}
        </>
      )}
      {err && (
        <p className="kb-err" role="alert">
          <b>{err.message}</b>
          {err.hint && <span className="sub"> {err.hint}</span>}
        </p>
      )}
      {done ? (
        <p className="kb-done" role="status">
          {done}
        </p>
      ) : (
        <div className="row">
          <button
            type="button"
            className="btn sm primary"
            disabled={
              busy ||
              (op === "commit" && !message.trim()) ||
              (op === "pr" && !title.trim())
            }
            onClick={() => void go()}
          >
            {busy ? "Working…" : label[op]}
          </button>
          <span className="sub">
            keel never force-pushes or pushes to main.
          </span>
        </div>
      )}
    </section>
  );
}
