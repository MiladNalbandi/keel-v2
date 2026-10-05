// Bell drawer (Inbox + Settings) and the in-app pop-ups (top right, with an action button).

import { useState } from "react";
import "./inbox.css";
import type { Notification as Note, NotificationSettings } from "../api";
import { clock } from "../format";
import { askPermission, EVTYPES, NTONE, permission, playSound } from "../notify";
import { useApp } from "../state";
import { Drawer, Tabs } from "./ui";

function NoteSettings() {
  const { nset, saveNset, project, notifyLocal, toast, showMascot, setShowMascot } = useApp();
  const [perm, setPerm] = useState(permission());
  const set = (patch: Partial<NotificationSettings>) => saveNset({ ...nset, ...patch });
  return (
    <>
      <div className="field">
        <span className="lab">Sound</span>
        <label className="chk"><input type="checkbox" id="n-sound" checked={nset.sound} onChange={(e) => set({ sound: e.target.checked })} /> Play a sound</label>
        <div className="row">
          <select id="n-tone" aria-label="Sound" value={nset.tone}
            onChange={(e) => {
              const tone = e.target.value as NotificationSettings["tone"];
              set({ tone });
              playSound("review", { ...nset, tone });
            }}>
            <option value="chime">Chime (two notes)</option>
            <option value="soft">Soft (one note)</option>
            <option value="off">Silent</option>
          </select>
          <label className="row" htmlFor="n-vol" style={{ gap: 6 }}>
            <span className="sub">Volume</span>
            <input type="range" id="n-vol" min={0} max={1} step={0.1} value={nset.volume}
              onChange={(e) => {
                const volume = Number(e.target.value);
                set({ volume });
                playSound("review", { ...nset, volume });
              }} />
          </label>
          <button className="btn sm" type="button" onClick={() => {
            playSound("review", nset);
            window.setTimeout(() => playSound("failed", nset), 700);
            toast("Needs-you chime, then the failure tone.");
          }}>Test sound</button>
        </div>
        <span className="hint">Failures use a lower tone, so you can tell them apart without looking.</span>
      </div>
      <div className="field">
        <span className="lab">Pop-ups</span>
        <label className="chk"><input type="checkbox" checked={nset.popup} onChange={(e) => set({ popup: e.target.checked })} /> Show a pop-up in keel (top right, with a button to act)</label>
        <label className="chk"><input type="checkbox" checked={nset.desktop} onChange={(e) => set({ desktop: e.target.checked })} /> Desktop notification, also when keel is in the background</label>
        <div className="row">
          <span className="sub">Browser permission: <b>{perm}</b></span>
          {perm !== "granted" && perm !== "unsupported" && (
            <button className="btn sm" type="button" onClick={async () => {
              const p = await askPermission();
              setPerm(p);
              if (p === "granted") set({ desktop: true });
              else toast("The browser did not allow desktop notifications. Change it in the site settings.");
            }}>Allow</button>
          )}
        </div>
      </div>
      <div className="field">
        <span className="lab">Tell me about</span>
        <div className="grid" style={{ gap: 6 }}>
          {EVTYPES.map(([k, l, d]) => (
            <label key={k} className="chk" style={{ alignItems: "flex-start" }}>
              <input type="checkbox" checked={nset.kinds[k]} onChange={(e) => set({ kinds: { ...nset.kinds, [k]: e.target.checked } })} />{" "}
              <span><b>{l}</b> <span className="sub">{d}</span></span>
            </label>
          ))}
        </div>
      </div>
      <div className="field">
        <span className="lab">From</span>
        <label className="radio"><input type="radio" name="n-scope" checked={nset.scope === "all"} onChange={() => set({ scope: "all" })} /><span>All projects</span></label>
        <label className="radio"><input type="radio" name="n-scope" checked={nset.scope === "project"} onChange={() => set({ scope: "project" })} /><span>Only {project?.name ?? "this project"}</span></label>
      </div>
      <div className="field">
        <span className="lab">keel</span>
        <label className="chk switch">
          <input type="checkbox" role="switch" id="n-mascot" checked={showMascot} onChange={(e) => setShowMascot(e.target.checked)} /> Show keel
        </label>
        <span className="hint">The little hull by the bell jumps when something arrives and says what it is. Saved in this browser.</span>
      </div>
      <label className="chk"><input type="checkbox" checked={nset.quiet} onChange={(e) => set({ quiet: e.target.checked })} /> Do not disturb — keep them in the list, no sound or pop-up</label>
      <button className="btn" type="button" onClick={() => notifyLocal({
        id: `test-${Date.now()}`, type: "review", project_id: project?.id ?? "", title: "Test notification",
        body: "This is how a gate that waits for you looks", link: "#/flow", at: new Date().toISOString(), read: false,
      })}>Send a test notification</button>
    </>
  );
}

