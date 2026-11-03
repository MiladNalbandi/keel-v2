// 0.18.57 Tasks › Automations in the new Tasks page: three tabs in one drawer, one Save.
// - When Jira moves: what keel does per status (nothing / propose the best flow / start it by itself, with how it runs
//   and a token cap), and how many flows keel may start by itself per day.
// - Jira statuses: the Jira status keel moves the ticket to for each keel status (the Jira plugin's connection; the
//   connection itself, the reviewers and the sync stay in Settings › Connections).
// - KeelBot's tickets: KeelBot's Jira account or label, and whether keel may post its reply by itself.
// The classic page keeps its own drawer (Automations.tsx); both send the same api calls.

import { useEffect, useState } from "react";
import {
  Drawer,
  ErrorBox,
  errorParts,
  Loading,
  settingsHash,
  useApp,
  type RunMode,
} from "@keel/web-sdk";
import {
  TASK_STATUSES,
  tasksApi,
  type AutomationMode,
  type AutomationRule,
  type AutomationSettings,
  type AutomationView,
  type JiraFound,
  type JiraLink,
  type JiraUser,
  type TaskStatus,
} from "./tasksApi";
import { RUN_WORDS, STATUS_WORDS } from "./taskModel";

type Err = { message: string; hint?: string };
type Tab = "rules" | "statuses" | "bot";
const TABS: [Tab, string][] = [
  ["rules", "When Jira moves"],
  ["statuses", "Jira statuses"],
  ["bot", "KeelBot's tickets"],
];

const MODE_WORDS: Record<AutomationMode, string> = {
  off: "Nothing",
  propose: "Propose the best flow (ask me)",
  start: "Start the best flow by itself",
};
const RULE_HELP: Record<string, string> = {
  in_progress: "Someone starts work on it in Jira.",
  in_review:
    "keel reviews its pull request, read only. It posts nothing without you.",
  blocked: "Someone marks it blocked in Jira.",
};
/** The Jira status keel uses when the map names none (the api's TaskStatus.DEFAULT_JIRA). */
const USUAL: Partial<Record<TaskStatus, string>> = {
  todo: "To Do",
  in_progress: "In Progress",
  in_review: "In Review",
  testing_pp: "Testing in PP",
  ready_prod: "Ready for Production",
  done: "Done",
};

