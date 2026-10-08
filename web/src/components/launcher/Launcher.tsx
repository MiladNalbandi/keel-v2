// v0.15.0 the launcher (⌘K on any page, ⇧⇧ in Code): one search box for everything keel knows — what waits for you,
// pull requests, files, code, tasks, flows, pages, actions and projects — and every result's actions (⌘K on a
// result). Keyboard first: ↑↓ move, ↩ opens, ⌘↩ asks KeelBot about the result, ⇥ changes the scope, ⌘1–9 picks.
// A prefix narrows it: > actions, @ code, # pull requests and tasks, ! flows and gates, ? asks KeelBot (read only)
// and shows the answer right here.

import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { api, errorParts, type GraphHit, type HelperSession } from "../../api";
import { navGroups, useFeatures, useView } from "../../addons";
import { repoHash } from "../../pages/repo/model";
import { go, useApp } from "../../state";
import { fileLink } from "../helper/model";
import { Markdown } from "../Markdown";
import { isMac, keyLabel, matches } from "../review/keymap";
// v0.15.4 its keys come from the one list the key cheat sheet shows
import { LAUNCHER } from "../../keys";
import {
  flat,
  groupJump,
  parseQuery,
  PREFIXES,
  rank,
  readRecent,
  remember,
  runs,
  SCOPES,
  type Action,
  type Group,
  type Item,
  type Scope,
} from "./model";
import {
  copyText,
  itemsFor,
  load,
  startGroups,
  symbols,
  type Ctx,
  type Data,
} from "./sources";

export const LAUNCHER_EVENT = "keel:launcher";

/** Open the launcher from anywhere (⇧⇧ in Code), in a scope or with text already typed. */
export function openLauncher(detail: { scope?: Scope; text?: string } = {}) {
  window.dispatchEvent(new CustomEvent(LAUNCHER_EVENT, { detail }));
}

type Props = {
  dark: boolean;
  toggleTheme: () => void;
  toggleNav: () => void;
  openNotes: () => void;
};

/** Mounted once by the shell: ⌘K (Ctrl+K off a Mac) opens and closes it on any page. */
export function Launcher(props: Props) {
  const [open, setOpen] = useState<{
    scope?: Scope;
    text?: string;
    n: number;
  } | null>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (matches(e, LAUNCHER.open)) {
        e.preventDefault();
        setOpen((o) => (o ? null : { n: Date.now() }));
      }
    };
    const onOpen = (e: Event) =>
      setOpen({ ...((e as CustomEvent).detail ?? {}), n: Date.now() });
    window.addEventListener("keydown", onKey);
    window.addEventListener(LAUNCHER_EVENT, onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener(LAUNCHER_EVENT, onOpen);
    };
  }, []);
  if (!open) return null;
  return createPortal(
    <Dialog
      key={open.n}
      {...props}
      start={open}
      onClose={() => setOpen(null)}
    />,
    document.body,
  );
}

const EMPTY: Data = {
  inbox: null,
  prs: null,
  host: null,
  files: null,
  tasks: null,
  flows: null,
  notes: [],
};

type Ask = {
  status: "idle" | "sending" | "running" | "done" | "failed";
  question: string;
  sid?: string;
  call?: string;
  n?: number;
  answer?: string;
  error?: string;
  session?: HelperSession;
};

type Panel = { item: Item; at: number; filter: string; confirm: Action | null };

