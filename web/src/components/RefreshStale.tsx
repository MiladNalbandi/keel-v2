// "Refresh stale": starts the knowledge-refresh flow (one librarian per stale section) and links to Flow.

import { useState } from "react";
import { api, errorParts } from "../api";
import { useApp } from "../state";
import { ErrorBox, GoButton } from "./ui";

export function RefreshStaleButton({ pid, sections, className = "btn sm" }: { pid: string; sections: string[]; className?: string }) {
  const { reloadProjects, toast } = useApp();
  const [busy, setBusy] = useState(false);
  const [started, setStarted] = useState<string | null>(null);
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      const t = await api.refreshWiki(pid, sections);
      setStarted(t?.thread_id ?? "");
      toast(`Knowledge refresh started for ${sections.length ? sections.join(", ") : "the stale sections"}.`);
      void reloadProjects();
    } catch (e) {
      setErr(errorParts(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="grid" style={{ gap: 6, justifyItems: "start" }}>
      <span className="row">
        <button className={className} type="button" onClick={run} disabled={busy}
          title={sections.length ? `Librarians rewrite: ${sections.join(", ")}` : "Librarians rewrite only the stale sections"}>
          {busy ? "Starting…" : "Refresh stale"}
        </button>
        {started !== null && (
          <span className="row" role="status">
            <span className="sub">Refresh is running{started ? <> · thread <span className="mono">{started}</span></> : null}.</span>
            <GoButton to="flow">Watch it in Flow</GoButton>
          </span>
        )}
      </span>
      {err && <ErrorBox error={err} />}
    </span>
  );
}
