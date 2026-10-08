// v0.14.0 a review's changed files as a folder tree (like IntelliJ's Changes view): folders first, a chain of single
// folders shown as one (src/main/kotlin), each folder with how many of its files you viewed, each file with its viewed
// box, status, comments and +/− lines.

import { useMemo, useState } from "react";
import { FileIcon, Icon } from "../../pages/repo/icons";
import type { ChangedFile } from "../../reviewApi";

type Node = { name: string; path: string; dirs: Node[]; files: ChangedFile[] };

function build(files: ChangedFile[]): Node {
  const root: Node = { name: "", path: "", dirs: [], files: [] };
  for (const f of files) {
    const parts = f.path.split("/");
    let at = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const path = parts.slice(0, i + 1).join("/");
      let d = at.dirs.find((x) => x.path === path);
      if (!d) {
        d = { name: parts[i], path, dirs: [], files: [] };
        at.dirs.push(d);
      }
      at = d;
    }
    at.files.push(f);
  }
  const sort = (n: Node): Node => {
    n.dirs.sort((a, b) => a.name.localeCompare(b.name));
    n.files.sort((a, b) => a.path.localeCompare(b.path));
    n.dirs = n.dirs.map(sort);
    return n;
  };
  // a folder with only one folder in it is shown as one row: src/main/kotlin
  const compact = (n: Node): Node => {
    let c = n;
    while (c.files.length === 0 && c.dirs.length === 1 && c.path !== "") {
      const d = c.dirs[0];
      c = { ...d, name: `${c.name}/${d.name}` };
    }
    return { ...c, dirs: c.dirs.map(compact) };
  };
  const r = sort(root);
  return { ...r, dirs: r.dirs.map(compact) };
}

/** The files in the order the tree shows them (Next file, Next change across files). */
export function treeOrder(files: ChangedFile[]): ChangedFile[] {
  const out: ChangedFile[] = [];
  const walk = (n: Node) => {
    n.dirs.forEach(walk);
    out.push(...n.files);
  };
  walk(build(files));
  return out;
}

const all = (n: Node): ChangedFile[] => [...n.dirs.flatMap(all), ...n.files];
const tone = (s: string) =>
  s === "A" ? "add" : s === "D" ? "del" : s === "R" ? "ren" : "mod";

export function FileTree({
  files,
  viewed,
  comments,
  active,
  onOpen,
  onViewed,
}: {
  files: ChangedFile[];
  viewed: string[];
  comments: (path: string) => number;
  active: string | null;
  onOpen: (path: string, pin: boolean) => void;
  onViewed: (path: string) => void;
}) {
  const tree = useMemo(() => build(files), [files]);
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const seen = new Set(viewed);
  const toggle = (p: string) =>
    setClosed((c) => {
      const n = new Set(c);
      if (n.has(p)) n.delete(p);
      else n.add(p);
      return n;
    });

  const fileRow = (f: ChangedFile, depth: number) => {
    const n = comments(f.path);
    const name = f.path.split("/").pop()!;
    return (
      <div
        key={f.path}
        role="treeitem"
        aria-selected={active === f.path}
        className={`rv-tf${active === f.path ? " on" : ""}`}
        style={{ paddingLeft: 8 + depth * 14 }}
      >
        <input
          type="checkbox"
          checked={seen.has(f.path)}
          aria-label={`Viewed ${f.path}`}
          onChange={() => onViewed(f.path)}
        />
        <button
          type="button"
          className={`rv-tf-b t-${tone(f.status)}`}
          onClick={() => onOpen(f.path, false)}
          onDoubleClick={() => onOpen(f.path, true)}
          title={f.from ? `${f.path} (renamed from ${f.from})` : f.path}
          aria-label={`Open the changes of ${f.path}`}
        >
          <FileIcon name={name} />
          <span
            className={`ex-name${seen.has(f.path) ? " rv-seen" : ""}${f.status === "D" ? " gone" : ""}`}
          >
            {name}
          </span>
          <span className={`ex-deco t-${tone(f.status)}`}>{f.status}</span>
          {n > 0 && (
            <span className="rv-cnt" aria-label={`${n} comments`}>
              💬{n}
            </span>
          )}
          <span className="rv-cnt">
            <span className="add">+{f.added}</span>{" "}
            <span className="del">−{f.removed}</span>
          </span>
        </button>
      </div>
    );
  };

  const dirRow = (d: Node, depth: number): JSX.Element => {
    const inside = all(d);
    const done = inside.filter((f) => seen.has(f.path)).length;
    const open = !closed.has(d.path);
    return (
      <div key={d.path} role="group" aria-label={d.path}>
        <button
          type="button"
          role="treeitem"
          aria-expanded={open}
          className="rv-td"
          style={{ paddingLeft: 6 + depth * 14 }}
          onClick={() => toggle(d.path)}
        >
          <span className={`ex-tw${open ? " open" : ""}`} aria-hidden="true">
            ▸
          </span>
          <Icon name="files" size={14} />
          <span className="rv-td-n">{d.name}</span>
          <span className={`rv-cnt${done === inside.length ? " rv-all" : ""}`}>
            {done}/{inside.length}
          </span>
        </button>
        {open && (
          <>
            {d.dirs.map((x) => dirRow(x, depth + 1))}
            {d.files.map((f) => fileRow(f, depth + 1))}
          </>
        )}
      </div>
    );
  };

  return (
    <div className="rv-tree" role="tree" aria-label="Changed files">
      {tree.dirs.map((d) => dirRow(d, 0))}
      {tree.files.map((f) => fileRow(f, 0))}
    </div>
  );
}
