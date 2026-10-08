// v0.15.4 Recent files (⌘E, like IntelliJ): the tabs you looked at last, newest first, open or closed. The first row is
// the one before the current tab, so ⌘E ↩ goes back to it. Type to filter; ↑↓ move, ↩ opens, Esc closes.

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { IDE_KEYS, keyLabel } from "../../components/review/keymap";
import { FileIcon, Icon } from "./icons";
import { nameOf, type EditorTab } from "./model";

export type RecentItem = { tab: EditorTab; title: string; open: boolean };

export function RecentFiles({
  items,
  onPick,
  onClose,
}: {
  items: RecentItem[];
  onPick: (tab: EditorTab) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [at, setAt] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const needle = q.trim().toLowerCase();
  const shown = useMemo(
    () =>
      items.filter(
        (x) =>
          !needle ||
          x.title.toLowerCase().includes(needle) ||
          x.tab.path.toLowerCase().includes(needle),
      ),
    [items, needle],
  );
  useEffect(() => setAt(0), [needle]);
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-i="${at}"]`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, [at]);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    return () => {
      // back where it was, unless the pick moved the focus on purpose
      if (
        before?.isConnected &&
        (document.activeElement === document.body || !document.activeElement)
      )
        before.focus?.();
    };
  }, []);

  const pick = (i: number) => {
    const x = shown[i];
    if (!x) return;
    onClose();
    onPick(x.tab);
  };

  return createPortal(
    <div
      className="qo-back"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="qo"
        role="dialog"
        aria-modal="true"
        aria-label="Recent files"
        data-own-keys=""
      >
        <div className="qo-top">
          <input
            autoFocus
            type="text"
            className="qo-in"
            role="combobox"
            aria-expanded="true"
            aria-controls="rf-list"
            aria-label="Filter the recent files"
            aria-activedescendant={shown[at] ? `rf-${at}` : undefined}
            placeholder="Recent files: type to filter"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === "Escape") {
                e.preventDefault();
                closeRef.current();
              } else if (e.key === "ArrowDown") {
                e.preventDefault();
                setAt((a) => Math.min(shown.length - 1, a + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setAt((a) => Math.max(0, a - 1));
              } else if (e.key === "Enter") {
                e.preventDefault();
                pick(at);
              }
            }}
          />
          <button type="button" className="qo-x btn sm ghost" onClick={onClose}>
            Close
          </button>
        </div>
        {shown.length ? (
          <ul
            id="rf-list"
            ref={listRef}
            className="qo-list"
            role="listbox"
            aria-label="Recent files"
          >
            {shown.map((x, i) => {
              const file = x.tab.kind === "file" || x.tab.kind === "commit";
              const dir = file
                ? x.tab.path.slice(0, Math.max(0, x.tab.path.lastIndexOf("/")))
                : "";
              return (
                <li
                  key={x.tab.id}
                  id={`rf-${i}`}
                  data-i={i}
                  role="option"
                  aria-selected={i === at}
                  className={i === at ? "on" : ""}
                  onMouseMove={() => setAt(i)}
                  onClick={() => pick(i)}
                >
                  {file ? (
                    <FileIcon name={nameOf(x.tab.path)} />
                  ) : (
                    <Icon
                      name={x.tab.kind === "review" ? "review" : "keel"}
                      size={15}
                    />
                  )}
                  <span className="qo-name">{x.title}</span>
                  {dir && <span className="qo-dir">{dir}</span>}
                  {!x.open && <span className="rf-closed">closed</span>}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="qo-note">
            {items.length
              ? `No recent file matches “${q}”.`
              : `No other file yet: open a few, then ${keyLabel(IDE_KEYS.find((a) => a.id === "recentFiles")!.keys.intellij[0])} goes back to them.`}
          </p>
        )}
      </div>
    </div>,
    document.body,
  );
}