/** Jira statuses: the map, "Find statuses in Jira", and where the rest of the connection lives. */
function StatusMap({
  link,
  map,
  setMap,
  found,
  onFind,
  finding,
}: {
  link: JiraLink | null | "off";
  map: Partial<Record<TaskStatus, string>>;
  setMap: (m: Partial<Record<TaskStatus, string>>) => void;
  found: JiraFound | null;
  onFind: () => void;
  finding: boolean;
}) {
  if (link === null) return <Loading what="Reading the Jira connection" />;
  if (link === "off" || !link.connected) {
    return (
      <div className="tkn-note" data-testid="jira-off">
        <p>
          This project has no Jira connection, so keel moves no ticket. Tasks
          stay in keel, with their history.
        </p>
        <a className="btn" href={settingsHash("connections")}>
          Connect Jira in Settings › Connections
        </a>
      </div>
    );
  }
  const names = found?.statuses.map((s) => s.name) ?? [];
  const known = new Set([...names, ...(found?.transitions ?? []).flatMap((x) => [x.name, x.to])].map((n) => n.toLowerCase()));
  /** After "Find statuses in Jira" (B4): why a row will not move the ticket, or that its status is not on this board. */
  const note = (s: TaskStatus, v: string): string | null => {
    if (!found || !names.length) return null;
    if (v === "-" && found.suggested[s] === "-") return `No match on the board${USUAL[s] ? ` (no “${USUAL[s]}”)` : ""}: keel will not move the ticket.`;
    if (v && v !== "-" && !known.has(v.toLowerCase())) return `Jira has no status “${v}” here: a move would fail.`;
    if (!v && USUAL[s] && !known.has(USUAL[s]!.toLowerCase())) return `Jira has no “${USUAL[s]}” here: pick a status, or do not move.`;
    return null;
  };
  return (
    <>
      <p className="sub">
        When a task moves in keel, keel moves its Jira ticket to this status.
      </p>
      <div className="tkn-map" role="table" aria-label="Status mapping">
        {TASK_STATUSES.map((s) => {
          const v = map[s] ?? "";
          const opts = [...new Set([...names, ...(v && v !== "-" ? [v] : [])])];
          return (
            <div key={s} className="tkn-map-row" role="row">
              <span role="rowheader">{STATUS_WORDS[s]}</span>
              <span aria-hidden="true" className="sub">
                →
              </span>
              <span role="cell">
                <select
                  aria-label={`Jira status for ${STATUS_WORDS[s]}`}
                  value={v}
                  onChange={(e) => setMap({ ...map, [s]: e.target.value })}
                >
                  <option value="">
                    {USUAL[s]
                      ? `usual: ${USUAL[s]}`
                      : "do not move (not mapped)"}
                  </option>
                  {opts.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                  <option value="-">do not move the ticket</option>
                </select>
                {note(s, v) && <span className="hint tkn-map-note" data-testid={`jira-note-${s}`}>{note(s, v)}</span>}
              </span>
            </div>
          );
        })}
      </div>
      <div className="row">
        <button
          className="btn"
          type="button"
          disabled={finding}
          onClick={onFind}
        >
          {finding ? "Asking Jira…" : "Find statuses in Jira"}
        </button>
        <span className="hint">
          {found
            ? `Jira has ${found.statuses.length} statuses on this board.`
            : "Lists the statuses your board has."}
        </span>
      </div>
      <p className="hint">
        The connection (
        {link.settings.kind === "server" ? "Jira Server" : "Jira Cloud"}
        {link.me ? ` as ${link.me.name}` : ""}), the reviewers and how often
        keel syncs:{" "}
        <a href={settingsHash("connections")}>Settings › Connections ›</a>
      </p>
    </>
  );
}

export function AutomationsPanel({
  pid,
  onClose,
}: {
  pid: string;
  onClose: () => void;
}) {
  const { toast } = useApp();
  const [tab, setTab] = useState<Tab>("rules");
  const [view, setView] = useState<AutomationView | null>(null);
  const [s, setS] = useState<AutomationSettings | null>(null);
  const [err, setErr] = useState<Err | null>(null);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [users, setUsers] = useState<JiraUser[] | null>(null);
  const [link, setLink] = useState<JiraLink | null | "off">(null);
  const [map, setMap] = useState<Partial<Record<TaskStatus, string>>>({});
  const [mapDirty, setMapDirty] = useState(false);
  const [found, setFound] = useState<JiraFound | null>(null);
  const [finding, setFinding] = useState(false);
  useEffect(() => {
    tasksApi.automations(pid).then(
      (v) => {
        setView(v);
        setS(v.settings);
      },
      (e) => setErr(errorParts(e)),
    );
    // the Jira plugin's connection (a 404 when the plugin is off: no map to show)
    tasksApi.jira(pid).then(
      (l) => {
        setLink(l);
        setMap(l.settings?.status_map ?? {});
      },
      () => setLink("off"),
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
  const findStatuses = async () => {
    setFinding(true);
    setErr(null);
    try {
      const d = await tasksApi.jiraStatuses(pid);
      setFound(d);
      // fill the empty rows with Jira's suggestion; what you chose stays
      setMap((m) => ({
        ...d.suggested,
        ...Object.fromEntries(Object.entries(m).filter(([, v]) => v)),
      }));
      setMapDirty(true);
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setFinding(false);
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
      if (mapDirty && link && link !== "off" && link.connected) {
        await tasksApi.saveStatusMap(
          pid,
          Object.fromEntries(
            Object.entries(map).filter(([, x]) => x),
          ) as Partial<Record<TaskStatus, string>>,
        );
      }
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
      className="tkn-drawer tkn-auto"
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
      <p className="sub tkn-auto-intro">
        What keel does by itself when a ticket changes. keel proposes, you
        approve: it asks in the Inbox (and on your phone). It never pushes or
        posts without you.
        {view &&
          !view.tracker &&
          " This project has no Jira connection: only the buttons on a task work."}
      </p>
      <div className="tkn-tabs" role="tablist" aria-label="Automations">
        {TABS.map(([k, label]) => (
          <button
            key={k}
            type="button"
            role="tab"
            id={`tkn-tab-${k}`}
            aria-selected={tab === k}
            aria-controls={`tkn-panel-${k}`}
            onClick={() => setTab(k)}
          >
            {label}
          </button>
        ))}
      </div>
      {!s || !view ? (
        err ? (
          <ErrorBox error={err} />
        ) : (
          <Loading what="Loading the automations" />
        )
      ) : (
        <>
          <div
            role="tabpanel"
            id="tkn-panel-rules"
            aria-labelledby="tkn-tab-rules"
            hidden={tab !== "rules"}
            className="tkn-panel"
          >
            {view.statuses.map((st) => {
              const r = rule(st);
              return (
                <div key={st} className="tkn-rule" data-testid={`rule-${st}`}>
                  <label htmlFor={`tkn-rule-${st}`}>
                    <b>Ticket moves to {STATUS_WORDS[st] ?? st}</b>
                  </label>
                  <span className="sub">{RULE_HELP[st] ?? ""}</span>
                  <select
                    id={`tkn-rule-${st}`}
                    value={r.mode}
                    onChange={(e) =>
                      setRule(st, { mode: e.target.value as AutomationMode })
                    }
                  >
                    {view.modes.map((m) => (
                      <option key={m} value={m}>
                        {MODE_WORDS[m] ?? m}
                      </option>
                    ))}
                  </select>
                  {r.mode === "start" && (
                    <div className="tkn-rule-start">
                      <label className="tkn-runs" htmlFor={`tkn-run-${st}`}>
                        <span className="sub">How it runs</span>
                        <select
                          id={`tkn-run-${st}`}
                          value={(r.run_mode ?? "manual") as RunMode}
                          onChange={(e) =>
                            setRule(st, { run_mode: e.target.value as RunMode })
                          }
                        >
                          {RUN_WORDS.map(([k, label]) => (
                            <option key={k} value={k}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="tkn-runs" htmlFor={`tkn-cap-${st}`}>
                        <span className="sub">Token cap</span>
                        <input
                          id={`tkn-cap-${st}`}
                          type="number"
                          min={1}
                          className="inline-input"
                          value={r.cap_tokens ?? ""}
                          placeholder="no cap"
                          onChange={(e) =>
                            setRule(st, {
                              cap_tokens: e.target.value
                                ? Number(e.target.value)
                                : null,
                            })
                          }
                        />
                      </label>
                      <span className="hint">
                        keel stops the flow at this many tokens.
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
            <label className="tkn-runs" htmlFor="tkn-daily">
              <span className="sub">
                Flows keel may start by itself per day
              </span>
              <input
                id="tkn-daily"
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
                {view.auto_starts_today} today. Above the limit keel asks you
                instead.
              </span>
            </label>
          </div>

          <div
            role="tabpanel"
            id="tkn-panel-statuses"
            aria-labelledby="tkn-tab-statuses"
            hidden={tab !== "statuses"}
            className="tkn-panel"
          >
            <StatusMap
              link={link}
              map={map}
              setMap={(m) => {
                setMap(m);
                setMapDirty(true);
              }}
              found={found}
              onFind={() => void findStatuses()}
              finding={finding}
            />
          </div>

          <div
            role="tabpanel"
            id="tkn-panel-bot"
            aria-labelledby="tkn-tab-bot"
            hidden={tab !== "bot"}
            className="tkn-panel"
          >
            <p>
              Tickets given to KeelBot are keel's own. On each new review
              comment on the pull request keel checks whether the reviewer is
              right, writes a short note on the ticket and asks you what to do.
              {!view.review_watch &&
                " Turn on the Code Review plugin for this."}
            </p>
            <div className="field">
              <label htmlFor="tkn-kb-account">KeelBot's Jira account</label>
              <input
                id="tkn-kb-account"
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
              <label htmlFor="tkn-kb-label">Or a label on the ticket</label>
              <input
                id="tkn-kb-label"
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
          </div>
          {err && <ErrorBox error={err} />}
        </>
      )}
    </Drawer>
  );
}
