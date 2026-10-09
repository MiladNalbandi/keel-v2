// v0.15.7 The menu's project switcher: a button with the project, its phase and branch line and the ◆ waiting count,
// that opens a list with a search box. Type to filter (name, folder or branch), ↑↓ move, Enter switches, Esc clears the
// search and then closes. "Add projects…" at the bottom opens the Add projects panel on All projects. The same switcher
// sits in the menu, on the phone bar and (as a small square) in the folded rail.

import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as KeyEvent,
} from "react";
import { createPortal } from "react-dom";
import type { Project } from "../api";
import { go, useApp, useRoute } from "../state";

/** The project's phase (or "no flow") and branch on one line; `full` adds the flow's name. */
export function projectLine(
  p: { branch: string; flow: string | null; phase: string },
  full = false,
) {
  const now = p.flow ? (full ? `${p.flow} · ${p.phase}` : p.phase) : "no flow";
  return `${now} · ⎇ ${p.branch || "—"}`;
}

/** Opens All projects with the Add projects panel open. */
export const openAddProjects = () => go("projects", "add");

const ADD = "add";
const printable = (e: KeyEvent) =>
  e.key.length === 1 && e.key !== " " && !e.ctrlKey && !e.metaKey && !e.altKey;
// the name, id, folder name and branch (not the whole path: every project shares /workspace)
const fits = (p: Project, f: string) =>
  !f ||
  `${p.name} ${p.id} ${p.root.split("/").pop() ?? ""} ${p.branch ?? ""}`.toLowerCase().includes(f);
/** Two letters for the folded rail's square: "ludus-engine" → "LE", "YegiResearcher" → "YR". */
export function initials(name: string) {
  const words = name
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .split(/[\s_.\-/]+/)
    .filter(Boolean);
  return (
    (words.length > 1 ? words[0][0] + words[1][0] : name.slice(0, 2)) || "?"
  ).toUpperCase();
}

