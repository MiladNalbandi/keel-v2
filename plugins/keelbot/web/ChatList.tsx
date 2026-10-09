// v0.15.2 KeelBot's chat list (history): every chat of this project, newest first, with search and folders. A folder
// can be made, renamed and deleted (its chats stay, with no folder); a chat can be opened, renamed, moved into a folder
// and deleted. Deleting asks first, right here in the list. The engine keeps the folders, so every browser sees them.
// In the Code page's panel the list opens over the conversation (the Chats button); on the KeelBot page it is a column.

import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { errorParts, useApp, type HelperSession } from "@keel/web-sdk";
import { kb, type HelperFolder } from "./keelbotApi";
import { ago, dropDraft, getFolded, groupChats, saveFolded } from "./chats";
import { markSeen } from "./unread";

type Props = {
  pid: string;
  chats: HelperSession[];
  folders: HelperFolder[];
  current: string | null;
  /** new answers per chat (not seen yet) */
  unread: Record<string, number>;
  onOpen: (sid: string) => void;
  /** the KeelBot page's column has its own New chat button (the panel has one in its head) */
  onNew?: () => void;
  /** a chat or a folder changed: read the lists again */
  onChanged: () => void;
  /** a chat was deleted (when it was the open one, the panel starts a new chat) */
  onDeleted: (sid: string) => void;
  /** the panel: back to the conversation */
  onClose?: () => void;
};

const MODE_TAG: Record<string, string> = { ask: "Ask", fix: "Fix", side: "Side" };

/** A name to type (a new folder, a new title): Enter saves, Escape cancels. */
function NameForm({ label, initial = "", save, max, onSave, onCancel }: {
  label: string; initial?: string; save: string; max: number; onSave: (name: string) => void; onCancel: () => void;
}) {
  const [v, setV] = useState(initial);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (v.trim()) onSave(v.trim());
  };
  return (
    <form className="hp-name" onSubmit={submit}>
      <input aria-label={label} value={v} maxLength={max} autoFocus placeholder={label}
        onChange={(e) => setV(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onCancel(); } }} />
      <button type="submit" className="btn sm primary" disabled={!v.trim()}>{save}</button>
      <button type="button" className="btn sm" onClick={onCancel}>Cancel</button>
    </form>
  );
}

/** "Are you sure?" inside the page (never the browser's confirm box). */
export function ConfirmRow({ text, yes, busy, onYes, onNo }: { text: string; yes: string; busy?: boolean; onYes: () => void; onNo: () => void }) {
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => btn.current?.focus(), []);
  return (
    <div className="hp-confirm" role="alertdialog" aria-label={yes}
      onKeyDown={(e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onNo(); } }}>
      <p>{text}</p>
      <div className="hp-confirm-btns">
        <button ref={btn} type="button" className="btn sm hp-del" disabled={busy} onClick={onYes}>{yes}</button>
        <button type="button" className="btn sm" disabled={busy} onClick={onNo}>Cancel</button>
      </div>
    </div>
  );
}

