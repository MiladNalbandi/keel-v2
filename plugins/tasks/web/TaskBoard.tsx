// 0.18.57 the new Tasks page: your tickets as a board (five columns: To do, In progress, In review, Testing, Done; Blocked
// in front when a task is blocked) or as a list grouped by status (always on a phone; the groups fold). On top: what needs
// you, the filters (source, everyone / mine, find), and the Jira sync. A task opens in a drawer (TaskPanel.tsx);
// #/tasks/<id> opens one. "Classic view" goes back to the page of 0.18.56 (Tasks.tsx) for one release.

import { useEffect, useMemo, useState } from "react";
import {
  agoText,
  EmptyState,
  ErrorBox,
  errorParts,
  go,
  settingsHash,
  Loading,
  PageHead,
  Pill,
  useApp,
  useLoad,
  useNarrow,
  useRoute,
  useStored,
} from "@keel/web-sdk";
import { ClassicTasksPage, NewTaskDrawer } from "./Tasks";
import { AutomationsPanel } from "./AutomationsPanel";
import { TaskPanel } from "./TaskPanel";
import { tasksApi, type Task, type TaskList } from "./tasksApi";
import {
  askOf,
  cardMeta,
  cardPill,
  groupOf,
  GROUPS,
  isHigh,
  keyOf,
  nameOf,
  needsYou,
} from "./taskModel";
import "./tasks.css";

type Source = "all" | "jira" | "local";
type Who = "all" | "mine";
type Layout = "board" | "list";

/** A row of buttons that pick one (aria-pressed), like the mockup's segmented controls. */
function Seg<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: [T, string, number?][];
  onChange: (v: T) => void;
}) {
  return (
    <div className="tkn-seg" role="group" aria-label={label}>
      {options.map(([k, text, n]) => (
        <button
          key={k}
          type="button"
          aria-pressed={value === k}
          onClick={() => onChange(k)}
        >
          {text}
          {n !== undefined && <span className="tkn-seg-n"> {n}</span>}
        </button>
      ))}
    </div>
  );
}

function Card({
  t,
  onOpen,
  row,
}: {
  t: Task;
  onOpen: () => void;
  row?: boolean;
}) {
  const pill = cardPill(t);
  const meta = cardMeta(t);
  const who = t.assignee ?? "nobody";
  return (
    <li
      className={`tkn-card${row ? " is-row" : ""}${needsYou(t) ? " is-needs" : ""} s-${t.status}`}
      data-testid="task-card"
    >
      <button
        type="button"
        onClick={onOpen}
        aria-label={`${nameOf(t)}: ${t.title}`}
      >
        <span className="tkn-card-top">
          <span className="mono">{keyOf(t)}</span>
          <span aria-hidden="true">·</span>
          <span>{t.type}</span>
          <span className="tkn-grow" />
          {isHigh(t) && (
            <span className="tkn-high">{(t.priority ?? "").toLowerCase()}</span>
          )}
        </span>
        <span className="tkn-card-title">{t.title}</span>
        {pill && <Pill tone={pill.tone}>{pill.text}</Pill>}
        {t.status === "blocked" && t.blocked_reason && (
          <span className="tkn-reason">{t.blocked_reason}</span>
        )}
        <span className="tkn-card-meta">
          <span className="tkn-avatar" aria-hidden="true">
            {who.slice(0, 1).toUpperCase()}
          </span>
          <span>{[who, ...meta].join(" · ")}</span>
        </span>
      </button>
      {row && (
        <span className="tkn-chev" aria-hidden="true">
          ›
        </span>
      )}
    </li>
  );
}

