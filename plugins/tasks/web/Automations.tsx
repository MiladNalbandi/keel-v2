// 0.18.52 task automations (docs/plans/task-automations.md): keel proposes, the person approves.
// - AutomationItem: a proposal ("I want to run the fix flow on SHOPS-1234", why, questions, Start / Use another flow /
//   Not now) or a KeelBot-owned ticket's review comment (Use keel's reply / Address it / I'll handle it). The Inbox card
//   and the task drawer both show it.
// - AutomationsDrawer: the project's rules per status, KeelBot's Jira account or label, and the switches.

import { useEffect, useMemo, useState } from "react";
import {
  answersOf,
  ClarifyForm,
  clearDraft,
  DraftKept,
  noKeys,
  useAnswerDraft,
  Drawer,
  ErrorBox,
  errorParts,
  Loading,
  RunModePicker,
  useApp,
  type ClarifyAnswers,
  type ClarifyQuestion,
  type RunMode,
  type Workflow,
} from "@keel/web-sdk";
import {
  STATUS_LABEL,
  tasksApi,
  type AutomationMode,
  type AutomationRule,
  type AutomationSettings,
  type AutomationView,
  type ItemActBody,
  type JiraUser,
} from "./tasksApi";

/** The item kinds keel's automations open, with their label in the Inbox (pill and Kind filter). */
/** 0.18.64 where a proposal's unsent answers are kept: the same for the Inbox card, the task drawer and the board, so
 *  an answer begun in one shows in the others. */
export const proposalDraft = (pid: string, itemId: number | string) => ({ project: pid, thread: `task-item-${itemId}`, question: "proposal" });

export const AUTOMATION_KINDS: Record<string, string> = {
  "task-proposal": "keel proposes",
  "task-review": "review comment",
};

const VERDICT: Record<string, string> = {
  reviewer_right: "the reviewer is right",
  author_right: "the code is fine as it is",
  partly: "the reviewer is partly right",
  unclear: "not clear",
};

type Action = { id: string; label: string };

/** The questions as the card's form takes them (the api sends {label} options; older items may hold plain strings). */
export function questionsOf(raw: unknown): ClarifyQuestion[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((q) => q && typeof q === "object")
    .map((q) => {
      const o = q as Record<string, unknown>;
      const options = (Array.isArray(o.options) ? o.options : []).map((x) =>
        typeof x === "string" ? { label: x } : (x as { label: string }),
      );
      return {
        id: String(o.id ?? ""),
        question: String(o.question ?? ""),
        why: o.why ? String(o.why) : undefined,
        options,
        multiple: !!o.multiple,
      };
    });
}

/**
 * A proposal or a review comment: its details, the questions, and the buttons. [onAct] sends the button with the picked
 * flow, the answers or the reply's text; it throws on a refusal (the error shows here).
 */
