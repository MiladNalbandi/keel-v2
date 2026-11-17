// The code graph index in the Code page's status bar (v0.17.1, docs/plugins/22-one-plugins-page.md): "Index: ready ·
// 412 files · 3,100 symbols · 5 min ago" and "↻ Rebuild", next to the project name. Until 0.17.0 they were in the page
// head. The index is the Graph plugin's api (/index): without the Graph (the Review set) there is no item and no request.

import { useState } from "react";
import { errorParts, plural, useApp, useLoad, usePages } from "@keel/web-sdk";
import { codeApi as api, type IndexStatus } from "./codeApi";

export function IndexItem({ pid }: { pid: string }) {
  const graph = usePages().some((p) => p.id === "graph");
  return graph ? <IndexRow pid={pid} /> : null;
}

function IndexRow({ pid }: { pid: string }) {
  const { toast } = useApp();
  const idx = useLoad(`index:${pid}`, () => api.index(pid));
  const [busy, setBusy] = useState(false);
  const i = idx.data;
  const rebuild = async () => {
    setBusy(true);
    try {
      idx.setData(await api.rebuildIndex(pid));
    } catch (e) {
      toast(errorParts(e).message);
    } finally {
      setBusy(false);
    }
  };
  if (!i) return null;
  return (
    <span className="sb-index" role="group" aria-label="Code graph index">
      <span className={`sb-i sb-wide sb-itext${i.status === "failed" && !noGraph(i) ? " bad" : ""}`} title={i.error ?? "The code graph agents use before grep"}>
        {indexText(i)}
      </span>
      <button type="button" className="sb-i" onClick={rebuild} disabled={busy || i.status === "indexing"}
        title="Rebuild the code graph index">
        <span aria-hidden="true">↻</span> {busy ? "Starting…" : "Rebuild"}
      </button>
    </span>
  );
}

function ago(iso: string): string {
  const min = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60_000));
  if (Number.isNaN(min)) return "";
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  if (min < 48 * 60) return `${Math.round(min / 60)} h ago`;
  return `${Math.round(min / 1440)} days ago`;
}

/** A keel without CodeGraph (the keel app, a native keel without npm): no failure, agents use grep. */
const noGraph = (i: IndexStatus) => i.status === "failed" && !!i.error?.startsWith("CodeGraph is not installed");

function indexText(i: IndexStatus): string {
  if (i.status === "indexing") return "Index: indexing…";
  if (noGraph(i)) return "Index: no code graph on this keel · agents use grep";
  if (i.status === "failed") return `Index: failed: ${i.error ?? "unknown reason"}`;
  if (i.status === "idle") return "Index: not built yet";
  const when = i.indexed_at ? ` · ${ago(i.indexed_at)}` : "";
  return `Index: ready · ${plural(i.files, "file")} · ${plural(i.symbols, "symbol")}${when}`;
}
