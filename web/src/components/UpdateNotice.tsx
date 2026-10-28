// keel 0.18.66 the update notice: a newer keel is out (the api's daily release check, api updates/ReleaseCheck). Top
// right, once per new version: what is new (the first lines of the release notes), "All changes", a note when flows
// run, "Copy the update command" (keel cannot update itself yet: that needs the host helper of a later release) and
// "Later", which hides it until the next version. Which version was dismissed is kept in this browser (localStorage).

import { useEffect, useState } from "react";
import { copyText } from "../clipboard";
import { helpApi, type UpdatesView } from "../helpApi";
import { plural } from "../format";
import { useApp } from "../state";
import { useWelcomeUi } from "./welcomeState";

const SEEN_KEY = "keel2.update-dismissed";
const AGAIN_MS = 60 * 60 * 1000;

function dismissed(): string | null {
  try {
    return localStorage.getItem(SEEN_KEY);
  } catch {
    return null;
  }
}

export function dismissUpdate(version: string) {
  try {
    localStorage.setItem(SEEN_KEY, version);
  } catch {
    /* a private window: it shows again next time */
  }
}

/** The first points of the release notes: its list items, else its first lines; no headings, no Markdown marks. */
export function whatsNew(notes: string, max = 4): string[] {
  const lines = notes.replace(/\r\n/g, "\n").split("\n").map((l) => l.trim()).filter(Boolean);
  const items = lines.filter((l) => /^([-*+]|\d+\.)\s+/.test(l)).map((l) => l.replace(/^([-*+]|\d+\.)\s+/, ""));
  const pick = (items.length ? items : lines.filter((l) => !l.startsWith("#") && !l.startsWith("|") && !l.startsWith("```")));
  return pick.slice(0, max).map((l) =>
    l.replace(/\*\*|__|`/g, "").replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").replace(/\s+/g, " ").slice(0, 160),
  );
}

export function UpdateNotice() {
  const [u, setU] = useState<UpdatesView | null>(null);
  const [gone, setGone] = useState<string | null>(dismissed);
  const [copied, setCopied] = useState<boolean | null>(null);
  const { projects } = useApp();
  // the first-run welcome comes first: the notice waits until it is closed (or hidden behind its button)
  const welcome = useWelcomeUi();
  useEffect(() => {
    let off = false;
    const load = () => helpApi.updates().then((v) => !off && setU(v), () => undefined);
    void load();
    const t = window.setInterval(load, AGAIN_MS);
    return () => {
      off = true;
      window.clearInterval(t);
    };
  }, []);
  if (!u?.newer || !u.latest || gone === u.latest || (welcome.open && !welcome.hidden)) return null;
  const latest = u.latest;
  const later = () => {
    dismissUpdate(latest);
    setGone(latest);
  };
  const flows = projects.reduce((a, p) => a + (p.flows ?? (p.running ? 1 : 0)), 0);
  const points = whatsNew(u.notes);
  return (
    <div className="upd" role="dialog" aria-labelledby="upd-h" aria-describedby="upd-d">
      <div className="upd-top">
        <span className="upd-new">New</span>
        <h2 id="upd-h">keel {latest} is out</h2>
        <button type="button" className="upd-x" aria-label="Close" title="Later" onClick={later}>✕</button>
      </div>
      <p id="upd-d" className="upd-sub">You run {u.current}.{points.length ? " What is new:" : ""}</p>
      {points.length > 0 && (
        <ul className="upd-list">
          {points.map((p, i) => <li key={i}>{p}</li>)}
        </ul>
      )}
      <a className="upd-all" href={u.url} target="_blank" rel="noreferrer">All changes ›</a>
      <div className="upd-foot">
        {flows > 0 && (
          <p className="upd-note">
            {plural(flows, "flow")} {flows === 1 ? "is" : "are"} running. keel restarts for the update (about 30 s); they go on after it.
          </p>
        )}
        <div className="upd-btns">
          <button type="button" className="btn primary" onClick={() => void copyText(u.command).then(setCopied)}>
            {copied ? "Copied" : "Copy the update command"}
          </button>
          <button type="button" className="btn ghost" onClick={later}>Later</button>
        </div>
        {copied && <p className="upd-note" role="status">Now run it in a terminal on this computer.</p>}
        {copied === false && <p className="upd-note" role="status">Select the command below and copy it.</p>}
        <p className="upd-small">
          In a terminal: <code>{u.command}</code> · <a href="#/settings/access?at=updates" onClick={() => setGone(latest)}>Settings › Updates</a>
        </p>
      </div>
    </div>
  );
}
