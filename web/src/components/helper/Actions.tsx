// v0.9.0 KeelBot's buttons (v0.10.0: keel-query and keel-git from the Database and Git plugins: QueryCard, GitCard).
// Since step 3 a block of a kind no core card handles is a plugin's card, from the slot keelbot.card (SlotCard below:
// the Database plugin's keel-query, plugins/db/web/QueryCard.tsx).
// An answer may end with action blocks (engine runtime/keelbot.py): ```keel-start {json}```
// becomes a card that starts a flow when the person presses Start; ```keel-workflow <yaml>``` becomes a card that keel
// checks (POST …/workflows/check) and the person saves (POST …/workflows/import, into a folder if they like). Nothing
// starts or is saved without a press.

import { useEffect, useState } from "react";
import { api, errorParts, type Workflow, type WorkflowCheck } from "../../api";
import { useSlot } from "../../sdk/registry";
import { SLOTS, type KeelbotBlock, type KeelbotCardItem } from "../../sdk/slots";
import { go, useLoad } from "../../state";
import { CodeBlock } from "../Code";
import { Markdown } from "../Markdown";

export type Segment =
  { kind: "text"; text: string } | { kind: "start" | "workflow" | "query" | "git" | "ci"; body: string };

const ACTION = /^\s*(`{3,}|~{3,})\s*keel-(start|workflow|query|git|ci)\s*$/;
const FENCE = /^\s*(`{3,}|~{3,})/;
const closes = (line: string, open: string) => {
  const t = line.trim();
  return t.length >= open.length && t === open[0].repeat(t.length);
};

/** The answer as text and action blocks, in order. A keel block inside another code block stays text. */
export function splitActions(text: string): Segment[] {
  const lines = (text ?? "").replace(/\r\n?/g, "\n").split("\n");
  const out: Segment[] = [];
  let buf: string[] = [];
  const flush = () => {
    if (buf.join("\n").trim()) out.push({ kind: "text", text: buf.join("\n") });
    buf = [];
  };
  let i = 0;
  while (i < lines.length) {
    const a = lines[i].match(ACTION);
    if (a) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !closes(lines[i], a[1])) body.push(lines[i++]);
      i++;
      flush();
      out.push({
        kind: a[2] as "start" | "workflow" | "query" | "git" | "ci",
        body: body.join("\n"),
      });
      continue;
    }
    const f = lines[i].match(FENCE);
    buf.push(lines[i++]);
    if (f) {
      while (i < lines.length && !closes(lines[i], f[1])) buf.push(lines[i++]);
      if (i < lines.length) buf.push(lines[i++]);
    }
  }
  flush();
  return out;
}

type StartSpec = { workflow: string; title: string; request?: string };

function parseStart(body: string): StartSpec | null {
  try {
    const v = JSON.parse(body) as Partial<StartSpec>;
    return v && typeof v.workflow === "string" && v.workflow
      ? {
          workflow: v.workflow,
          title: String(v.title ?? ""),
          request: v.request ? String(v.request) : undefined,
        }
      : null;
  } catch {
    return null;
  }
}

