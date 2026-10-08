// v0.15.4 the key cheat sheet: ⌘/ (Ctrl+/ off a Mac) on any page, or ? when you are not typing. It lists the keys for
// the page you are on (and every page with "Every page" or a search), from the one list in keys.ts, in the keymap
// chosen for Code (IntelliJ or VS Code). Esc closes it and the focus goes back where it was.

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AREAS,
  KEYS,
  isTyping,
  modalOpen,
  rowText,
  type KeyRow,
} from "../keys";
import { useRoute } from "../state";
import {
  keyLabel,
  matches,
  readKeymap,
  saveKeymap,
  type Keymap,
} from "./review/keymap";

/** Mounted once by the shell. */
export function KeySheet() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      const chord = matches(e, KEYS.sheet);
      const plain =
        e.key === KEYS.sheetPlain &&
        !e.metaKey &&
        !e.ctrlKey &&
        !e.altKey &&
        !isTyping(e.target);
      if (!chord && !plain) return;
      // another popup has the keys: only ⌘/ closes this one
      if (!chord && modalOpen()) return;
      if (chord && !document.getElementById("key-sheet") && modalOpen()) return;
      e.preventDefault();
      setOpen((o) => (plain ? true : !o));
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
  return open ? <Sheet onClose={() => setOpen(false)} /> : null;
}

function Keys({ r }: { r: KeyRow }) {
  const all = [...(r.keys ?? []).map(keyLabel), ...(r.raw ?? [])];
  return (
    <>
      {all.map((k, i) => (
        <span key={i}>
          {i > 0 && <span className="ks-or"> or </span>}
          <kbd>{k}</kbd>
        </span>
      ))}
    </>
  );
}

function Sheet({ onClose }: { onClose: () => void }) {
  const route = useRoute();
  const [q, setQ] = useState("");
  const [every, setEvery] = useState(false);
  const [keymap, setKeymap] = useState<Keymap>(readKeymap);
  const input = useRef<HTMLInputElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    // a frame later: a key tool that sends the ? as its own text event must not type it into the search
    const raf = requestAnimationFrame(() => input.current?.focus());
    const onMap = (e: Event) =>
      setKeymap(((e as CustomEvent).detail as Keymap) ?? readKeymap());
    window.addEventListener("keel:keymap", onMap);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("keel:keymap", onMap);
      before?.focus?.();
    };
  }, []);

  const needle = q.trim().toLowerCase();
  const areas = useMemo(
    () =>
      AREAS.filter(
        (a) => needle || every || !a.pages || a.pages.includes(route.page),
      )
        .map((a) => ({
          ...a,
          list: a
            .rows(keymap)
            .filter((r) => !needle || rowText(r).includes(needle)),
        }))
        .filter((a) => a.list.length)
        // this page's keys first, then the ones that work everywhere
        .sort((a, b) => Number(!a.pages?.includes(route.page)) - Number(!b.pages?.includes(route.page))),
    [needle, every, keymap, route.page],
  );

  return createPortal(
    <div
      className="ks-back"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="ks"
        id="key-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ks-h"
        data-own-keys=""
        onKeyDown={(e) => {
          // the page's own keys stay quiet while the sheet is open
          e.stopPropagation();
          if (e.key === "Escape") {
            e.preventDefault();
            closeRef.current();
          } else if (matches(e.nativeEvent, KEYS.sheet)) {
            e.preventDefault();
            closeRef.current();
          }
        }}
      >
        <header className="ks-top">
          <h2 id="ks-h">Keyboard keys</h2>
          <span
            className="seg ks-map"
            role="group"
            aria-label="Keymap for Code"
          >
            {(["intellij", "vscode"] as Keymap[]).map((k) => (
              <button
                key={k}
                type="button"
                aria-pressed={keymap === k}
                onClick={() => {
                  saveKeymap(k);
                  setKeymap(k);
                }}
              >
                {k === "intellij" ? "IntelliJ" : "VS Code"}
              </button>
            ))}
          </span>
          <button type="button" className="btn sm ghost" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="ks-find">
          <input
            ref={input}
            type="search"
            className="ks-in"
            aria-label="Find a key"
            placeholder="Find a key: tab, menu, ⌘E…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <label className="ks-every">
            <input
              type="checkbox"
              checked={every}
              onChange={(e) => setEvery(e.target.checked)}
            />{" "}
            Every page
          </label>
        </div>
        <div className="ks-body">
          {/* the columns sit inside the scroll box: columns in a box of fixed height would spill sideways */}
          <div className="ks-cols">
          {areas.map((a) => (
            <section
              key={a.id}
              className="ks-area"
              aria-labelledby={`ks-a-${a.id}`}
            >
              <h3 id={`ks-a-${a.id}`}>{a.title}</h3>
              <dl>
                {a.list.map((r, i) => (
                  <div key={i} className="ks-row">
                    <dt>
                      {r.label}
                      {r.note && <small>{r.note}</small>}
                    </dt>
                    <dd>
                      <Keys r={r} />
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
          </div>
          {!areas.length && <p className="ks-none">No key matches “{q}”.</p>}
        </div>
      </div>
    </div>,
    document.body,
  );
}
