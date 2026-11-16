// 0.16.1 plugin track: the marketplace for workflows on Build › Workflows (docs/plugins/21-workflow-marketplace.md §4):
// the Install review in words (shared with Settings › Plugins), the badge "from marketplace · 1.2.0" and its update,
// the workflow's menu (Update, Remove, Publish to marketplace, Delete), the Delete confirm, the update question (keep my copy or replace) and the
// Publish dialog (download the package; signing and the pull request stay with the person).

import type { Err } from "../marketHttp";
import { useEffect, useRef, useState } from "react";
import { api, errorParts, type Workflow, type WorkflowCheck } from "../api";
import { useApp } from "../state";
import {
  WORKFLOW_CATEGORIES,
  reviewShort,
  slugName,
  workflowMarketApi,
  type PublishInfo,
  type WorkflowReview,
} from "../workflowMarketApi";
import { Drawer, ErrorBox, Loading } from "./ui";


// ---------- the Install review ----------

/** What a workflow does before it runs: agents, MCP servers, shell commands (red), files, keel rules, warnings. */
export function ReviewList({
  review,
  label = "Install review",
}: {
  review: WorkflowReview;
  label?: string;
}) {
  const agents = review.agents ?? [];
  const mcp = review.mcp ?? [];
  const commands = review.commands ?? [];
  return (
    <ul className="perms wfm-review" aria-label={label}>
      <li className="perm">
        <span className="lvl low">steps</span>
        <span>
          <b>{reviewShort(review)}</b>
          <span className="why">
            {review.gates
              ? "A gate waits for a person before the flow goes on."
              : "No gate: no step waits for a person."}
          </span>
        </span>
      </li>
      <li className="perm">
        <span className="lvl low">agents</span>
        <span>
          <b>{agents.length ? `Runs ${agents.join(", ")}` : "Runs no agent"}</b>
        </span>
      </li>
      <li className="perm">
        <span className={`lvl ${mcp.length ? "medium" : "low"}`}>MCP</span>
        <span>
          <b>
            {mcp.length
              ? `Uses the MCP servers ${mcp.join(", ")}`
              : "Uses no MCP server"}
          </b>
        </span>
      </li>
      <li className={`perm${commands.length ? " wfm-cmd" : ""}`}>
        <span className={`lvl ${commands.length ? "high" : "low"}`}>shell</span>
        <span>
          <b>
            {commands.length
              ? `Runs ${commands.length} shell command${commands.length === 1 ? "" : "s"}`
              : "Runs no shell command"}
          </b>
          {commands.length > 0 && (
            <span className="wfm-cmds" aria-label="Shell commands">
              {commands.map((c, i) => (
                <code key={i}>{c}</code>
              ))}
            </span>
          )}
          {commands.length > 0 && (
            <span className="why">
              They run in the project's folder. keel's guard and gates still
              apply.
            </span>
          )}
        </span>
      </li>
      <li className="perm">
        <span className={`lvl ${review.edits_files ? "medium" : "low"}`}>
          files
        </span>
        <span>
          <b>
            {review.edits_files
              ? "Edits files in the project"
              : "Does not edit files"}
          </b>
          {review.edits_files && (
            <span className="why">keel's diff guard checks each edit.</span>
          )}
        </span>
      </li>
      <li className="perm">
        <span className={`lvl ${review.keel_rules ? "low" : "high"}`}>
          rules
        </span>
        <span>
          <b>keel rules {review.keel_rules ? "on" : "off"}</b>
          <span className="why">
            {review.keel_rules
              ? "Spec approval, the test checks and the final review stay locked."
              : "Its flows are not keel-checked: any step can be removed."}
          </span>
        </span>
      </li>
      {(review.warnings ?? []).map((w, i) => (
        <li key={`w${i}`} className="perm">
          <span className="lvl medium">warning</span>
          <span>
            <b>{w}</b>
          </span>
        </li>
      ))}
    </ul>
  );
}

// ---------- Build › Workflows ----------

