// Settings › Notify: the channels a person added (a chat's webhook, a mail server), each with its switch and a test, what a
// message holds, and the last messages keel tried to send. A secret is written once and never read back. It imports only
// react and @keel/web-sdk.

import { useState } from "react";
import { Async, EmptyState, del, errorParts, get, Note, Panel, post, Seg, Switch, useApp, useLoad } from "@keel/web-sdk";

export type Channel = {
  id: string; kind: "chat" | "mail"; name: string; on: boolean; events: string[]; project: string | null; host: string; port: number;
  security: string; user: string; from: string; to: string[];
};
export type Sent = { at: string; channel: string; channel_name: string; text: string; ok: boolean; error: string | null };
export type NotifyView = { channels: Channel[]; sent: Sent[]; sends: string; example: string };

const PATH = "/plugins/notify/channels";
export const notifyApi = {
  view: () => get<NotifyView>(PATH),
  add: (body: Record<string, unknown>) => post<Channel>(PATH, body),
  change: (id: string, body: Record<string, unknown>) => post<Channel>(`${PATH}/${encodeURIComponent(id)}`, body),
  remove: (id: string) => del<unknown>(`${PATH}/${encodeURIComponent(id)}`),
  test: (id: string) => post<{ ok: boolean; error: string | null }>(`${PATH}/${encodeURIComponent(id)}/test`),
};

const when = (iso: string) => iso.replace("T", " ").replace(/\.\d+Z?$|Z$/, "");
const word = (e: string) => (e === "failed" ? "a flow failed" : "a flow waits");

/** One line in Settings › Connections: the channels have their own tab. */
export function NotifyConnections() {
  return <p className="ss-sub">Chat and mail messages when a flow waits for you are in <a href="#/settings/notify">Notify</a>.</p>;
}

function AddChannel({ onAdded }: { onAdded: () => Promise<void> }) {
  const { toast } = useApp();
  const [kind, setKind] = useState<"chat" | "mail">("chat");
  const [f, setF] = useState({ name: "", url: "", host: "", port: "587", security: "starttls", user: "", password: "", from: "", to: "" });
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      await notifyApi.add(kind === "chat" ? { kind, name: f.name, url: f.url } : {
        kind, name: f.name, host: f.host, port: Number(f.port), security: f.security, user: f.user, password: f.password, from: f.from,
        to: f.to.split(",").map((x) => x.trim()).filter(Boolean),
      });
      setF({ name: "", url: "", host: "", port: "587", security: "starttls", user: "", password: "", from: "", to: "" });
      toast("Channel added. Send a test to see that it works.");
      await onAdded();
    } catch (err) {
      const p = errorParts(err);
      setProblem(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={add} aria-label="Add a channel" className="grid" style={{ gap: 8 }}>
      <Seg label="Kind of channel" value={kind} onChange={setKind} options={[["chat", "Chat webhook"], ["mail", "Mail"]]} />
      <div className="field"><label htmlFor="nf-f1">Name</label> <input id="nf-f1" value={f.name} onChange={set("name")} placeholder={kind === "chat" ? "Team chat" : "My mail"} /></div>
      {kind === "chat" ? (
        <div className="field">
          <label htmlFor="nf-f2">Webhook address</label> <input id="nf-f2" type="password" autoComplete="off" required value={f.url} onChange={set("url")} placeholder="https://…" />
          <span className="hint">Your chat service makes one: look for "incoming webhook". It holds a secret: keel keeps it encrypted and never shows it again.</span>
        </div>
      ) : (
        <>
          <div className="field"><label htmlFor="nf-f3">Mail server</label> <input id="nf-f3" required value={f.host} onChange={set("host")} placeholder="mail.example.com" /></div>
          <div className="field"><label htmlFor="nf-f4">Port</label> <input id="nf-f4" required inputMode="numeric" value={f.port} onChange={set("port")} /></div>
          <div className="field"><label htmlFor="nf-f9">Security</label> <select id="nf-f9" value={f.security} onChange={set("security")}>
              <option value="starttls">STARTTLS (usually port 587)</option>
              <option value="tls">TLS (usually port 465)</option>
              <option value="none">None (only a server on this computer)</option>
            </select></div>
          <div className="field"><label htmlFor="nf-f5">User</label> <input id="nf-f5" autoComplete="off" value={f.user} onChange={set("user")} /></div>
          <div className="field"><label htmlFor="nf-f6">Password</label> <input id="nf-f6" type="password" autoComplete="off" value={f.password} onChange={set("password")} /></div>
          <div className="field"><label htmlFor="nf-f7">From</label> <input id="nf-f7" required value={f.from} onChange={set("from")} placeholder="keel@example.com" /></div>
          <div className="field"><label htmlFor="nf-f8">To</label> <input id="nf-f8" required value={f.to} onChange={set("to")} placeholder="you@example.com, more@example.com" /></div>
        </>
      )}
      {problem && <Note tone="err" role="alert">{problem}</Note>}
      <div><button className="btn primary" type="submit" disabled={busy}>{busy ? "Adding…" : "Add the channel"}</button></div>
    </form>
  );
}