function SyncLine({
  data,
  busy,
  onSync,
}: {
  data: TaskList;
  busy: boolean;
  onSync: () => void;
}) {
  const s = data.sync;
  return (
    <div className="tkn-sync" data-testid="sync-line">
      <span
        className={`tkn-dot d-${!s.connected ? "idle" : s.last_sync_error ? "bad" : "ok"}`}
        aria-hidden="true"
      />
      {s.connected ? (
        <span className="sub">
          Jira {s.kind === "server" ? "Server" : "Cloud"}
          {s.me ? ` as ${s.me}` : ""} ·{" "}
          {s.last_sync_at
            ? `synced ${agoText(s.last_sync_at)}`
            : "not synced yet"}
          {s.poll_minutes ? `, every ${s.poll_minutes} min` : ", only by hand"}
        </span>
      ) : (
        <span className="sub">
          Not connected to Jira: tasks stay in keel, with their history.{" "}
          <a href={settingsHash("connections")}>Connect Jira</a> in Settings ›
          Connections.
        </span>
      )}
      <button
        className="linkbtn tkn-link"
        type="button"
        onClick={onSync}
        disabled={busy}
      >
        {busy ? "Syncing…" : "Sync now"}
      </button>
      {s.last_sync_error && (
        <span className="badc tkn-sync-err" role="alert">
          Last sync failed: {s.last_sync_error}
        </span>
      )}
    </div>
  );
}