/** "Start a flow": the workflow KeelBot suggests, a title the person can change, and the request every agent gets. */
export function StartCard({ pid, body }: { pid: string; body: string }) {
  const spec = parseStart(body);
  const wfs = useLoad(pid ? `wfs:${pid}` : null, () => api.workflows(pid), {
    live: false,
  });
  const [title, setTitle] = useState(spec?.title ?? "");
  const [busy, setBusy] = useState(false);
  const [started, setStarted] = useState<{ tid: string; title: string } | null>(
    null,
  );
  const [err, setErr] = useState<{
    message: string;
    hint?: string;
    dirty?: boolean;
  } | null>(null);
  if (!spec)
    return (
      <p className="kb-card bad" role="note">
        KeelBot's start button could not be read. Ask it to give the button
        again.
      </p>
    );
  const wf = wfs.data?.find((w) => w.id === spec.workflow);
  const unknown = !!wfs.data && !wf;
  const start = async (allowDirty = false) => {
    setBusy(true);
    setErr(null);
    try {
      const t = await api.startFlow(pid, {
        workflow_id: spec.workflow,
        title: title.trim(),
        request: spec.request,
        ...(allowDirty ? { allow_dirty: true } : {}),
      });
      setStarted({ tid: t.thread_id, title: title.trim() });
    } catch (e) {
      const p = errorParts(e);
      setErr({ ...p, dirty: /uncommitted changes/i.test(p.message) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      className="kb-card"
      aria-label={`Start a flow: ${spec.title || spec.workflow}`}
    >
      <div className="kb-card-h">
        <span className="kb-tag">Start a flow</span>
        <b>{wf?.name ?? spec.workflow}</b>
      </div>
      {started ? (
        <p className="kb-done">
          Started <b>{started.title}</b>.{" "}
          <button
            type="button"
            className="btn sm"
            onClick={() => go("flow", started.tid)}
          >
            Open the flow ▸
          </button>
        </p>
      ) : (
        <>
          <label className="kb-field">
            <span>Title</span>
            <input
              className="inline-input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              aria-label="Title of the flow"
            />
          </label>
          {spec.request && (
            <p className="kb-request">
              <span className="sub">What the agents get:</span> {spec.request}
            </p>
          )}
          {unknown && (
            <p className="kb-err" role="alert">
              This project has no workflow called {spec.workflow}.
            </p>
          )}
          {err && (
            <div className="kb-err" role="alert">
              <b>{err.message}</b>
              {err.hint && <span className="sub"> {err.hint}</span>}
              {err.dirty && (
                <button
                  type="button"
                  className="btn sm"
                  onClick={() => void start(true)}
                  disabled={busy}
                >
                  Start anyway
                </button>
              )}
            </div>
          )}
          <div className="row">
            <button
              type="button"
              className="btn sm primary"
              disabled={busy || !title.trim() || unknown}
              onClick={() => void start()}
            >
              {busy ? "Starting…" : "Start the flow"}
            </button>
            {wf && (
              <button
                type="button"
                className="btn sm ghost"
                onClick={() => go("workflows", wf.id)}
              >
                See the workflow
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n} ${n === 1 ? one : many}`;

/** A workflow KeelBot wrote: keel's check of it, the YAML, and Save (into a folder of the Workflows page). */
export function WorkflowCard({
  pid,
  body,
  onAsk,
}: {
  pid: string;
  body: string;
  onAsk?: (text: string) => void;
}) {
  const [check, setCheck] = useState<WorkflowCheck | null>(null);
  const [checkErr, setCheckErr] = useState<string | null>(null);
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<Workflow | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(
    null,
  );
  useEffect(() => {
    let live = true;
    api.checkWorkflow(pid, body).then(
      (c) => live && setCheck(c),
      (e) => live && setCheckErr(errorParts(e).message),
    );
    return () => {
      live = false;
    };
  }, [pid, body]);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.importWorkflow(pid, {
        yaml: body,
        ...(folder.trim() ? { folder: folder.trim() } : {}),
      });
      setSaved(r.workflow);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const name = check?.name || "New workflow";
  const problems = check && !check.valid ? check.errors : [];
  return (
    <section className="kb-card" aria-label={`New workflow: ${name}`}>
      <div className="kb-card-h">
        <span className="kb-tag">New workflow</span>
        <b>{name}</b>
      </div>
      {!check && !checkErr && <p className="sub">keel checks it…</p>}
      {checkErr && (
        <p className="kb-err" role="alert">
          keel could not check it: {checkErr}
        </p>
      )}
      {check && (
        <p className="sub kb-meta">
          {plural(check.steps, "step")} · {plural(check.gates, "gate")} ·{" "}
          {check.agents.length
            ? `agents: ${check.agents.join(", ")}`
            : "no agents (code steps only, no model runs)"}
          {check.commands.length > 0 && (
            <>
              {" "}
              · runs{" "}
              {check.commands.map((c) => (
                <code key={c}>{c}</code>
              ))}
            </>
          )}
        </p>
      )}
      {check?.warnings.map((w) => (
        <p key={w} className="kb-warn">
          {w}
        </p>
      ))}
      {problems.length > 0 && (
        <div className="kb-err" role="alert">
          <b>keel cannot run it yet:</b>
          <ul>
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
          {onAsk && (
            <button
              type="button"
              className="btn sm"
              onClick={() =>
                onAsk(
                  `keel's check found problems in the workflow "${name}":\n${problems.map((p) => `- ${p}`).join("\n")}\nFix them and give the whole workflow again.`,
                )
              }
            >
              Ask KeelBot to fix it
            </button>
          )}
        </div>
      )}
      <details className="kb-yaml">
        <summary>Show the YAML</summary>
        <CodeBlock text={body} lang="yaml" gutter={false} />
      </details>
      {saved ? (
        <p className="kb-done">
          Saved as <b>{saved.name}</b>
          {saved.folder ? (
            <>
              {" "}
              in the folder <b>{saved.folder}</b>
            </>
          ) : null}
          .{" "}
          <button
            type="button"
            className="btn sm"
            onClick={() => go("workflows", saved.id)}
          >
            Open it ▸
          </button>
        </p>
      ) : (
        <>
          {err && (
            <p className="kb-err" role="alert">
              <b>{err.message}</b>
              {err.hint && <span className="sub"> {err.hint}</span>}
            </p>
          )}
          <div className="row">
            <input
              className="inline-input kb-folder"
              placeholder="Folder (optional)"
              aria-label="Folder for the workflow"
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
            />
            <button
              type="button"
              className="btn sm primary"
              disabled={busy || !check?.valid}
              onClick={() => void save()}
            >
              {busy ? "Saving…" : "Save the workflow"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

function parseJson<T>(body: string): T | null {
  try {
    const v = JSON.parse(body);
    return v && typeof v === "object" ? (v as T) : null;
  } catch {
    return null;
  }
}

/** A block no core card handles (```keel-query```): the card a plugin put in the slot keelbot.card for its kind (the
 *  Database plugin's). Without one (the plugin is not there) the block shows as the code it is. */
export function SlotCard({ pid, block }: { pid: string; block: KeelbotBlock }) {
  const card = useSlot<KeelbotCardItem>(SLOTS.keelbotCard).find((c) => c.kind === block.kind);
  if (!card) return <CodeBlock text={block.body} gutter={false} />;
  const Card = card.component;
  return <Card block={block} pid={pid} />;
}

type GitOp = { op?: string; message?: string; title?: string; body?: string; draft?: boolean; branch?: string; create?: boolean };

/** A git step KeelBot gives as a button (Git plugin): commit, push, open the pull request, switch branch, sync. */
export function GitCard({ pid, body }: { pid: string; body: string }) {
  const spec = parseJson<GitOp>(body);
  const [message, setMessage] = useState(spec?.message ?? "");
  const [title, setTitle] = useState(spec?.title ?? "");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const op = spec?.op ?? "";
  const label: Record<string, string> = {
    commit: "Commit", push: "Push", pr: "Open the pull request", switch: spec?.create ? "Create and switch" : "Switch",
    sync: "Update from the base branch",
  };
  if (!spec || !label[op]) return <p className="kb-card bad" role="note">KeelBot's git button could not be read. Ask it to give it again.</p>;
  const go = async () => {
    setBusy(true);
    setErr(null);
    try {
      if (op === "commit") {
        const r = await api.gitCommit(pid, message);
        setDone(`Committed ${r.sha.slice(0, 7)} ${r.subject} (${r.files.length} file${r.files.length === 1 ? "" : "s"}).`);
      } else if (op === "push") {
        const r = await api.gitPush(pid);
        setDone(`Pushed ${r.branch} (${r.sha.slice(0, 7)}).`);
      } else if (op === "pr") {
        const r = await api.gitOpenPr(pid, { title, body: spec.body ?? "", draft: !!spec.draft });
        setDone(`${r.updated ? "Updated" : "Opened"} the pull request${r.url ? `: ${r.url}` : "."}`);
      } else if (op === "switch") {
        const r = await api.gitSwitch(pid, spec.branch ?? "", !!spec.create);
        setDone(`On ${r.branch} now.`);
      } else {
        const r = await api.gitSync(pid);
        setDone(r.merged ? `Merged ${r.from} into ${r.branch}.` : `${r.branch} already has ${r.from}.`);
      }
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="kb-card" aria-label={`Git: ${label[op]}`}>
      <div className="kb-card-h"><span className="kb-tag">Git</span><b>{label[op]}{op === "switch" ? ` ${spec.branch ?? ""}` : ""}</b></div>
      {op === "commit" && !done && (
        <textarea className="inline-input kb-msg" rows={3} aria-label="Commit message" value={message} onChange={(e) => setMessage(e.target.value)} />
      )}
      {op === "pr" && !done && (
        <>
          <label className="kb-field"><span>Title</span>
            <input className="inline-input" aria-label="Pull request title" value={title} onChange={(e) => setTitle(e.target.value)} /></label>
          {spec.body && <details className="kb-yaml"><summary>Show the body</summary><Markdown text={spec.body} /></details>}
        </>
      )}
      {err && <p className="kb-err" role="alert"><b>{err.message}</b>{err.hint && <span className="sub"> {err.hint}</span>}</p>}
      {done ? <p className="kb-done" role="status">{done}</p> : (
        <div className="row">
          <button type="button" className="btn sm primary" disabled={busy || (op === "commit" && !message.trim()) || (op === "pr" && !title.trim())}
            onClick={() => void go()}>{busy ? "Working…" : label[op]}</button>
          <span className="sub">keel never force-pushes or pushes to main.</span>
        </div>
      )}
    </section>
  );
}

/** A CI step KeelBot gives as a button (CI/CD plugin): fix starts the ci-fix flow on this branch; rerun runs the failed
 *  jobs of the run again. */
export function CiCard({ pid, body }: { pid: string; body: string }) {
  const spec = parseJson<{ op?: string; run?: number }>(body);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ text: string; tid?: string } | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const op = spec?.op ?? "";
  if (!spec || !["fix", "rerun"].includes(op)) return <p className="kb-card bad" role="note">KeelBot's CI button could not be read. Ask it to give it again.</p>;
  const go_ = async () => {
    setBusy(true);
    setErr(null);
    try {
      if (op === "fix") {
        const t = await api.ciFix(pid, spec.run);
        setDone({ text: "The fix flow started: it reads the failure, fixes it, commits, pushes and waits for CI.", tid: t.thread_id });
      } else {
        const run = spec.run ?? (await api.ciRuns(pid)).find((r) => r.failed)?.id;
        if (!run) {
          setDone({ text: "No failed run to start again." });
        } else {
          await api.ciRerun(pid, run);
          setDone({ text: `The failed jobs of run #${run} run again.` });
        }
      }
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  const label = op === "fix" ? "Fix it" : "Run the failed jobs again";
  return (
    <section className="kb-card" aria-label={`CI: ${label}`}>
      <div className="kb-card-h"><span className="kb-tag">CI</span><b>{label}</b>{spec.run ? <span className="sub">run #{spec.run}</span> : null}</div>
      {err && <p className="kb-err" role="alert"><b>{err.message}</b>{err.hint && <span className="sub"> {err.hint}</span>}</p>}
      {done ? (
        <p className="kb-done" role="status">{done.text}{" "}
          {done.tid && <button type="button" className="btn sm" onClick={() => go("flow", done.tid)}>Open the flow ▸</button>}</p>
      ) : (
        <div className="row">
          <button type="button" className="btn sm primary" disabled={busy} onClick={() => void go_()}>{busy ? "Starting…" : label}</button>
          {op === "fix" && <span className="sub">It pushes the fix with the Git plugin, following your push setting.</span>}
        </div>
      )}
    </section>
  );
}