export function ProjectSwitcher({ rail = false }: { rail?: boolean }) {
  const { projects, project, setProjectId, toast } = useApp();
  const { page } = useRoute();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [active, setActive] = useState(0);
  const [place, setPlace] = useState<CSSProperties>({});
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLInputElement>(null);
  const id = rail ? "ps-rail" : "ps";
  const f = filter.trim().toLowerCase();
  const shown = projects.filter((p) => fits(p, f));
  // the rows the keys walk: the projects shown, then "Add projects…"
  const rows: string[] = [...shown.map((p) => p.id), ADD];
  const act = Math.min(active, rows.length - 1);

  const show = (text = "") => {
    setFilter(text);
    const i = text
      ? 0
      : projects
          .filter((p) => fits(p, ""))
          .findIndex((p) => p.id === project?.id);
    setActive(Math.max(0, i));
    setOpen(true);
  };
  const close = (focus = true) => {
    setOpen(false);
    setFilter("");
    if (focus) btn.current?.focus();
  };
  const pick = (rowId: string) => {
    close();
    if (rowId === ADD) {
      openAddProjects();
      return;
    }
    if (rowId === project?.id) return;
    setProjectId(rowId);
    const name = projects.find((p) => p.id === rowId)?.name ?? rowId;
    if (page === "projects") go("flow");
    toast(`Now showing ${name}`);
  };
  const onButtonKey = (e: KeyEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      show();
    } else if (printable(e)) {
      e.preventDefault();
      show(e.key);
    }
  };
  const onBoxKey = (e: KeyEvent) => {
    const last = rows.length - 1;
    const k = e.key;
    const to =
      k === "ArrowDown"
        ? Math.min(act + 1, last)
        : k === "ArrowUp"
          ? Math.max(act - 1, 0)
          : k === "PageDown"
            ? Math.min(act + 8, last)
            : k === "PageUp"
              ? Math.max(act - 8, 0)
              : null;
    if (to !== null) {
      e.preventDefault();
      setActive(to);
    } else if (k === "Enter") {
      e.preventDefault();
      pick(rows[act]);
    } else if (k === "Escape") {
      // only the list closes, not a drawer or a page mode around it; a search is cleared first
      e.preventDefault();
      e.stopPropagation();
      if (filter) {
        setFilter("");
        setActive(0);
      } else close();
    } else if (k === "Tab") close();
  };

  useEffect(() => {
    if (open) box.current?.focus({ preventScroll: true });
  }, [open]);
  useEffect(() => {
    if (open)
      pop.current
        ?.querySelector<HTMLElement>(".ps-opt.is-active")
        ?.scrollIntoView?.({ block: "nearest" });
  }, [open, act]);
  // the list floats under the button (beside it in the folded rail) and stays inside the screen; a click anywhere
  // else closes it
  useLayoutEffect(() => {
    if (!open) return;
    const placeIt = () => {
      const b = btn.current?.getBoundingClientRect();
      if (!b) return;
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const gap = 8;
      if (rail) {
        setPlace({
          left: b.right + gap,
          top: Math.max(gap, Math.min(b.top, vh - 420)),
          maxHeight: vh - 2 * gap,
          width: Math.min(340, vw - b.right - 2 * gap),
        });
        return;
      }
      const below = vh - b.bottom - gap;
      const above = b.top - gap;
      const up = below < 240 && above > below;
      const v = {
        maxHeight: Math.max(160, Math.min(520, (up ? above : below) - 4)),
        ...(up ? { bottom: vh - b.top + 4 } : { top: b.bottom + 4 }),
      };
      if (vw <= 600) return setPlace({ ...v, left: gap, width: vw - 2 * gap }); // a phone: the screen's width
      const width = Math.min(Math.max(b.width, 300), vw - 2 * gap);
      setPlace({
        ...v,
        width,
        left: Math.max(gap, Math.min(b.left, vw - gap - width)),
      });
    };
    const away = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!btn.current?.contains(t) && !pop.current?.contains(t)) close(false);
    };
    placeIt();
    window.addEventListener("resize", placeIt);
    document.addEventListener("mousedown", away);
    return () => {
      window.removeEventListener("resize", placeIt);
      document.removeEventListener("mousedown", away);
    };
  }, [open, rail]);

  const name = project?.name ?? "no projects";
  const optId = (i: number) => `${id}-o${i}`;
  const option = (p: Project, i: number) => (
    <div
      key={p.id}
      id={optId(i)}
      role="option"
      aria-selected={p.id === project?.id}
      aria-label={p.name}
      className={`ps-opt${i === act ? " is-active" : ""}`}
      onMouseDown={(e) => e.preventDefault()}
      onMouseMove={() => i !== act && setActive(i)}
      onClick={() => pick(p.id)}
    >
      <span className="ps-on">{p.name}</span>
      {p.waiting > 0 ? (
        <b className="ps-wait amber" title={`${p.waiting} waiting for you`}>
          ◆ {p.waiting}
        </b>
      ) : (
        <span />
      )}
      <span className="ps-check" aria-hidden="true">
        {p.id === project?.id ? "✓" : ""}
      </span>
      <span className="ps-ol mono" title={p.root}>
        {projectLine(p)}
      </span>
    </div>
  );
  return (
    <>
      <button
        ref={btn}
        type="button"
        id={`${id}-btn`}
        className={rail ? "rail-btn ps-railbtn" : "ps-btn"}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        aria-label={`Project: ${name}${project?.waiting ? `, ${project.waiting} waiting` : ""}`}
        title={
          project
            ? `${project.name} · ${projectLine(project, true)} — switch project`
            : "No project yet"
        }
        onMouseDown={(e) => open && e.preventDefault()}
        onClick={() => (open ? close() : show())}
        onKeyDown={onButtonKey}
      >
        {rail ? (
          <>
            <span className="ps-mono" aria-hidden="true">
              {project ? initials(project.name) : "+"}
            </span>
            {project && project.waiting > 0 && (
              <span className="rail-count" aria-hidden="true">
                {project.waiting > 9 ? "9+" : project.waiting}
              </span>
            )}
          </>
        ) : (
          <>
            <span className="ps-name">{name}</span>
            <svg className="ps-caret" viewBox="0 0 10 6" aria-hidden="true">
              <path d="M1 1l4 4 4-4" />
            </svg>
            {project && (
              <span className="pb-sub ps-sub">
                <span className="pb-line mono">{projectLine(project)}</span>
                {project.waiting > 0 && (
                  <b className="amber">◆ {project.waiting}</b>
                )}
              </span>
            )}
          </>
        )}
      </button>
      {open &&
        createPortal(
          <div
            ref={pop}
            className={`ps-pop${rail ? " ps-pop-rail" : ""}`}
            style={place}
            data-testid="project-switcher"
            onBlur={(e) => {
              const t = e.relatedTarget as Node | null;
              if (t && !btn.current?.contains(t) && !pop.current?.contains(t))
                close(false);
            }}
          >
            <div className="ps-search">
              <span aria-hidden="true">⌕</span>
              <input
                ref={box}
                type="text"
                role="combobox"
                aria-label="Find a project"
                placeholder="Find a project"
                value={filter}
                spellCheck={false}
                autoComplete="off"
                aria-expanded="true"
                aria-controls={`${id}-list`}
                aria-activedescendant={optId(act)}
                aria-autocomplete="list"
                onChange={(e) => {
                  setFilter(e.target.value);
                  setActive(0);
                }}
                onKeyDown={onBoxKey}
              />
              <span className="ps-count">
                {shown.length} of {projects.length}
              </span>
            </div>
            <div
              className="ps-list"
              role="listbox"
              id={`${id}-list`}
              aria-label="Projects"
            >
              {shown.map(option)}
              {!shown.length && (
                <div className="ps-none sub">
                  {projects.length
                    ? `No project matches “${filter.trim()}”.`
                    : "No project yet."}
                </div>
              )}
              <div
                id={optId(shown.length)}
                role="option"
                aria-selected={false}
                aria-label="Add projects…"
                className={`ps-opt ps-add${act === shown.length ? " is-active" : ""}`}
                onMouseDown={(e) => e.preventDefault()}
                onMouseMove={() =>
                  act !== shown.length && setActive(shown.length)
                }
                onClick={() => pick(ADD)}
              >
                <span className="ps-on">+ Add projects…</span>
                <span />
                <span />
                <span className="ps-ol">pick repos from your folders</span>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