export function AutomationItem({
  pid,
  kind,
  data,
  actions,
  idBase,
  itemId,
  onAct,
}: {
  pid: string;
  kind: string;
  data: Record<string, unknown>;
  actions: Action[];
  idBase: string;
  /** 0.18.64 the task item's id: its unsent answers are kept under it (none: not kept) */
  itemId?: number;
  onAct: (action: string, b: ItemActBody) => Promise<void>;
}) {
  const questions = questionsOf(data.questions);
  const place = useMemo(() => (itemId == null ? null : proposalDraft(pid, itemId)), [pid, itemId]);
  const [picked, setPicked, keptPicked] = useAnswerDraft<ClarifyAnswers>(place, "picked", {}, noKeys);
  const [typed, setTyped, keptTyped] = useAnswerDraft<ClarifyAnswers>(place, "typed", {});
  const [other, setOther] = useState(false);
  const [wfs, setWfs] = useState<Workflow[] | null>(null);
  const [wid, setWid] = useState("");
  const [reply, setReply] = useState(String(data.reply ?? ""));
  const [allowDirty, setAllowDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(
    null,
  );
  useEffect(() => {
    if (!other || wfs) return;
    tasksApi.workflows(pid).then(
      (l) => {
        setWfs(l);
        setWid(l.find((w) => w.id !== data.workflow_id)?.id ?? l[0]?.id ?? "");
      },
      (e) => setErr(errorParts(e)),
    );
  }, [other, wfs, pid, data.workflow_id]);

  const send = async (action: string) => {
    if (action === "other" && !other) {
      setOther(true);
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const answers = answersOf(questions, picked, typed);
      await onAct(action, {
        ...(action === "other" ? { workflow_id: wid } : {}),
        ...((action === "start" || action === "other") &&
        Object.keys(answers).length
          ? { answers }
          : {}),
        ...(action === "reply" &&
        reply.trim() &&
        reply.trim() !== String(data.reply ?? "").trim()
          ? { note: reply.trim() }
          : {}),
        ...(allowDirty ? { allow_dirty: true } : {}),
      });
      // the answers went with the start: nothing to keep
      if (action === "start" || action === "other") clearDraft(place);
    } catch (e) {
      setErr(errorParts(e));
      setBusy(false);
    }
  };
  const dirty = !!err && /uncommitted changes/i.test(err.message);

  return (
    <div className="tk-auto" data-testid="automation-item">
      {kind === "task-proposal" && !data.no_pr && (
        <dl className="kv tk-kv">
          <dt>Flow</dt>
          <dd>
            <span className="mono">{String(data.workflow_id ?? "")}</span>
            {data.fallback ? (
              <span className="hint"> (keel's default)</span>
            ) : null}
          </dd>
          {data.title ? (
            <>
              <dt>Title</dt>
              <dd>{String(data.title)}</dd>
            </>
          ) : null}
          {data.trigger ? (
            <>
              <dt>Because</dt>
              <dd>{String(data.trigger)}</dd>
            </>
          ) : null}
        </dl>
      )}
      {kind === "task-review" && (
        <dl className="kv tk-kv">
          <dt>{String(data.author ?? "Reviewer")}</dt>
          <dd>
            “{String(data.said ?? "")}”
            {data.where ? (
              <span className="hint"> · {String(data.where)}</span>
            ) : null}
          </dd>
          <dt>keel's check</dt>
          <dd>
            {data.verdict
              ? (VERDICT[String(data.verdict)] ?? String(data.verdict))
              : "not checked"}
            {data.why ? ` — ${String(data.why)}` : ""}
          </dd>
        </dl>
      )}
      {kind === "task-proposal" && questions.length > 0 && (
        <ClarifyForm
          questions={questions}
          picked={picked}
          typed={typed}
          onPick={(id, v) => setPicked((p) => ({ ...p, [id]: v }))}
          onType={(id, v) => setTyped((p) => ({ ...p, [id]: v }))}
        />
      )}
      {kind === "task-review" && actions.some((a) => a.id === "reply") && (
        <div className="field">
          <label htmlFor={`${idBase}-reply`}>
            keel's reply (posted on the pull request only when you press Use
            keel's reply)
          </label>
          <textarea
            id={`${idBase}-reply`}
            rows={3}
            value={reply}
            onChange={(e) => setReply(e.target.value)}
          />
        </div>
      )}
      {other && (
        <div className="field">
          <label htmlFor={`${idBase}-wf`}>Another flow</label>
          <select
            id={`${idBase}-wf`}
            value={wid}
            onChange={(e) => setWid(e.target.value)}
            disabled={!wfs}
          >
            {!wfs && <option value="">Loading…</option>}
            {wfs?.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </div>
      )}
      {(dirty || allowDirty) && (
        <label className="chk">
          <input
            type="checkbox"
            checked={allowDirty}
            onChange={(e) => setAllowDirty(e.target.checked)}
          />{" "}
          Start anyway — my uncommitted files stay out of keel's commits
        </label>
      )}
      <div className="row inbox-actions">
        {actions.map((a, n) => (
          <button
            key={a.id}
            type="button"
            className={`btn sm${n === 0 ? " primary" : a.id === "later" || a.id === "handle" ? " ghost" : ""}`}
            disabled={busy || (a.id === "other" && other && !wid)}
            onClick={() => void send(a.id)}
          >
            {a.id === "other" && other ? `Start ${wid || "…"}` : a.label}
          </button>
        ))}
        <DraftKept shown={keptPicked || keptTyped} />
      </div>
      {err && <ErrorBox error={err} />}
    </div>
  );
}

const MODE_LABEL: Record<AutomationMode, string> = {
  off: "Nothing",
  propose: "Propose the best flow (ask me)",
  start: "Start the best flow by itself",
};
const RULE_HELP: Record<string, string> = {
  in_progress: "A ticket moved to an implementation status in Jira.",
  in_review:
    "A ticket moved to review in Jira: keel reviews its pull request (read-only, it posts nothing without you).",
  blocked: "A ticket moved to blocked in Jira.",
};

/** Tasks › Automations: what keel does when a ticket moves in Jira, and KeelBot's tickets. */
export function AutomationsDrawer({
  pid,
  onClose,
}: {
  pid: string;
  onClose: () => void;
}) {
  const { toast } = useApp();
  const [view, setView] = useState<AutomationView | null>(null);
  const [s, setS] = useState<AutomationSettings | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [users, setUsers] = useState<JiraUser[] | null>(null);
  useEffect(() => {
    tasksApi.automations(pid).then(
      (v) => {
        setView(v);
        setS(v.settings);
      },
      (e) => setErr(errorParts(e)),
    );
  }, [pid]);

  const rule = (status: string): AutomationRule =>
    s?.rules[status] ?? { mode: "off" };
  const setRule = (status: string, r: Partial<AutomationRule>) =>
    setS((cur) =>
      cur
        ? {
            ...cur,
            rules: { ...cur.rules, [status]: { ...rule(status), ...r } },
          }
        : cur,
    );
  const find = async () => {
    setErr(null);
    try {
      setUsers(await tasksApi.jiraUsers(pid, q.trim()));
    } catch (e) {
      setErr(errorParts(e));
    }
  };
  const save = async () => {
    if (!s) return;
    setBusy(true);
    setErr(null);
    try {
      const v = await tasksApi.saveAutomations(pid, s);
      setView(v);
      setS(v.settings);
      toast("Automations saved.");
      onClose();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer
      title="Automations"
      onClose={onClose}
      id="tk-automations"
      footer={
        <>
          <button className="btn" type="button" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn primary"
            type="button"
            onClick={() => void save()}
            disabled={busy || !s}
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      {!s || !view ? (
        err ? (
          <ErrorBox error={err} />
        ) : (
          <Loading what="Loading the automations" />
        )
      ) : (
        <>
          <p className="sub">
            keel proposes, you approve: when a ticket moves in Jira, keel reads
            it, picks the best flow and asks you in the Inbox (and on your
            phone). A rule may let it start by itself.
            {!view.tracker &&
              " This project has no Jira connection: only the buttons on a task work."}
          </p>
          <section className="tk-sec" aria-label="Rules">
            <h3>When a ticket moves in Jira</h3>
            {view.statuses.map((st) => {
              const r = rule(st);
              return (
                <div
                  key={st}
                  className="field tk-rule"
                  data-testid={`rule-${st}`}
                >
                  <label htmlFor={`tk-rule-${st}`}>{STATUS_LABEL[st]}</label>
                  <select
                    id={`tk-rule-${st}`}
                    value={r.mode}
                    onChange={(e) =>
                      setRule(st, { mode: e.target.value as AutomationMode })
                    }
                  >
                    {view.modes.map((m) => (
                      <option key={m} value={m}>
                        {MODE_LABEL[m]}
                      </option>
                    ))}
                  </select>
                  <span className="hint">{RULE_HELP[st]}</span>
                  {r.mode === "start" && (
                    <div className="row">
                      <RunModePicker
                        value={(r.run_mode ?? "manual") as RunMode}
                        onChange={(m) => setRule(st, { run_mode: m })}
                      />
                      <label className="row" htmlFor={`tk-cap-${st}`}>
                        <span className="sub">Token cap</span>
                        <input
                          id={`tk-cap-${st}`}
                          type="number"
                          min={1}
                          className="inline-input"
                          value={r.cap_tokens ?? ""}
                          onChange={(e) =>
                            setRule(st, {
                              cap_tokens: e.target.value
                                ? Number(e.target.value)
                                : null,
                            })
                          }
                        />
                      </label>
                    </div>
                  )}
                </div>
              );
            })}
            <div className="field">
              <label htmlFor="tk-daily">
                Flows keel may start by itself per day
              </label>
              <input
                id="tk-daily"
                type="number"
                min={0}
                max={100}
                className="inline-input"
                value={s.daily_auto_starts}
                onChange={(e) =>
                  setS({ ...s, daily_auto_starts: Number(e.target.value) })
                }
              />
              <span className="hint">
                {view.auto_starts_today} today. Above the limit keel asks
                instead.
              </span>
            </div>
          </section>
          <section className="tk-sec" aria-label="KeelBot's tickets">
            <h3>Tickets assigned to KeelBot</h3>
            <p className="sub">
              keel owns these: on each new review comment on the pull request it
              checks whether the reviewer is right, writes a short note on the
              ticket and asks you what to do.
              {!view.review_watch &&
                " Turn on the Code Review plugin for this."}
            </p>
            <div className="field">
              <label htmlFor="tk-kb-account">KeelBot's Jira account</label>
              <input
                id="tk-kb-account"
                className="inline-input"
                value={s.keelbot.account ?? ""}
                placeholder="account id or user name"
                onChange={(e) =>
                  setS({
                    ...s,
                    keelbot: { ...s.keelbot, account: e.target.value },
                  })
                }
              />
              {view.tracker && (
                <div className="row">
                  <input
                    aria-label="Find a Jira user"
                    className="inline-input"
                    value={q}
                    placeholder="Find a Jira user"
                    onChange={(e) => setQ(e.target.value)}
                  />
                  <button
                    className="btn sm"
                    type="button"
                    disabled={q.trim().length < 2}
                    onClick={() => void find()}
                  >
                    Find
                  </button>
                </div>
              )}
              {users &&
                (users.length === 0 ? (
                  <span className="hint">Nobody found.</span>
                ) : (
                  <ul className="tk-users">
                    {users.map((u) => (
                      <li key={u.account_id ?? u.username ?? u.name}>
                        <button
                          className="linkbtn"
                          type="button"
                          onClick={() => {
                            setS({
                              ...s,
                              keelbot: {
                                ...s.keelbot,
                                account: u.account_id ?? u.username ?? u.name,
                              },
                            });
                            setUsers(null);
                          }}
                        >
                          {u.name}
                          {u.email ? ` · ${u.email}` : ""}
                        </button>
                      </li>
                    ))}
                  </ul>
                ))}
            </div>
            <div className="field">
              <label htmlFor="tk-kb-label">Or a label</label>
              <input
                id="tk-kb-label"
                className="inline-input"
                value={s.keelbot.label ?? ""}
                placeholder="keel"
                onChange={(e) =>
                  setS({
                    ...s,
                    keelbot: { ...s.keelbot, label: e.target.value },
                  })
                }
              />
            </div>
            <label className="chk">
              <input
                type="checkbox"
                checked={s.allow_reply}
                onChange={(e) => setS({ ...s, allow_reply: e.target.checked })}
              />{" "}
              Let keel post its reply on the pull request without asking (it
              never pushes or changes code by itself)
            </label>
          </section>
          {err && <ErrorBox error={err} />}
        </>
      )}
    </Drawer>
  );
}
