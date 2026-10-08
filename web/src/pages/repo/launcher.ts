// The Code page in the launcher (⌘K): the project's files (matched by path), the code graph's hits, Focus mode, and
// the link that opens a file at a line. Registered by ./register.tsx (slot launcher.source).

import { api, type GraphHit } from "../../api";
import {
  askAction,
  copyAction,
  goHash,
  type Ctx,
} from "../../components/launcher/sources";
import type { Item } from "../../components/launcher/model";
import { hashFor } from "../../routes";
import type { LauncherSourceItem } from "../../sdk/slots";
import { FOCUS_EVENT, FOCUS_KEYS, rankFiles, repoHash } from "./model";

export function fileItem(
  ctx: Ctx,
  path: string,
  line: number | null,
  score?: number,
  hits?: number[],
): Item {
  const at = line ? `${path}:${line}` : path;
  const slash = path.lastIndexOf("/");
  return {
    id: `file:${path}`,
    kind: "file",
    title: path.slice(slash + 1),
    detail: slash > 0 ? path.slice(0, slash) : undefined,
    mono: true,
    sub: line ? `line ${line}` : undefined,
    actions: [
      {
        id: "open",
        label: line ? `Open at line ${line}` : "Open in Code",
        keys: "enter",
        run: goHash(ctx, repoHash(path, line ?? undefined)),
      },
      askAction(ctx, `Explain ${at}: what it does and how it is used.`),
      copyAction(ctx, at, "Copy the path"),
    ],
    ask: `Explain ${at}: what it does and how it is used.`,
    copy: at,
    preview: { kind: "code", path, line },
    score,
    // the hits are on the whole path; the title shows the name only
    hits: hits?.filter((h) => h > slash).map((h) => h - slash - 1),
    ranked: score !== undefined,
  };
}

export function symbolItem(ctx: Ctx, h: GraphHit, score: number): Item {
  const at = `${h.file}:${h.line}`;
  return {
    id: `sym:${h.file}:${h.line}:${h.name}`,
    kind: "symbol",
    title: h.name,
    sub: at,
    mono: true,
    badge: h.kind,
    actions: [
      {
        id: "open",
        label: "Go to the declaration",
        keys: "enter",
        run: goHash(ctx, repoHash(h.file, h.line)),
      },
      askAction(
        ctx,
        `Explain ${h.name} (${at}): what it does and who calls it.`,
      ),
      copyAction(ctx, at, "Copy file:line"),
    ],
    ask: `Explain ${h.name} (${at}): what it does and who calls it.`,
    copy: at,
    preview: { kind: "code", path: h.file, line: h.line },
    score,
    ranked: true,
  };
}


/** Focus mode in Code: on the Code page it turns Focus mode on and off; elsewhere it opens Code in Focus mode. */
function focusItem(ctx: Ctx): Item {
  const run = () => {
    ctx.close();
    if (location.hash.startsWith("#/repo")) window.dispatchEvent(new CustomEvent(FOCUS_EVENT));
    else {
      try {
        localStorage.setItem("keel2.repo.focus", "1");
      } catch {
        /* private window */
      }
      location.hash = hashFor("repo");
    }
  };
  return {
    id: "act:focus",
    kind: "action",
    title: "Focus mode in Code",
    keys: FOCUS_KEYS,
    sub: "only the code, like an IDE",
    actions: [{ id: "run", label: "Focus mode in Code", keys: "enter", run }],
  };
}

export const codeLauncher: LauncherSourceItem = {
  id: "code",
  title: "Files",
  order: 20,
  load: (pid, notes) => api.repoFiles(pid).then((v) => v.files, notes.failed("Files")),
  items: (ctx, data, q, { code, filesPer }) => {
    const out: Item[] = [];
    const files = data as string[] | null;
    if (q.kinds.includes("file") && files)
      rankFiles(files, q.text, filesPer).forEach((r, i) =>
        out.push(fileItem(ctx, r.path, q.line, 60 - i * 3, r.hits)),
      );
    if (q.kinds.includes("symbol"))
      code.forEach((h, i) => out.push(symbolItem(ctx, h, 55 - i * 3)));
    return out;
  },
  recent: (ctx, r) => {
    if (r.kind === "file") return fileItem(ctx, r.id.slice(5), null);
    if (r.kind !== "symbol") return null;
    const m = r.id.match(/^sym:(.+):(\d+):([^:]+)$/);
    return m
      ? symbolItem(
          ctx,
          { id: r.id, name: m[3], kind: "", file: m[1], line: Number(m[2]), unit: "", group: "" },
          0,
        )
      : null;
  },
  actions: (ctx) => [focusItem(ctx)],
  fileHash: repoHash,
};