export function NotificationDrawer({ onClose }: { onClose: () => void }) {
  const { notes, unread, markAllRead, markRead, deleteNote, clearNotes, openNote, projects } = useApp();
  const [tab, setTab] = useState<"inbox" | "settings">("inbox");
  const [confirmClear, setConfirmClear] = useState(false);
  const pname = (id: string) => projects.find((p) => p.id === id)?.name ?? id;
  return (
    <Drawer title="Notifications" onClose={onClose} id="notedrawer">
      <Tabs value={tab} onChange={setTab} label="Notifications" options={[["inbox", `Inbox${unread ? ` (${unread})` : ""}`], ["settings", "Settings"]]} />
      {tab === "settings" ? <NoteSettings /> : notes.length ? (
        <>
          <ul className="note-list" aria-label="Notifications" style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {notes.map((n) => (
              <li key={n.id} className="note-row">
                <button type="button" className={`note ${n.read ? "" : "unread"} ${n.done ? "done" : ""} t-${NTONE[n.type] ?? "run"}`}
                  onClick={() => { openNote(n); onClose(); }}>
                  <span className="row" style={{ justifyContent: "space-between" }}><b>{n.title}</b><span className="hint">{clock(n.at, false)}</span></span>
                  <span className="sub">{pname(n.project_id)} · {n.body}</span>
                  {n.done && <span className="note-done">✓ decided</span>}
                </button>
                <span className="note-acts">
                  {!n.read && <button type="button" className="btn sm ghost" aria-label={`Mark read: ${n.title}`} title="Mark read" onClick={() => markRead(n.id)}>✓</button>}
                  <button type="button" className="btn sm ghost" aria-label={`Delete: ${n.title}`} title="Delete" onClick={() => deleteNote(n.id)}>×</button>
                </span>
              </li>
            ))}
          </ul>
          <div className="note-foot">
            {unread > 0 && <button className="btn sm ghost" type="button" onClick={markAllRead}>Mark all as read</button>}
            {confirmClear ? (
              <>
                <span className="sub">Delete all {notes.length}?</span>
                <button className="btn sm warn" type="button" onClick={() => { clearNotes(); setConfirmClear(false); }}>Yes, clear all</button>
                <button className="btn sm ghost" type="button" onClick={() => setConfirmClear(false)}>Keep them</button>
              </>
            ) : <button className="btn sm ghost" type="button" onClick={() => setConfirmClear(true)}>Clear all</button>}
          </div>
        </>
      ) : <div className="empty">Nothing yet.</div>}
    </Drawer>
  );
}

function PopupCard({ note, onClose }: { note: Note; onClose: () => void }) {
  const { openNote, projects } = useApp();
  const pname = projects.find((p) => p.id === note.project_id)?.name ?? note.project_id;
  return (
    <div className={`pop t-${NTONE[note.type] ?? "run"}`} role="status">
      <div className="pop-h">
        <b>{note.title}</b>
        <button type="button" className="btn sm ghost" aria-label="Dismiss" onClick={onClose}>×</button>
      </div>
      <span className="sub">{pname} · {note.body}</span>
      <div className="row">
        <button className={`btn sm ${note.type === "review" ? "warn" : ""}`} type="button" onClick={() => { openNote(note); onClose(); }}>
          {note.type === "review" ? "Review now" : "Open"}
        </button>
        <span className="hint">{clock(note.at, false)}</span>
      </div>
    </div>
  );
}

export function Popups() {
  const { popups, dismissPopup } = useApp();
  if (!popups.length) return null;
  return (
    <div className="popstack" aria-live="polite" data-testid="popups">
      {popups.map((p) => <PopupCard key={p.key} note={p.note} onClose={() => dismissPopup(p.key)} />)}
    </div>
  );
}