function Dialog({
  start,
  onClose,
  dark,
  toggleTheme,
  toggleNav,
  openNotes,
}: Props & {
  start: { scope?: Scope; text?: string };
  onClose: () => void;
}) {
  const {
    pid,
    projects,
    setProjectId,
    toast,
    recent: events,
    liveSteps,
  } = useApp();
  const features = useFeatures();
  const view = useView();
  const input = useRef<HTMLInputElement>(null);
  const panelInput = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const first = start.text && PREFIXES[start.text[0]] ? start.text[0] : null;
  const [prefix, setPrefix] = useState<string | null>(first);
  const [text, setText] = useState(
    first ? start.text!.slice(1) : (start.text ?? ""),
  );
  const [scope, setScope] = useState<Scope>(start.scope ?? "all");
  const [at, setAt] = useState(0);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [data, setData] = useState<Data | null>(null);
  const [code, setCode] = useState<GraphHit[]>([]);
  const [ask, setAsk] = useState<Ask>({ status: "idle", question: "" });
  const recentList = useMemo(() => readRecent(), []);

  useEffect(() => {
    let live = true;
    load(pid).then(
      (d) => live && setData(d),
      () => live && setData({ ...EMPTY, notes: ["keel did not answer."] }),
    );
    return () => {
      live = false;
    };
  }, [pid]);

  const q = parseQuery((prefix ?? "") + text, scope);
  const wide = scope === "all" && !prefix;
  // code: the code graph's search, a moment after the typing stops
  const wantCode = !!pid && !q.ask && q.kinds.includes("symbol") && q.text.length >= 2;
  useEffect(() => {
    if (!wantCode) {
      setCode([]);
      return;
    }
    let live = true;
    const t = window.setTimeout(
      () => symbols(pid!, q.text).then((r) => live && setCode(r)),
      160,
    );
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [wantCode, pid, q.text]);

  const focusMain = () => window.setTimeout(() => input.current?.focus(), 0);
  const ctx: Ctx = {
    pid,
    projects,
    setProjectId,
    toast,
    close: onClose,
    dark,
    toggleTheme,
    toggleNav,
    openNotes,
    nav: navGroups(features, view),
    ask: (question) => {
      setPanel(null);
      setPrefix("?");
      setText(question);
      setAsk({ status: "idle", question: "" });
      focusMain();
    },
    setScope: (s) => {
      setScope(s);
      setPrefix(null);
      focusMain();
    },
  };

  const empty = !q.text && !prefix && scope === "all";
  const groups: Group[] = q.ask
    ? []
    : empty
      ? startGroups(ctx, data ?? EMPTY, recentList)
      : rank(
          itemsFor(ctx, data ?? EMPTY, q, code, wide ? 6 : 60),
          q,
          wide ? 5 : 60,
        );
  const rows = flat(groups);
  const sel = rows[Math.min(at, rows.length - 1)] ?? null;
  useEffect(() => setAt(0), [prefix, text, scope]);
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-i="${at}"]`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, [at]);

  const run = (it: Item, a: Action, confirmed = false) => {
    if (a.disabled) {
      toast(a.disabled);
      return;
    }
    if (a.confirm && !confirmed) {
      setPanel({ item: it, at: 0, filter: "", confirm: a });
      return;
    }
    remember(it, pid);
    void a.run();
  };
  const openPanel = (it: Item | null) => {
    if (!it) return;
    setPanel({ item: it, at: 0, filter: "", confirm: null });
    window.setTimeout(() => panelInput.current?.focus(), 0);
  };
  const closePanel = () => {
    setPanel(null);
    focusMain();
  };

  // ---- Ask: a read-only KeelBot chat, its answer shown here
  const send = async () => {
    const question = text.trim();
    if (
      !question ||
      !pid ||
      ask.status === "sending" ||
      ask.status === "running"
    )
      return;
    setAsk({ status: "sending", question });
    try {
      const s = await api.helperCreate(pid, {
        mode: "ask",
        title: question.slice(0, 80),
      });
      const t = await api.helperTurn(pid, s.id, { text: question });
      setAsk({
        status: "running",
        question,
        sid: s.id,
        call: t.call_id,
        n: t.n,
      });
    } catch (e) {
      const p = errorParts(e);
      setAsk({
        status: "failed",
        question,
        error: p.hint ? `${p.message} ${p.hint}` : p.message,
      });
    }
  };
  const finishedAt = useMemo(
    () =>
      ask.sid
        ? [...events]
            .reverse()
            .find(
              (e) => e.thread_id === ask.sid && e.type === "helper.finished",
            )?.at
        : undefined,
    [events, ask.sid],
  );
  useEffect(() => {
    if (ask.status !== "running" || !ask.sid || !pid) return;
    let live = true;
    const check = async () => {
      try {
        const s = await api.helperSession(pid, ask.sid!);
        const reply = (s.messages ?? [])
          .filter((m) => m.n > (ask.n ?? 0) && m.role !== "user")
          .pop();
        if (live && reply && !s.busy && s.status !== "running")
          setAsk((a) => ({
            ...a,
            status: reply.role === "note" ? "failed" : "done",
            answer: reply.role === "note" ? undefined : reply.text,
            error: reply.role === "note" ? reply.text : undefined,
            session: s,
          }));
      } catch {
        /* the next try */
      }
    };
    void check();
    const t = window.setInterval(check, 1500);
    return () => {
      live = false;
      window.clearInterval(t);
    };
  }, [ask.status, ask.sid, ask.n, pid, finishedAt]);
  const continueInKeelBot = () => {
    if (!pid || !ask.sid) return;
    try {
      localStorage.setItem(`keel2.helper.${pid}.session`, ask.sid);
    } catch {
      /* private window */
    }
    onClose();
    go("helper");
  };
  const steps = ask.call ? (liveSteps[ask.call] ?? []) : [];

  // ---- keys
  const onKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    // the page's own keys (Code's ⌘P, ⇧⇧, the review's F7) stay quiet while the launcher is open
    e.stopPropagation();
    const k = e.nativeEvent;
    const move = (to: number) => {
      e.preventDefault();
      setAt(Math.max(0, Math.min(rows.length - 1, to)));
    };
    if (e.key === "Escape") {
      e.preventDefault();
      if (q.ask && ask.status !== "idle")
        setAsk({ status: "idle", question: "" });
      else if (text) setText("");
      else if (prefix) setPrefix(null);
      else if (scope !== "all") setScope("all");
      else onClose();
      return;
    }
    if (
      e.key === "Backspace" &&
      !text &&
      (e.currentTarget.selectionStart ?? 0) === 0
    ) {
      if (prefix) {
        e.preventDefault();
        setPrefix(null);
        setAsk({ status: "idle", question: "" });
      } else if (scope !== "all") {
        e.preventDefault();
        setScope("all");
      }
      return;
    }
    if (q.ask) {
      if (matches(k, LAUNCHER.ask) && ask.sid) {
        e.preventDefault();
        continueInKeelBot();
      } else if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void send();
      }
      return;
    }
    if (e.key === "ArrowDown" || matches(k, LAUNCHER.down))
      return move(e.altKey ? groupJump(groups, at, 1) : at + 1);
    if (e.key === "ArrowUp" || matches(k, LAUNCHER.up))
      return move(e.altKey ? groupJump(groups, at, -1) : at - 1);
    if (e.key === "Tab") {
      e.preventDefault();
      const i = SCOPES.findIndex((s) => s.id === scope);
      setScope(
        SCOPES[(i + (e.shiftKey ? SCOPES.length - 1 : 1)) % SCOPES.length].id,
      );
      setPrefix(null);
      return;
    }
    if (matches(k, LAUNCHER.actions)) {
      e.preventDefault();
      openPanel(sel);
      return;
    }
    if (matches(k, LAUNCHER.ask)) {
      e.preventDefault();
      if (sel?.ask && pid) ctx.ask(sel.ask);
      else if (sel)
        toast(
          pid
            ? "KeelBot has no question for this one."
            : "Choose a project first.",
        );
      return;
    }
    if (matches(k, LAUNCHER.copy)) {
      e.preventDefault();
      if (sel?.copy) void copyText(ctx, sel.copy);
      return;
    }
    const digit = /^Digit([1-9])$/.exec(k.code ?? "");
    if (digit && (isMac ? k.metaKey : k.ctrlKey) && !k.shiftKey && !k.altKey) {
      e.preventDefault();
      const it = rows[Number(digit[1]) - 1];
      if (it) run(it, it.actions[0]);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (sel) run(sel, sel.actions[0]);
      return;
    }
    // a result's own action keys (⌘O open on GitHub, ⌘⇧O check out)
    const own = sel?.actions.find(
      (a) =>
        a.keys &&
        a.keys !== "enter" &&
        a.keys.includes("+") &&
        matches(k, a.keys),
    );
    if (own && sel) {
      e.preventDefault();
      run(sel, own);
    }
  };

  const onChange = (v: string) => {
    if (!prefix && v && PREFIXES[v[0]]) {
      setPrefix(v[0]);
      setText(v.slice(1).trimStart());
    } else setText(v);
  };

  // ---- the actions panel (⌘K on a result)
  const panelActions = panel
    ? panel.item.actions.filter((a) =>
        a.label.toLowerCase().includes(panel.filter.toLowerCase()),
      )
    : [];
  const onPanelKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    e.stopPropagation();
    if (!panel) return;
    const k = e.nativeEvent;
    if (e.key === "Escape" || matches(k, LAUNCHER.actions)) {
      e.preventDefault();
      if (panel.confirm && panel.item.actions.length > 1)
        setPanel({ ...panel, confirm: null });
      else closePanel();
      return;
    }
    if (panel.confirm) {
      if (e.key === "Enter") {
        e.preventDefault();
        const a = panel.confirm;
        setPanel(null);
        run(panel.item, a, true);
        focusMain();
      }
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setPanel({
        ...panel,
        at: Math.min(panelActions.length - 1, panel.at + 1),
      });
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setPanel({ ...panel, at: Math.max(0, panel.at - 1) });
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const a = panelActions[panel.at];
      if (a) pickAction(a);
      return;
    }
    const own = panel.item.actions.find(
      (a) => a.keys && a.keys.includes("+") && matches(k, a.keys),
    );
    if (own) {
      e.preventDefault();
      pickAction(own);
    }
  };
  const pickAction = (a: Action) => {
    if (!panel) return;
    if (a.confirm) {
      setPanel({ ...panel, confirm: a });
      return;
    }
    setPanel(null);
    run(panel.item, a);
    focusMain();
  };

  const scopeLabel = prefix ? PREFIXES[prefix].label : null;
  const loading = !data;
  const count = rows.length;
  return (
    <div
      className="lx-back"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className={`lx${q.ask ? " ask" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label="Launcher"
        data-own-keys=""
      >
        <div className="lx-top">
          <svg
            className="lx-glass"
            viewBox="0 0 24 24"
            width="18"
            height="18"
            aria-hidden="true"
          >
            <circle
              cx="11"
              cy="11"
              r="7"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            />
            <path
              d="M20 20l-3.5-3.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
          {prefix && (
            <button
              type="button"
              className={`lx-prefix${q.ask ? " ask" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                setPrefix(null);
                focusMain();
              }}
              title="Remove (⌫ on an empty field)"
            >
              <span className="mono">{prefix}</span> {scopeLabel}
            </button>
          )}
          <input
            ref={input}
            autoFocus
            type="text"
            className="lx-in"
            role="combobox"
            aria-expanded={!q.ask}
            aria-controls="lx-list"
            aria-activedescendant={!q.ask && sel ? `lx-${at}` : undefined}
            aria-label={q.ask ? "Ask keel" : "Search keel"}
            value={text}
            placeholder={
              q.ask
                ? "Ask about this project, then press ↩"
                : "Search files, code, pull requests, tasks, actions…"
            }
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={onKey}
          />
          {q.ask ? (
            <span className="lx-meta">read only</span>
          ) : q.text ? (
            <span className="lx-meta" aria-live="polite">
              {count} {count === 1 ? "result" : "results"}
            </span>
          ) : (
            <kbd className="lx-kbd">esc</kbd>
          )}
        </div>

        {!q.ask && (
          <div className="lx-scopes" role="tablist" aria-label="Scope">
            {SCOPES.map((s) => (
              <button
                key={s.id}
                type="button"
                role="tab"
                aria-selected={scope === s.id && !prefix}
                className={scope === s.id && !prefix ? "on" : ""}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => ctx.setScope(s.id)}
              >
                {s.label}
              </button>
            ))}
            <span className="lx-hint">
              <kbd>⇥</kbd> next scope
            </span>
          </div>
        )}

        {q.ask ? (
          <AskView
            ask={ask}
            pid={pid}
            steps={steps.length}
            lastStep={steps[steps.length - 1]?.text}
            onOpen={(path, line) => {
              onClose();
              location.hash = repoHash(path, line);
            }}
            onAgain={() => {
              setAsk({ status: "idle", question: "" });
              focusMain();
            }}
            onContinue={continueInKeelBot}
            onCopy={() => ask.answer && void copyText(ctx, ask.answer)}
          />
        ) : (
          <div className="lx-body">
            <div
              className="lx-list"
              id="lx-list"
              role="listbox"
              aria-label="Results"
              ref={listRef}
            >
              {groups.map((g) => {
                const base = rows.indexOf(g.items[0]);
                return (
                  <div key={g.label} role="group" aria-label={g.label}>
                    <div className="lx-group" aria-hidden="true">
                      {g.label}
                    </div>
                    {g.items.map((it, j) => {
                      const i = base + j;
                      return (
                        <Row
                          key={`${g.label}:${it.id}`}
                          it={it}
                          i={i}
                          on={i === at}
                          onHover={() => setAt(i)}
                          onPick={() => run(it, it.actions[0])}
                        />
                      );
                    })}
                  </div>
                );
              })}
              {!groups.length && (
                <p className="lx-note">
                  {loading ? (
                    <span className="pg-spin" role="status">
                      Loading…
                    </span>
                  ) : q.text ? (
                    `Nothing matches “${q.text}”.`
                  ) : (
                    "Nothing here yet."
                  )}
                </p>
              )}
              {loading && groups.length > 0 && (
                <p className="lx-note">
                  <span className="pg-spin" role="status">
                    Loading pull requests, files and tasks…
                  </span>
                </p>
              )}
              {data?.notes.map((n) => (
                <p key={n} className="lx-note bad">
                  {n}
                </p>
              ))}
            </div>
            {sel?.preview && <PreviewPane pid={pid} it={sel} />}
          </div>
        )}

        <div className="lx-foot">
          {q.ask ? (
            <>
              <span>
                <kbd>↩</kbd> ask
              </span>
              {ask.sid && (
                <span>
                  <kbd>{keyLabel(LAUNCHER.ask)}</kbd> continue in KeelBot
                </span>
              )}
              <span>
                <kbd>esc</kbd> back
              </span>
              <span className="lx-right">
                <kbd>⌫</kbd> on an empty field: back to search
              </span>
            </>
          ) : (
            <>
              <span>
                <kbd>↑↓</kbd> move
              </span>
              <span>
                <kbd>↩</kbd> {sel?.actions[0]?.label.toLowerCase() ?? "open"}
              </span>
              <span>
                <kbd>{keyLabel(LAUNCHER.actions)}</kbd> actions
              </span>
              <span>
                <kbd>{keyLabel(LAUNCHER.ask)}</kbd> ask KeelBot
              </span>
              <span className="lx-right">
                Prefixes <kbd>&gt;</kbd> <kbd>@</kbd> <kbd>#</kbd> <kbd>!</kbd>{" "}
                <kbd>?</kbd>
              </span>
            </>
          )}
        </div>

        {panel && (
          <div
            className="lx-panel"
            role="dialog"
            aria-label={`Actions for ${panel.item.title}`}
          >
            <div className="lx-panel-h">
              <b>{panel.item.title}</b>
              {panel.item.sub && <span>{panel.item.sub}</span>}
            </div>
            {panel.confirm ? (
              <div className="lx-confirm">
                <p>{panel.confirm.confirm}</p>
                <div className="row">
                  <button
                    type="button"
                    className="btn primary sm"
                    onClick={() => {
                      const a = panel.confirm!;
                      setPanel(null);
                      run(panel.item, a, true);
                      focusMain();
                    }}
                  >
                    Yes, {panel.confirm.label.toLowerCase()} <kbd>↩</kbd>
                  </button>
                  <button
                    type="button"
                    className="btn ghost sm"
                    onClick={closePanel}
                  >
                    Cancel <kbd>esc</kbd>
                  </button>
                </div>
              </div>
            ) : (
              <ul className="lx-actions" role="listbox" aria-label="Actions">
                {panelActions.map((a, i) => (
                  <li
                    key={a.id}
                    role="option"
                    aria-selected={i === panel.at}
                    aria-disabled={!!a.disabled}
                    className={`${i === panel.at ? "on" : ""}${a.disabled ? " off" : ""}`}
                    onMouseDown={(e) => e.preventDefault()}
                    onMouseMove={() => setPanel({ ...panel, at: i })}
                    onClick={() => pickAction(a)}
                  >
                    <span>{a.label}</span>
                    {a.disabled ? (
                      <small>{a.disabled}</small>
                    ) : (
                      a.keys && <kbd>{keyLabel(a.keys)}</kbd>
                    )}
                  </li>
                ))}
                {!panelActions.length && (
                  <li className="off">No action matches.</li>
                )}
              </ul>
            )}
            <input
              ref={panelInput}
              type="text"
              className="lx-panel-in"
              aria-label="Search actions"
              placeholder={panel.confirm ? "↩ yes · esc no" : "Search actions…"}
              value={panel.filter}
              readOnly={!!panel.confirm}
              onChange={(e) =>
                setPanel({ ...panel, filter: e.target.value, at: 0 })
              }
              onKeyDown={onPanelKey}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function Hl({ text, hits }: { text: string; hits?: number[] }) {
  return (
    <>
      {runs(text, hits).map(([t, on], i) =>
        on ? <b key={i}>{t}</b> : <span key={i}>{t}</span>,
      )}
    </>
  );
}

const ICONS: Record<string, ReactNode> = {
  wait: <path d="M12 7v6M12 16.5v.5M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z" />,
  pr: (
    <path d="M6 3.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM6 8.5v7M6 15.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM18 15.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM18 15.5V10a3 3 0 0 0-3-3h-4" />
  ),
  file: (
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5" />
  ),
  symbol: <path d="M8 6l-5 6 5 6M16 6l5 6-5 6" />,
  task: <path d="M4 6h16M4 12h10M4 18h13" />,
  flow: <path d="M4 12h4l3-7 4 14 3-7h2" />,
  page: <path d="M4 5h16v14H4zM4 9h16" />,
  action: <path d="M13 3L5 13h6l-1 8 8-10h-6z" />,
  project: <path d="M3 7h7l2 2h9v10H3z" />,
};

function Row({
  it,
  i,
  on,
  onHover,
  onPick,
}: {
  it: Item;
  i: number;
  on: boolean;
  onHover: () => void;
  onPick: () => void;
}) {
  return (
    <div
      id={`lx-${i}`}
      data-i={i}
      role="option"
      aria-selected={on}
      aria-label={[it.title, it.detail, it.sub].filter(Boolean).join(", ")}
      className={`lx-row k-${it.kind}${on ? " on" : ""}`}
      onMouseMove={onHover}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onPick}
    >
      <svg
        className="lx-ic"
        viewBox="0 0 24 24"
        width="16"
        height="16"
        aria-hidden="true"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {ICONS[it.kind]}
      </svg>
      {it.badge && <span className="lx-badge">{it.badge}</span>}
      <span className={`lx-title${it.mono ? " mono" : ""}`}>
        <Hl text={it.title} hits={it.hits} />
        {it.detail && <span className="lx-detail mono">{it.detail}</span>}
      </span>
      {it.sub && <span className="lx-sub">{it.sub}</span>}
      {it.keys && <kbd className="lx-key">{keyLabel(it.keys)}</kbd>}
      {i < 9 && on && (
        <kbd className="lx-key lx-n">{keyLabel(`meta+${i + 1}`)}</kbd>
      )}
    </div>
  );
}

const texts = new Map<string, string>();

/** The selected result in a few lines: a file around its line, a pull request, a task, a flow. */
function PreviewPane({ pid, it }: { pid: string | null; it: Item }) {
  const p = it.preview!;
  const path = p.kind === "code" ? p.path : null;
  const key = pid && path ? `${pid}:${path}` : null;
  const [text, setText] = useState<string | null>(
    key ? (texts.get(key) ?? null) : null,
  );
  const [err, setErr] = useState(false);
  useEffect(() => {
    setErr(false);
    if (!key || !pid || !path) return;
    const known = texts.get(key);
    if (known !== undefined) {
      setText(known);
      return;
    }
    setText(null);
    let live = true;
    const t = window.setTimeout(() => {
      api.raw(pid, path).then(
        (v) => {
          const shown = v.length > 400_000 || v.includes("\u0000") ? "" : v;
          texts.set(key, shown);
          if (live) setText(shown);
        },
        () => live && setErr(true),
      );
    }, 120);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [key, pid, path]);

  if (p.kind === "text")
    return (
      <aside className="lx-prev" aria-label="Preview">
        <div className="lx-prev-h">
          <b>{p.title}</b>
        </div>
        <dl className="lx-facts">
          {p.lines.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
        {p.body && (
          <div className="lx-prev-body">
            <Markdown
              text={p.body.length > 1200 ? p.body.slice(0, 1200) + "…" : p.body}
            />
          </div>
        )}
      </aside>
    );
  const line = p.line ?? 0;
  const all = text?.split("\n") ?? [];
  const from = Math.max(0, (line || 1) - 7);
  const shown = all.slice(from, from + 18);
  return (
    <aside className="lx-prev" aria-label="Preview">
      <div className="lx-prev-h">
        <b className="mono">{p.path.split("/").pop()}</b>
        <span className="mono">
          {p.path}
          {line ? `:${line}` : ""}
        </span>
      </div>
      {err ? (
        <p className="lx-note">No preview.</p>
      ) : text === null ? (
        <p className="lx-note">
          <span className="pg-spin" role="status">
            Reading…
          </span>
        </p>
      ) : !text ? (
        <p className="lx-note">No preview (a big or binary file).</p>
      ) : (
        <pre className="lx-code">
          {shown.map((l, i) => (
            <div key={i} className={from + i + 1 === line ? "hit" : ""}>
              <span className="n">{from + i + 1}</span>
              {l || " "}
            </div>
          ))}
        </pre>
      )}
    </aside>
  );
}

function AskView({
  ask,
  pid,
  steps,
  lastStep,
  onOpen,
  onAgain,
  onContinue,
  onCopy,
}: {
  ask: Ask;
  pid: string | null;
  steps: number;
  lastStep?: string;
  onOpen: (path: string, line?: number) => void;
  onAgain: () => void;
  onContinue: () => void;
  onCopy: () => void;
}) {
  const click = (target: EventTarget | null) => {
    const el = (target as HTMLElement | null)?.closest?.(".cite");
    const link = el ? fileLink(el.textContent ?? "") : null;
    if (link) onOpen(link.path, link.line);
    return !!link;
  };
  if (!pid)
    return (
      <div className="lx-ask">
        <p className="lx-note">
          Choose a project first: KeelBot answers about a project.
        </p>
      </div>
    );
  if (ask.status === "idle")
    return (
      <div className="lx-ask">
        <p className="lx-note">
          KeelBot reads this project and answers here. It only reads: it changes
          nothing. File links in the answer open the file at that line.
        </p>
      </div>
    );
  const s = ask.session;
  return (
    <div className="lx-ask">
      <p className="lx-q">{ask.question}</p>
      {(ask.status === "sending" || ask.status === "running") && (
        <p className="lx-note" role="status">
          <span className="pg-spin">
            KeelBot is reading
            {steps ? ` · ${steps} ${steps === 1 ? "step" : "steps"}` : ""}…
          </span>
          {lastStep && (
            <span className="lx-step mono"> {lastStep.slice(0, 120)}</span>
          )}
        </p>
      )}
      {ask.status === "failed" && (
        <p className="lx-note bad" role="alert">
          {ask.error ?? "KeelBot did not answer."}
        </p>
      )}
      {ask.answer && (
        <div
          className="lx-answer hp-answer"
          onClick={(e) => click(e.target)}
          onKeyDown={(e) => {
            if ((e.key === "Enter" || e.key === " ") && click(e.target))
              e.preventDefault();
          }}
        >
          <Markdown text={ask.answer} breaks />
        </div>
      )}
      {(ask.status === "done" || ask.status === "failed") && (
        <div className="lx-ask-foot">
          {ask.sid && (
            <button
              type="button"
              className="btn primary sm"
              onClick={onContinue}
            >
              Continue in KeelBot <kbd>{keyLabel(LAUNCHER.ask)}</kbd>
            </button>
          )}
          {ask.answer && (
            <button type="button" className="btn ghost sm" onClick={onCopy}>
              Copy the answer
            </button>
          )}
          <button type="button" className="btn ghost sm" onClick={onAgain}>
            Ask something else
          </button>
          {s && (
            <span className="lx-cost mono">
              {(s.tokens_in + s.tokens_out).toLocaleString()} tokens
              {s.cost_usd ? ` · $${s.cost_usd.toFixed(2)}` : ""}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
