// v0.14.0 the review's popups (usages, declarations, Find action, Submit, Merge, line actions, recent places) and its keys
// (keymap.ts: IntelliJ's macOS keymap or VS Code's) while a review's file tab is the active editor tab.

import { useEffect, useState } from "react";
import { askAssistant } from "../../sdk/assistant";
import { ErrorBox, Pill } from "../ui";
import { useApp } from "../../state";
import {
  prLabel,
  prWord,
  type Places,
  type SubmitEvent,
} from "../../reviewApi";
import {
  ACTIONS,
  actionFor,
  keyLabel,
  keysFor,
  readKeymap,
  saveKeymap,
  type Keymap,
  type ReviewAction,
} from "./keymap";
import { neighbour } from "./ReviewSide";
import * as R from "./store";

export function ReviewLayer({
  pid,
  active,
}: {
  pid: string;
  active: { key: string; path: string; view: R.FileView } | null;
}) {
  R.useReviewStore();
  const { toast } = useApp();
  const ui = R.uiState();
  const [keymap, setKeymap] = useState<Keymap>(readKeymap);

  useEffect(() => {
    const on = (e: Event) =>
      setKeymap(((e as CustomEvent).detail as Keymap) ?? readKeymap());
    window.addEventListener("keel:keymap", on);
    return () => window.removeEventListener("keel:keymap", on);
  }, []);

  useEffect(() => {
    if (!active) return;
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing =
        !!t &&
        (t.tagName === "TEXTAREA" ||
          t.tagName === "INPUT" ||
          t.isContentEditable);
      const a = actionFor(e, keymap);
      if (!a || (typing && a !== "closePopup")) return;
      if (
        R.uiState().popup &&
        a !== "closePopup" &&
        a !== "back" &&
        a !== "forward"
      )
        return;
      e.preventDefault();
      e.stopPropagation();
      run(a);
    };
    window.addEventListener("keydown", on, true);
    return () => window.removeEventListener("keydown", on, true);
  }); // re-bound every render: it reads the store's latest state

  const selected = (): string | null => {
    const s = window.getSelection()?.toString().trim();
    return s && /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(s) ? s : null;
  };

  function run(a: ReviewAction) {
    if (!active) return;
    const { key, path, view } = active;
    const r = R.review(pid, key);
    const v = r.view;
    const s = R.uiState();
    const at: R.Place | null =
      s.current && s.current.key === key && s.current.path === path
        ? s.current
        : null;
    switch (a) {
      case "nextChange":
      case "prevChange": {
        if (!v) return;
        const dir = a === "nextChange" ? 1 : -1;
        const cur =
          s.change && s.change.key === key && s.change.path === path
            ? s.change.index
            : -1;
        const next = cur + dir;
        if (view === "diff" && next >= 0 && next < s.changes) {
          R.setUi({ change: { key, path, index: next, n: Date.now() } });
          return;
        }
        const other = neighbour(v, path, dir);
        if (!other) return;
        R.openPlace({
          key,
          path: other,
          view: "diff",
          line: null,
          side: "RIGHT",
        });
        R.setUi({
          change: { key, path: other, index: dir > 0 ? 0 : 1e9, n: Date.now() },
        });
        return;
      }
      case "nextFile":
      case "prevFile": {
        const other = v && neighbour(v, path, a === "nextFile" ? 1 : -1);
        if (other)
          R.openPlace({
            key,
            path: other,
            view: "diff",
            line: null,
            side: "RIGHT",
          });
        return;
      }
      case "nextFinding":
      case "prevFinding": {
        const list = (r.ai?.findings?.result?.findings ?? []).filter(
          (f) => f.path && r.ai?.decisions[f.id]?.decision !== "dismissed",
        );
        if (!list.length) {
          toast(
            r.ai?.findings
              ? "No open finding."
              : "Run Find problems in the Review window first.",
          );
          return;
        }
        const i =
          (s.findingIdx + (a === "nextFinding" ? 1 : -1) + list.length) %
          list.length;
        R.setUi({ findingIdx: i });
        const f = list[i];
        R.openPlace({
          key,
          path: f.path!,
          view: v?.files.some((x) => x.path === f.path) ? "diff" : "code",
          line: f.line,
          side: f.side,
        });
        return;
      }
      case "declaration":
      case "usages": {
        const w = selected();
        if (!w) {
          toast("Select a name first (double-click it), or ⌘-click it.");
          return;
        }
        void R.lookUp(
          pid,
          key,
          w,
          a === "declaration" ? "declaration" : "usages",
        ).then((msg) => msg && toast(msg));
        return;
      }
      case "jumpToSource":
        R.openPlace({
          key,
          path,
          view: "code",
          line: at?.line ?? null,
          side: "RIGHT",
        });
        return;
      case "back":
      case "forward": {
        const h = s.history;
        const i = h.at + (a === "back" ? -1 : 1);
        if (i < 0 || i >= h.list.length) return;
        R.setUi({ history: { ...h, at: i }, popup: null });
        R.openPlace(h.list[i], { remember: false });
        return;
      }
      case "explain": {
        const w = selected();
        const line = at?.line;
        askAssistant(
          `Explain ${w ? `\`${w}\` at ` : ""}${path}${line ? `:${line}` : ""} in ${v?.kind === "pr" ? `${prWord(v.host)} ${prLabel(v.host, v.number)}` : `branch ${v?.branch}`}: ` +
            `what it does and why it changed. Read it with git show ${v?.head_sha.slice(0, 12)}:${path}.`,
        );
        return;
      }
      case "lineActions":
        if (at) R.setUi({ popup: { kind: "line", at } });
        else toast("Click a line first.");
        return;
      case "toggleViewed":
        void R.toggleViewed(pid, key, path);
        return;
      case "submit":
        if (v?.can_post) R.setUi({ popup: { kind: "submit", key } });
        else toast(`Only a ${prWord(v?.host)} takes a review.`);
        return;
      case "findAction":
        R.setUi({ popup: { kind: "actions" } });
        return;
      case "recent":
        R.setUi({ popup: { kind: "recent" } });
        return;
      case "closePopup":
        R.setUi({ popup: null, composing: null });
        return;
    }
  }

  const close = () => R.setUi({ popup: null });
  const p = ui.popup;
  if (!p) return null;
  if (p.kind === "places") {
    const v = R.review(pid, p.key).view;
    const changed = new Set(v?.files.map((f) => f.path) ?? []);
    return (
      <PlacesPopup
        title={p.title}
        places={p.places}
        onClose={close}
        onPick={(x) => {
          close();
          R.openPlace({
            key: p.key,
            path: x.path,
            view: changed.has(x.path) ? "diff" : "code",
            line: x.line,
            side: "RIGHT",
          });
        }}
      />
    );
  }
  if (p.kind === "recent") {
    const list = [...ui.history.list].reverse();
    return (
      <PlacesPopup
        title="Recent places"
        onClose={close}
        places={{
          symbol: "",
          ref: "",
          truncated: false,
          places: list.map((x) => ({
            path: x.path,
            line: x.line ?? 1,
            text: x.view === "code" ? "whole file" : "changes",
            declaration: false,
            test: false,
            changed: x.view === "diff",
          })),
        }}
        onPick={(x) => {
          close();
          const pl = list.find(
            (y) => y.path === x.path && (y.line ?? 1) === x.line,
          );
          if (pl) R.openPlace(pl);
        }}
      />
    );
  }
  if (p.kind === "actions") {
    return (
      <FindAction
        keymap={keymap}
        onKeymap={(k) => {
          saveKeymap(k);
          setKeymap(k);
        }}
        onClose={close}
        onRun={(a) => {
          close();
          if (active) run(a);
          else toast("Open a file of a review first.");
        }}
      />
    );
  }
  if (p.kind === "line") {
    const v = R.review(pid, p.at.key).view;
    const can = !!v && (v.can_post || v.kind === "branch");
    return (
      <Modal title={`${p.at.path}:${p.at.line}`} onClose={close}>
        <div
          className="rv-places"
          ref={(el) => el?.querySelector<HTMLButtonElement>("button")?.focus()}
        >
          {can && (
            <button
              type="button"
              className="rv-place rv-action"
              onClick={() => R.setUi({ popup: null, composing: p.at })}
            >
              <span>Add a comment on this line</span>
            </button>
          )}
          <button
            type="button"
            className="rv-place rv-action"
            onClick={() => {
              close();
              run("explain");
            }}
          >
            <span>Ask keel to explain it</span>
            <span className="rv-keys">{keysFor("explain", keymap)}</span>
          </button>
          <button
            type="button"
            className="rv-place rv-action"
            onClick={() => {
              void navigator.clipboard
                ?.writeText(`${p.at.path}:${p.at.line}`)
                .catch(() => undefined);
              toast("Copied.");
              close();
            }}
          >
            <span>Copy file:line</span>
          </button>
        </div>
      </Modal>
    );
  }
  if (p.kind === "submit")
    return (
      <SubmitDialog
        pid={pid}
        reviewKey={p.key}
        initial={p.event}
        keymap={keymap}
        onClose={close}
        onDone={(msg) => {
          close();
          toast(msg);
        }}
      />
    );
  if (p.kind === "merge")
    return (
      <MergeDialog
        pid={pid}
        reviewKey={p.key}
        onClose={close}
        onDone={(msg) => {
          close();
          toast(msg);
        }}
      />
    );
  return null;
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="qo-back"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="qo rv-pop"
        role="dialog"
        aria-label={title}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.stopPropagation();
            onClose();
          }
        }}
      >
        <div className="rv-pop-h">
          <b>{title}</b>
          <button
            type="button"
            className="btn sm ghost"
            onClick={onClose}
            aria-label="Close"
          >
            Esc
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function PlacesPopup({
  title,
  places,
  onPick,
  onClose,
}: {
  title: string;
  places: Places;
  onPick: (p: Places["places"][number]) => void;
  onClose: () => void;
}) {
  const [i, setI] = useState(0);
  const list = places.places;
  return (
    <Modal title={title} onClose={onClose}>
      <div
        className="rv-places"
        role="listbox"
        aria-label={title}
        tabIndex={0}
        ref={(el) => el?.focus()}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setI((x) => Math.min(list.length - 1, x + 1));
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            setI((x) => Math.max(0, x - 1));
          }
          if (e.key === "Enter" && list[i]) {
            e.preventDefault();
            onPick(list[i]);
          }
        }}
      >
        {list.map((x, n) => (
          <button
            key={`${x.path}:${x.line}:${n}`}
            type="button"
            role="option"
            aria-selected={n === i}
            className={`rv-place${n === i ? " on" : ""}`}
            onClick={() => onPick(x)}
          >
            <span className="rv-place-h">
              <span className="mono">{x.path}</span>
              {x.declaration && <Pill tone="run">declaration</Pill>}
              {x.test && <Pill tone="ok">test</Pill>}
              {x.changed && <Pill tone="warn">changed</Pill>}
            </span>
            <span className="mono rv-dim">
              {x.line} {x.text}
            </span>
          </button>
        ))}
        {places.truncated && (
          <span className="rv-dim">
            There are more; the first ones are shown.
          </span>
        )}
      </div>
      <span className="rv-dim rv-pop-f">
        ↩ open · ↑↓ move · {keysFor("back")} back · Esc close
      </span>
    </Modal>
  );
}

function FindAction({
  keymap,
  onKeymap,
  onRun,
  onClose,
}: {
  keymap: Keymap;
  onKeymap: (k: Keymap) => void;
  onRun: (a: ReviewAction) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const list = ACTIONS.filter(
    (a) =>
      a.id !== "findAction" &&
      a.id !== "closePopup" &&
      a.label.toLowerCase().includes(q.toLowerCase()),
  );
  return (
    <Modal title="Find action" onClose={onClose}>
      <div className="qo-top">
        <input
          className="qo-in"
          aria-label="Find an action"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          autoFocus
          placeholder="Type an action: usages, next change, submit…"
          onKeyDown={(e) => {
            if (e.key === "Enter" && list[0]) onRun(list[0].id);
          }}
        />
      </div>
      <div className="rv-places">
        {list.map((a) => (
          <button
            key={a.id}
            type="button"
            className="rv-place rv-action"
            onClick={() => onRun(a.id)}
          >
            <span>{a.label}</span>
            <span className="rv-keys">
              {a.keys[keymap].map(keyLabel).join(" or ")}
            </span>
          </button>
        ))}
      </div>
      <div className="rv-pop-f rv-row">
        <span className="rv-dim">Keymap:</span>
        <button
          type="button"
          className={`rv-chip${keymap === "intellij" ? " on" : ""}`}
          aria-pressed={keymap === "intellij"}
          onClick={() => onKeymap("intellij")}
        >
          IntelliJ (macOS)
        </button>
        <button
          type="button"
          className={`rv-chip${keymap === "vscode" ? " on" : ""}`}
          aria-pressed={keymap === "vscode"}
          onClick={() => onKeymap("vscode")}
        >
          VS Code
        </button>
        <span className="rv-dim">On a MacBook, F1, F2 and F7 need fn.</span>
      </div>
    </Modal>
  );
}

function SubmitDialog({
  pid,
  reviewKey,
  initial,
  keymap,
  onClose,
  onDone,
}: {
  pid: string;
  reviewKey: string;
  initial?: SubmitEvent;
  keymap: Keymap;
  onClose: () => void;
  onDone: (msg: string) => void;
}) {
  const r = R.review(pid, reviewKey);
  const v = r.view!;
  const own = !!v.mine;
  const [event, setEvent] = useState<SubmitEvent>(
    initial && !own ? initial : "COMMENT",
  );
  const [body, setBody] = useState("");
  const gitlab = v.host?.kind === "gitlab";
  const opts: [SubmitEvent, string, string][] = [
    ["COMMENT", "Comment", "General feedback, no decision."],
    [
      "APPROVE",
      "Approve",
      own ? "You cannot approve your own change." : "Ready to merge.",
    ],
    [
      "REQUEST_CHANGES",
      "Request changes",
      own
        ? "You cannot request changes on your own change."
        : gitlab
          ? "Sent as a note, and your approval is taken back."
          : "Must be fixed before merging.",
    ],
  ];
  const lines = v.drafts.filter((d) => d.path && d.line).length;
  const send = async () => {
    const res = await R.submit(pid, reviewKey, event, body);
    if (res)
      onDone(
        `Review sent: ${res.posted} line comment${res.posted === 1 ? "" : "s"}${res.in_body ? `, ${res.in_body} in the text` : ""}.`,
      );
  };
  return (
    <Modal
      title={`Submit your review of ${prLabel(v.host, v.number)}`}
      onClose={onClose}
    >
      <form
        className="rv-submit"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <textarea
          className="rv-ta"
          aria-label="Your review"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          autoFocus
          placeholder="A summary for the author (optional)"
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              e.stopPropagation();
              void send();
            }
          }}
        />
        {opts.map(([k, l, t]) => (
          <label key={k} className="rv-radio">
            <input
              type="radio"
              name="rv-event"
              checked={event === k}
              disabled={own && k !== "COMMENT"}
              onChange={() => setEvent(k)}
            />
            <span>
              <b>{l}</b>
              <br />
              <span className="rv-dim">{t}</span>
            </span>
          </label>
        ))}
        <span className="rv-dim">
          {lines} line comment{lines === 1 ? "" : "s"} and{" "}
          {v.drafts.length - lines} general comment
          {v.drafts.length - lines === 1 ? "" : "s"} go with it.
        </span>
        {r.err && <ErrorBox error={r.err} />}
        <div className="rv-row">
          <button
            type="submit"
            className="btn sm primary"
            disabled={r.busy === "submit"}
          >
            {r.busy === "submit" ? "Sending…" : "Submit review"}
          </button>
          <button type="button" className="btn sm ghost" onClick={onClose}>
            Cancel
          </button>
          <span className="rv-dim">{keysFor("submit", keymap)}</span>
        </div>
      </form>
    </Modal>
  );
}

function MergeDialog({
  pid,
  reviewKey,
  onClose,
  onDone,
}: {
  pid: string;
  reviewKey: string;
  onClose: () => void;
  onDone: (msg: string) => void;
}) {
  const r = R.review(pid, reviewKey);
  const v = r.view!;
  const gitlab = v.host?.kind === "gitlab";
  const [method, setMethod] = useState("merge");
  const [del, setDel] = useState(v.same_repo);
  const methods: [string, string][] = gitlab
    ? [
        ["merge", "Merge commit"],
        ["squash", "Squash"],
      ]
    : [
        ["merge", "Merge commit"],
        ["squash", "Squash and merge"],
        ["rebase", "Rebase and merge"],
      ];
  const go = async () => {
    const res = await R.merge(pid, reviewKey, method, del);
    if (res) onDone(`${prLabel(v.host, v.number)} merged.`);
  };
  return (
    <Modal title={`Merge ${prLabel(v.host, v.number)}`} onClose={onClose}>
      <div className="rv-submit">
        <p>
          <b>{v.title}</b>
          <br />
          <span className="rv-dim mono">
            {v.base} ← {v.branch}
          </span>
        </p>
        {v.mergeable === false && (
          <p className="rv-warn">
            {v.host?.kind === "gitlab" ? "GitLab" : "GitHub"} says it cannot be
            merged yet{v.merge_state ? ` (${v.merge_state})` : ""}: checks,
            reviews or conflicts.
          </p>
        )}
        {methods.map(([k, l]) => (
          <label key={k} className="rv-radio">
            <input
              type="radio"
              name="rv-merge"
              checked={method === k}
              onChange={() => setMethod(k)}
            />
            <span>{l}</span>
          </label>
        ))}
        {v.same_repo && (
          <label className="chk">
            <input
              type="checkbox"
              checked={del}
              onChange={(e) => setDel(e.target.checked)}
            />{" "}
            Delete the branch {v.branch} after the merge
          </label>
        )}
        {r.err && <ErrorBox error={r.err} />}
        <div className="rv-row">
          <button
            type="button"
            className="btn sm primary"
            disabled={r.busy === "merge"}
            onClick={() => void go()}
          >
            {r.busy === "merge" ? "Merging…" : `Merge into ${v.base}`}
          </button>
          <button type="button" className="btn sm ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </Modal>
  );
}