export function ChatList({ pid, chats, folders, current, unread, onOpen, onNew, onChanged, onDeleted, onClose }: Props) {
  const { toast } = useApp();
  const [query, setQuery] = useState("");
  const [folded, setFolded] = useState<string[]>(() => getFolded(pid));
  const [menu, setMenu] = useState<string | null>(null);        // "c:<chat id>" or "f:<folder id>": its actions are open
  const [confirm, setConfirm] = useState<string | null>(null);  // the same keys: its delete waits for a yes
  const [naming, setNaming] = useState<string | null>(null);    // the same keys, or "new": a name is being typed
  const [busy, setBusy] = useState(false);
  const groups = useMemo(() => groupChats(chats, folders, query), [chats, folders, query]);
  const searching = !!query.trim();
  const folderName = (id?: string | null) => folders.find((f) => f.id === id)?.name;

  const fold = (id: string) => {
    const next = folded.includes(id) ? folded.filter((x) => x !== id) : [...folded, id];
    setFolded(next);
    saveFolded(pid, next);
  };

  const act = async (what: () => Promise<unknown>, failed: string) => {
    setBusy(true);
    try {
      await what();
      setMenu(null);
      setConfirm(null);
      setNaming(null);
      onChanged();
      return true;
    } catch (e) {
      const p = errorParts(e);
      toast(`${failed}: ${p.message}${p.hint ? ` ${p.hint}` : ""}`);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const newFolder = (name: string) => act(() => kb.folderCreate(pid, name), "No folder made");
  const renameFolder = (f: HelperFolder, name: string) => act(() => kb.folderRename(pid, f.id, name), "Not renamed");
  const deleteFolder = async (f: HelperFolder) => {
    if (await act(() => kb.folderDelete(pid, f.id), "Not deleted")) toast(`Folder ${f.name} deleted. Its chats are kept, with no folder.`);
  };
  const renameChat = (s: HelperSession, title: string) => act(() => kb.patch(pid, s.id, { title }), "Not renamed");
  const moveChat = async (s: HelperSession, folder: string) => {
    if (await act(() => kb.patch(pid, s.id, { folder }), "Not moved")) toast(folder ? `Moved to ${folderName(folder)}.` : "Moved out of the folder.");
  };
  const deleteChat = async (s: HelperSession) => {
    if (await act(() => kb.remove(pid, s.id), "Not deleted")) {
      markSeen(pid, s.id);
      dropDraft(pid, s.id);
      onDeleted(s.id);
    }
  };

  const chatRow = (s: HelperSession) => {
    const key = `c:${s.id}`;
    const n = unread[s.id] ?? 0;
    const running = s.status === "running" || s.busy;
    return (
      <li key={s.id} className={`hp-chat${current === s.id ? " on" : ""}`}>
        {naming === key ? (
          <NameForm label="Chat name" initial={s.title} save="Save" max={120} onSave={(t) => void renameChat(s, t)} onCancel={() => setNaming(null)} />
        ) : (
          <div className="hp-chat-row">
            <button type="button" className="hp-chat-open" onClick={() => onOpen(s.id)} aria-current={current === s.id ? "true" : undefined}
              title={s.title}>
              <span className="hp-chat-t">{s.title}</span>
              <span className="hp-chat-m">
                <span className={`hp-chat-mode ${s.mode}`}>{MODE_TAG[s.mode] ?? s.mode}</span>
                {running ? <span className="hp-chat-run">answering…</span> : <span>{ago(s.updated_at)}</span>}
              </span>
              {n > 0 && <span className="hp-chat-new" aria-label={n === 1 ? "1 new answer" : `${n} new answers`}>{n}</span>}
            </button>
            <button type="button" className="hp-more" aria-label={`More for ${s.title}`} aria-expanded={menu === key}
              onClick={() => { setMenu(menu === key ? null : key); setConfirm(null); }}>⋯</button>
          </div>
        )}
        {menu === key && naming !== key && confirm !== key && (
          <div className="hp-acts" role="group" aria-label={`Actions for ${s.title}`}>
            <button type="button" className="hp-tb" onClick={() => setNaming(key)}>Rename</button>
            <label className="hp-move">
              <span>Folder</span>
              <select aria-label={`Folder of ${s.title}`} value={s.folder && folderName(s.folder) ? s.folder : ""} disabled={busy}
                onChange={(e) => void moveChat(s, e.target.value)}>
                <option value="">No folder</option>
                {folders.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
              </select>
            </label>
            <button type="button" className="hp-tb hp-tb-del" onClick={() => setConfirm(key)}>Delete</button>
          </div>
        )}
        {confirm === key && (
          <ConfirmRow yes="Delete chat" busy={busy} onYes={() => void deleteChat(s)} onNo={() => setConfirm(null)}
            text={`Delete “${s.title}”? Its messages are gone for good.${s.mode === "side" && s.worktree ? ` Its worktree and the branch ${s.branch ?? ""} are deleted too.` : ""}`} />
        )}
      </li>
    );
  };

  const total = chats.length;
  return (
    <nav className="hp-list" id="hp-list" aria-label="KeelBot chats">
      <div className="hp-list-head">
        <b>Chats</b>
        <span className="hp-list-n">{total}</span>
        <span className="hp-list-tools">
          {onNew && <button type="button" className="hp-tb hp-tb-new" onClick={onNew} aria-label="New chat" title="Start a new chat">+ New chat</button>}
          <button type="button" className="hp-tb" onClick={() => setNaming(naming === "new" ? null : "new")} aria-expanded={naming === "new"}
            title="Make a folder to sort your chats">+ New folder</button>
          {onClose && <button type="button" className="hp-tb" onClick={onClose} title="Back to the open chat">Back to the chat</button>}
        </span>
      </div>
      <input type="search" className="hp-search" aria-label="Search chats" placeholder="Search chats by name or folder…" value={query}
        onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape" && query) { e.preventDefault(); e.stopPropagation(); setQuery(""); } }} />
      {naming === "new" && <NameForm label="Folder name" save="Make folder" max={60} onSave={(n) => void newFolder(n)} onCancel={() => setNaming(null)} />}

      <div className="hp-list-body">
        {!total && <p className="hp-list-empty">No chats yet. Ask something and your chat is kept here.</p>}
        {total > 0 && searching && !groups.length && <p className="hp-list-empty">No chat matches “{query.trim()}”.</p>}
        {groups.map((g) => {
          const id = g.folder?.id ?? "none";
          const key = `f:${id}`;
          // with no folder at all the chats are one plain list; the "No folder" group shows only when it has chats
          if (!g.folder && !folders.length) return <ul key={id} className="hp-chats">{g.chats.map(chatRow)}</ul>;
          if (!g.folder && !g.chats.length) return null;
          const open = searching || !folded.includes(id);
          const name = g.folder?.name ?? "No folder";
          return (
            <section key={id} className="hp-group" aria-label={g.folder ? `Folder ${name}` : name}>
              {naming === key && g.folder ? (
                <NameForm label="Folder name" initial={g.folder.name} save="Save" max={60} onSave={(n) => void renameFolder(g.folder!, n)} onCancel={() => setNaming(null)} />
              ) : (
                <div className="hp-fold">
                  <button type="button" className="hp-fold-btn" aria-expanded={open} onClick={() => fold(id)} disabled={searching}>
                    <span className="hp-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
                    <span className="hp-fold-name">{name}</span>
                    <span className="hp-fold-n">{g.chats.length}</span>
                  </button>
                  {g.folder && (
                    <button type="button" className="hp-more" aria-label={`More for the folder ${name}`} aria-expanded={menu === key}
                      onClick={() => { setMenu(menu === key ? null : key); setConfirm(null); }}>⋯</button>
                  )}
                </div>
              )}
              {g.folder && menu === key && naming !== key && confirm !== key && (
                <div className="hp-acts" role="group" aria-label={`Actions for the folder ${name}`}>
                  <button type="button" className="hp-tb" onClick={() => setNaming(key)}>Rename folder</button>
                  <button type="button" className="hp-tb hp-tb-del" onClick={() => setConfirm(key)}>Delete folder</button>
                </div>
              )}
              {g.folder && confirm === key && (
                <ConfirmRow yes="Delete folder" busy={busy} onYes={() => void deleteFolder(g.folder!)} onNo={() => setConfirm(null)}
                  text={`Delete the folder “${name}”? Its chats are kept, with no folder.`} />
              )}
              {open && (g.chats.length
                ? <ul className="hp-chats">{g.chats.map(chatRow)}</ul>
                : <p className="hp-list-empty sm">No chats here. Use ⋯ on a chat to move it into this folder.</p>)}
            </section>
          );
        })}
      </div>
    </nav>
  );
}