/** "from marketplace · 1.2.0" and "1.3.0 ready" with Update. Nothing for a workflow that did not come from there. */
export function MarketBadge({
  w,
  onUpdate,
}: {
  w: Workflow;
  onUpdate: () => void;
}) {
  const m = w.market;
  if (!m) return null;
  return (
    <>
      <span className="tag keel" title={`${m.name} from the catalog ${m.catalog}`}>
        from marketplace · {m.version}
      </span>
      {m.update && (
        <>
          <span className="tag warn">{m.update} ready</span>
          <button className="btn sm" type="button" onClick={onUpdate}>
            Update
          </button>
        </>
      )}
    </>
  );
}

export const isTemplate = (w: Workflow) =>
  w.source === "keel" || (!w.source && !w.based_on);

/** A small menu's open state: a click outside or Esc closes it. */
export function useMenu() {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const pick = (fn: () => void) => () => {
    setOpen(false);
    fn();
  };
  return { open, setOpen, box, pick };
}

/** The workflow's menu: Update and Remove for one from the marketplace, Publish and Delete for the person's own.
 *  keel's templates have none. */
export function WorkflowMenu({
  w,
  onUpdate,
  onRemove,
  onPublish,
}: {
  w: Workflow;
  onUpdate: () => void;
  /** Remove (marketplace) or Delete (the person's own): the same confirm, DELETE /api/workflows/{wid} */
  onRemove: () => void;
  onPublish: () => void;
}) {
  const { open, setOpen, box, pick } = useMenu();
  if (isTemplate(w) && !w.market) return null;
  return (
    <div className="wfm-menu" ref={box}>
      <button
        className="btn sm"
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`More for ${w.name}`}
        onClick={() => setOpen((o) => !o)}
      >
        ⋯
      </button>
      {open && (
        <div className="wfm-menu-list" role="menu" aria-label={`${w.name} menu`}>
          {w.market ? (
            <>
              {w.market.update && (
                <button type="button" role="menuitem" onClick={pick(onUpdate)}>
                  Update to {w.market.update}
                </button>
              )}
              <button
                type="button"
                role="menuitem"
                className="mk-danger"
                onClick={pick(onRemove)}
              >
                Remove
              </button>
            </>
          ) : (
            <>
              <button type="button" role="menuitem" onClick={pick(onPublish)}>
                Publish to marketplace
              </button>
              <button
                type="button"
                role="menuitem"
                className="mk-danger"
                onClick={pick(onRemove)}
              >
                Delete
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * "Delete X?" over the page (role alertdialog, like Stop flow): the workflow and its versions go; flows that ran with
 * it keep their history. One from the marketplace goes the marketplace's way (workflowMarketApi.remove), so the
 * Marketplace no longer lists it as installed here. A 409 (a flow of it runs or waits at a gate) shows in the dialog.
 */
export function DeleteWorkflow({
  w,
  onDone,
  onNo,
}: {
  w: Workflow;
  onDone: () => void;
  onNo: () => void;
}) {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const keep = useRef<HTMLButtonElement>(null);
  // onNo is a new arrow on every parent render: use the latest one from a ref and run this once on open.
  const no = useRef(onNo);
  no.current = onNo;
  useEffect(() => {
    keep.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        no.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const yes = async () => {
    setBusy(true);
    setErr(null);
    try {
      if (w.market) await workflowMarketApi.remove(w.id);
      else await api.deleteWorkflow(w.id);
      toast(
        `${w.name} is deleted. Flows that ran with it keep their history.` +
          (w.market ? " You can install it again from the marketplace." : ""),
      );
      onDone();
    } catch (e) {
      setErr(errorParts(e));
      setBusy(false);
    }
  };
  return (
    <div
      className="fl-confirm-back"
      onMouseDown={(e) => e.target === e.currentTarget && !busy && onNo()}
    >
      <div
        className="fl-confirm panel"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="wf-del-h"
        aria-describedby="wf-del-p"
      >
        <h2 id="wf-del-h">Delete {w.name}?</h2>
        <p id="wf-del-p">
          Flows that ran with it keep their history.
          {w.market
            ? ` It came from the marketplace (${w.market.name} ${w.market.version}): you can install it again there.`
            : ""}
        </p>
        {err && <ErrorBox error={err} />}
        <div className="row">
          <button
            className="btn warn"
            type="button"
            disabled={busy}
            onClick={() => void yes()}
          >
            {busy ? "Deleting…" : "Delete"}
          </button>
          <button
            ref={keep}
            className="btn"
            type="button"
            disabled={busy}
            onClick={onNo}
          >
            Keep it
          </button>
        </div>
      </div>
    </div>
  );
}

/** Update to the newer version: keep my copy (default) or replace it. */
export function UpdateDialog({
  pid,
  w,
  onClose,
  onDone,
}: {
  pid: string;
  w: Workflow;
  onClose: () => void;
  onDone: (w: Workflow) => void;
}) {
  const { toast } = useApp();
  const [mode, setMode] = useState<"keep" | "replace">("keep");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Err>(null);
  const to = w.market?.update ?? undefined;
  const update = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await workflowMarketApi.update(pid, w.id, {
        ...(to ? { version: to } : {}),
        mode,
      });
      toast(
        mode === "keep"
          ? `${w.name} is updated to ${to ?? "the new version"}. Your changes stay in a copy.`
          : `${w.name} is updated to ${to ?? "the new version"}.`,
      );
      onDone(r.workflow);
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer
      title={`Update ${w.name}${to ? ` to ${to}` : ""}?`}
      onClose={onClose}
      id="wf-update"
      footer={
        <>
          <button className="btn" type="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="btn primary"
            type="button"
            onClick={() => void update()}
            disabled={busy}
          >
            {busy ? "Updating…" : "Update"}
          </button>
        </>
      }
    >
      <p className="sub" style={{ margin: 0 }}>
        You have {w.market?.version}. If you changed this workflow, what happens
        to your changes?
      </p>
      <div className="field">
        <span className="lab">Your changes</span>
        <label className="radio">
          <input
            type="radio"
            name="wfupd"
            checked={mode === "keep"}
            onChange={() => setMode("keep")}
          />
          <span>
            <b>Keep my copy</b> — your changes stay as a copy; the new version
            comes next to it
          </span>
        </label>
        <label className="radio">
          <input
            type="radio"
            name="wfupd"
            checked={mode === "replace"}
            onChange={() => setMode("replace")}
          />
          <span>
            <b>Replace</b> — the new version takes its place (Roll back still
            has the old one)
          </span>
        </label>
      </div>
      <p className="hint" style={{ margin: 0 }}>
        Running flows keep their version. keel checks the new version's
        signature and review before it installs it.
      </p>
      {err && <ErrorBox error={err} />}
    </Drawer>
  );
}

const PUBLISHER_KEY = "keel2.publisher";
function storedPublisher(): string {
  try {
    return localStorage.getItem(PUBLISHER_KEY) ?? "";
  } catch {
    return "";
  }
}

/** The summary the YAML gives (description: or summary:), if any. */
const yamlSummary = (yaml: string) =>
  yaml.match(/^(?:summary|description):\s*["']?(.+?)["']?\s*$/m)?.[1] ?? "";

/** Publish to marketplace: what the package holds, fix it, download it; then sign it and open a pull request. */
export function PublishDialog({
  pid,
  w,
  onClose,
}: {
  pid: string;
  w: Workflow;
  onClose: () => void;
}) {
  const [info, setInfo] = useState<PublishInfo>(() => ({
    name: slugName(w.name || w.id) || slugName(w.id),
    version: "1.0.0",
    summary: yamlSummary(w.yaml ?? ""),
    category: "other",
    publisher: storedPublisher(),
  }));
  const [check, setCheck] = useState<WorkflowCheck | null>(null);
  const [checkErr, setCheckErr] = useState<Err>(null);
  useEffect(() => {
    let live = true;
    api.checkWorkflow(pid, w.yaml ?? "").then(
      (c) => live && setCheck(c),
      (e) => live && setCheckErr(errorParts(e)),
    );
    return () => {
      live = false;
    };
  }, [pid, w.yaml]);
  const set = (k: keyof PublishInfo) => (v: string) =>
    setInfo((x) => ({ ...x, [k]: v }));
  const nameOk = /^[a-z][a-z0-9-]{1,39}$/.test(info.name);
  const versionOk = /^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$/.test(info.version);
  const ready =
    nameOk && versionOk && !!info.summary.trim() && !!info.publisher.trim();
  const file = `${info.name}-${info.version}.yaml`;
  const href = workflowMarketApi.publishUrl(w.id, {
    ...info,
    summary: info.summary.trim(),
    publisher: info.publisher.trim(),
  });
  const keep = () => {
    try {
      localStorage.setItem(PUBLISHER_KEY, info.publisher.trim());
    } catch {
      /* private window: not kept */
    }
  };
  const review: WorkflowReview | null = check
    ? {
        steps: check.steps,
        gates: check.gates,
        agents: check.agents,
        mcp: check.mcp,
        commands: check.commands,
        edits_files: check.edits_files,
        keel_rules: check.keel_rules,
        warnings: [...check.errors, ...check.warnings],
      }
    : null;
  const field = (
    k: keyof PublishInfo,
    label: string,
    hint?: string,
    bad?: boolean,
  ) => (
    <div className="field">
      <label htmlFor={`pub-${k}`}>{label}</label>
      <input
        id={`pub-${k}`}
        type="text"
        value={info[k]}
        aria-invalid={bad || undefined}
        onChange={(e) => set(k)(e.target.value)}
      />
      {hint && <span className={`hint${bad ? " amber" : ""}`}>{hint}</span>}
    </div>
  );
  return (
    <Drawer title="Publish to marketplace" onClose={onClose} id="wf-publish">
      <p className="sub" style={{ margin: 0 }}>
        keel makes a package of <b>{w.name}</b>: the workflow as{" "}
        <span className="mono">{file}</span> and its marketplace entry{" "}
        <span className="mono">workflows/{info.name}.yml</span>. You sign it
        with your own key: it never goes to keel.
      </p>
      {field(
        "name",
        "Name",
        nameOk
          ? "Its id in the catalog."
          : "Lower case letters, digits and dashes; it starts with a letter.",
        !nameOk,
      )}
      {field(
        "version",
        "Version",
        versionOk ? undefined : "Like 1.0.0.",
        !versionOk,
      )}
      <div className="field">
        <label htmlFor="pub-summary">Summary</label>
        <textarea
          id="pub-summary"
          rows={2}
          value={info.summary}
          placeholder="What it does, in one sentence"
          onChange={(e) => set("summary")(e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="pub-category">Category</label>
        <select
          id="pub-category"
          value={info.category}
          onChange={(e) => set("category")(e.target.value)}
        >
          {WORKFLOW_CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
      {field(
        "publisher",
        "Publisher",
        "Your publisher name in the marketplace (the one your key is listed under).",
      )}
      <div className="field">
        <span className="lab">What it does (the Install review)</span>
        {checkErr ? (
          <ErrorBox error={checkErr} />
        ) : !review ? (
          <Loading what="Checking the workflow" />
        ) : (
          <ReviewList review={review} label="Publish review" />
        )}
        {check && !check.valid && (
          <span className="hint amber">
            keel finds problems in this workflow: fix them before you publish.
          </span>
        )}
      </div>
      <div className="row">
        {ready ? (
          <a
            className="btn primary"
            href={href}
            download={`${info.name}-${info.version}.zip`}
            onClick={keep}
            style={{ textDecoration: "none" }}
          >
            Download the package
          </a>
        ) : (
          <button className="btn primary" type="button" disabled>
            Download the package
          </button>
        )}
        {!ready && (
          <span className="hint">Fill in the name, version, summary and publisher.</span>
        )}
      </div>
      <section className="wfm-next" aria-label="Next steps">
        <span className="lab">Then, on your computer</span>
        <ol>
          <li>
            Unzip it and sign the workflow with your key:
            <pre className="mono">{`keel-plugin workflow lint ${file}\nkeel-plugin sign ${file} --key-file you.key`}</pre>
          </li>
          <li>
            Open a pull request to{" "}
            <a
              href="https://github.com/keel-studio/keel-marketplace"
              target="_blank"
              rel="noreferrer"
            >
              keel-studio/keel-marketplace
            </a>{" "}
            with <span className="mono">workflows/{info.name}.yml</span>, and
            attach <span className="mono">{file}</span> and{" "}
            <span className="mono">{file}.minisig</span> to a release of your
            repo (or add them under{" "}
            <span className="mono">
              workflows/{info.name}/{info.version}.yaml
            </span>
            ).
          </li>
          <li>The check on the pull request runs; a maintainer merges it.</li>
        </ol>
      </section>
    </Drawer>
  );
}