function ChannelRow({ c, onChanged }: { c: Channel; onChanged: () => Promise<void> }) {
  const { toast } = useApp();
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    try {
      await fn();
      if (done) toast(done);
    } catch (err) {
      const p = errorParts(err);
      toast(p.hint ? `${p.message} ${p.hint}` : p.message);
    } finally {
      setBusy(false);
      await onChanged();
    }
  };
  const toggle = (e: string) => {
    const has = c.events.includes(e);
    const next = has ? c.events.filter((x) => x !== e) : [...c.events, e];
    if (next.length) void run(() => notifyApi.change(c.id, { events: next }));
  };
  const test = () => run(async () => {
    const r = await notifyApi.test(c.id);
    toast(r.ok ? "The test message went out." : `The test failed: ${r.error}`);
  });
  return (
    <li aria-label={c.name} style={{ padding: "8px 0", borderTop: "1px solid var(--border)" }}>
      <b>{c.name}</b> <span className="sub">{c.kind === "chat" ? `chat webhook at ${c.host}` : `mail through ${c.host}:${c.port} to ${c.to.join(", ")}`}</span>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", marginTop: 4 }}>
        <Switch on={c.on} disabled={busy} onChange={(on: boolean) => void run(() => notifyApi.change(c.id, { on }))}>On</Switch>
        {["waiting", "failed"].map((e) => (
          <label key={e}><input type="checkbox" disabled={busy} checked={c.events.includes(e)} onChange={() => toggle(e)} /> {word(e)}</label>
        ))}
        <button type="button" className="btn sm" disabled={busy} onClick={() => void test()}>Send a test</button>
        <button type="button" className="btn sm ghost" disabled={busy} onClick={() => void run(() => notifyApi.remove(c.id), "Channel removed.")}>Remove</button>
      </div>
    </li>
  );
}

export function NotifyTab() {
  const view = useLoad("notify", () => notifyApi.view());
  return (
    <Async r={view} what="Loading the channels">
      {(v) => (
        <div className="grid" style={{ gap: 12 }}>
          <Panel title="What is sent">
            <p>{v.sends}</p>
            <p className="sub">A message looks like this:</p>
            <blockquote>{v.example}</blockquote>
          </Panel>
          <Panel title={`Channels (${v.channels.length})`}>
            {v.channels.length ? (
              <ul aria-label="Channels" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {v.channels.map((c) => <ChannelRow key={c.id} c={c} onChanged={view.reload} />)}
              </ul>
            ) : <EmptyState title="No channel yet">keel sends nothing outside until you add one.</EmptyState>}
          </Panel>
          <Panel title="Add a channel"><AddChannel onAdded={view.reload} /></Panel>
          {v.sent.length > 0 && (
            <Panel title="Last messages">
              <ul aria-label="Last messages" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {v.sent.map((s, i) => (
                  <li key={i}><span className="mono">{when(s.at)}</span> {s.channel_name}: {s.ok ? "sent" : <b>failed: {s.error}</b>} <span className="sub">{s.text}</span></li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      )}
    </Async>
  );
}
