// KeelBot on a page of its own (#/helper): the same chats, modes and models as the panel in the Code page, in one
// wide chat column. A file:line link opens the Code page at that line (0.18.57: as the branch or ref the answer was made
// from has it, `?ref=`); "Back to the code" goes to the Code page.

import { useLayoutEffect, useRef } from "react";
import { codeHref, go, useApp } from "@keel/web-sdk";
import { HelperPanel } from "./HelperPanel";

export function HelperPage({ pid }: { pid: string }) {
  const { project } = useApp();
  const root = useRef<HTMLDivElement>(null);
  // the page fills the window below the app's head, like the Code page's IDE
  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    const set = () => el.style.setProperty("--hp-top", `${Math.max(0, el.getBoundingClientRect().top + window.scrollY)}px`);
    set();
    window.addEventListener("resize", set);
    return () => window.removeEventListener("resize", set);
  }, []);
  return (
    <>
      {/* keel 0.28 the redesign: the page head (title, one line, Back to the code), then the chats and the chat */}
      <header className="kb-ph">
        <div className="kb-ph-t">
          <h1>KeelBot</h1>
          <p>Ask about {project?.name ?? "this project"}. Answers link to the lines. Fix, Edit and Side change files, always inside keel's rules.</p>
        </div>
        <div className="kb-ph-a">
          <button type="button" className="btn ghost kb-back" onClick={() => go("repo")} title="The Code page, with the code and KeelBot side by side"
            aria-label="Back to the code">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M8 7l-5 5 5 5M16 7l5 5-5 5" /></svg>
            Back to the code
          </button>
        </div>
      </header>
      <div ref={root} className="helper-page">
        <HelperPanel pid={pid} layout="page"
          onOpenFile={(path, line, ref) => {
            if (ref) location.hash = codeHref(path, line, ref);
            else go("repo", line ? `${path}:${line}` : path);
          }}
          onOpenDiff={(path) => go("repo", path)}
          onClose={() => go("repo")} />
      </div>
    </>
  );
}