export function TaskBoardPage({
  pid,
  onClassic,
}: {
  pid: string;
  onClassic: () => void;
}) {
  const { toast, reloadProjects } = useApp();
  const { arg } = useRoute();
  const narrow = useNarrow(760);
  const r = useLoad(`tasks:${pid}`, () => tasksApi.list(pid));
  // 0.21.0 a name on a task (from Slack's `assign`, or Jira's assignee): a filter of its own. It is a label, not an account
  const [named, setNamed] = useStored<string>(`keel2.tasks.named.${pid}`, "", {
    valid: (v) => typeof v === "string",
  });
  const [who, setWho] = useStored<Who>(`keel2.tasks.who.${pid}`, "all", {
    valid: (v) => v === "all" || v === "mine",
  });
  const [source, setSource] = useStored<Source>(
    `keel2.tasks.source.${pid}`,
    "all",
    { valid: (v) => v === "all" || v === "jira" || v === "local" },
  );
  const [q, setQ] = useState("");
  const [layout, setLayout] = useStored<Layout>("keel2.tasks.layout", "board", {
    store: "local",
    valid: (v) => v === "board" || v === "list",
  });
  const [folded, setFolded] = useStored<Record<string, boolean>>(
    "keel2.tasks.folded",
    {},
    { valid: (v) => !!v && typeof v === "object" },
  );
  const [making, setMaking] = useState(false);
  const [open, setOpen] = useState<string | null>(arg ?? null);
  const [syncing, setSyncing] = useState(false);
  const [automations, setAutomations] = useState(false);
  useEffect(() => setOpen(arg ?? null), [arg]);

  const all = r.data?.tasks ?? [];
  const me = r.data?.sync.me ?? null;
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return all.filter(
      (t) =>
        (who === "all" || !t.assignee || t.assignee === me) &&
        (!named || t.assignee === named) &&
        (source === "all" || t.source === source) &&
        (!needle ||
          `${t.external_key ?? ""} ${t.title}`.toLowerCase().includes(needle)),
    );
  }, [all, who, source, q, me, named]);
  const names = useMemo(() => [...new Set(all.flatMap((t) => (t.assignee ? [t.assignee] : [])))].sort(), [all]);
  const needy = all.filter(needsYou);
  const list = narrow || layout === "list";
  const groups = GROUPS.filter(
    (g) => g.id !== "blocked" || shown.some((t) => t.status === "blocked"),
  );
  const total = all.length;

  const sync = async () => {
    setSyncing(true);
    try {
      const s = await tasksApi.sync(pid);
      if (!s.ok) toast(`Sync failed: ${s.error ?? "unknown"}`);
      else
        toast(
          s.jira
            ? `Synced: ${s.created} new, ${s.updated} changed, ${s.moved} moved${s.reviews_checked ? `; ${s.reviews_checked} PR(s) read` : ""}${s.comments_checked ? `; ${s.comments_checked} review comment(s) checked` : ""}.`
            : s.reviews_checked
              ? `${s.reviews_checked} PR(s) read, ${s.reviews_moved} approved.`
              : "Nothing to sync: Jira is not connected.",
        );
      await Promise.all([r.reload(), reloadProjects()]);
    } catch (e) {
      toast(errorParts(e).message);
    } finally {
      setSyncing(false);
    }
  };
  const close = () => {
    setOpen(null);
    if (arg) go("tasks");
  };
  const clear = () => {
    setSource("all");
    setWho("all");
    setNamed("");
    setQ("");
  };

  return (
    <div className="tkn-page">
      <PageHead
        title="Tasks"
        sub="Your tickets as a board. Open one to start a flow; keel moves it through review and testing, and asks you at PP and the release."
        actions={
          <>
            <button
              className="btn ghost tkn-classic"
              type="button"
              onClick={onClassic}
              title="The Tasks page as it was before 0.18.57 (for this release)"
            >
              Classic view
            </button>
            <button
              className="btn"
              type="button"
              onClick={() => setAutomations(true)}
            >
              Automations
            </button>
            <button
              className="btn primary"
              type="button"
              onClick={() => setMaking(true)}
            >
              New task
            </button>
          </>
        }
      />
      {automations && (
        <AutomationsPanel pid={pid} onClose={() => setAutomations(false)} />
      )}

      {needy.length > 0 && (
        <section className="tkn-needs" aria-label="Tasks that need you">
          <b className="tkn-needs-h">
            {needy.length} {needy.length === 1 ? "task needs" : "tasks need"}{" "}
            you
          </b>
          <ul>
            {needy.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => setOpen(t.id)}
                  aria-label={`Open ${nameOf(t)}: ${askOf(t)}`}
                >
                  <span className="mono sub">{keyOf(t)}</span>
                  <span className="tkn-needs-t">{t.title}</span>
                  <span className="sub">{askOf(t)}</span>
                  <span className="tkn-link">Open ▸</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="tkn-filters" role="group" aria-label="Filters">
        <Seg
          label="Source"
          value={source}
          onChange={setSource}
          options={[
            ["all", "All", total],
            ["jira", "Jira", all.filter((t) => t.source === "jira").length],
            ["local", "Local", all.filter((t) => t.source === "local").length],
          ]}
        />
        <Seg
          label="Whose tasks"
          value={who}
          onChange={setWho}
          options={[
            ["all", "Everyone"],
            ["mine", "Mine"],
          ]}
        />
        {names.length > 0 && (
          <label className="tkn-named" title="A name on a task is a label, not an account: keel has one person.">
            Assigned to{" "}
            <select value={named} onChange={(e) => setNamed(e.target.value)} aria-label="Assigned to">
              <option value="">anyone</option>
              {names.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        )}
        <label className="tkn-search">
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.75"
            aria-hidden="true"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.5-3.5" />
          </svg>
          <input
            type="search"
            placeholder="Find a task or a key"
            aria-label="Find a task"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        {r.data && (
          <span className="hint tkn-count" aria-live="polite">
            {shown.length === total
              ? `${total} tasks`
              : `${shown.length} of ${total} tasks`}
          </span>
        )}
        <span className="tkn-grow" />
        {!narrow && (
          <Seg
            label="View"
            value={layout}
            onChange={setLayout}
            options={[
              ["board", "Board"],
              ["list", "List"],
            ]}
          />
        )}
      </div>
      {r.data && (
        <SyncLine data={r.data} busy={syncing} onSync={() => void sync()} />
      )}

      {r.error && !r.data ? (
        <ErrorBox error={r.error} onRetry={() => void r.reload()} />
      ) : !r.data ? (
        <Loading what="Loading tasks" />
      ) : !total ? (
        <div className="panel">
          <EmptyState
            title="No task yet"
            action={
              <>
                <button
                  className="btn primary"
                  type="button"
                  onClick={() => setMaking(true)}
                >
                  New task
                </button>
                {r.data.sync.connected ? (
                  <button
                    className="btn"
                    type="button"
                    onClick={() => void sync()}
                  >
                    Sync now
                  </button>
                ) : (
                  <a className="btn" href={settingsHash("connections")}>
                    Connect Jira
                  </a>
                )}
              </>
            }
          >
            Create one, or connect Jira to bring in your tickets. You can still
            start a flow directly on the Flow page.
          </EmptyState>
        </div>
      ) : !shown.length ? (
        <p className="tkn-nomatch">
          No task fits these filters.{" "}
          <button className="linkbtn tkn-link" type="button" onClick={clear}>
            Show all tasks
          </button>
        </p>
      ) : list ? (
        <div className="tkn-list" aria-label="Task list">
          {groups.map((g) => {
            const items = shown.filter((t) => groupOf(t.status) === g.id);
            const isOpen = !folded[g.id];
            return (
              <section
                key={g.id}
                className={`tkn-group g-${g.id}`}
                aria-labelledby={`tkn-g-${g.id}`}
                data-testid={`col-${g.id}`}
              >
                <h2 className="tkn-group-h">
                  <button
                    type="button"
                    id={`tkn-g-${g.id}`}
                    aria-expanded={isOpen}
                    onClick={() => setFolded({ ...folded, [g.id]: isOpen })}
                  >
                    <span className={`tkn-dot d-${g.dot}`} aria-hidden="true" />
                    <span>{g.label}</span>
                    <span className="sub tkn-n">{items.length}</span>
                    <span className="tkn-grow" />
                    <span className="sub" aria-hidden="true">
                      {isOpen ? "▾" : "▸"}
                    </span>
                  </button>
                </h2>
                {isOpen &&
                  (items.length ? (
                    <ul className="tkn-rows">
                      {items.map((t) => (
                        <Card
                          key={t.id}
                          t={t}
                          row
                          onOpen={() => setOpen(t.id)}
                        />
                      ))}
                    </ul>
                  ) : (
                    <p className="tkn-none">Nothing here</p>
                  ))}
              </section>
            );
          })}
        </div>
      ) : (
        <div className="tkn-board" aria-label="Task board">
          {groups.map((g) => {
            const items = shown.filter((t) => groupOf(t.status) === g.id);
            return (
              <section
                key={g.id}
                className={`tkn-col g-${g.id}`}
                aria-labelledby={`tkn-c-${g.id}`}
                data-testid={`col-${g.id}`}
              >
                <h2 id={`tkn-c-${g.id}`} className="tkn-col-h">
                  <span className={`tkn-dot d-${g.dot}`} aria-hidden="true" />
                  <span className="tkn-grow">{g.label}</span>
                  <span className="tkn-n">{items.length}</span>
                </h2>
                {g.hint && <p className="tkn-hint">{g.hint}</p>}
                {items.length ? (
                  <ul className="tkn-cards">
                    {items.map((t) => (
                      <Card key={t.id} t={t} onOpen={() => setOpen(t.id)} />
                    ))}
                  </ul>
                ) : (
                  <p className="tkn-none">Nothing here</p>
                )}
              </section>
            );
          })}
        </div>
      )}
      {making && (
        <NewTaskDrawer
          pid={pid}
          onClose={() => setMaking(false)}
          onMade={(t) => {
            setMaking(false);
            toast(`${nameOf(t)} created.`);
            void r.reload();
            setOpen(t.id);
          }}
        />
      )}
      {open && (
        <TaskPanel
          key={open}
          id={open}
          pid={pid}
          onClose={close}
          onChanged={() => {
            void r.reload();
            void reloadProjects();
          }}
        />
      )}
    </div>
  );
}

/** The Tasks page: the new board, or the classic page when this browser chose "Classic view" (0.18.57, for one release). */
export function TasksPage({ pid }: { pid: string }) {
  const [classic, setClassic] = useStored<boolean>("keel2.tasks.classic", false, { store: "local", valid: (v) => typeof v === "boolean" });
  return classic ? <ClassicTasksPage pid={pid} onNewView={() => setClassic(false)} /> : <TaskBoardPage pid={pid} onClassic={() => setClassic(true)} />;
}
